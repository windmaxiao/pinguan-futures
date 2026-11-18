# -*- coding: utf-8 -*-
"""数据健康检查：发现问题就发告警；相同问题 12 小时内不重复发。

被 mp-data.service 的 ExecStartPost 调用，所以每次抓取后都会体检一次。
"""
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db       # noqa: E402
import notify   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
STATE_PATH = os.path.join(ROOT, "db", "notify_state.json")
SITE = "https://data.example.com/"
DEDUPE_HOURS = 12

# (series_id, 名称, 最多允许多少天没更新)
FRESHNESS = [
    ("A4.spot", "豆粕现货价", 4),
    ("B1.inventory", "油厂豆粕库存", 12),
    ("B2.soy_inventory", "油厂大豆库存", 12),
    ("B3.crush_rate", "油厂开机率", 12),
    ("C1.hog_profit", "生猪养殖利润", 12),
]


def check(conn):
    problems = []
    today = datetime.date.today()

    for sid, name, max_age in FRESHNESS:
        row = db.latest(conn, sid)
        if not row:
            problems.append("缺失：%s（%s）从未录入" % (name, sid))
            continue
        age = (today - datetime.date.fromisoformat(row["date"])).days
        if age > max_age:
            problems.append("过期：%s（%s）已 %d 天未更新，最后是 %s"
                            % (name, sid, age, row["date"]))

    cutoff = (today - datetime.timedelta(days=7)).isoformat()
    bad = conn.execute(
        "SELECT series_id, date, value FROM series "
        "WHERE series_id LIKE 'A1.%.settle' AND value <= 0 AND date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()
    if bad:
        problems.append("异常：近期有 %d 条非正结算价，例如 %s@%s=%s"
                        % (len(bad), bad[0]["series_id"], bad[0]["date"], bad[0]["value"]))

    basis = db.latest(conn, "A5.basis.main")
    if basis and abs(basis["value"]) > 400:
        problems.append("异常：基差 %.0f 元/吨，超出常理（可能是口径或数据问题）"
                        % basis["value"])

    return problems


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_state(state):
    items = sorted(state.items(), key=lambda kv: kv[1], reverse=True)[:20]
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(dict(items), fh, ensure_ascii=False, indent=1)


def main(db_path=None):
    conn = db.connect(db_path or DB_PATH)
    problems = check(conn)
    if not problems:
        print("[health] 全部正常")
        return

    key = hashlib.sha1("\n".join(problems).encode("utf-8")).hexdigest()[:12]
    state = load_state()
    last = state.get(key)
    now = datetime.datetime.now()
    if last:
        try:
            near = datetime.datetime.fromisoformat(last)
            if (now - near).total_seconds() < DEDUPE_HOURS * 3600:
                print("[health] 相同问题 %d 小时内已通知过，跳过" % DEDUPE_HOURS)
                return
        except ValueError:
            pass

    body = "豆粕数据平台发现以下问题：\n\n"
    body += "\n".join("- " + p for p in problems)
    body += ("\n\n看板：%s\n时间：%s\n\n"
             "（相同问题 %d 小时内只提醒一次）" % (SITE, now.strftime("%Y-%m-%d %H:%M"), DEDUPE_HOURS))

    if notify.send("[豆粕数据] 发现 %d 个问题" % len(problems), body,
                   channels=("mail",)):
        state[key] = now.isoformat(timespec="seconds")
        save_state(state)
    else:
        print("[health] 告警发送失败，下次运行会重试")


if __name__ == "__main__":
    # 可选参数：指定数据库路径（用于测试，不传则用线上库）
    main(sys.argv[1] if len(sys.argv) > 1 else None)
