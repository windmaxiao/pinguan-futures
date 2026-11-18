# -*- coding: utf-8 -*-
"""手工数据录入（现货价、油厂库存、开机率等无公开接口的数据）。

用法：
    python manual.py list                      # 看有哪些可录入的指标
    python manual.py set A4.spot 3450          # 录入今日现货价
    python manual.py set B1.inventory 134.69 --date 2026-11-16
    python manual.py set B3.crush_rate 52.17 --date 2026-11-14 --note "Mysteel 第45周"
    python manual.py show B1.inventory 8       # 看最近 8 条
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出可录入指标")

    p_set = sub.add_parser("set", help="录入一个数")
    p_set.add_argument("series")
    p_set.add_argument("value", type=float)
    p_set.add_argument("--date", default=None, help="默认今天")
    p_set.add_argument("--note", default="")

    p_week = sub.add_parser("weekly", help="一次录入周度产业数据")
    p_week.add_argument("--date", default=None, help="默认今天")
    p_week.add_argument("--inv", type=float, help="油厂豆粕库存（万吨）")
    p_week.add_argument("--soy", type=float, help="油厂大豆库存（万吨）")
    p_week.add_argument("--rate", type=float, help="油厂开机率（%%）")
    p_week.add_argument("--arrival", type=float, help="周度大豆到港（万吨）")
    p_week.add_argument("--note", default="")

    p_show = sub.add_parser("show", help="查看某指标最近 N 条")
    p_show.add_argument("series")
    p_show.add_argument("n", nargs="?", type=int, default=10)

    args = ap.parse_args()
    cfg = load_config()
    conn = db.connect(DB_PATH)

    if args.cmd == "list":
        print("%-16s %-18s %-8s %s" % ("编号", "名称", "单位", "频率/说明"))
        print("-" * 70)
        for sid, meta in cfg["manual_series"].items():
            print("%-16s %-18s %-8s %s" % (sid, meta["name"], meta["unit"],
                                           meta.get("freq", "")))
        return

    if args.cmd == "set":
        if args.series not in cfg["manual_series"]:
            print("未知指标：%s\n可用指标：\n  %s"
                  % (args.series, "\n  ".join(cfg["manual_series"])))
            sys.exit(1)
        meta = cfg["manual_series"][args.series]
        date = args.date or datetime.date.today().isoformat()
        db.upsert(conn, args.series, date, args.value,
                  unit=meta["unit"], freq=meta.get("freq", "D"),
                  source="manual", sample="", quality="manual",
                  note=args.note)
        conn.commit()
        print("已录入 %s (%s) = %s %s @ %s"
              % (args.series, meta["name"], args.value, meta["unit"], date))
        return

    if args.cmd == "weekly":
        date = args.date or datetime.date.today().isoformat()
        pairs = [
            ("B1.inventory", args.inv, "油厂豆粕库存"),
            ("B2.soy_inventory", args.soy, "油厂大豆库存"),
            ("B3.crush_rate", args.rate, "油厂开机率"),
            ("B4.arrival", args.arrival, "周度大豆到港"),
        ]
        n = 0
        for sid, value, name in pairs:
            if value is None:
                continue
            meta = cfg["manual_series"][sid]
            db.upsert(conn, sid, date, value, unit=meta["unit"],
                      freq=meta.get("freq", "W"), source="manual",
                      sample="", quality="manual", note=args.note)
            print("  %s (%s) = %s %s" % (sid, name, value, meta["unit"]))
            n += 1
        if n == 0:
            print("没有提供任何数值，用法示例：")
            print("  weekly --inv 125.23 --soy 795.39 --rate 48.88 --date 2026-11-13")
            sys.exit(1)
        conn.commit()
        print("已录入 %d 项 @ %s，记得跑 render.py 刷新页面" % (n, date))
        return

    if args.cmd == "show":
        rows = db.history(conn, args.series, limit=args.n)
        if not rows:
            print("没有数据：%s" % args.series)
            return
        for r in rows:
            print("%s  %12.2f %-6s [%s] %s"
                  % (r["date"], r["value"], r["unit"], r["quality"], r["note"]))
        return


if __name__ == "__main__":
    main()
