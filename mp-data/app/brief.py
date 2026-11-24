# -*- coding: utf-8 -*-
"""每日简报：豆粕简报 + 全局简报，合并成一封邮件。

由 mp-data-brief.timer 在每天 07:30 调用。
"""
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db      # noqa: E402
import llm     # noqa: E402
import news    # noqa: E402
import notify  # noqa: E402
import render  # noqa: E402
import siteurl  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
SITE = os.path.join(ROOT, "site")
BRIEF_DIR = os.path.join(SITE, "brief")
MACRO_DIR = os.path.join(SITE, "macro")
KEEP_DAYS = 90
WEEKDAYS = "一二三四五六日"
SECTOR_ORDER = ["宏观", "黑色", "有色", "能化", "农产品", "贵金属", "股指", "汇率"]


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fmt(v, nd=1):
    return render.fmt(v, nd)


def _clean(text, limit=200):
    return re.sub(r"\s+", " ", text or "")[:limit]


# ---------------------------------------------------------------- AI 导语

def build_summary(data, items):
    """豆粕简报的模型输入。"""
    dims = data["dims"]
    lines = ["日期：%s" % datetime.date.today().isoformat()]
    lines.append("四维判断：成本端 %s / 供给端 %s / 需求端 %s / 结构 %s" % tuple(
        render.DIM_STATE[k].get(render.dim_sign(dims[k]), "中性")
        for k in ("cost", "supply", "demand", "struct")))

    q = next((x for x in data["quotes"] if x.get("is_main")), None)
    parts = []
    if q:
        parts.append("主力 %s 结算 %s（%s）" % (q["code"], fmt(q["settle"], 1), fmt(q["delta"], 1)))
    if data["us"]["last"] is not None:
        parts.append("美豆 %s 美分/蒲" % fmt(data["us"]["last"], 0))
    wasde = data.get("wasde") or {}
    if wasde.get("value") is not None:
        parts.append("美豆期末库存 %s 亿蒲" % fmt(wasde["value"], 2))
    if data["basis"]["value"] is not None:
        s = "基差 %s" % fmt(data["basis"]["value"], 0)
        if data["basis"].get("delta") is not None:
            s += "（变化 %+.0f）" % data["basis"]["delta"]
        parts.append(s)
    if data["crush_margin"]["value"] is not None:
        parts.append("盘面压榨利润 %s 元/吨" % fmt(data["crush_margin"]["value"], 0))
    inv = data["inventory"]
    if inv["value"] is not None:
        s = "油厂豆粕库存 %s 万吨" % fmt(inv["value"], 1)
        if inv.get("delta") is not None:
            s += "（变化 %+.2f）" % inv["delta"]
        if inv.get("pct") is not None:
            s += "，历史 %d%% 分位" % inv["pct"]
        parts.append(s)
    if data["hog_profit"]["value"] is not None:
        parts.append("生猪自繁自养 %s 元/头" % fmt(data["hog_profit"]["value"], 0))
    if data.get("spreads"):
        sp = []
        for x in data["spreads"]:
            t = "%s %s" % (x["name"], fmt(x["value"], 0))
            if x.get("pct") is not None:
                t += "（%d%% 分位）" % x["pct"]
            sp.append(t)
        parts.append("价差 " + "、".join(sp))
    lines.append("关键数据：" + "；".join(parts))

    lines.append("过去 24 小时资讯：")
    for it in items:
        lines.append("- [%s] %s" % ((it.get("pub_time") or "")[5:16],
                                    _clean(it.get("content"), 120)))
    return "\n".join(lines)


def build_macro_input(fx, by_sector):
    """全局简报的模型输入。"""
    lines = ["日期：%s" % datetime.date.today().isoformat()]
    if fx and fx.get("value") is not None:
        s = "美元兑人民币 %s" % fmt(fx["value"], 4)
        if fx.get("delta") is not None:
            s += "（较前值 %+.4f）" % fx["delta"]
        lines.append(s)
    empty = [s for s in SECTOR_ORDER if not by_sector.get(s)]
    if empty:
        lines.append("今日无要闻的板块：" + "、".join(empty))
    lines.append("过去 24 小时各板块要闻：")
    for sector in SECTOR_ORDER:
        items = by_sector.get(sector) or []
        if not items:
            continue
        lines.append("【%s】共 %d 条" % (sector, len(items)))
        for it in items[:4]:
            lines.append("- [%s] %s" % ((it.get("pub_time") or "")[5:16],
                                        _clean(it.get("content"), 130)))
    return "\n".join(lines)


def ai_summary(data, items):
    if not items or not llm.configured():
        return ""
    try:
        return llm.chat([
            {"role": "system", "content":
             "你是一名豆粕交易员，给同事写一条盘前判断。直接说，不客套，总长 220 字以内：\n"
             "1. 先给结论：短期（1-2 周）偏多 / 偏空 / 震荡\n"
             "2. 给两条支撑依据，必须来自用户提供的数据\n"
             "3. 给一个反证条件：出现什么情况这个判断就错了\n"
             "只依据给的数据，不编造数字，不写免责声明，"
             "不要使用 Markdown 标记（**、# 等），直接写纯文本。"},
            {"role": "user", "content": build_summary(data, items)},
        ], max_tokens=500, temperature=0.3)
    except Exception as e:  # noqa: BLE001
        print("[brief] 豆粕导语失败：%s" % e)
        return ""


def ai_macro(fx, by_sector):
    total = sum(len(v) for v in by_sector.values())
    if not total or not llm.configured():
        return ""
    try:
        return llm.chat([
            {"role": "system", "content":
             "你是一名商品市场研究员，给交易者写每日全品类速览。"
             "从用户给的要闻里整理成四部分，总长 450 到 650 字，分段落书写：\n"
             "【全景】用一两句说今天市场整体在交易什么主线\n"
             "【各板块】每个板块单独一行，以“·板块名：”开头写一句；"
             "无明显变化的直接写“无重要变化”\n"
             "【重大事件】列出 1 到 3 条影响面最大的事件，说明它影响哪些品种、大致持续多久\n"
             "【机会线索】写 1 到 2 条值得跟踪的方向（只指出观察角度，不必给买卖建议）\n"
             "要求：只依据给的要闻，数字必须准确，不编造，不写免责声明，"
             "不要使用 Markdown 标记（**、#、- 等）。"},
            {"role": "user", "content": build_macro_input(fx, by_sector)},
        ], max_tokens=900, temperature=0.4)
    except Exception as e:  # noqa: BLE001
        print("[brief] 全局导语失败：%s" % e)
        return ""


# ---------------------------------------------------------------- 页面

def _news_list(items, limit=200):
    if not items:
        return "<div class='hint'>无</div>"
    out = []
    for it in items:
        link = ""
        if it.get("url"):
            link = " <a href='%s' target='_blank' rel='noopener'>原文</a>" % html.escape(it["url"])
        out.append("<li><span class='ntime'>%s</span>%s%s</li>"
                   % (html.escape((it.get("pub_time") or "")[5:16]),
                      html.escape(_clean(it.get("content"), limit)), link))
    return "<ul class='newslist'>%s</ul>" % "".join(out)


def _page_head(title, nav_items, prefix=""):
    """prefix：简报页在 brief/ 子目录下，需要 ../ 才能引到根目录的 style.css 与 favicon.svg。"""
    nav = "".join("<a href='%s'>%s</a>" % (u, t) for t, u in nav_items)
    return ("<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            "<title>%s</title>\n"
            "<link rel=\"icon\" href=\"%sfavicon.svg\" type=\"image/svg+xml\">\n"
            "<link rel=\"stylesheet\" href=\"%sstyle.css\"></head>\n"
            "<body><div class=\"wrap\">\n"
            "<header><h1>%s</h1>" % (title, prefix, prefix, title))


def render_brief(date_text, data, items_high, items_norm, summary, ai_ok):
    weekday = WEEKDAYS[datetime.date.fromisoformat(date_text).weekday()]
    now = datetime.datetime.now().strftime("%H:%M")

    if summary:
        lead = ("<div class='lead-card brief-lead'><div><div class='eyebrow'>DAILY BRIEF · 豆粕研究</div>"
                "<div>%s</div><div class='note'>AI 生成，仅供参考</div></div></div>" % html.escape(summary).replace("**", "").replace("\n", "<br>"))
    else:
        lead = ("<div class='lead-card brief-lead'><div><div class='eyebrow'>DAILY BRIEF · 豆粕研究</div>"
                "<div>%s</div><div class='note'>AI 摘要未生成（%s）</div></div></div>"
                % (html.escape(data["lead"]), "未配置模型" if not ai_ok else "接口不可用"))

    q = next((x for x in data["quotes"] if x.get("is_main")), None)
    nums = []
    if q:
        nums.append("主力 %s %s（%s）" % (q["code"], fmt(q["settle"], 1), fmt(q["delta"], 1)))
    if data["us"]["last"] is not None:
        nums.append("美豆 %s 美分" % fmt(data["us"]["last"], 0))
    if data["basis"]["value"] is not None:
        nums.append("基差 %s" % fmt(data["basis"]["value"], 0))
    if data["crush_margin"]["value"] is not None:
        nums.append("压榨 %s 元" % fmt(data["crush_margin"]["value"], 0))

    dims = data["dims"]
    dim_html = "".join(
        "<div class='dim %s'><div class='dim-name'>%s</div><div class='dim-state'>%s</div></div>"
        % (render.DIM_TAG[render.dim_sign(dims[k])], render.DIM_META[k],
           render.DIM_STATE[k].get(render.dim_sign(dims[k]), "中性"))
        for k in ("cost", "supply", "demand", "struct"))

    cal = "".join(
        "<tr><td class='nstar'>%s</td><td>%s<div class='hint'>%s</div></td></tr>"
        % (html.escape(c.get("keywords") or ""), html.escape(c.get("title") or ""),
           html.escape((c.get("pub_time") or "")[5:16]))
        for c in data.get("calendar", []))

    return """{head}<div class="meta">{date} 周{weekday} · {now} 生成</div>
{nav}</header>

{lead}

<h2>昨日数据</h2>
<div class="card">
  <div>{nums}</div>
  <div class="dims" style="margin-top:12px">{dim_html}</div>
</div>

<h2>重要快讯（{n_high} 条）</h2>
<div class="card">{high_html}</div>

<h2>相关快讯（{n_norm} 条）</h2>
<div class="card">{norm_html}</div>

<h2>今日重要数据</h2>
<div class="card">{cal_html}</div>

<footer>覆盖过去 24 小时 · 数据来源：金十数据 / 新浪财经 / 生意社 · 本页仅供参考</footer>
</div></body></html>
""".format(head=_page_head("豆粕简报 · %s" % date_text, [], "../"),
           nav=render._nav("brief.html", "../"),
           date=date_text, weekday=weekday, now=now,
           lead=lead, nums=html.escape(" · ".join(nums) or "暂无行情"),
           dim_html=dim_html, n_high=len(items_high), n_norm=len(items_norm),
           high_html=_news_list(items_high), norm_html=_news_list(items_norm),
           cal_html=("<table><thead><tr><th style='width:60px'>星级</th><th>事件</th>"
                     "</tr></thead><tbody>%s</tbody></table>" % cal) if cal
           else "<div class='hint'>本周暂无高星级数据</div>")


def render_macro(date_text, by_sector, summary, fx):
    weekday = WEEKDAYS[datetime.date.fromisoformat(date_text).weekday()]
    now = datetime.datetime.now().strftime("%H:%M")

    if summary:
        lead = ("<div class='lead-card brief-lead'><div><div class='eyebrow'>MARKET BRIEF · 全球市场</div>"
                "<div>%s</div><div class='note'>AI 生成，仅供参考</div></div></div>" % html.escape(summary).replace("**", "").replace("\n", "<br>"))
    else:
        lead = ("<div class='lead-card brief-lead'><div><div class='eyebrow'>MARKET BRIEF · 全球市场</div>"
                "<div>全球市场要闻汇总</div><div class='note'>AI 摘要未生成</div></div></div>")

    fx_text = ""
    if fx and fx.get("value") is not None:
        fx_text = "美元兑人民币 %s" % fmt(fx["value"], 4)
        if fx.get("delta") is not None:
            fx_text += "（%+.4f）" % fx["delta"]

    blocks = []
    for sector in SECTOR_ORDER:
        items = by_sector.get(sector) or []
        if not items:
            continue
        blocks.append("<h2>%s（%d 条）</h2><div class='card'>%s</div>"
                      % (sector, len(items), _news_list(items, 180)))
    if not blocks:
        blocks.append("<div class='card'><div class='hint'>过去 24 小时无可归类要闻</div></div>")

    return """{head}<div class="meta">{date} 周{weekday} · {now} 生成</div>
{nav}</header>

{lead}

<h2>汇率行情</h2>
<div class="card">{fx_text}</div>

{blocks}

<footer>覆盖过去 24 小时 · 数据来源：金十数据 / 新浪财经 · 本页仅供参考</footer>
</div></body></html>
""".format(head=_page_head("全局简报 · %s" % date_text, [], "../"),
           nav=render._nav("macro.html", "../"),
           date=date_text, weekday=weekday, now=now, lead=lead,
           fx_text=html.escape(fx_text or "暂无数据"), blocks="".join(blocks))


def update_index(directory, date_text, payload):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "index.json")
    try:
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
    except Exception:  # noqa: BLE001
        entries = []
    entries = [e for e in entries if e.get("date") != date_text]
    entry = {"date": date_text}
    entry.update(payload)
    entries.append(entry)
    entries.sort(key=lambda e: e["date"], reverse=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=1)
    return entries


def render_index_page(title, entries, kind):
    rows = []
    for e in entries:
        d = e["date"]
        try:
            wd = WEEKDAYS[datetime.date.fromisoformat(d).weekday()]
        except ValueError:
            wd = ""
        rows.append(
            "<tr><td><a href='%s/%s.html'>%s</a></td><td>周%s</td>"
            "<td class='num'>%s</td><td class='hint'>%s</td></tr>"
            % (kind, d, d, wd, e.get("total", 0), html.escape(e.get("summary") or "")))
    body = "".join(rows) or "<tr><td colspan='4'>暂无简报</td></tr>"
    return """{head}<div class="meta">共 {n} 期，按日期倒序</div>
{nav}</header>
<div class="card"><div class="tablewrap"><table>
<thead><tr><th>日期</th><th>星期</th><th class="num">条数</th><th>导语</th></tr></thead>
<tbody>{body}</tbody></table></div></div>
</div></body></html>
""".format(head=_page_head(title, []), n=len(entries),
           nav=render._nav(kind + ".html"),
           other="macro" if kind == "brief" else "brief",
           other_name="全局简报" if kind == "brief" else "豆粕简报",
           body=body)


def prune_entries(directory, entries):
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    kept = []
    for e in entries:
        if e["date"] >= cutoff:
            kept.append(e)
            continue
        path = os.path.join(directory, e["date"] + ".html")
        if os.path.exists(path):
            os.remove(path)
    return kept


def main():
    cfg = load_config()
    conn = db.connect(DB_PATH)
    today = datetime.date.today().isoformat()

    # 1. 抓一次资讯（补齐夜间）
    try:
        news.collect(conn, cfg)
        news.prune(conn)
        conn.commit()
    except Exception as e:  # noqa: BLE001
        print("[brief] 资讯采集失败（继续生成简报）：%s" % e)

    # 2. 昨日行情与四维
    data = render.build(conn, cfg)
    fx_row = db.latest(conn, "A8.USDCNY")
    fx = {"value": fx_row["value"] if fx_row else None, "delta": None}
    if fx_row:
        prev = db.history(conn, "A8.USDCNY", limit=2)
        if len(prev) > 1:
            fx["delta"] = prev[-1]["value"] - prev[0]["value"]

    # 3. 过去 24 小时资讯
    since = (datetime.datetime.now() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM news WHERE kind IN ('flash','news') AND pub_time >= ? "
        "ORDER BY pub_time DESC", (since,))]
    soy = [r for r in rows if (r.get("keywords") or "").strip()]
    soy_high = [r for r in soy if r.get("level") == "high"]
    soy_norm = [r for r in soy if r.get("level") != "high"]

    by_sector = {}
    for r in rows:
        sector = (r.get("sector") or "").strip()
        if sector:
            by_sector.setdefault(sector, []).append(r)

    # 4. AI 导语
    soy_summary = ai_summary(data, (soy_high + soy_norm)[:20])
    macro_summary = ai_macro(fx, by_sector)
    if soy_summary:
        print("[brief] 豆粕导语：%s" % soy_summary[:60])
    if macro_summary:
        print("[brief] 全局导语：%s" % macro_summary[:60])

    # 5. 豆粕简报
    os.makedirs(BRIEF_DIR, exist_ok=True)
    with open(os.path.join(BRIEF_DIR, today + ".html"), "w", encoding="utf-8") as fh:
        fh.write(render_brief(today, data, soy_high, soy_norm, soy_summary, llm.configured()))
    entries = update_index(BRIEF_DIR, today,
                           {"total": len(soy), "summary": (soy_summary or data["lead"])[:100]})
    entries = prune_entries(BRIEF_DIR, entries)
    with open(os.path.join(BRIEF_DIR, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(SITE, "brief.html"), "w", encoding="utf-8") as fh:
        fh.write(render_index_page("豆粕简报", entries, "brief"))

    # 6. 全局简报
    os.makedirs(MACRO_DIR, exist_ok=True)
    total_macro = sum(len(v) for v in by_sector.values())
    with open(os.path.join(MACRO_DIR, today + ".html"), "w", encoding="utf-8") as fh:
        fh.write(render_macro(today, by_sector, macro_summary, fx))
    m_entries = update_index(MACRO_DIR, today,
                             {"total": total_macro, "summary": (macro_summary or "全市场要闻")[:100]})
    m_entries = prune_entries(MACRO_DIR, m_entries)
    with open(os.path.join(MACRO_DIR, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(m_entries, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(SITE, "macro.html"), "w", encoding="utf-8") as fh:
        fh.write(render_index_page("全局简报", m_entries, "macro"))

    print("[brief] %s：豆粕 %d 条（重要 %d）/ 全市场 %d 条"
          % (today, len(soy), len(soy_high), total_macro))

    # 7. 一封邮件，两个导语 + 两个链接
    base = siteurl.base_url()
    body = "【豆粕】\n%s\n%s\n\n详细：%s/brief/%s.html\n\n" % (
        soy_summary or data["lead"], "重要快讯 %d 条" % len(soy_high), base, today)
    body += "【全市场】\n%s\n\n详细：%s/macro/%s.html\n" % (
        macro_summary or "过去 24 小时各板块要闻", base, today)
    notify.send("[豆粕早报] %s" % today, body, channels=("mail",))


if __name__ == "__main__":
    main()
