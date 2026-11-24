# -*- coding: utf-8 -*-
"""交易记录与复盘。

两张表：
  journal  每日判断（方向 / 理由 / 反证条件）
  trade    模拟或实盘交易（开仓、平仓、盈亏）

统计口径：
  判断准确率——按判断日之后第 N 个交易日的结算价方向是否与判断一致。
  交易统计——已平仓交易的盈亏、胜率、平均持仓天数。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
MULTIPLIER = 10  # 豆粕 10 吨/手

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    date      TEXT PRIMARY KEY,
    direction TEXT,
    contract  TEXT,
    reason    TEXT,
    risk      TEXT,
    note      TEXT,
    created   TEXT
);
CREATE TABLE IF NOT EXISTS trade (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contract    TEXT,
    side        TEXT,
    lots        INTEGER,
    multiplier  INTEGER DEFAULT 10,
    open_date   TEXT,
    open_price  REAL,
    close_date  TEXT,
    close_price REAL,
    pnl         REAL,
    fee         REAL DEFAULT 0,
    reason      TEXT,
    review      TEXT,
    status      TEXT DEFAULT 'open',
    created     TEXT
);
"""


def init(conn):
    conn.executescript(SCHEMA)
    # 老库补列
    tcols = {row[1] for row in conn.execute("PRAGMA table_info(trade)")}
    if "pnl" not in tcols:
        conn.execute("ALTER TABLE trade ADD COLUMN pnl REAL")
    jcols = {row[1] for row in conn.execute("PRAGMA table_info(journal)")}
    if "contract" not in jcols:
        conn.execute("ALTER TABLE journal ADD COLUMN contract TEXT")
    conn.commit()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 判断

def save_journal(conn, date, direction, reason, risk, note="", contract=""):
    init(conn)
    conn.execute(
        "INSERT OR REPLACE INTO journal (date, direction, contract, reason, risk, note, created)"
        " VALUES (?,?,?,?,?,?,?)",
        (date, direction, (contract or "").strip().upper(), reason, risk, note, _now()))
    conn.commit()


def list_journal(conn, limit=60):
    init(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM journal ORDER BY date DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- 交易

def save_trade(conn, contract, side, lots, open_date, open_price,
               reason="", multiplier=MULTIPLIER):
    init(conn)
    cur = conn.execute(
        "INSERT INTO trade (contract, side, lots, multiplier, open_date, open_price,"
        " reason, status, created) VALUES (?,?,?,?,?,?,?,'open',?)",
        (contract, side, int(lots), int(multiplier), open_date, float(open_price),
         reason, _now()))
    conn.commit()
    return cur.lastrowid


def close_trade(conn, trade_id, close_date, close_price, fee=0.0, review=""):
    init(conn)
    row = conn.execute("SELECT * FROM trade WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise ValueError("交易不存在")
    sign = 1 if row["side"] == "多" else -1
    pnl = (float(close_price) - row["open_price"]) * sign * row["lots"] * row["multiplier"]
    pnl -= float(fee or 0)
    conn.execute(
        "UPDATE trade SET close_date=?, close_price=?, fee=?, pnl=?, review=?, status='closed'"
        " WHERE id=?",
        (close_date, float(close_price), float(fee or 0), round(pnl, 2), review, trade_id))
    conn.commit()
    return round(pnl, 2)


def delete_trade(conn, trade_id):
    init(conn)
    conn.execute("DELETE FROM trade WHERE id=?", (trade_id,))
    conn.commit()


def list_trades(conn, limit=100):
    init(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM trade ORDER BY COALESCE(close_date, open_date) DESC, id DESC LIMIT ?",
        (limit,))]
    return rows


# ---------------------------------------------------------------- 统计

def _settle_series(conn, contract):
    rows = conn.execute(
        "SELECT date, value FROM series WHERE series_id=? ORDER BY date",
        ("A1.%s.settle" % (contract or "").strip().upper(),)).fetchall()
    return [(r["date"], r["value"]) for r in rows]


def _default_contract(conn):
    """当前主力合约，取不到再退回 M2701。"""
    row = conn.execute("SELECT value FROM meta WHERE key='main_contract'").fetchone()
    if row and row[0]:
        return str(row[0])
    return "M2701"


def judge_hit_rate(conn, entries=None, horizon=3):
    """判断准确率：判断日之后第 horizon 个交易日结算价的涨跌是否与方向一致。

    周末/节假日写的判断，基准取该日期之前最近的交易日（你下单时能看到的最新结算价）。
    """
    init(conn)
    entries = entries if entries is not None else list_journal(conn, 500)
    fallback = _default_contract(conn)
    series_cache = {}
    checked, hits = [], []

    for e in entries:
        d = e.get("direction")
        if d not in ("多", "空"):
            continue
        contract = (e.get("contract") or e.get("note") or fallback).strip().upper()
        series = series_cache.setdefault(contract, _settle_series(conn, contract))
        dates = [x[0] for x in series]
        if not dates:
            continue
        if e["date"] in dates:
            i = dates.index(e["date"])
        else:
            # 非交易日：取判断日之前最近的交易日作为基准
            prior = [k for k, day in enumerate(dates) if day <= e["date"]]
            if not prior:
                continue
            i = prior[-1]
        j = i + horizon
        if j >= len(series):
            continue
        change = series[j][1] - series[i][1]
        ok = (change > 0) if d == "多" else (change < 0)
        checked.append({"date": e["date"], "direction": d,
                        "base": dates[i], "change": round(change, 1), "ok": ok})
        hits.append(ok)

    return {
        "checked": checked,
        "n": len(checked),
        "hits": sum(1 for h in hits if h),
        "rate": round(sum(1 for h in hits if h) * 100.0 / len(checked), 1) if checked else None,
    }


def trade_stats(conn, trades=None):
    trades = trades if trades is not None else list_trades(conn, 500)
    closed = [t for t in trades if t["status"] == "closed" and t.get("pnl") is not None]
    opens = [t for t in trades if t["status"] == "open"]

    total = round(sum(t["pnl"] for t in closed), 2) if closed else 0.0
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    evens = [t for t in closed if t["pnl"] == 0]

    holding = []
    for t in closed:
        try:
            d0 = datetime.date.fromisoformat(t["open_date"])
            d1 = datetime.date.fromisoformat(t["close_date"])
            holding.append((d1 - d0).days)
        except (TypeError, ValueError):
            pass

    by_contract = {}
    for t in closed:
        k = t["contract"] or "?"
        a = by_contract.setdefault(k, {"n": 0, "pnl": 0.0})
        a["n"] += 1
        a["pnl"] += t["pnl"]

    return {
        "closed_n": len(closed),
        "open_n": len(opens),
        "total_pnl": total,
        "win_n": len(wins),
        "loss_n": len(losses),
        "even_n": len(evens),
        "win_rate": round(len(wins) * 100.0 / len(closed), 1) if closed else None,
        "avg_holding": round(sum(holding) / len(holding), 1) if holding else None,
        "max_win": round(max((t["pnl"] for t in closed), default=0), 2),
        "max_loss": round(min((t["pnl"] for t in closed), default=0), 2),
        "by_contract": by_contract,
    }


def summary(conn):
    entries = list_journal(conn, 200)
    trades = list_trades(conn, 200)
    return {
        "journal": entries,
        "trades": trades,
        "judge": judge_hit_rate(conn, entries),
        "trade": trade_stats(conn, trades),
    }


if __name__ == "__main__":
    conn = db.connect(DB_PATH)
    init(conn)
    import json
    print(json.dumps(summary(conn), ensure_ascii=False, indent=1, default=str))
    conn.close()
