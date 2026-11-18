# -*- coding: utf-8 -*-
"""金十数据（MCP）资讯采集：快讯 / 深度资讯 / 财经日历。

只用标准库。Token 放在 /etc/mp-data/jin10.json（权限 600），结构 {"token": "..."}。
"""
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcp      # noqa: E402
import notify   # noqa: E402
import sources  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_token(nc):
    path = os.environ.get("JIN10_MCP_TOKEN_FILE") or nc.get("token_file") \
        or "/etc/mp-data/jin10.json"
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("token", "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _uid(kind, pub_time, text):
    raw = "%s|%s|%s" % (kind, pub_time or "", (text or "")[:120])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _parse_time(value):
    """兼容 '2026-11-18T08:41:50+08:00' / '2026-11-18 10:14:14' / '11-18 10:14'。"""
    if not value:
        return None
    text = str(value).strip().replace("/", "-")
    dt = None
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M"):
            try:
                dt = datetime.datetime.strptime(text, fmt)
                if fmt.startswith("%m"):
                    dt = dt.replace(year=datetime.date.today().year)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _hits(text, words):
    return [w for w in words if w in text]


def _classify(text, sectors):
    """按板块关键词归类；一条消息只归第一个命中的板块。"""
    for name, words in (sectors or {}).items():
        if any(w in text for w in words):
            return name
    return ""


def _save(conn, kind, source, pub_time, title, content, url, keywords, level, sector=""):
    """返回 True 表示这条是新的。已有记录若板块为空则补上。"""
    uid = _uid(kind, pub_time, content or title)
    cur = conn.execute(
        "INSERT OR IGNORE INTO news (uid, kind, source, pub_time, title, content, url,"
        " keywords, level, sector, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (uid, kind, source, pub_time, title, content, url, keywords, level, sector,
         datetime.datetime.now().isoformat(timespec="seconds")),
    )
    if cur.rowcount == 0 and sector:
        conn.execute("UPDATE news SET sector = ? WHERE uid = ? "
                     "AND (sector IS NULL OR sector = '')", (sector, uid))
    return cur.rowcount > 0


def collect(conn, cfg, flash_only=False):
    """flash_only=True 时只抓快讯（供高频定时任务使用），跳过深度资讯与日历。"""
    nc = cfg.get("news") or {}
    if not nc.get("enabled"):
        return
    token = load_token(nc)
    if not token:
        print("[news] 未配置金十 token，跳过")
        return

    url = nc.get("mcp_url") or "https://mcp.jin10.com/mcp"
    keywords = nc.get("keywords") or ["豆粕", "大豆"]
    alert_words = nc.get("alert_keywords") or []
    sectors = nc.get("sectors") or {}
    max_age = int(nc.get("max_age_hours", 72))

    try:
        client = mcp.McpClient(url, token, timeout=40).initialize()
    except Exception as e:  # noqa: BLE001
        print("[news] 金十 MCP 连接失败: %s" % e)
        return

    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(hours=max_age)
    # 深度资讯很少，放宽窗口（USDA 月报这类可能一周前才发）
    news_cutoff = now - datetime.timedelta(days=int(nc.get("news_max_age_days", 30)))
    seen = set()
    flash_count = 0
    alerts = []

    # ---- 快讯：按关键词逐个检索 ----
    for kw in keywords:
        try:
            payload = client.call("search_flash", {"keyword": kw})
        except Exception as e:  # noqa: BLE001
            print("[news] search_flash(%s) 失败: %s" % (kw, e))
            continue
        items = ((payload or {}).get("data") or {}).get("items") or []
        for it in items:
            content = str(it.get("content") or "").strip()
            if len(content) < 20:
                continue
            pub = _parse_time(it.get("time"))
            if pub and pub < cutoff:
                continue
            key = content[:80]
            if key in seen:
                continue
            seen.add(key)
            pub_text = pub.strftime("%Y-%m-%d %H:%M:%S") if pub else str(it.get("time") or "")
            hits = _hits(content, list(keywords) + list(alert_words))
            level = "high" if _hits(content, alert_words) else "normal"
            is_new = _save(conn, "flash", "jin10", pub_text,
                           str(it.get("title") or ""), content,
                           str(it.get("url") or ""), ",".join(hits), level,
                           _classify(content, sectors) or "农产品")
            flash_count += 1
            if is_new and level == "high" and pub and pub >= now - datetime.timedelta(hours=6):
                alerts.append((pub_text, content))

    # ---- 新浪 7x24：全量流兜底（没有检索能力，靠关键词过滤）----
    sina_cfg = nc.get("sina_flash") or {}
    sina_count = 0
    if sina_cfg.get("enabled"):
        try:
            batch = sources.sina_flash(pages=int(sina_cfg.get("pages", 1)),
                                       page_size=int(sina_cfg.get("page_size", 50)))
        except Exception as e:  # noqa: BLE001
            print("[news] 新浪 7x24 抓取失败: %s" % e)
            batch = []
        for it in batch:
            content = it.get("content") or ""
            if len(content) < 20:
                continue
            if not _hits(content, keywords):
                continue
            pub = _parse_time(it.get("time"))
            if pub and pub < cutoff:
                continue
            key = content[:80]
            if key in seen:          # 和金十重复的跳过
                continue
            seen.add(key)
            pub_text = pub.strftime("%Y-%m-%d %H:%M:%S") if pub else str(it.get("time") or "")
            hits = _hits(content, list(keywords) + list(alert_words))
            level = "high" if _hits(content, alert_words) else "normal"
            if _save(conn, "flash", "sina", pub_text, "", content,
                     str(it.get("url") or ""), ",".join(hits), level):
                sina_count += 1
                if level == "high" and pub and pub >= now - datetime.timedelta(hours=6):
                    alerts.append((pub_text, content))

    # ---- 全量流：按板块归类，供「全局简报」使用 ----
    pipeline = nc.get("sector_pipeline") or {}
    sector_count = 0
    if pipeline.get("enabled") and sectors:
        cursor = ""
        for _ in range(max(1, int(pipeline.get("pages", 2)))):
            args = {"cursor": cursor} if cursor else {}
            try:
                payload = client.call("list_flash", args)
            except Exception as e:  # noqa: BLE001
                print("[news] list_flash 失败：%s" % e)
                break
            data = (payload or {}).get("data") or {}
            batch = data.get("items") or []
            cursor = str(data.get("next_cursor") or "")
            for it in batch:
                content = str(it.get("content") or "").strip()
                if len(content) < 20:
                    continue
                pub = _parse_time(it.get("time"))
                if pub and pub < cutoff:
                    continue
                suffix = _classify(content, sectors)
                if not suffix:
                    continue
                pub_text = pub.strftime("%Y-%m-%d %H:%M:%S") if pub else str(it.get("time") or "")
                if _save(conn, "flash", "jin10", pub_text, str(it.get("title") or ""),
                         content, str(it.get("url") or ""), "", "normal", suffix):
                    sector_count += 1
            if not cursor:
                break
        if sector_count:
            print("[news] 板块快讯新增 %d 条" % sector_count)

    if flash_only:
        conn.commit()
        print("[news] 快讯 %d 条 / 新浪 %d 条（仅快讯模式）" % (flash_count, sina_count))
        _send_alerts(alerts)
        return

    # ---- 深度资讯：主动检索，而不是翻最新列表（列表里多数与豆类无关）----
    news_count = 0
    for kw in (nc.get("news_keywords") or ["豆粕", "大豆"]):
        try:
            payload = client.call("search_news", {"keyword": kw})
        except Exception as e:  # noqa: BLE001
            print("[news] search_news(%s) 失败: %s" % (kw, e))
            continue
        data = (payload or {}).get("data")
        items = data.get("items") if isinstance(data, dict) else data
        for it in (items or []):
            title = str(it.get("title") or "").strip()
            intro = str(it.get("introduction") or "").strip()
            text = title + " " + intro
            if not _hits(text, keywords):
                continue
            pub = _parse_time(it.get("time"))
            if pub and pub < news_cutoff:
                continue
            pub_text = pub.strftime("%Y-%m-%d %H:%M:%S") if pub else str(it.get("time") or "")
            hits = _hits(text, list(keywords) + list(alert_words))
            level = "high" if _hits(text, alert_words) else "normal"
            if _save(conn, "news", "jin10", pub_text, title, intro,
                     str(it.get("url") or ""), ",".join(hits), level):
                news_count += 1

    # ---- 财经日历 ----
    cal_count = 0
    try:
        payload = client.call("list_calendar", {})
        data = (payload or {}).get("data")
        if isinstance(data, dict):
            data = data.get("items")
        min_star = int(nc.get("calendar_star", 3))
        cal_words = nc.get("calendar_keywords") or []
        for it in (data or []):
            try:
                star = int(it.get("star") or 0)
            except (TypeError, ValueError):
                star = 0
            if star < min_star:
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            if cal_words and not _hits(title, cal_words):
                continue
            pub = _parse_time(it.get("pub_time"))
            if pub and pub.date() < datetime.date.today():
                continue          # 只保留今天及以后的数据
            pub_text = pub.strftime("%Y-%m-%d %H:%M") if pub else str(it.get("pub_time") or "")
            body = "实际 %s / 预期 %s / 前值 %s" % (
                it.get("actual") or "—", it.get("consensus") or "—", it.get("previous") or "—")
            if _save(conn, "calendar", "jin10", pub_text, title, body,
                     "", "★" * star, "normal"):
                cal_count += 1
    except Exception as e:  # noqa: BLE001
        print("[news] list_calendar 失败: %s" % e)

    conn.commit()
    print("[news] 金十快讯 %d 条 / 新浪快讯 %d 条新增 / 资讯 %d 条 / 日历 %d 条"
          % (flash_count, sina_count, news_count, cal_count))
    _send_alerts(alerts)


def _send_alerts(alerts):
    if not alerts:
        return
    body = "金十快讯命中关键词：\n\n"
    for t, c in alerts[:5]:
        body += "【%s】%s\n\n" % (t, c[:200])
    body += "\n看板：https://data.example.com/"
    # 日常快讯只发邮件；Server酱 的 5 条/天额度留给系统故障
    notify.send("[豆粕数据] 重要快讯 %d 条" % len(alerts), body, channels=("mail",))


def prune(conn, days=14):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM news WHERE substr(pub_time,1,10) < ?", (cutoff,))
    if cur.rowcount:
        print("[news] 清理旧资讯 %d 条" % cur.rowcount)


if __name__ == "__main__":
    import db
    cfg = load_config()
    conn = db.connect(DB_PATH)
    collect(conn, cfg, flash_only="--flash-only" in sys.argv)
    prune(conn)
    conn.commit()
