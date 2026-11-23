# -*- coding: utf-8 -*-
"""USDA WASDE 报告抓取：取美国大豆期末库存。

WASDE 每月中旬发布，官网同时提供 .txt 纯文本版，表格规整，正则即可解析：
    https://www.usda.gov/oce/commodity/wasde/wasdeMMYY.txt

只取一个字段：美国大豆期末库存（百万蒲式耳→亿蒲式耳），写入 D1.wasde_stock。

用法：
    python3 app/wasde.py            # 有新版则写入
    python3 app/wasde.py --dry      # 只解析不写库
"""
import datetime
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
BASE = "https://www.usda.gov/oce/commodity/wasde/wasde%02d%02d.txt"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

_CTX = ssl.create_default_context()


def _fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def latest_report():
    """从当前月往前找最近的可用报告，返回 (月序号, 文本)。"""
    today = datetime.date.today()
    for back in range(0, 4):
        month = today.month - back
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        url = BASE % (month, year % 100)
        try:
            text = _fetch(url)
        except urllib.error.HTTPError:
            continue
        except Exception as exc:  # noqa: BLE001
            print("[wasde] %s 抓取失败：%s" % (url, str(exc)[:70]))
            continue
        if "WASDE" in text[:2000]:
            return month, text
    return None, None


def parse_soybean(text):
    """返回美国大豆期末库存等信息（单位：亿蒲式耳）。"""
    lines = text.splitlines()
    head = next((l for l in lines[:6] if "WASDE" in l), "")
    m = re.search(r"WASDE\s*-\s*(\d+)", head)
    report_no = m.group(1) if m else ""

    # 美国部分的第一处 SOYBEANS
    start = next((i for i, l in enumerate(lines) if l.strip() == "SOYBEANS"), None)
    if start is None:
        return None

    for line in lines[start:start + 40]:
        if not line.strip().startswith("Ending Stocks"):
            continue
        nums = re.findall(r"[\d,]+", line.split("Ending Stocks", 1)[1])
        if len(nums) < 4:
            return None
        vals = [int(n.replace(",", "")) for n in nums[:4]]
        return {
            "report_no": report_no,
            "market_year": "2026/27",
            "ending_stocks_mbu": vals[3],       # 本月预测
            "prev_mbu": vals[2],                # 上月预测
            "ending_stocks": round(vals[3] / 100.0, 2),   # 亿蒲式耳
            "head_line": head.strip()[:60],
        }
    return None


def main():
    dry = "--dry" in sys.argv[1:]
    month, text = latest_report()
    if not text:
        print("[wasde] 没找到可用报告")
        return

    info = parse_soybean(text)
    if not info:
        print("[wasde] 解析失败（%d 月报告）" % month)
        return

    print("[wasde] 报告 #%s（%d 月）" % (info["report_no"], month))
    print("[wasde] 美豆期末库存 %s 亿蒲（上月预测 %s 亿蒲，变动 %+.2f）" % (
        info["ending_stocks"], round(info["prev_mbu"] / 100.0, 2),
        (info["ending_stocks_mbu"] - info["prev_mbu"]) / 100.0))

    if dry:
        print("[wasde] --dry：未写库")
        return

    conn = db.connect(DB_PATH)
    try:
        latest = db.latest(conn, "D1.wasde_stock")
        note = (latest["note"] or "") if latest else ""
        tag = "WASDE %s" % info["report_no"]
        if tag in note:
            print("[wasde] 已是当期报告（%s），跳过" % tag)
            return
        today = datetime.date.today().isoformat()
        db.upsert(conn, "D1.wasde_stock", today, info["ending_stocks"],
                  unit="亿蒲", freq="M", source="usda", sample="WASDE",
                  quality="auto", note=tag)
        conn.commit()
        print("[wasde] 已写入 D1.wasde_stock @ %s = %s 亿蒲" % (today, info["ending_stocks"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
