# -*- coding: utf-8 -*-
"""抓取入口：拉行情 -> 落库 -> 算派生指标。

合约列表按交割月自动滚动（见 contracts.py）：进入交割月的合约不再抓取展示，
历史数据保留在库里。

用法：
    python fetch.py                  # 抓当日行情并算派生
    python fetch.py --backfill M2701 M2703   # 回填指定合约历史日线
    python fetch.py --backfill-all   # 回填当前全部活跃合约
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts as contract_mod  # noqa: E402
import db                        # noqa: E402
import news                      # noqa: E402
import sources                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
RAW_DIR = os.path.join(ROOT, "raw")


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def group_codes(cfg):
    """{'M': ['M2701', ...], 'RM': [...], 'Y': [...]}，按交割月由近及远。"""
    return contract_mod.groups(cfg)


def all_inner_codes(cfg):
    return [c for codes in group_codes(cfg).values() for c in codes]


def fetch_quotes(conn, cfg, codes):
    q = sources.quotes(codes, raw_dir=RAW_DIR)
    stored = 0
    for code, item in q.items():
        date = item["date"]
        if not date:
            continue
        # 开盘前/盘中，交易所还没出结算价，新浪返回 0。
        # 这种值绝不能入库，否则基差 = 现货 - 0，整个结构维度会算反。
        settle = item["settle"]
        if settle and settle > 0:
            db.upsert(conn, "A1.%s.settle" % code, date, settle,
                      unit="元/吨", source="sina", sample=code)
        last = item["last"]
        if last and last > 0:
            db.upsert(conn, "A1.%s.close" % code, date, last,
                      unit="元/吨", source="sina", sample=code)
        oi = item["oi"]
        if oi and oi > 0:
            db.upsert(conn, "A1.%s.oi" % code, date, oi,
                      unit="手", source="sina", sample=code)
        vol = item["volume"]
        if vol is not None and vol >= 0:
            db.upsert(conn, "A1.%s.volume" % code, date, vol,
                      unit="手", source="sina", sample=code)
        stored += 1
    print("[quotes] 请求 %d 个合约，入库 %d 个" % (len(codes), stored))
    return q


def fetch_foreign(conn, cfg):
    syms = list(cfg.get("foreign", {}).keys())
    if not syms:
        return {}
    fr = sources.foreign(syms, raw_dir=RAW_DIR)
    for sym, item in fr.items():
        if item["date"] and item["last"] is not None:
            db.upsert(conn, "A3.%s.close" % sym, item["date"], item["last"],
                      unit="美分/蒲", source="sina", sample=sym)
    print("[foreign] %d 个外盘合约" % len(fr))
    return fr


def fetch_fx(conn, cfg):
    fxc = cfg.get("fx") or {}
    if not fxc.get("symbol"):
        return
    try:
        row = sources.fx_rate(fxc["symbol"], raw_dir=RAW_DIR)
    except Exception as e:  # noqa: BLE001
        print("[fx] 汇率抓取失败（不影响主流程）: %s" % e)
        return
    if not row:
        print("[fx] 汇率无数据")
        return
    date = row["date"] or datetime.date.today().isoformat()
    db.upsert(conn, fxc["series"], date, row["value"], unit="CNY/USD",
              source="sina", sample=fxc["symbol"], quality="ok")
    print("[fx] %s = %.4f" % (fxc["symbol"], row["value"]))


def fetch_spot_alt(conn, cfg):
    """生意社基准现货价：只作对照，不覆盖手工录入的华东价。

    生意社按日发布，当天页面可能还没生成，所以往回找最近 5 天。
    """
    sa = cfg.get("spot_alt") or {}
    if not sa.get("enabled"):
        return
    row = None
    for i in range(0, 5):
        day = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        try:
            row = sources.spot_100ppi(date=day, raw_dir=RAW_DIR)
        except Exception as e:  # noqa: BLE001
            print("[spot] 生意社 %s 抓取失败（不影响主流程）: %s" % (day, e))
            continue
        if row and row["spot"]:
            break
        row = None
    if not row:
        print("[spot] 生意社近 5 天均无豆粕数据，跳过")
        return
    db.upsert(conn, sa["series"], row["date"], row["spot"], unit="元/吨",
              freq="D", source="100ppi", sample=sa["vendor"], quality="ok",
              note="参照合约 %s" % row.get("main_code", ""))
    print("[spot] 生意社豆粕基准价 = %.2f（%s，主力 %s = %s）"
          % (row["spot"], row["date"], row.get("main_code", "?"),
             row.get("main_price") if row.get("main_price") is not None else "?"))

    # 生意社自己的「现货 - 近月」基差，单独存一条，便于和历史对照
    near = row.get("near_price")
    if near and near > 0:
        db.upsert(conn, "A5.basis.100ppi", row["date"], row["spot"] - near,
                  unit="元/吨", freq="D", source="derived",
                  sample="生意社·%s" % row.get("near_code", ""), quality="derived")
        print("[spot] 生意社近月基差 = %.2f（%s）"
              % (row["spot"] - near, row.get("near_code", "?")))


def derive(conn, cfg, groups):
    months = groups["M"]

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 ("active.M", json.dumps(months, ensure_ascii=False)))
    print("[derive] 活跃合约: %s" % " ".join(months))

    # 主力：持仓量最大
    best, best_oi = None, -1.0
    for code in months:
        row = db.latest(conn, "A1.%s.oi" % code)
        if row and row["value"] > best_oi:
            best, best_oi = code, row["value"]
    if best:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     ("main_contract", best))
        print("[derive] 主力合约 = %s (持仓 %.0f)" % (best, best_oi))

    # 月差：相邻合约 近月 - 远月
    for i in range(len(months) - 1):
        near, far = months[i], months[i + 1]
        rn = db.latest(conn, "A1.%s.settle" % near)
        rf = db.latest(conn, "A1.%s.settle" % far)
        if rn and rf and rn["date"] == rf["date"] \
                and rn["value"] > 0 and rf["value"] > 0:
            db.upsert(conn, "A6.spread.%s-%s" % (near, far), rn["date"],
                      rn["value"] - rf["value"], unit="元/吨",
                      source="derived", sample="settle", quality="derived")

    # 豆菜粕价差
    rm_codes = groups.get("RM") or []
    if rm_codes and best:
        r_m = db.latest(conn, "A1.%s.settle" % best)
        r_rm = db.latest(conn, "A1.%s.settle" % rm_codes[0])
        if r_m and r_rm and r_m["date"] == r_rm["date"] \
                and r_m["value"] > 0 and r_rm["value"] > 0:
            db.upsert(conn, "A7.spread.M-RM", r_m["date"],
                      r_m["value"] - r_rm["value"], unit="元/吨",
                      source="derived", sample="%s-%s" % (best, rm_codes[0]),
                      quality="derived")

    # 生猪期货主力：需求端的日频信号（持仓量最大者）
    lh_codes = groups.get("LH") or []
    if lh_codes:
        best_lh, best_lh_oi = None, -1.0
        for code in lh_codes:
            row = db.latest(conn, "A1.%s.oi" % code)
            if row and row["value"] > best_lh_oi:
                best_lh, best_lh_oi = code, row["value"]
        if best_lh:
            r = db.latest(conn, "A1.%s.settle" % best_lh)
            if r and r["value"] > 0:
                db.upsert(conn, "C2.hog_future.main", r["date"], r["value"],
                          unit="元/吨", source="derived", sample=best_lh,
                          quality="derived")
                print("[derive] 生猪期货主力 = %s (%.0f 元/吨)" % (best_lh, r["value"]))

    # 压榨利润（盘面估算）
    crush = cfg.get("crush") or {}
    if crush.get("enabled") and best:
        y_codes = groups.get("Y") or []
        y_main, y_oi = None, -1.0
        for code in y_codes:
            row = db.latest(conn, "A1.%s.oi" % code)
            if row and row["value"] > y_oi:
                y_main, y_oi = code, row["value"]

        us = db.latest(conn, "A3.S.close")
        fx = db.latest(conn, "A8.USDCNY")
        m_settle = db.latest(conn, "A1.%s.settle" % best)
        y_settle = db.latest(conn, "A1.%s.settle" % y_main) if y_main else None

        if us and fx and m_settle and y_settle:
            # 美分/蒲 -> 美元/吨：1 吨 = 36.7437 蒲；再叠加升贴水与海运费(FOB->CNF)
            usd_per_ton = (us["value"] + crush.get("premium_cents", 0)) * 0.367437
            usd_per_ton += crush.get("freight_usd", 0)
            cost = usd_per_ton * fx["value"]
            cost *= (1 + crush.get("tariff", 0.13)) * (1 + crush.get("vat", 0.09))
            cost += crush.get("port_fee", 100)
            margin = (m_settle["value"] * crush["meal_yield"]
                      + y_settle["value"] * crush["oil_yield"]
                      - cost - crush["processing_fee"])
            date = m_settle["date"]
            db.upsert(conn, "B6.soy_cost", date, cost, unit="元/吨",
                      source="derived", sample="进口大豆含税到港", quality="derived")
            db.upsert(conn, "B6.crush_margin", date, margin, unit="元/吨",
                      source="derived", sample="%s/%s" % (best, y_main), quality="derived")
            print("[derive] 大豆到港成本 %.0f 元/吨，盘面压榨利润 %.0f 元/吨"
                  % (cost, margin))
        else:
            print("[derive] 跳过压榨利润：缺美豆价/汇率/豆油价")

    # 基差：现货 - 主力结算价
    spot = db.latest(conn, cfg["spot"]["series"])
    if spot and best:
        r_m = db.latest(conn, "A1.%s.settle" % best)
        if r_m and r_m["value"] > 0:
            note = ""
            if spot["date"] != r_m["date"]:
                note = "现货日期 %s，行情日期 %s" % (spot["date"], r_m["date"])
            db.upsert(conn, "A5.basis.main", r_m["date"],
                      spot["value"] - r_m["value"], unit="元/吨",
                      source="derived", sample=cfg["spot"]["region"],
                      quality="derived", note=note)
            print("[derive] 基差 = %.1f (现货 %.1f, 主力 %.1f)"
                  % (spot["value"] - r_m["value"], spot["value"], r_m["value"]))
    else:
        print("[derive] 跳过基差：缺少现货价，请先运行 manual.py set A4.spot <价格>")


def backfill(conn, codes):
    for code in codes:
        try:
            bars = sources.daily_kline(code, raw_dir=RAW_DIR)
        except Exception as e:  # noqa: BLE001
            print("[backfill] %s 失败: %s" % (code, e))
            continue
        if not bars:
            print("[backfill] %s 无历史数据" % code)
            continue
        for b in bars:
            if not b["d"]:
                continue
            # 同样跳过 0：日线接口在极少数情况下会返回 0 结算价
            if b["s"] is not None and b["s"] > 0:
                db.upsert(conn, "A1.%s.settle" % code, b["d"], b["s"],
                          unit="元/吨", source="sina.kline", sample=code)
            if b["c"] is not None and b["c"] > 0:
                db.upsert(conn, "A1.%s.close" % code, b["d"], b["c"],
                          unit="元/吨", source="sina.kline", sample=code)
            if b["p"] is not None and b["p"] > 0:
                db.upsert(conn, "A1.%s.oi" % code, b["d"], b["p"],
                          unit="手", source="sina.kline", sample=code)
            if b["v"] is not None and b["v"] >= 0:
                db.upsert(conn, "A1.%s.volume" % code, b["d"], b["v"],
                          unit="手", source="sina.kline", sample=code)
        print("[backfill] %s 入库 %d 条（%s ~ %s）"
              % (code, len(bars), bars[0]["d"], bars[-1]["d"]))


def repair_recent_settles(conn, codes, days=6):
    """用日线接口核对最近几天的结算价，把缺失的那几天补回来。

    日线是权威且按日期精确的。实时报价在「开盘前」和「夜盘切换交易日」这两种
    时刻都拿不到当日结算价（返回 0），所以这里做一层自愈：
    不管前面几次有没有抓到，第二天早上一定会补上。
    """
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    written = 0
    for code in codes:
        try:
            bars = sources.daily_kline(code, raw_dir=RAW_DIR)
        except Exception as e:  # noqa: BLE001
            print("[repair] %s 跳过：%s" % (code, e))
            continue
        for b in bars:
            if not b["d"] or b["d"] < cutoff:
                continue
            if b["s"] and b["s"] > 0:
                db.upsert(conn, "A1.%s.settle" % code, b["d"], b["s"],
                          unit="元/吨", source="sina.kline", sample=code)
                written += 1
    print("[repair] 近 %d 天结算价核对完成，写入 %d 条" % (days, written))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", nargs="*", default=None,
                    help="回填指定合约历史日线，如 M2701 M2703")
    ap.add_argument("--backfill-all", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    groups = group_codes(cfg)
    conn = db.connect(DB_PATH)

    if args.backfill is not None or args.backfill_all:
        codes = all_inner_codes(cfg) if args.backfill_all else (args.backfill or [])
        backfill(conn, codes)
        conn.commit()
        return

    codes = all_inner_codes(cfg)
    repair_recent_settles(conn, codes)
    fetch_quotes(conn, cfg, codes)
    fetch_foreign(conn, cfg)
    fetch_fx(conn, cfg)
    fetch_spot_alt(conn, cfg)
    try:
        news.collect(conn, cfg)
        news.prune(conn)
    except Exception as e:  # noqa: BLE001
        print("[news] 采集失败（不影响主流程）: %s" % e)
    derive(conn, cfg, groups)
    conn.commit()
    print("[done] ok")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            import notify
            notify.send("[豆粕数据] 抓取任务失败", tb[-3000:])
        except Exception as e:  # noqa: BLE001
            print("[notify] 告警也失败了：%s" % e)
        raise
