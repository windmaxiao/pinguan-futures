# -*- coding: utf-8 -*-
"""用已有的合约日线回填历史衍生序列。

目前只做价差：A6.spread（相邻月差）与 A7.spread.M-RM（豆菜粕）。
这些值本来就能从结算价算出来，之前只在当天现算，历史白白空着。

用法：
    python3 app/backfill.py spreads
    python3 app/backfill.py spreads --dry   # 只看会写多少条，不落库
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")


def _contract_key(code):
    """M2701 -> (27, 1)，按交割年月排序。"""
    m = re.match(r"^[A-Z]+(\d{2})(\d{2})$", code)
    if not m:
        return (99, 99)
    return (int(m.group(1)), int(m.group(2)))


def _load(conn, field):
    """返回 {date: {code: value}}，code 如 M2701。"""
    pattern = "A1.%." + field
    out = {}
    rows = conn.execute(
        "SELECT series_id, date, value FROM series WHERE series_id LIKE ?",
        (pattern,)).fetchall()
    for r in rows:
        code = r["series_id"][3:].rsplit(".", 1)[0]
        out.setdefault(r["date"], {})[code] = r["value"]
    return out


def backfill_spreads(conn, dry=False):
    settles = _load(conn, "settle")
    ois = _load(conn, "oi")

    # 把豆粕/菜粕各自的序列分开
    m_settle, rm_settle = {}, {}
    for date, items in settles.items():
        for code, val in items.items():
            if code.startswith("M"):
                m_settle.setdefault(date, {})[code] = val
            elif code.startswith("RM"):
                rm_settle.setdefault(date, {})[code] = val

    pairs = 0
    spread_rows = []
    for date in sorted(m_settle):
        items = m_settle[date]
        codes = sorted(items, key=_contract_key)
        for near, far in zip(codes, codes[1:]):
            a, b = items[near], items[far]
            if a is None or b is None or a <= 0 or b <= 0:
                continue
            spread_rows.append(("%s-%s" % (near, far), date, a - b))
            pairs += 1

    # 豆菜粕：主力取当日持仓最大者
    mr_rows = []
    for date in sorted(m_settle):
        rms = rm_settle.get(date) or {}
        if not rms:
            continue
        m_items = m_settle[date]
        oi_items = ois.get(date) or {}
        candidates = [(oi_items.get(c, -1), c) for c in m_items if oi_items.get(c, -1) > 0]
        if not candidates:
            continue
        _, main = max(candidates)
        rm_code = sorted(rms, key=_contract_key)[0]
        if m_items[main] > 0 and rms[rm_code] > 0:
            mr_rows.append((main, rm_code, date, m_items[main] - rms[rm_code]))

    print("[backfill] 月差 %d 条，覆盖 %d 个交易日" % (pairs, len(set(d for _, d, _ in spread_rows))))
    print("[backfill] 豆菜粕 %d 条" % len(mr_rows))
    if dry:
        print("[backfill] --dry：未写入")
        return

    for name, date, value in spread_rows:
        db.upsert(conn, "A6.spread.%s" % name, date, value, unit="元/吨",
                  source="derived", sample="settle", quality="derived")
    for main, rm_code, date, value in mr_rows:
        db.upsert(conn, "A7.spread.M-RM", date, value, unit="元/吨",
                  source="derived", sample="%s-%s" % (main, rm_code), quality="derived")
    conn.commit()

    n1 = conn.execute("SELECT COUNT(*) FROM series WHERE series_id LIKE 'A6.spread.%'").fetchone()[0]
    n2 = conn.execute("SELECT COUNT(*) FROM series WHERE series_id = 'A7.spread.M-RM'").fetchone()[0]
    print("[backfill] 写入完成：A6 共 %d 条，A7 共 %d 条" % (n1, n2))


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "spreads"
    dry = "--dry" in args
    conn = db.connect(DB_PATH)
    try:
        if cmd == "spreads":
            backfill_spreads(conn, dry=dry)
        else:
            print(__doc__)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
