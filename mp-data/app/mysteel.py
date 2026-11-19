# -*- coding: utf-8 -*-
"""Mysteel 豆粕频道抓取（只读探查模式）。

当前阶段只把提取结果写入 probe 表，不进入正式序列。
口径以 Mysteel 为准，正式切换前用 probe 数据与手工值做对照。

用法：
    python3 app/mysteel.py            # 抓今天的文章
    python3 app/mysteel.py --days 3   # 回看最近 3 天
    python3 app/mysteel.py --show     # 查看已存记录
"""
import datetime
import json
import os
import re
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

LIST_URL = "https://siliao.mysteel.com/doupo/"
SOURCE = "mysteel"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def _text(html):
    """只留正文：Mysteel 模板在“内容由AI生成”之后才是正文。"""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    if "内容由AI生成" in t:
        t = t.split("内容由AI生成", 1)[1]
    return t.split("免责声明", 1)[0].strip()


def classify(title):
    if "库存" in title:
        return "库存"
    if "到港" in title or "压榨利润" in title:
        return "到港利润"
    if "开机" in title or "压榨" in title:
        return "压榨"
    if "现货价格" in title or "出厂价" in title or "市场豆粕" in title or "早报" in title:
        return "现货价"
    return "其他"


# 允许数字前出现“升至/为/达/至/约”等虚词
_FILLER = r"[^0-9%]{0,14}"

PATTERNS = [
    ("港口大豆库存", r"港口大豆库存" + _FILLER + r"([\d.]+)\s*万吨", "万吨"),
    ("豆粕库存", r"豆粕库存" + _FILLER + r"([\d.]+)\s*万吨", "万吨"),
    ("大豆库存", r"大豆库存" + _FILLER + r"([\d.]+)\s*万吨", "万吨"),
    ("开机率", r"(?:开机率|开工率)" + _FILLER + r"([\d.]+)\s*%", "%"),
    ("开机量", r"(?:开机量|压榨量)" + _FILLER + r"([\d.]+)\s*万吨", "万吨"),
    ("成交量", r"成交量" + _FILLER + r"([\d.]+)\s*(万吨|吨)", ""),
]

SPOT_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,10})\s*报价\s*([\d]{3,4})\s*元/吨"
    r"(?:[，,]\s*(上涨|下跌|持平|稳定)?\s*(\d+)?\s*元/吨)?"
)


def extract(text):
    out = {}
    for key, pattern, unit in PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                out[key] = {"value": float(m.group(1)), "unit": unit or m.group(2)}
            except ValueError:
                pass
    spots = []
    for m in SPOT_RE.finditer(text):
        try:
            spots.append({
                "firm": m.group(1),
                "price": float(m.group(2)),
                "change": (m.group(3) or "") + (m.group(4) or ""),
            })
        except ValueError:
            pass
    if spots:
        out["现货报价"] = spots
    # 部分文章会给出全国均价
    m = re.search(r"全国均价" + _FILLER + r"([\d.]+)\s*元/吨", text)
    if m:
        out["全国均价"] = {"value": float(m.group(1)), "unit": "元/吨"}
    return out


def fetch_list():
    html = _get(LIST_URL)
    items = re.findall(
        r'href="(https?://[^"]*/a/(\d{8,10})/[A-Za-z0-9]+\.html)"[^>]*>([^<]{6,80})</a>',
        html)
    seen, out = set(), []
    for url, code, title in items:
        if url in seen:
            continue
        seen.add(url)
        out.append((url, code, title.strip()))
    return out


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probe (
            date        TEXT NOT NULL,
            source      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            detail      TEXT NOT NULL,
            title       TEXT,
            url         TEXT,
            ingest_time TEXT,
            PRIMARY KEY (date, source, kind, url)
        )
    """)
    conn.commit()


def collect(days=1):
    today = datetime.date.today()
    want = {(today - datetime.timedelta(days=i)).strftime("%y%m%d") for i in range(days)}
    conn = db.connect(DB_PATH)
    _init_table(conn)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = kept = 0
    for url, code, title in fetch_list():
        total += 1
        if code[:6] not in want:
            continue
        kind = classify(title)
        if kind == "其他":
            continue
        try:
            detail = extract(_text(_get(url)))
        except Exception as exc:  # noqa: BLE001
            print("[mysteel] 抓取失败 %s：%s" % (title[:30], exc))
            continue
        if not detail:
            print("[mysteel] 无数字 %s" % title[:40])
            continue
        date = "20%s-%s-%s" % (code[:2], code[2:4], code[4:6])
        conn.execute(
            "INSERT OR REPLACE INTO probe (date, source, kind, detail, title, url, ingest_time)"
            " VALUES (?,?,?,?,?,?,?)",
            (date, SOURCE, kind, json.dumps(detail, ensure_ascii=False),
             title[:80], url, now))
        kept += 1
        keys = "、".join(detail.keys())
        print("[mysteel] %s | %s | %s" % (date, kind, keys))
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM probe WHERE source=?", (SOURCE,)).fetchone()[0]
    conn.close()
    print("[mysteel] 列表 %d 篇，命中 %d 篇；probe 表累计 %d 条" % (total, kept, n))


def show(limit=20):
    conn = db.connect(DB_PATH)
    _init_table(conn)
    rows = conn.execute(
        "SELECT date, kind, title, detail FROM probe WHERE source=? ORDER BY date DESC, kind LIMIT ?",
        (SOURCE, limit)).fetchall()
    if not rows:
        print("[mysteel] probe 表为空")
    for r in rows:
        print("%s  %-6s  %s" % (r["date"], r["kind"], (r["title"] or "")[:38]))
        print("        %s" % (r["detail"] or "")[:150])
    conn.close()


def main():
    args = sys.argv[1:]
    if "--show" in args:
        show()
        return
    days = 1
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            pass
    collect(days)


if __name__ == "__main__":
    main()
