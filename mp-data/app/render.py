# -*- coding: utf-8 -*-
"""生成静态页面：site/index.html（盘前看板）、site/weekly.html、site/data/summary.json。

只用标准库。页面引用外部 site/style.css，不内联样式。
"""
import datetime
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts as contract_mod  # noqa: E402
import db  # noqa: E402
import judge  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
SITE = os.path.join(ROOT, "site")

# 页面引用的外部 CSS 路径（相对于 site/）
CSS_HREF = "style.css"

# ─── 通用 JS（相对时间 + 新闻展开 + 保存表单） ───────────────────────────────

COMMON_JS = """
<script>
(function () {
  // 相对时间：把 data-time 属性的 ISO 时间戳转为"X 分钟前"
  function reltime(isoStr) {
    if (!isoStr) return '';
    var d = new Date(isoStr.replace(' ', 'T'));
    if (isNaN(d.getTime())) return isoStr;
    var diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60)   return Math.round(diff) + ' 秒前';
    if (diff < 3600) return Math.round(diff / 60) + ' 分钟前';
    if (diff < 86400)return Math.round(diff / 3600) + ' 小时前';
    return Math.round(diff / 86400) + ' 天前';
  }
  document.querySelectorAll('[data-time]').forEach(function (el) {
    var r = reltime(el.getAttribute('data-time'));
    if (r) el.textContent = r;
  });

  // 新闻"展开更多"
  var more = document.querySelector('.news-more');
  if (more) {
    more.addEventListener('click', function () {
      var ul = document.querySelector('.newslist');
      if (!ul) return;
      ul.classList.toggle('expanded');
      more.textContent = ul.classList.contains('expanded') ? '收起 ▲' : '展开更多 ▼';
    });
  }

  // 迷你涨跌柱宽度（data-delta / data-maxdelta）
  document.querySelectorAll('.mini-bar[data-delta]').forEach(function (el) {
    var d = parseFloat(el.getAttribute('data-delta') || '0');
    var mx = parseFloat(el.closest('table').getAttribute('data-maxdelta') || '50');
    if (!mx) return;
    var pct = Math.min(Math.abs(d) / mx * 100, 100);
    el.style.width = pct + '%';
  });
})();
</script>
"""

FORM_JS = """
<script>
(function () {
  var btn = document.getElementById('save-btn');
  var msg = document.getElementById('form-msg');
  function setMsg(t, ok) {
    if (!msg) return;
    msg.textContent = t;
    msg.style.color = ok ? '#3f6e51' : '#9e4a37';
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
        setMsg('✓ 已保存 ' + res.length + ' 项，刷新中…', true);
        setTimeout(function () { location.reload(); }, 600);
      }
    }).catch(function (e) {
      setMsg('网络错误：' + e, false);
      btn.disabled = false;
    });
  });
})();
</script>
"""

# ─── 常量 ────────────────────────────────────────────────────────────────────

DIM_META = {
    "cost":   "成本端",
    "supply": "供给端",
    "demand": "需求端",
    "struct": "结构",
}

DIM_STATE = {
    "cost":   {1: "偏紧", 0: "中性", -1: "偏松"},
    "supply": {1: "偏紧", 0: "中性", -1: "偏松"},
    "demand": {1: "偏强", 0: "中性", -1: "偏弱"},
    "struct": {1: "偏强", 0: "中性", -1: "偏弱"},
}
DIM_TAG = {1: "tight", 0: "neutral", -1: "loose"}


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def dim_sign(v):
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
    rows = db.history(conn, sid, limit=2)
    if not rows:
        return None, None, None
    last = rows[-1]
    prev = rows[0] if len(rows) > 1 else None
    delta = (last["value"] - prev["value"]) if prev else None
    return last, prev, delta


def percentile(conn, sid):
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


def sparkline_svg(values, width=64, height=22):
    """生成内联 SVG 迷你折线图，只用标准库。values 为数值列表（升序时间）。"""
    if not values or len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn
    if rng == 0:
        # 水平线
        y = height / 2
        pts = " ".join("%.1f,%.1f" % (i * width / (len(values) - 1), y)
                       for i in range(len(values)))
    else:
        pts = " ".join(
            "%.1f,%.1f" % (
                i * width / (len(values) - 1),
                height - (v - mn) / rng * height,
            )
            for i, v in enumerate(values)
        )
    return (
        '<svg class="sparkline" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<polyline points="%s" fill="none" stroke="#336650" stroke-width="1.5"'
        ' stroke-linejoin="round" stroke-linecap="round"/></svg>'
        % (width, height, width, height, pts)
    )


def _page_head(title, active_page=""):
    """返回 <head> 标签块（引用外部 CSS 与 Favicon）。"""
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>%s</title>\n'
        '<link rel="icon" href="favicon.svg" type="image/svg+xml">\n'
        '<link rel="stylesheet" href="%s">\n'
        '</head>\n' % (html.escape(title), CSS_HREF)
    )


def _nav(active="", prefix=""):
    """站点主导航。prefix 供子目录页面使用（如 ../）。"""
    links = [
        ("index.html",  "盘前看板"),
        ("brief.html",  "豆粕简报"),
        ("macro.html",  "全局简报"),
        ("weekly.html", "周度数据"),
        ("input.html",  "数据录入"),
        ("journal.html", "交易复盘"),
        ("admin.html",  "管理"),
    ]
    items = "".join(
        '<a href="%s%s"%s>%s</a>' % (
            prefix,
            href,
            ' class="active"' if active == href else "",
            label,
        )
        for href, label in links
    )
    return '<nav aria-label="主导航">%s</nav>' % items


# ─── build 数据层（不改） ─────────────────────────────────────────────────────

def build(conn, cfg):
    th = cfg["thresholds"]
    data = {"thresholds": th}

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

    main = None
    if quotes:
        main = max(quotes, key=lambda q: q["oi"] or 0)["code"]
    for q in quotes:
        q["is_main"] = (q["code"] == main)
    data["main"] = main
    data["active"] = m_codes

    us_last, us_prev, us_delta = latest_pair(conn, "A3.S.close")
    data["us"] = {"last": us_last["value"] if us_last else None,
                  "delta": us_delta,
                  "chg_pct": pct(us_prev["value"], us_last["value"])
                  if us_last and us_prev else None,
                  "date": us_last["date"] if us_last else None}

    spot, _, _ = latest_pair(conn, cfg["spot"]["series"])
    basis, _, basis_delta = latest_pair(conn, "A5.basis.main")
    data["spot"] = {"value": spot["value"] if spot else None,
                    "date": spot["date"] if spot else None}
    data["basis"] = {"value": basis["value"] if basis else None,
                     "delta": basis_delta,
                     "date": basis["date"] if basis else None}

    inv, inv_prev, inv_delta = latest_pair(conn, "B1.inventory")
    inv_pct = percentile(conn, "B1.inventory")
    data["inventory"] = {
        "value": inv["value"] if inv else None,
        "date": inv["date"] if inv else None,
        "delta": inv_delta, "pct": inv_pct,
    }

    r_wasde = db.latest(conn, "D1.wasde_stock")
    data["wasde"] = {
        "value": r_wasde["value"] if r_wasde else None,
        "date": r_wasde["date"] if r_wasde else None,
    }

    spreads = []
    for i in range(len(m_codes) - 1):
        sid = "A6.spread.%s-%s" % (m_codes[i], m_codes[i + 1])
        r, _, d = latest_pair(conn, sid)
        if r:
            spreads.append({"name": "%s−%s" % (m_codes[i], m_codes[i + 1]),
                            "value": r["value"], "delta": d,
                            "pct": percentile(conn, sid)})
    r_mr = db.latest(conn, "A7.spread.M-RM")
    if r_mr:
        spreads.append({"name": "豆粕−菜粕", "value": r_mr["value"], "delta": None,
                        "pct": percentile(conn, "A7.spread.M-RM")})
    data["spreads"] = spreads

    dims = {}

    cost = 0
    if data["us"]["chg_pct"] is not None:
        if data["us"]["chg_pct"] > 0.3:
            cost = 1
        elif data["us"]["chg_pct"] < -0.3:
            cost = -1
    dims["cost"] = cost

    supply = 0
    if inv is not None:
        if inv_pct is not None:
            if inv_pct >= th["inventory_high_pct"]:
                supply = -1
            elif inv_pct <= th["inventory_low_pct"]:
                supply = 1
        else:
            abs_th = th.get("inventory_abs", {})
            if inv["value"] >= abs_th.get("loose", 115):
                supply = -1
            elif inv["value"] <= abs_th.get("tight", 60):
                supply = 1
    dims["supply"] = supply
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
        # 近 3 次历史值
        hist = db.history(conn, sid, limit=4)
        recent_vals = [{"date": r["date"], "value": r["value"]}
                       for r in reversed(hist)][-4:-1]  # 最多 3 次，不含当前
        manual.append({
            "sid": sid, "name": meta["name"], "unit": meta["unit"],
            "hint": meta.get("hint", ""),
            "value": row["value"] if row else None,
            "date": row["date"] if row else None,
            "recent": recent_vals,
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


def dim_brief(key, data):
    if key == "cost":
        us = data["us"]
        if us["last"] is None:
            return ["数据缺失"]
        first = "%s 美分" % fmt(us["last"], 0)
        if us["chg_pct"] is not None:
            first += " %+.2f%%" % us["chg_pct"]
        out = [first]
        wasde = data.get("wasde") or {}
        if wasde.get("value") is not None:
            # 1 亿蒲（大豆，60 磅/蒲）= 272.155 万吨
            out.append("美豆库存 %s 亿蒲（%s 万吨）" % (
                fmt(wasde["value"], 2), fmt(wasde["value"] * 272.155, 0)))
        return out
    if key == "supply":
        out = []
        inv = data["inventory"]
        if inv["value"] is not None:
            s = "库存 %s 万吨" % fmt(inv["value"], 1)
            if inv.get("pct") is not None:
                s += "（%d%% 分位）" % inv["pct"]
            out.append(s)
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


# ─── render_index ─────────────────────────────────────────────────────────────

def render_index(cfg, data):
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Hero strip ──
    main_q = next((q for q in data["quotes"] if q["is_main"]), None)
    if main_q:
        price_str = fmt(main_q["settle"], 1)
        delta_cls = cls_of(main_q["delta"])
        delta_str = ("%+.1f %s" % (main_q["delta"], arrow(main_q["delta"]))) if main_q["delta"] is not None else "—"
        hero_left = """
<div class="hero-cell">
  <div class="hero-eyebrow">主力合约 · 结算价</div>
  <div class="hero-main-price">%s</div>
  <div class="hero-contract">%s &nbsp;元/吨 · %s</div>
  <div class="hero-delta %s">%s</div>
</div>""" % (
            html.escape(price_str),
            html.escape(main_q["code"]),
            html.escape(main_q["date"] or ""),
            delta_cls,
            html.escape(delta_str),
        )
    else:
        hero_left = '<div class="hero-cell"><div class="hero-eyebrow">主力合约</div><div class="hero-val">暂无数据</div></div>'

    # 中间格：现货 / 基差 / 美豆
    spot_val = fmt(data["spot"]["value"], 0) if data["spot"]["value"] is not None else "—"
    spot_date = data["spot"]["date"] or ""
    basis_val = fmt(data["basis"]["value"], 0) if data["basis"]["value"] is not None else "—"
    basis_cls = cls_of(data["basis"]["value"])
    us_val = (fmt(data["us"]["last"], 0) + " 美分") if data["us"]["last"] is not None else "—"
    us_chg = ("%+.2f%%" % data["us"]["chg_pct"]) if data["us"]["chg_pct"] is not None else ""
    hero_mid = """
<div class="hero-cell">
  <div class="hero-kv">
    <div class="hero-label">现货价（华东）</div>
    <div class="hero-val">%s <span style="font-size:12px;opacity:.6">元/吨</span></div>
    <div class="hero-sub">%s</div>
  </div>
  <div class="hero-kv">
    <div class="hero-label">基差（主力）</div>
    <div class="hero-val %s">%s</div>
  </div>
  <div class="hero-kv">
    <div class="hero-label">CBOT 美豆</div>
    <div class="hero-val">%s <span style="font-size:12px;opacity:.6">%s</span></div>
  </div>
</div>""" % (
        html.escape(spot_val), html.escape(spot_date),
        basis_cls, html.escape(basis_val),
        html.escape(us_val), html.escape(us_chg),
    )

    # 右侧：判断语（规则 + AI 并列）
    ai_text = (data.get("ai") or "").strip()
    if ai_text:
        ai_block = """
  <div class="hero-split"></div>
  <div class="hero-eyebrow">AI 判断</div>
  <div class="hero-ai">%s</div>
  <div class="hero-lead-note">模型生成，仅供研究参考。</div>""" % html.escape(ai_text)
    else:
        ai_block = ""
    hero_right = """
<div class="hero-cell">
  <div class="hero-eyebrow">规则判断</div>
  <div class="hero-lead">%s</div>
  <div class="hero-lead-note">规则根据下方数据生成，仅供研究参考。</div>%s
</div>""" % (html.escape(data["lead"]), ai_block)

    hero_html = '<div class="hero">%s%s%s</div>' % (hero_left, hero_mid, hero_right)

    # ── 四维状态条 ──
    dim_cards = []
    for key in ("cost", "supply", "demand", "struct"):
        v = data["dims"][key]
        if key == "demand" and data.get("demand_missing"):
            tag, text = "neutral", "缺数据"
        else:
            sign = dim_sign(v)
            tag = DIM_TAG.get(sign, "neutral")
            text = DIM_STATE[key].get(sign, "中性")
        data_lines = dim_brief(key, data)
        data_html = "".join("<div>%s</div>" % html.escape(x) for x in data_lines)
        dim_cards.append(
            "<div class='dim %s'>"
            "<div class='dim-name'>%s</div>"
            "<div class='dim-state'>%s</div>"
            "<div class='dim-data'>%s</div>"
            "</div>" % (tag, DIM_META[key], html.escape(text), data_html)
        )
    dims_html = '<div class="dims">%s</div>' % "".join(dim_cards)

    # ── 合约行情表（带迷你涨跌柱） ──
    if data["quotes"]:
        max_abs_delta = max((abs(q["delta"]) for q in data["quotes"] if q["delta"] is not None), default=50) or 50
    else:
        max_abs_delta = 50

    quote_rows = ""
    for q in data["quotes"]:
        cls = " class='mainrow'" if q["is_main"] else ""
        star = " <span class='star'>主力</span>" if q["is_main"] else ""
        oi_wan = fmt(q["oi"] / 10000, 1) if q["oi"] else "—"
        vol_wan = fmt(q["vol"] / 10000, 1) if q["vol"] else "—"
        # 迷你柱
        bar_cls = cls_of(q["delta"])
        bar = ("<div class='mini-bar %s' data-delta='%.1f'></div>"
               % (bar_cls, q["delta"] if q["delta"] is not None else 0))
        quote_rows += (
            "<tr%s><td>%s%s</td>"
            "<td class='num'>%s</td>"
            "<td class='num %s'>%s %s</td>"
            "<td class='bar-cell'>%s</td>"
            "<td class='num'>%s</td>"
            "<td class='num'>%s</td></tr>"
        ) % (
            cls, q["code"], star,
            fmt(q["settle"], 1),
            cls_of(q["delta"]), fmt(q["delta"], 1), arrow(q["delta"]),
            bar,
            oi_wan, vol_wan,
        )
    if not quote_rows:
        quote_rows = "<tr><td colspan='6'>暂无行情数据</td></tr>"

    quote_html = """
<h2>行情（结算价）</h2>
<div class="card">
  <div class="note" style="margin:0 0 8px">合约按交割月自动滚动，进入交割月的合约不再展示。持仓/成交量单位：万手。</div>
  <div class="tablewrap"><table data-maxdelta="%.1f">
    <thead><tr><th>合约</th><th class="num">结算价</th><th class="num">涨跌</th>
    <th class="bar-cell"></th><th class="num">持仓</th><th class="num">成交</th></tr></thead>
    <tbody>%s</tbody>
  </table></div>
</div>""" % (max_abs_delta, quote_rows)

    # ── 价差 ──
    spread_rows = "".join(
        "<tr><td>%s</td><td class='num %s'>%s %s</td><td class='num'>%s</td></tr>"
        % (s["name"], cls_of(s["delta"]), fmt(s["value"], 1), arrow(s["delta"]),
           ("%.0f%%" % s["pct"]) if s.get("pct") is not None else "—")
        for s in data["spreads"]
    ) or "<tr><td colspan='3'>暂无价差数据</td></tr>"

    spread_html = """
<h2>价差结构</h2>
<div class="card"><table>
  <thead><tr><th>价差</th><th class="num">数值（元/吨）</th><th class="num">历史分位</th></tr></thead>
  <tbody>%s</tbody>
</table>
  <div class="note" style="margin-top:8px">历史分位＝当前价差在过去一年同组合约中的位置，低位偏近月弱、高位偏近月强。</div>
</div>""" % spread_rows

    # ── 核心指标 2×2 ──
    def kpi_card(label, val_str, sub_str, pct_val=None):
        pct_html = ""
        if pct_val is not None:
            pct_html = (
                "<div class='pct-bar-wrap'><div class='pct-bar' style='width:%.0f%%'></div></div>"
                "<div class='pct-label'>历史 %.0f%% 分位</div>" % (pct_val, pct_val)
            )
        return (
            "<div class='kpi-card'>"
            "<div class='kpi-label'>%s</div>"
            "<div class='kpi-val'>%s</div>"
            "<div class='kpi-sub'>%s</div>"
            "%s</div>"
        ) % (html.escape(label), html.escape(val_str), html.escape(sub_str), pct_html)

    spot_v = fmt(data["spot"]["value"], 0)
    spot_sub = "华东 · %s" % (data["spot"]["date"] or "未录入")
    basis_v = fmt(data["basis"]["value"], 0)
    basis_sub_parts = []
    if data["basis"]["delta"] is not None:
        basis_sub_parts.append("环比 %+.0f" % data["basis"]["delta"])
    if data["basis"]["date"]:
        basis_sub_parts.append(data["basis"]["date"])
    basis_sub = " · ".join(basis_sub_parts) or "暂无"

    inv_v = (fmt(data["inventory"]["value"], 1) + " 万吨") if data["inventory"]["value"] is not None else "—"
    inv_sub = data["inventory"]["date"] or "未录入"

    hog_v = (fmt(data["hog_profit"]["value"], 0) + " 元/头") if data["hog_profit"]["value"] is not None else "—"
    hog_sub = data["hog_profit"]["date"] or "未录入"

    kpi_html = (
        "<div class='kpi-grid'>"
        + kpi_card("现货价（华东）", spot_v + " 元/吨", spot_sub)
        + kpi_card("基差（主力）", basis_v + " 元/吨", basis_sub)
        + kpi_card("油厂豆粕库存", inv_v, inv_sub, data["inventory"]["pct"])
        + kpi_card("生猪养殖利润", hog_v, hog_sub)
        + "</div>"
    )

    # ── 右侧：资讯 ──
    news_items = []
    for idx, n in enumerate(data.get("news", [])):
        t = n.get("pub_time") or ""
        title = n.get("title") or ""
        text = n.get("content") or ""
        hl = " class='hl%s'" % (" news-hidden" if idx >= 6 else "") if n.get("level") == "high" else (
            " class='news-hidden'" if idx >= 6 else "")
        link = ""
        if n.get("url"):
            link = " <a href='%s' target='_blank' rel='noopener'>原文</a>" % html.escape(n["url"])
        head = ("<b>%s</b> " % html.escape(title)) if title else ""
        news_items.append(
            "<li%s><span class='ntime' data-time='%s'>%s</span>%s%s%s</li>"
            % (hl, html.escape(t), html.escape(t[5:16]), head, html.escape(text[:160]), link)
        )

    news_html = ""
    if news_items:
        more_btn = ""
        if len(news_items) > 6:
            more_btn = "<span class='news-more'>展开更多 ▼</span>"
        news_html = (
            "<h2>相关资讯</h2><div class='card'>"
            "<ul class='newslist'>%s</ul>%s"
            "<div class='note'>金十数据，按关键词过滤；红色高亮为重要关键词</div>"
            "</div>"
        ) % ("".join(news_items), more_btn)

    cal_items = []
    for c in data.get("calendar", []):
        sub = "%s　%s" % ((c.get("pub_time") or "")[5:16], c.get("content") or "")
        cal_items.append(
            "<tr><td class='nstar'>%s</td><td>%s<div class='hint'>%s</div></td></tr>"
            % (html.escape(c.get("keywords") or ""), html.escape(c.get("title") or ""),
               html.escape(sub))
        )
    cal_html = ""
    if cal_items:
        cal_html = (
            "<h2>本周重要数据</h2><div class='card'><table>"
            "<thead><tr><th style='width:60px'>星级</th><th>事件</th></tr></thead>"
            "<tbody>%s</tbody></table></div>"
        ) % "".join(cal_items)

    aside = news_html + cal_html or "<div class='card'><div class='hint'>暂无资讯</div></div>"

    # ── 数据体检 ──
    check_html = "".join(
        "<div class='%s'><b>%s</b> — %s%s</div>"
        % ("warn" if t in ("过期", "缺失", "冲突") else "info",
           html.escape(t), html.escape(m),
           ("　→ " + html.escape(a)) if a else "")
        for t, m, a in data["checks"]
    )

    issues = [c for c in data["checks"] if c[0] in ("过期", "缺失", "冲突")]
    notes = len(data["checks"]) - len(issues)
    open_attr = " open" if issues else ""
    if issues:
        summary = "数据体检 · %d 项待处理" % len(issues)
    elif notes:
        summary = "数据体检 · 正常（另有 %d 条说明）" % notes
    else:
        summary = "数据体检 · 全部正常"

    return """{head}
<body><div class="wrap">
<header>
  <h1>{title}</h1>
  <div class="meta">{today} · 主力 {main} · {now} 更新</div>
  {nav}
</header>

{hero}
{dims}

<div class="grid">
  <div class="col-main">
    {kpi}
    {quote_html}
    {spread_html}
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
</div>
{js}
</body></html>
""".format(
        head=_page_head("%s · %s" % (cfg["site_title"], today)),
        title=html.escape(cfg["site_title"]),
        today=today, now=now,
        main=html.escape(str(data["main"])),
        nav=_nav("index.html"),
        hero=hero_html,
        dims=dims_html,
        kpi=kpi_html,
        quote_html=quote_html,
        spread_html=spread_html,
        aside=aside,
        open_attr=open_attr,
        summary=html.escape(summary),
        check_html=check_html,
        js=COMMON_JS,
    )


# ─── render_input ─────────────────────────────────────────────────────────────

def render_input(conn, cfg, data):
    today = datetime.date.today().isoformat()

    # 录入卡
    input_cards = []
    for m in data["manual"]:
        cur_str = ("%s %s" % (fmt(m["value"], 2), m["unit"])) if m["value"] is not None else "—"
        date_str = m["date"] or "未录入"

        # 近 3 次历史参考
        hist_str = ""
        if m.get("recent"):
            parts = ["%s:%s" % (r["date"][5:], fmt(r["value"], 2)) for r in m["recent"]]
            hist_str = "近期：" + " / ".join(parts)

        input_cards.append("""
<div class="input-card">
  <div class="input-card-info">
    <div class="input-card-name">%s</div>
    <div class="input-card-hint">%s</div>
    <div class="input-card-cur">%s</div>
    <div class="input-card-date">%s</div>
    %s
  </div>
  <div class="input-card-right">
    <label>新值（%s）</label>
    <input type="text" inputmode="decimal" data-series="%s" placeholder="留空=不修改">
  </div>
</div>""" % (
            html.escape(m["name"]),
            html.escape(m["hint"]),
            html.escape(cur_str),
            html.escape(date_str),
            ("<div class='input-card-history'>%s</div>" % html.escape(hist_str)) if hist_str else "",
            html.escape(m["unit"]),
            html.escape(m["sid"]),
        ))

    # 最近录入记录
    recent = []
    for r in conn.execute(
            "SELECT series_id, date, value, unit, note FROM series "
            "WHERE quality = 'manual' ORDER BY ingest_time DESC LIMIT 12"):
        recent.append(
            "<tr><td>%s</td><td>%s</td><td class='num'>%s %s</td>"
            "<td class='hint'>%s</td></tr>"
            % (html.escape(r["series_id"]), html.escape(r["date"]),
               fmt(r["value"], 2), html.escape(r["unit"] or ""),
               html.escape(r["note"] or ""))
        )

    return """{head}
<body><div class="wrap">
<header>
  <h1>数据录入</h1>
  <div class="meta">{today}</div>
  {nav}
</header>

<div class="card" id="gate">
  <div class="note" style="margin:0 0 12px">录入数据需要管理码验证（访问码只用来查看）</div>
  <form class="gate-form" id="gform" style="max-width:400px">
    <input type="password" id="acode" autocomplete="off" autofocus placeholder="管理码">
    <button id="gbtn" type="submit">验证</button>
  </form>
  <div id="gmsg" class="gate-msg"></div>
</div>

<div id="area" style="display:none">
<div class="note" style="margin:0 0 14px">只更新填了值的项，留空的不动；保存后看板会自动重新生成。</div>

<div class="input-cards">{cards}</div>

<div class="card" style="margin-top:14px">
  <div class="formbar">
    <label>日期 <input type="date" id="f-date" value="{today}" style="width:160px"></label>
    <label>备注 <input type="text" id="f-note" maxlength="100" placeholder="如 Mysteel 46周" style="width:200px"></label>
    <button id="save-btn" type="button">保存</button>
  </div>
  <div id="form-msg"></div>
</div>

<h2>最近录入</h2>
<div class="card"><div class="tablewrap"><table class="recent-table">
  <thead><tr><th>指标</th><th>日期</th><th class="num">数值</th><th>备注</th></tr></thead>
  <tbody>{recent}</tbody>
</table></div></div>
</div>

<footer>保存接口只能经 nginx 代理后访问；直接双击本地文件打开时无法保存。</footer>
</div>
<script>
(function () {{
  var gate = document.getElementById('gate');
  var area = document.getElementById('area');
  var gmsg = document.getElementById('gmsg');
  var gbtn = document.getElementById('gbtn');
  function show() {{ gate.style.display = 'none'; area.style.display = ''; }}
  function failed(t) {{ gmsg.style.color = '#9e4a37'; gmsg.textContent = t; gbtn.disabled = false; }}
  fetch('/auth/admin/status').then(function (r) {{ return r.json(); }})
    .then(function (j) {{ if (j.ok) show(); }}).catch(function () {{}});
  document.getElementById('gform').addEventListener('submit', function (e) {{
    e.preventDefault();
    var code = document.getElementById('acode').value.trim();
    if (!code) {{ failed('请输入管理码'); return; }}
    gbtn.disabled = true; gmsg.style.color = '#767c70'; gmsg.textContent = '验证中…';
    fetch('/auth/admin/login', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{code: code}})
    }}).then(function (r) {{ return r.json(); }}).then(function (j) {{
      if (j.ok) {{ show(); return; }}
      failed(j.error || '管理码不正确');
    }}).catch(function () {{ failed('网络错误'); }});
  }});
}})();
</script>
{form_js}
{common_js}
</body></html>
""".format(
        head=_page_head("数据录入 · " + cfg["site_title"]),
        nav=_nav("input.html"),
        today=today,
        cards="".join(input_cards),
        recent="".join(recent) or "<tr><td colspan='4'>暂无记录</td></tr>",
        form_js=FORM_JS,
        common_js=COMMON_JS,
    )


# ─── render_unlock ────────────────────────────────────────────────────────────

def render_journal(cfg):
    """交易记录与复盘页：管理码验证后从 /api/journal 拉数据。"""
    tpl = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>交易复盘 · __TITLE__</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="style.css"></head>
<body><div class="wrap">
<header>
  <h1>交易复盘</h1>
  <div class="meta">判断记录 · 模拟盘 · 归因</div>
  <nav><a href="index.html">← 返回看板</a><a href="input.html">录入数据</a></nav>
</header>

<div class="card" id="gate">
  <div class="note" style="margin:0 0 12px">复盘数据需要管理码验证</div>
  <form class="formbar" id="gform">
    <label>管理码 <input type="password" id="acode" autocomplete="off" autofocus></label>
    <button id="gbtn" type="submit">验证</button>
  </form>
  <div id="gmsg"></div>
</div>

<div id="area" style="display:none">
  <div class="kpi-grid" id="stats"></div>

  <h2>今日判断</h2>
  <div class="card">
    <form id="jform">
      <div class="formbar">
        <label>日期 <input type="date" id="j-date"></label>
        <label>合约 <input type="text" id="j-note" value="M2701" style="width:90px"></label>
        <label>方向
          <select id="j-direction">
            <option>观望</option><option>多</option><option>空</option>
          </select>
        </label>
      </div>
      <div style="margin-top:10px">
        <div class="note">理由（看到什么、为什么）</div>
        <textarea id="j-reason" rows="3" style="width:100%"></textarea>
      </div>
      <div style="margin-top:10px">
        <div class="note">反证条件（出现什么说明判断错了）</div>
        <textarea id="j-risk" rows="2" style="width:100%"></textarea>
      </div>
      <div class="formbar"><button type="submit">保存判断</button></div>
      <div id="jmsg"></div>
    </form>
  </div>

  <h2>记录开仓</h2>
  <div class="card">
    <form id="tform">
      <div class="formbar">
        <label>合约 <input type="text" id="t-contract" value="M2701" style="width:90px"></label>
        <label>方向 <select id="t-side"><option>多</option><option>空</option></select></label>
        <label>手数 <input type="number" id="t-lots" value="1" min="1" style="width:70px"></label>
        <label>开仓价 <input type="number" id="t-price" step="0.1" style="width:100px"></label>
        <label>日期 <input type="date" id="t-date"></label>
      </div>
      <div style="margin-top:10px">
        <div class="note">开仓理由</div>
        <textarea id="t-reason" rows="2" style="width:100%"></textarea>
      </div>
      <div class="formbar"><button type="submit">记录开仓</button></div>
      <div id="tmsg"></div>
    </form>
  </div>

  <h2>持仓中</h2>
  <div class="card"><div class="tablewrap"><table>
    <thead><tr><th>合约</th><th>方向</th><th class="num">手数</th><th class="num">开仓价</th>
    <th>开仓日</th><th>理由</th><th></th></tr></thead>
    <tbody id="open-body"></tbody></table></div></div>

  <h2>已平仓</h2>
  <div class="card"><div class="tablewrap"><table>
    <thead><tr><th>合约</th><th>方向</th><th class="num">手数</th><th class="num">开仓</th>
    <th class="num">平仓</th><th class="num">盈亏</th><th>持仓天数</th><th>复盘</th></tr></thead>
    <tbody id="closed-body"></tbody></table></div></div>

  <h2>判断记录</h2>
  <div class="card"><div class="tablewrap"><table>
    <thead><tr><th>日期</th><th>方向</th><th>理由</th><th>反证条件</th><th class="num">事后验证</th></tr></thead>
    <tbody id="list-body"></tbody></table></div></div>
</div>
</div>
<script>
(function () {
  var COOKIE_MSG = '需要管理码';
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }
  function say(el, t, ok) {
    var node = $(el);
    node.style.color = ok ? '#0a7d32' : '#c0392b';
    node.textContent = t;
  }

  function renderStats(d) {
    var j = d.judge, t = d.trade;
    var cards = [
      ['判断样本', j.n + ' 次'],
      ['判断准确率', j.rate == null ? '—' : j.rate + '%'],
      ['已平仓', t.closed_n + ' 笔'],
      ['累计盈亏', (t.total_pnl >= 0 ? '+' : '') + t.total_pnl.toFixed(0) + ' 元'],
      ['胜率', t.win_rate == null ? '—' : t.win_rate + '%'],
      ['平均持仓', t.avg_holding == null ? '—' : t.avg_holding + ' 天']
    ];
    $('stats').innerHTML = cards.map(function (c) {
      return "<div class='kpi-card'><div class='kpi-label'>" + c[0] +
             "</div><div class='kpi-val'>" + esc(c[1]) + "</div></div>";
    }).join('');
  }

  function renderOpen(trades) {
    var opens = trades.filter(function (x) { return x.status === 'open'; });
    if (!opens.length) { $('open-body').innerHTML = "<tr><td colspan='7'>无持仓</td></tr>"; return; }
    $('open-body').innerHTML = opens.map(function (x) {
      return "<tr><td>" + esc(x.contract) + "</td><td>" + esc(x.side) +
        "</td><td class='num'>" + x.lots + "</td><td class='num'>" + x.open_price +
        "</td><td>" + esc(x.open_date) + "</td><td>" + esc((x.reason || '').slice(0, 30)) +
        "</td><td><button type='button' data-close='" + x.id + "'>平仓</button></td></tr>";
    }).join('');
  }

  function renderClosed(trades) {
    var closed = trades.filter(function (x) { return x.status === 'closed'; });
    if (!closed.length) { $('closed-body').innerHTML = "<tr><td colspan='8'>暂无</td></tr>"; return; }
    $('closed-body').innerHTML = closed.map(function (x) {
      var cls = x.pnl >= 0 ? 'up' : 'dn';
      return "<tr><td>" + esc(x.contract) + "</td><td>" + esc(x.side) +
        "</td><td class='num'>" + x.lots + "</td><td class='num'>" + x.open_price +
        "</td><td class='num'>" + x.close_price + "</td><td class='num " + cls + "'>" +
        (x.pnl >= 0 ? '+' : '') + Number(x.pnl).toFixed(0) +
        "</td><td>" + esc(x.open_date) + " ~ " + esc(x.close_date) +
        "</td><td>" + esc((x.review || '').slice(0, 24)) + "</td></tr>";
    }).join('');
  }

  function renderJournal(entries, judge) {
    var map = {};
    (judge.checked || []).forEach(function (c) {
      map[c.date] = (c.ok ? '✅ ' : '❌ ') + '3日后 ' + (c.change > 0 ? '+' : '') + c.change;
    });
    if (!entries.length) { $('list-body').innerHTML = "<tr><td colspan='5'>暂无判断记录</td></tr>"; return; }
    $('list-body').innerHTML = entries.map(function (x) {
      return "<tr><td>" + esc(x.date) + "</td><td>" + esc(x.direction) +
        "</td><td>" + esc((x.reason || '').slice(0, 40)) +
        "</td><td>" + esc((x.risk || '').slice(0, 30)) +
        "</td><td class='num'>" + esc(map[x.date] || '—') + "</td></tr>";
    }).join('');
  }

  function load() {
    return fetch('/api/journal').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return false;
      renderStats(d); renderOpen(d.trades); renderClosed(d.trades);
      renderJournal(d.journal, d.judge);
      return true;
    });
  }

  // 管理码验证
  fetch('/auth/admin/status').then(function (r) { return r.json(); }).then(function (j) {
    if (j.ok) { $('gate').style.display = 'none'; $('area').style.display = ''; load(); }
  }).catch(function () {});

  $('gform').addEventListener('submit', function (e) {
    e.preventDefault();
    var code = $('acode').value.trim();
    if (!code) { say('gmsg', '请输入管理码', false); return; }
    post('/auth/admin/login', {code: code}).then(function (j) {
      if (!j.ok) { say('gmsg', j.error || '管理码不正确', false); return; }
      $('gate').style.display = 'none'; $('area').style.display = '';
      load();
    }).catch(function () { say('gmsg', '网络错误', false); });
  });

  // 保存判断
  $('jform').addEventListener('submit', function (e) {
    e.preventDefault();
    var body = {
      date: $('j-date').value || new Date().toISOString().slice(0, 10),
      direction: $('j-direction').value,
      reason: $('j-reason').value,
      risk: $('j-risk').value,
      note: $('j-note').value
    };
    if (!body.reason.trim()) { say('jmsg', '请写理由', false); return; }
    post('/api/journal', body).then(function (j) {
      if (!j.ok) { say('jmsg', j.error || '保存失败', false); return; }
      say('jmsg', '已保存', true);
      $('j-reason').value = ''; $('j-risk').value = '';
      load();
    }).catch(function () { say('jmsg', '网络错误', false); });
  });

  // 记录开仓
  $('tform').addEventListener('submit', function (e) {
    e.preventDefault();
    var body = {
      contract: $('t-contract').value.trim(),
      side: $('t-side').value,
      lots: Number($('t-lots').value),
      open_price: Number($('t-price').value),
      open_date: $('t-date').value || new Date().toISOString().slice(0, 10),
      reason: $('t-reason').value
    };
    if (!(body.open_price > 0)) { say('tmsg', '请填开仓价', false); return; }
    post('/api/trade', body).then(function (j) {
      if (!j.ok) { say('tmsg', j.error || '保存失败', false); return; }
      say('tmsg', '已记录开仓', true);
      $('t-price').value = ''; $('t-reason').value = '';
      load();
    }).catch(function () { say('tmsg', '网络错误', false); });
  });

  // 平仓（点击持仓行）
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-close]');
    if (!btn) return;
    var id = btn.getAttribute('data-close');
    var price = prompt('平仓价：');
    if (!price) return;
    var review = prompt('复盘（为什么平仓，可留空）：') || '';
    post('/api/trade/close', {
      id: Number(id), close_price: Number(price), review: review
    }).then(function (j) {
      if (!j.ok) { alert(j.error || '平仓失败'); return; }
      alert('已平仓，盈亏 ' + j.pnl.toFixed(0) + ' 元');
      load();
    });
  });

  var today = new Date().toISOString().slice(0, 10);
  $('j-date').value = today;
  $('t-date').value = today;
})();
</script>
</body></html>"""
    return (tpl.replace("__TITLE__", html.escape(cfg["site_title"])))


def render_unlock(cfg):
    title = html.escape(cfg["site_title"])
    return """{head}
<body class="gate-body"><div class="gate-card">
  <div class="gate-brand">品观 · 豆粕数据平台</div>
  <h1>{title}</h1>
  <div class="gate-sub">输入访问码解锁查看</div>
  <form class="gate-form" id="f">
    <input id="code" placeholder="访问码（XXXX-XXXX-XXXX-XXXX）" autocomplete="off" autofocus>
    <button id="btn" type="submit">解锁</button>
  </form>
  <div class="gate-msg" id="msg"></div>
  <div class="gate-foot"><span>HTTP 401 · 需要访问码</span><span>{title}</span></div>
</div>
<script>
(function () {{
  var f = document.getElementById('f');
  var btn = document.getElementById('btn');
  var msg = document.getElementById('msg');
  f.addEventListener('submit', function (e) {{
    e.preventDefault();
    var code = document.getElementById('code').value.trim();
    if (!code) {{ msg.style.color = '#9e4a37'; msg.textContent = '请输入访问码'; return; }}
    btn.disabled = true; msg.style.color = '#767c70'; msg.textContent = '验证中…';
    fetch('/auth/login', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{code: code}})
    }}).then(function (r) {{ return r.json(); }}).then(function (j) {{
      if (j.ok) {{ location.reload(); return; }}
      msg.style.color = '#9e4a37';
      msg.textContent = j.error || '访问码不正确';
      btn.disabled = false;
    }}).catch(function () {{
      msg.style.color = '#9e4a37'; msg.textContent = '网络错误'; btn.disabled = false;
    }});
  }});
}})();
</script>
</body></html>
""".format(
        head=_page_head("需要访问码 · " + cfg["site_title"]),
        title=title,
    )


# ─── render_admin ─────────────────────────────────────────────────────────────

def render_admin(cfg):
    title = html.escape(cfg["site_title"])
    return """{head}
<body class="gate-body"><div class="gate-card" style="max-width:520px">
  <div class="gate-brand">品观 · 豆粕数据平台</div>
  <h1>管理</h1>
  <div class="gate-sub">输入管理码后，可以生成新访问码、开关门禁</div>
  <form class="gate-form" id="f">
    <input id="acode" placeholder="管理码" autocomplete="off" autofocus>
    <button id="btn" type="submit">验证</button>
  </form>
  <div class="gate-msg" id="msg"></div>

  <div class="gate-row" style="margin:16px 0 0">
    <a href="index.html"><button type="button">← 返回看板</button></a>
  </div>

  <div id="panel" style="display:none">
    <div class="gate-row"><span>门禁状态：<b id="state">—</b></span></div>
    <div class="gate-row"><span>访问码指纹：<b id="hint">—</b></span></div>
    <div class="gate-row">
      <button type="button" id="toggle">切换门禁</button>
      <button type="button" id="gen">生成新访问码</button>
      <a href="input.html"><button type="button">数据录入</button></a>
    </div>
    <div id="newcode"></div>
  </div>

  <div class="gate-foot"><span>管理会话 24 小时</span><span>{title}</span></div>
</div>
<script>
(function () {{
  var msg = document.getElementById('msg');
  var btn = document.getElementById('btn');
  var panel = document.getElementById('panel');
  var stateEl = document.getElementById('state');
  var hintEl = document.getElementById('hint');
  var newEl = document.getElementById('newcode');
  var gateOn = false;

  function say(t, ok) {{ msg.style.color = ok ? '#3f6e51' : '#9e4a37'; msg.textContent = t; }}
  function post(url, body) {{
    return fetch(url, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body || {{}})
    }}).then(function (r) {{ return r.json(); }});
  }}
  function loadStatus() {{
    return fetch('/auth/admin/status').then(function (r) {{ return r.json(); }})
      .then(function (j) {{
        if (!j.ok) return false;
        gateOn = !!j.gate_enabled;
        stateEl.textContent = gateOn ? '开启' : '关闭';
        hintEl.textContent = j.access_set ? (j.access_hint + '****') : '未设置';
        panel.style.display = '';
        return true;
      }});
  }}

  document.getElementById('f').addEventListener('submit', function (e) {{
    e.preventDefault();
    var code = document.getElementById('acode').value.trim();
    if (!code) {{ say('请输入管理码', false); return; }}
    btn.disabled = true;
    post('/auth/admin/login', {{code: code}}).then(function (j) {{
      btn.disabled = false;
      if (!j.ok) {{ say(j.error || '管理码不正确', false); return; }}
      say('已验证', true);
      loadStatus().then(function (ok) {{ if (!ok) say('状态读取失败', false); }});
    }}).catch(function () {{ btn.disabled = false; say('网络错误', false); }});
  }});

  document.getElementById('toggle').addEventListener('click', function () {{
    post('/auth/admin/toggle', {{enabled: !gateOn}}).then(function (j) {{
      if (!j.ok) {{ say(j.error || '操作失败', false); return; }}
      say('门禁已' + (j.gate_enabled ? '开启' : '关闭'), true);
      loadStatus();
    }});
  }});

  document.getElementById('gen').addEventListener('click', function () {{
    if (!confirm('生成新访问码会让所有已解锁的浏览器失效，继续？')) return;
    post('/auth/admin/gen', {{}}).then(function (j) {{
      if (!j.ok) {{ say(j.error || '操作失败', false); return; }}
      say('新访问码已生成，旧会话全部失效', true);
      var code = j.code;
      newEl.innerHTML = '<div class="code-box" id="codebox">' + code + '</div>'
        + '<button class="copy-btn" id="copybtn">复制</button>'
        + '<div class="hint" style="margin-top:8px">只显示这一次，请立即保存</div>';
      document.getElementById('copybtn').addEventListener('click', function () {{
        navigator.clipboard.writeText(code).then(function () {{
          document.getElementById('copybtn').textContent = '已复制 ✓';
        }});
      }});
      loadStatus();
    }});
  }});

  loadStatus();
}})();
</script>
</body></html>
""".format(
        head=_page_head("管理 · " + cfg["site_title"]),
        title=title,
    )


# ─── render_weekly ────────────────────────────────────────────────────────────

def render_weekly(conn, cfg, data):
    series_list = [
        ("A4.spot",          "豆粕现货价（元/吨）"),
        ("A5.basis.main",    "基差（元/吨）"),
        ("A5.basis.100ppi",  "生意社近月基差（元/吨）"),
        ("B1.inventory",     "油厂豆粕库存（万吨）"),
        ("B2.soy_inventory", "油厂大豆库存（万吨）"),
        ("B3.crush_rate",    "油厂开机率（%）"),
        ("C1.hog_profit",    "生猪自繁自养利润（元/头）"),
        ("C2.hog_future.main", "生猪期货主力（元/吨）"),
        ("B6.soy_cost",      "进口大豆到港成本（元/吨）"),
        ("B6.crush_margin",  "盘面压榨利润（元/吨）"),
    ]

    blocks = []
    for sid, title in series_list:
        rows = db.history(conn, sid, limit=16)
        if not rows:
            body = "<tr><td colspan='3'>暂无数据</td></tr>"
            spark = ""
        else:
            vals = [r["value"] for r in rows]
            spark = '<div class="sparkline-wrap">%s</div>' % sparkline_svg(vals)
            # 表格：日期 / 数值 / 环比
            body_rows = []
            for i, r in enumerate(reversed(rows)):
                prev_r = rows[-(i + 2)] if i + 1 < len(rows) else None
                if prev_r is not None:
                    chg = r["value"] - prev_r["value"]
                    chg_cls = cls_of(chg)
                    chg_str = "%+.2f %s" % (chg, arrow(chg))
                else:
                    chg_cls = "flat"
                    chg_str = "—"
                body_rows.append(
                    "<tr><td>%s</td><td class='num'>%s</td>"
                    "<td class='num chg %s'>%s</td></tr>"
                    % (r["date"], fmt(r["value"], 2), chg_cls, chg_str)
                )
            body = "".join(body_rows)

        blocks.append("""
<section class="series-block">
<h2>%s%s</h2>
<div class="card"><table>
<thead><tr><th>日期</th><th class="num">数值</th><th class="num">环比</th></tr></thead>
<tbody>%s</tbody>
</table></div>
</section>""" % (html.escape(title), spark, body))

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return """{head}
<body><div class="wrap">
<header>
  <h1>周度数据</h1>
  <div class="meta">最近 16 条记录 · 生成于 {now}</div>
  {nav}
</header>
<main class="series-grid">{blocks}</main>
</div>
{js}
</body></html>
""".format(
        head=_page_head("周度数据 · " + cfg["site_title"]),
        nav=_nav("weekly.html"),
        now=now_str,
        blocks="".join(blocks),
        js=COMMON_JS,
    )


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()
    conn = db.connect(DB_PATH)

    data = build(conn, cfg)
    try:
        data["ai"] = judge.current(conn, data)
    except Exception as exc:  # noqa: BLE001
        print("[render] AI 判断跳过：%s" % str(exc)[:100])
        data["ai"] = ""
    os.makedirs(SITE, exist_ok=True)
    os.makedirs(os.path.join(SITE, "data"), exist_ok=True)

    # 样式表：源码在 app/style.css，每次渲染复制到站点目录
    css_src = os.path.join(APP, "style.css")
    if os.path.exists(css_src):
        with open(css_src, "rb") as fh:
            css_bytes = fh.read()
        with open(os.path.join(SITE, CSS_HREF), "wb") as fh:
            fh.write(css_bytes)

    # 同步静态资产（favicon、logo 等）到 site/
    assets_dir = os.path.join(APP, "assets")
    if os.path.exists(assets_dir):
        for fname in os.listdir(assets_dir):
            src_f = os.path.join(assets_dir, fname)
            if os.path.isfile(src_f):
                dst_f = os.path.join(SITE, fname)
                with open(src_f, "rb") as sf, open(dst_f, "wb") as df:
                    df.write(sf.read())

    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(render_index(cfg, data))
    with open(os.path.join(SITE, "input.html"), "w", encoding="utf-8") as fh:
        fh.write(render_input(conn, cfg, data))
    with open(os.path.join(SITE, "unlock.html"), "w", encoding="utf-8") as fh:
        fh.write(render_unlock(cfg))
    with open(os.path.join(SITE, "admin.html"), "w", encoding="utf-8") as fh:
        fh.write(render_admin(cfg))
    with open(os.path.join(SITE, "journal.html"), "w", encoding="utf-8") as fh:
        fh.write(render_journal(cfg))
    with open(os.path.join(SITE, "weekly.html"), "w", encoding="utf-8") as fh:
        fh.write(render_weekly(conn, cfg, data))
    with open(os.path.join(SITE, "data", "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)

    print("[render] index.html / input.html / weekly.html / data/summary.json 已生成")
    print("[render] %s" % data["lead"])


if __name__ == "__main__":
    main()
