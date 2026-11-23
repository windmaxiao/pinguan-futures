# -*- coding: utf-8 -*-
"""盘前 AI 判断：让模型基于当日数据给一段简评，与规则判断并列展示。

结果按 (日期 + 数据指纹) 缓存在 ai_judge 表，数据没变就不重复调用模型。
模型不可用时返回空串，页面只显示规则判断。
"""
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm  # noqa: E402

SYSTEM = (
    "你是一名豆粕研究员。说话直接、克制，不写套话和免责声明。"
    "只输出 2 到 3 句中文，不用 Markdown、不列条目，总共不超过 90 字。"
)

TABLE = """
CREATE TABLE IF NOT EXISTS ai_judge (
    key     TEXT PRIMARY KEY,
    date    TEXT,
    text    TEXT,
    model   TEXT,
    created TEXT
)
"""


def _fmt(value, unit="", nd=1):
    if value is None:
        return "无"
    try:
        return ("%." + str(nd) + "f%s") % (float(value), unit)
    except (TypeError, ValueError):
        return "无"


def _facts(data):
    """挑出判断需要的要素，顺序固定，便于生成稳定指纹。"""
    main = data.get("main") or ""
    quote = next((q for q in (data.get("quotes") or []) if q.get("code") == main), None)
    spot = data.get("spot") or {}
    basis = data.get("basis") or {}
    inv = data.get("inventory") or {}
    crush = data.get("crush_margin") or {}
    hog = data.get("hog_profit") or {}
    us = data.get("us") or {}
    dims = data.get("dims") or {}

    lines = [
        "主力 %s 结算 %s（涨跌 %s）" % (
            main, _fmt(quote and quote.get("settle")), _fmt(quote and quote.get("delta"))),
        "现货（华东）%s 元/吨，基差 %s 元/吨" % (
            _fmt(spot.get("value")), _fmt(basis.get("value"), nd=0)),
        "油厂豆粕库存 %s 万吨，开机率 %s" % (
            _fmt(inv.get("value"), nd=2), _fmt(data.get("crush_rate"), "%")),
        "压榨利润 %s 元/吨" % _fmt(crush.get("value"), nd=0),
        "CBOT 美豆 %s 美分（%s）" % (
            _fmt(us.get("last")), _fmt(us.get("chg_pct"), "%", nd=2)),
    ]
    if hog.get("value") is not None:
        lines.append("生猪养殖利润 %s 元/头" % _fmt(hog.get("value"), nd=0))
    lines.append("四维评分：成本 %s、供给 %s、需求 %s、结构 %s" % (
        dims.get("cost"), dims.get("supply"), dims.get("demand"), dims.get("struct")))
    return lines


def _fingerprint(lines):
    raw = json.dumps(lines, ensure_ascii=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:16]


def current(conn, data):
    """返回 AI 判断文本；未配置模型或调用失败时返回空串。"""
    if not llm.configured():
        return ""

    facts = _facts(data)
    today = datetime.date.today().isoformat()
    key = "%s:%s" % (today, _fingerprint(facts))

    conn.execute(TABLE)
    conn.commit()
    row = conn.execute("SELECT text FROM ai_judge WHERE key=?", (key,)).fetchone()
    if row and row["text"]:
        return row["text"]

    prompt = "今日盘前数据：\n" + "\n".join("- " + x for x in facts) + \
             "\n\n请判断：当前豆粕的主要矛盾是什么，短期偏多还是偏空，最该盯哪个变量。"
    try:
        text = llm.chat([{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}],
                        max_tokens=220, temperature=0.4)
    except Exception as exc:  # noqa: BLE001
        print("[judge] AI 判断生成失败：%s" % str(exc)[:120])
        return ""

    text = (text or "").strip()
    if not text:
        return ""

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO ai_judge (key, date, text, model, created) VALUES (?,?,?,?,?)",
        (key, today, text, (llm.load_config().get("model") or "deepseek-chat"), now))
    # 只保留 60 天
    conn.execute("DELETE FROM ai_judge WHERE date < ?",
                 ((datetime.date.today() - datetime.timedelta(days=60)).isoformat(),))
    conn.commit()
    print("[judge] 已生成 AI 判断（%d 字）" % len(text))
    return text
