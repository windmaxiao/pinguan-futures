# -*- coding: utf-8 -*-
"""生成静态页面：site/index.html（盘前一页纸）、site/weekly.html、site/data/summary.json。

只用标准库。页面不依赖任何 CDN 或后端接口。
"""
import datetime
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts as contract_mod  # noqa: E402
import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
SITE = os.path.join(ROOT, "site")

CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:20px 16px 60px; background:#f6f7f9; color:#1f2328;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
       font-size:14px; line-height:1.6; }
.wrap { max-width:1180px; margin:0 auto; }
h1 { font-size:20px; margin:0; }
h2 { font-size:15px; margin:22px 0 10px; padding-bottom:6px; border-bottom:2px solid #e5e7eb; }
header { display:flex; flex-wrap:wrap; align-items:baseline; gap:10px; margin-bottom:16px; }
.meta { color:#6b7280; font-size:12px; }
nav { margin-left:auto; font-size:13px; display:flex; gap:14px; }
.lead-card { background:#fff; border:1px solid #e5e7eb; border-left:4px solid #0969da;
             border-radius:10px; padding:16px 18px; font-size:17px; font-weight:600; }
.lead-card .note { font-weight:400; }
.dims { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:14px 0 4px; }
.dim { background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; }
.dim-name { font-size:12px; color:#6b7280; }
.dim-state { font-size:17px; font-weight:700; margin:2px 0 6px; }
.dim-num { font-size:12px; color:#57606a; font-variant-numeric:tabular-nums; }
.dim.tight { border-left:4px solid #0a7d32; } .dim.tight .dim-state { color:#0a7d32; }
.dim.loose { border-left:4px solid #c0392b; } .dim.loose .dim-state { color:#c0392b; }
.dim.neutral { border-left:4px solid #d0d7de; } .dim.neutral .dim-state { color:#6b7280; }
.grid { display:grid; grid-template-columns:2fr 1fr; gap:16px; align-items:start; }
.col-main h2:first-child, .col-side h2:first-child { margin-top:0; }
@media (max-width:900px) {
  .dims { grid-template-columns:repeat(2,1fr); }
  .grid { grid-template-columns:1fr; }
}
.card { background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px 16px; margin-bottom:14px; }
.note { color:#6b7280; font-size:12px; margin-top:6px; }
table { width:100%; border-collapse:collapse; }
th,td { padding:7px 9px; text-align:left; border-bottom:1px solid #eef0f3; font-size:13px; }
th { color:#6b7280; font-weight:500; background:#fafbfc; }
th.num, td.num { text-align:right; font-variant-numeric:tabular-nums; }
tr:last-child td { border-bottom:none; }
.tablewrap { overflow-x:auto; }
.star { display:inline-block; padding:0 6px; margin-left:6px; border-radius:8px;
        background:#0969da; color:#fff; font-size:11px; font-weight:600; vertical-align:1px; }
.mainrow td { background:#f7fbff; }
.up { color:#0a7d32; } .dn { color:#c0392b; } .flat { color:#6b7280; }
.warn { background:#fff8e1; border:1px solid #ffe082; color:#8a6d00;
        border-radius:8px; padding:10px 14px; margin-bottom:10px; font-size:13px; }
.info { background:#eef6ff; border:1px solid #b6d7f7; color:#1a4f8a;
        border-radius:8px; padding:10px 14px; margin-bottom:10px; font-size:13px; }
.newslist { margin:0; padding-left:0; list-style:none; max-height:420px; overflow-y:auto; }
.newslist li { padding:7px 0; border-bottom:1px dashed #eef0f3; font-size:13px; line-height:1.65; }
.newslist li:last-child { border-bottom:none; }
.newslist li.hl { background:#fff8e1; margin:0 -8px; padding:7px 8px; border-radius:6px; }
.ntime { color:#8c959f; font-size:12px; margin-right:8px; font-variant-numeric:tabular-nums; }
.nstar { color:#e6a400; letter-spacing:1px; font-size:12px; }
details.checks { background:#fff; border:1px solid #e5e7eb; border-radius:10px;
                 padding:12px 16px; margin-top:16px; }
details.checks summary { cursor:pointer; font-size:13px; color:#57606a; }
details.checks summary::before { content:"\\25B8  "; }
details.checks[open] summary::before { content:"\\25BE  "; }
details.checks .body { margin-top:10px; }
footer { color:#9ca3af; font-size:12px; margin-top:26px; border-top:1px solid #e5e7eb; padding-top:14px; }
a { color:#0969da; text-decoration:none; }
.hint { color:#8c959f; font-size:11px; margin-top:2px; }
.formbar { display:flex; gap:14px; align-items:center; flex-wrap:wrap; margin-top:14px; }
.formbar label { font-size:13px; color:#57606a; display:flex; gap:6px; align-items:center; }
input[type=text], input[type=date] { padding:6px 8px; border:1px solid #d0d7de;
        border-radius:6px; font-size:13px; font-family:inherit; color:#1f2328; background:#fff; }
input[data-series] { width:100%; }
#save-btn { background:#0969da; color:#fff; border:0; padding:8px 18px;
        border-radius:6px; font-size:13px; cursor:pointer; font-family:inherit; }
#save-btn:disabled { opacity:.6; cursor:default; }
#form-msg { margin-top:10px; font-size:13px; min-height:18px; }
"""

FORM_JS = """
<script>
(function () {
  var btn = document.getElementById('save-btn');
  var msg = document.getElementById('form-msg');
  function setMsg(t, ok) {
    if (!msg) return;
    msg.textContent = t;
    msg.style.color = ok ? '#0a7d32' : '#c0392b';
  }
  if (!btn) return;
  btn.addEventListener('click', function () {
    var dateEl = document.getElementById('f-date');
    var noteEl = document.getElementById('f-note');
    var date = dateEl ? dateEl.value : '';
    var note = noteEl ? noteEl.value : '';
    var inputs = document.querySelectorAll('input[data-series]');
    var jobs = [];
    for (var i = 0; i < inputs.length; i++) {
      var v = inputs[i].value.trim();
      if (v === '') continue;
      jobs.push(postOne(inputs[i].getAttribute('data-series'), v, date, note));
    }
    function postOne(sid, val, day, noteText) {
      return fetch('/api/manual', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({series: sid, value: Number(val), date: day, note: noteText})
      }).then(function (r) {
        return r.json().then(function (j) {
          return {sid: sid, ok: r.ok && j.ok, err: j.error || ''};
        }, function () {
          return {sid: sid, ok: false, err: '响应解析失败'};
        });
      });
    }
    if (!jobs.length) { setMsg('没有填写任何数值', false); return; }
    btn.disabled = true;
    setMsg('保存中…', true);
    Promise.all(jobs).then(function (res) {
      var bad = res.filter(function (x) { return !x.ok; });
      if (bad.length) {
        setMsg('部分失败：' + bad.map(function (x) {
          return x.sid + ' ' + x.err;
        }).join('；'), false);
        btn.disabled = false;
      } else {
        setMsg('已保存 ' + res.length + ' 项，刷新中…', true);
        setTimeout(function () { location.reload(); }, 500);
      }
    }).catch(function (e) {
      setMsg('网络错误：' + e, false);
      btn.disabled = false;
    });
  });
})();
</script>
"""

DIM_META = {
    "cost": "成本端",
    "supply": "供给端",
    "demand": "需求端",
    "struct": "结构",
}

# 各维度的状态措辞与配色（1=偏紧/偏强，0=中性，-1=偏松/偏弱）
DIM_STATE = {
    "cost":   {1: "偏紧", 0: "中性", -1: "偏松"},
    "supply": {1: "偏紧", 0: "中性", -1: "偏松"},
    "demand": {1: "偏强", 0: "中性", -1: "偏弱"},
    "struct": {1: "偏强", 0: "中性", -1: "偏弱"},
}
DIM_TAG = {1: "tight", 0: "neutral", -1: "loose"}


def dim_sign(v):
    """把维度得分归一化到 -1 / 0 / 1（结构维度可能累加到 ±2）。"""
    return 1 if v > 0 else (-1 if v < 0 else 0)


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fmt(v, nd=1):
    if v is None:
        return "—"
    return ("{:,.%df}" % nd).format(v)


def pct(before, after):
    if not before:
        return None
    return (after - before) / abs(before) * 100.0


def cls_of(delta):
    if delta is None:
        return "flat"
    if delta > 0:
        return "up"
    if delta < 0:
        return "dn"
    return "flat"


def arrow(delta):
    if delta is None:
        return ""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def latest_pair(conn, sid):
    """返回 (最新行, 前一行, 变化量)。"""
    rows = db.history(conn, sid, limit=2)
    if not rows:
        return None, None, None
    last = rows[-1]
    prev = rows[0] if len(rows) > 1 else None
    delta = (last["value"] - prev["value"]) if prev else None
    return last, prev, delta


def percentile(conn, sid):
    """最新值在历史中的百分位（0-100）。样本不足返回 None。"""
    rows = db.history(conn, sid)
    if len(rows) < 8:
        return None
    vals = [r["value"] for r in rows]
    cur = vals[-1]
    below = sum(1 for v in vals if v < cur)
    return below / (len(vals) - 1) * 100.0


def stale_days(row):
    if not row or not row["ingest_time"]:
        return None
    try:
        t = datetime.datetime.fromisoformat(row["ingest_time"])
    except ValueError:
        return None
    return (datetime.date.today() - t.date()).days


def build(conn, cfg):
    th = cfg["thresholds"]
    data = {"thresholds": th}

    # ---- 行情（合约按交割月滚动） ----
    groups = contract_mod.groups(cfg)
    m_codes = groups["M"]
    quotes = []
    for code in m_codes:
        last, prev, delta = latest_pair(conn, "A1.%s.settle" % code)
        if not last:
            continue
        oi = db.latest(conn, "A1.%s.oi" % code)
        vol = db.latest(conn, "A1.%s.volume" % code)
        quotes.append({
            "code": code, "date": last["date"], "settle": last["value"],
            "delta": delta, "oi": oi["value"] if oi else None,
            "vol": vol["value"] if vol else None,
        })
    data["quotes"] = quotes

    # 主力 = 当前活跃合约里持仓量最大的
    main = None
    if quotes:
        main = max(quotes, key=lambda q: q["oi"] or 0)["code"]
    for q in quotes:
        q["is_main"] = (q["code"] == main)
    data["main"] = main
    data["active"] = m_codes

    # ---- 外盘 ----
    us_last, us_prev, us_delta = latest_pair(conn, "A3.S.close")
    data["us"] = {"last": us_last["value"] if us_last else None,
                  "delta": us_delta,
                  "chg_pct": pct(us_prev["value"], us_last["value"])
                  if us_last and us_prev else None,
                  "date": us_last["date"] if us_last else None}

    # ---- 现货 / 基差 ----
    spot, _, _ = latest_pair(conn, cfg["spot"]["series"])
    basis, _, basis_delta = latest_pair(conn, "A5.basis.main")
    data["spot"] = {"value": spot["value"] if spot else None,
                    "date": spot["date"] if spot else None}
    data["basis"] = {"value": basis["value"] if basis else None,
                     "delta": basis_delta,
                     "date": basis["date"] if basis else None}

    # ---- 库存与分位 ----
    inv, inv_prev, inv_delta = latest_pair(conn, "B1.inventory")
    inv_pct = percentile(conn, "B1.inventory")
    data["inventory"] = {
        "value": inv["value"] if inv else None,
        "date": inv["date"] if inv else None,
        "delta": inv_delta, "pct": inv_pct,
    }

    # ---- 价差 ----
    spreads = []
    for i in range(len(m_codes) - 1):
        sid = "A6.spread.%s-%s" % (m_codes[i], m_codes[i + 1])
        r, _, d = latest_pair(conn, sid)
        if r:
            spreads.append({"name": "%s−%s" % (m_codes[i], m_codes[i + 1]),
                            "value": r["value"], "delta": d})
    r_mr = db.latest(conn, "A7.spread.M-RM")
    if r_mr:
        spreads.append({"name": "豆粕−菜粕", "value": r_mr["value"], "delta": None})
    data["spreads"] = spreads

    # ---- 四维状态 ----
    dims = {}

    cost = 0
    if data["us"]["chg_pct"] is not None:
        if data["us"]["chg_pct"] > 0.3:
            cost = 1
        elif data["us"]["chg_pct"] < -0.3:
            cost = -1
    dims["cost"] = cost

    supply = 0
    supply_basis = "无数据"
    if inv is not None:
        if inv_pct is not None:
            supply_basis = "5年分位"
            if inv_pct >= th["inventory_high_pct"]:
                supply = -1
            elif inv_pct <= th["inventory_low_pct"]:
                supply = 1
        else:
            abs_th = th.get("inventory_abs", {})
            supply_basis = "绝对水平"
            if inv["value"] >= abs_th.get("loose", 115):
                supply = -1
            elif inv["value"] <= abs_th.get("tight", 60):
                supply = 1
    dims["supply"] = supply
    data["supply_basis"] = supply_basis
    data["inv_n"] = db.count(conn, "B1.inventory")

    profit, profit_prev, profit_delta = latest_pair(conn, "C1.hog_profit")
    demand = 0
    if profit is not None:
        if profit["value"] >= th["demand_profit_high"]:
            demand = 1
        elif profit["value"] <= th["demand_profit_low"]:
            demand = -1
    dims["demand"] = demand
    data["hog_profit"] = {
        "value": profit["value"] if profit else None,
        "date": profit["date"] if profit else None,
        "delta": profit_delta,
    }
    data["demand_missing"] = profit is None

    hf, _, hf_delta = latest_pair(conn, "C2.hog_future.main")
    data["hog_future"] = {
        "value": hf["value"] if hf else None,
        "delta": hf_delta,
        "date": hf["date"] if hf else None,
    }

    cm, _, cm_delta = latest_pair(conn, "B6.crush_margin")
    data["crush_margin"] = {
        "value": cm["value"] if cm else None,
        "delta": cm_delta,
        "date": cm["date"] if cm else None,
    }
    sc, _, _ = latest_pair(conn, "B6.soy_cost")
    data["soy_cost"] = {"value": sc["value"] if sc else None}

    nc = cfg.get("news") or {}
    if nc.get("enabled"):
        data["news"] = [dict(r) for r in conn.execute(
            "SELECT * FROM news WHERE kind IN ('flash','news') "
            "ORDER BY pub_time DESC LIMIT ?", (int(nc.get("show_count", 10)),))]
        data["calendar"] = [dict(r) for r in conn.execute(
            "SELECT * FROM news WHERE kind='calendar' "
            "ORDER BY pub_time ASC LIMIT ?", (int(nc.get("calendar_count", 8)),))]
    else:
        data["news"] = []
        data["calendar"] = []

    struct = 0
    if data["basis"]["value"] is not None:
        struct += -1 if data["basis"]["value"] < 0 else 1
    if spreads and spreads[0]["value"] is not None:
        struct += -1 if spreads[0]["value"] < 0 else 1
    dims["struct"] = struct

    data["dims"] = dims
    data["lead"] = make_lead(dims, data)
    data["checks"] = make_checks(conn, cfg, data)

    manual = []
    for sid, meta in cfg["manual_series"].items():
        row = db.latest(conn, sid)
        manual.append({
            "sid": sid, "name": meta["name"], "unit": meta["unit"],
            "hint": meta.get("hint", ""),
            "value": row["value"] if row else None,
            "date": row["date"] if row else None,
        })
    data["manual"] = manual
    return data


def make_lead(dims, data):
    cost, supply = dims["cost"], dims["supply"]
    demand, struct = dims["demand"], dims["struct"]
    if demand < 0 and supply < 0 and struct <= 0:
        return "供给宽松、养殖亏损、基差偏弱：需求端无支撑，反弹空间有限。"
    if supply <= 0 and struct <= 0 and cost >= 0:
        return "外盘成本有支撑，内盘库存与基差偏弱：震荡偏强，反弹受限。"
    if supply <= 0 and struct <= 0 and cost < 0:
        return "内外皆弱：供给宽松叠加成本回落，趋势偏空。"
    if supply > 0 and cost > 0:
        return "供给偏紧、成本抬升：偏强，回调可跟。"
    if supply > 0 and cost <= 0:
        return "供给端有支撑但成本回落：区间震荡，别追高。"
    return "多空交织，等待驱动。"


def make_checks(conn, cfg, data):
    """数据体检：把 stale / missing / manual 全部暴露出来。"""
    th = cfg["thresholds"]
    out = []

    if data["us"]["last"] is None:
        out.append(("缺失", "A3 美豆收盘价未入库", "运行 fetch.py"))
    if data["spot"]["value"] is None:
        out.append(("缺失", "A4 现货价未录入", "运行 manual.py set A4.spot <价格>"))
    if data["inventory"]["value"] is None:
        out.append(("缺失", "B1 油厂豆粕库存未录入", "运行 manual.py set B1.inventory <万吨>"))

    manual_items = []
    limits = {"D": th["stale_days"], "W": th["stale_days"] + 7, "M": 45}
    for sid, meta in cfg["manual_series"].items():
        row = db.latest(conn, sid)
        if not row:
            continue
        limit = limits.get(meta.get("freq", "D"), th["stale_days"])
        d = stale_days(row)
        if d is not None and d > limit:
            out.append(("过期", "%s 已 %d 天未更新（%s）" % (sid, d, row["date"]),
                        "请补录最新数据（%s）" % meta["name"]))
        if row["quality"] == "manual":
            manual_items.append("%s=%s%s(%s)" % (
                sid, fmt(row["value"], 2), row["unit"], row["date"]))

    if manual_items:
        out.append(("手工", "本次展示含 %d 项手工录入" % len(manual_items),
                    "；".join(manual_items) + "，请核对口径"))

    if data["inventory"]["value"] is not None and data["inventory"]["pct"] is None:
        out.append(("样本", "B1 历史仅 %d 条，分位不可用，已改用绝对水平判断"
                    % data["inv_n"],
                    "样本满 %d 周后自动切回分位" % th["min_history_for_pct"]))

    if data["hog_profit"]["value"] is None:
        out.append(("缺失", "C1 生猪养殖利润未录入", "在下方表单填写，或用 manual.py"))

    sa = cfg.get("spot_alt") or {}
    if sa.get("enabled") and data["spot"]["value"] is not None:
        alt = db.latest(conn, sa["series"])
        nb = db.latest(conn, "A5.basis.100ppi")
        if alt:
            extra = ""
            if nb:
                extra = "；生意社近月基差 %+.0f" % nb["value"]
            out.append(("口径", "现货两口径：华东 %s vs %s%s %s（差 %+.0f）%s"
                        % (fmt(data["spot"]["value"], 0), sa["vendor"],
                           sa["label"], fmt(alt["value"], 2),
                           data["spot"]["value"] - alt["value"], extra),
                        "基差一律以华东价为准，此处仅作交叉校验"))

    if not out:
        out.append(("正常", "所有关键序列均在有效期内", ""))
    return out


def render_index(cfg, data):
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = []
    for key in ("cost", "supply", "demand", "struct"):
        v = data["dims"][key]
        if key == "demand" and data.get("demand_missing"):
            tag, text = "neutral", "缺数据"
        else:
            sign = dim_sign(v)
            tag = DIM_TAG.get(sign, "neutral")
            text = DIM_STATE[key].get(sign, "中性")
        nums = "".join("<div class='dim-num'>%s</div>" % html.escape(x)
                       for x in dim_brief(key, data))
        cards.append("<div class='dim %s'><div class='dim-name'>%s</div>"
                     "<div class='dim-state'>%s</div>%s</div>"
                     % (tag, DIM_META[key], text, nums))

    quote_rows = "".join(
        "<tr%s><td>%s%s</td><td class='num'>%s</td><td class='num %s'>%s %s</td>"
        "<td class='num'>%s</td><td class='num'>%s</td></tr>"
        % (" class='mainrow'" if q["is_main"] else "", q["code"],
           " <span class='star'>主力</span>" if q["is_main"] else "",
           fmt(q["settle"], 1), cls_of(q["delta"]),
           fmt(q["delta"], 1), arrow(q["delta"]),
           fmt(q["oi"], 0) if q["oi"] else "—",
           fmt(q["vol"], 0) if q["vol"] else "—")
        for q in data["quotes"]
    ) or "<tr><td colspan='5'>暂无行情数据</td></tr>"

    spread_rows = "".join(
        "<tr><td>%s</td><td class='num %s'>%s %s</td></tr>"
        % (s["name"], cls_of(s["delta"]), fmt(s["value"], 1), arrow(s["delta"]))
        for s in data["spreads"]
    ) or "<tr><td colspan='2'>暂无价差数据</td></tr>"

    check_html = "".join(
        "<div class='%s'><b>%s</b> — %s%s</div>"
        % ("warn" if t in ("过期", "缺失", "冲突") else "info",
           html.escape(t), html.escape(m),
           ("　→ " + html.escape(a)) if a else "")
        for t, m, a in data["checks"]
    )

    news_items = []
    for n in data.get("news", []):
        t = (n.get("pub_time") or "")[5:16]
        title = n.get("title") or ""
        text = n.get("content") or ""
        hl = " class='hl'" if n.get("level") == "high" else ""
        link = ""
        if n.get("url"):
            link = " <a href='%s' target='_blank' rel='noopener'>原文</a>" % html.escape(n["url"])
        head = ("<b>%s</b> " % html.escape(title)) if title else ""
        news_items.append("<li%s><span class='ntime'>%s</span>%s%s%s</li>"
                          % (hl, html.escape(t), head, html.escape(text[:170]), link))
    news_html = ""
    if news_items:
        news_html = ("<h2>相关资讯</h2><div class='card'><ul class='newslist'>%s</ul>"
                     "<div class='note'>金十数据，按关键词过滤；黄色为重要关键词</div>"
                     "</div>" % "".join(news_items))

    cal_items = []
    for c in data.get("calendar", []):
        sub = "%s　%s" % ((c.get("pub_time") or "")[5:16], c.get("content") or "")
        cal_items.append(
            "<tr><td class='nstar'>%s</td><td>%s<div class='hint'>%s</div></td></tr>"
            % (html.escape(c.get("keywords") or ""), html.escape(c.get("title") or ""),
               html.escape(sub)))
    cal_html = ""
    if cal_items:
        cal_html = ("<h2>本周重要数据</h2><div class='card'><table>"
                    "<thead><tr><th style='width:60px'>星级</th><th>事件</th></tr></thead>"
                    "<tbody>%s</tbody></table></div>" % "".join(cal_items))

    aside = news_html + cal_html or "<div class='card'><div class='hint'>暂无资讯</div></div>"

    issues = [c for c in data["checks"] if c[0] in ("过期", "缺失", "冲突")]
    notes = len(data["checks"]) - len(issues)
    open_attr = " open" if issues else ""
    if issues:
        summary = "数据体检 · %d 项待处理" % len(issues)
    elif notes:
        summary = "数据体检 · 正常（另有 %d 条说明）" % notes
    else:
        summary = "数据体检 · 全部正常"

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {today}</title><style>{css}</style></head>
<body><div class="wrap">
<header>
  <h1>{title}</h1>
  <div class="meta">{today} · 主力 {main} · {now} 更新</div>
  <nav><a href="brief.html">豆粕简报</a><a href="macro.html">全局简报</a><a href="input.html">录入数据</a><a href="weekly.html">周度数据</a></nav>
</header>

<div class="lead-card">{lead}<div class="note">本句由规则根据下方数据生成，不是投资建议。</div></div>

<div class="dims">{cards}</div>

<div class="grid">
  <div class="col-main">
    <h2>行情（结算价）</h2>
    <div class="card">
      <div class="note" style="margin:0 0 8px">合约按交割月自动滚动，进入交割月的合约不再展示。</div>
      <div class="tablewrap"><table>
        <thead><tr><th>合约</th><th class="num">结算价</th><th class="num">涨跌</th>
        <th class="num">持仓量</th><th class="num">成交量</th></tr></thead>
        <tbody>{quote_rows}</tbody></table></div>
    </div>
    <h2>价差结构</h2>
    <div class="card"><table>
      <thead><tr><th>价差</th><th class="num">数值（元/吨）</th></tr></thead>
      <tbody>{spread_rows}</tbody></table></div>
  </div>
  <aside class="col-side">
    {aside}
  </aside>
</div>

<details class="checks"{open_attr}>
  <summary>{summary}</summary>
  <div class="body">{check_html}</div>
</details>

<footer>
数据来源：新浪财经（行情/外盘/汇率）、生意社（现货对照）、金十数据（资讯/日历），其余为手工录入。<br>
行情为结算价口径；基差 = 现货 − 主力结算价；月差 = 近月 − 远月。本页仅供参考，不构成投资建议。
</footer>
</div></body></html>
""".format(title=html.escape(cfg["site_title"]), today=today, now=now,
           main=html.escape(str(data["main"])), lead=html.escape(data["lead"]),
           cards="".join(cards), quote_rows=quote_rows,
           spread_rows=spread_rows, check_html=check_html,
           aside=aside, open_attr=open_attr, summary=summary, css=CSS)


def dim_brief(key, data):
    """四维卡片里的短数值，最多两行。"""
    if key == "cost":
        us = data["us"]
        if us["last"] is None:
            return ["数据缺失"]
        first = "%s 美分" % fmt(us["last"], 0)
        if us["chg_pct"] is not None:
            first += " %+.2f%%" % us["chg_pct"]
        return [first]
    if key == "supply":
        out = []
        inv = data["inventory"]
        if inv["value"] is not None:
            out.append("库存 %s 万吨" % fmt(inv["value"], 1))
        cm = data.get("crush_margin") or {}
        if cm.get("value") is not None:
            out.append("压榨 %s 元" % fmt(cm["value"], 0))
        return out or ["数据缺失"]
    if key == "demand":
        out = []
        p = data["hog_profit"]
        if p["value"] is not None:
            out.append("利润 %s 元" % fmt(p["value"], 0))
        f = data.get("hog_future") or {}
        if f.get("value") is not None:
            out.append("生猪 %s" % fmt(f["value"], 0))
        return out or ["数据缺失"]
    if key == "struct":
        out = []
        if data["basis"]["value"] is not None:
            out.append("基差 %s" % fmt(data["basis"]["value"], 0))
        if data["spreads"]:
            out.append("月差 %s" % fmt(data["spreads"][0]["value"], 0))
        return out or ["数据缺失"]
    return []


def render_input(conn, cfg, data):
    today = datetime.date.today().isoformat()

    rows = []
    for m in data["manual"]:
        cur = ("%s %s" % (fmt(m["value"], 2), m["unit"])) if m["value"] is not None else "—"
        rows.append(
            "<tr><td><b>%s</b><div class='hint'>%s</div></td>"
            "<td class='num'>%s<div class='hint'>%s</div></td>"
            "<td><input type='text' inputmode='decimal' data-series='%s' "
            "placeholder='留空=不修改'></td></tr>"
            % (html.escape(m["name"]), html.escape(m["hint"]), cur,
               html.escape(m["date"] or ""), html.escape(m["sid"])))

    recent = []
    for r in conn.execute(
            "SELECT series_id, date, value, unit, note FROM series "
            "WHERE quality = 'manual' ORDER BY ingest_time DESC LIMIT 12"):
        recent.append(
            "<tr><td>%s</td><td>%s</td><td class='num'>%s %s</td>"
            "<td class='hint'>%s</td></tr>"
            % (html.escape(r["series_id"]), html.escape(r["date"]),
               fmt(r["value"], 2), html.escape(r["unit"] or ""),
               html.escape(r["note"] or "")))

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据录入 · {title}</title><style>{css}</style></head>
<body><div class="wrap">
<header>
  <h1>数据录入</h1>
  <div class="meta">{today}</div>
  <nav><a href="index.html">← 返回看板</a></nav>
</header>

<div class="card">
  <div class="note" style="margin:0 0 12px">只更新填了值的项，留空的不动；保存后看板会自动重新生成。</div>
  <div class="tablewrap"><table>
    <thead><tr><th style="width:34%">指标</th><th style="width:30%">当前值</th>
    <th>新值</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
  <div class="formbar">
    <label>日期 <input type="date" id="f-date" value="{today}"></label>
    <label>备注 <input type="text" id="f-note" maxlength="100"
      placeholder="如 Mysteel 46周"></label>
    <button id="save-btn" type="button">保存</button>
  </div>
  <div id="form-msg"></div>
</div>

<h2>最近录入</h2>
<div class="card"><div class="tablewrap"><table>
  <thead><tr><th>指标</th><th>日期</th><th class="num">数值</th><th>备注</th></tr></thead>
  <tbody>{recent}</tbody></table></div></div>

<footer>保存接口只能经 https://data.windxiao.site/ 访问；
直接双击本地文件打开时无法保存。</footer>
</div>{form_js}</body></html>
""".format(title=html.escape(cfg["site_title"]), today=today, css=CSS,
           rows="".join(rows), recent="".join(recent) or
           "<tr><td colspan='4'>暂无记录</td></tr>", form_js=FORM_JS)



def render_weekly(conn, cfg, data):
    series_list = [
        ("A4.spot", "豆粕现货价（元/吨）"),
        ("A5.basis.main", "基差（元/吨）"),
        ("A5.basis.100ppi", "生意社近月基差（元/吨）"),
        ("B1.inventory", "油厂豆粕库存（万吨）"),
        ("B2.soy_inventory", "油厂大豆库存（万吨）"),
        ("B3.crush_rate", "油厂开机率（%）"),
        ("C1.hog_profit", "生猪自繁自养利润（元/头）"),
        ("C2.hog_future.main", "生猪期货主力（元/吨）"),
        ("B6.soy_cost", "进口大豆到港成本（元/吨）"),
        ("B6.crush_margin", "盘面压榨利润（元/吨）"),
    ]
    blocks = []
    for sid, title in series_list:
        rows = db.history(conn, sid, limit=16)
        if not rows:
            body = "<tr><td colspan='2'>暂无数据</td></tr>"
        else:
            body = "".join("<tr><td>%s</td><td class='num'>%s</td></tr>"
                           % (r["date"], fmt(r["value"], 2)) for r in reversed(rows))
        blocks.append("""<h2>%s</h2><div class="card"><table>
<thead><tr><th>日期</th><th class="num">数值</th></tr></thead><tbody>%s</tbody></table></div>
""" % (html.escape(title), body))

    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>周度数据</title><style>%s</style></head><body><div class="wrap">
<header><h1>周度数据</h1><div class="meta">最近 16 条记录 · 生成于 %s</div>
<nav><a href="index.html">← 返回看板</a><a href="input.html">录入数据</a></nav></header>
%s
</div></body></html>
""" % (CSS, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "".join(blocks))


def main():
    cfg = load_config()
    conn = db.connect(DB_PATH)

    data = build(conn, cfg)
    os.makedirs(SITE, exist_ok=True)
    os.makedirs(os.path.join(SITE, "data"), exist_ok=True)

    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(render_index(cfg, data))
    with open(os.path.join(SITE, "input.html"), "w", encoding="utf-8") as fh:
        fh.write(render_input(conn, cfg, data))
    with open(os.path.join(SITE, "weekly.html"), "w", encoding="utf-8") as fh:
        fh.write(render_weekly(conn, cfg, data))
    with open(os.path.join(SITE, "data", "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)

    print("[render] index.html / input.html / weekly.html / data/summary.json 已生成")
    print("[render] %s" % data["lead"])


if __name__ == "__main__":
    main()
