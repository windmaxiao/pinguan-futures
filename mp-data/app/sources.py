# -*- coding: utf-8 -*-
"""数据源：新浪财经（期货实时报价、外盘、历史日线）。只用标准库，无第三方依赖。

说明：
- 大商所官网有 WAF（412），不作为数据源；行情统一走新浪。
- 现货价、油厂库存等无公开稳定接口，走 manual.py 手工录入。
"""
import datetime
import html
import json
import os
import re
import ssl
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://finance.sina.com.cn"
CTX = ssl.create_default_context()

QUOTE_URL = "http://hq.sinajs.cn/list="
KLINE_URL = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
             "var%20t=/InnerFuturesNewService.getDailyKLine?symbol=")
SPOT_URL = "https://www.100ppi.com/sf/day-%s.html"
SINA_NEWS_URL = ("https://zhibo.sina.com.cn/api/zhibo/feed"
                 "?page=%d&page_size=%d&zhibo_id=152&tag_id=0&dire=f&dpc=1")
_CHALLENGE = re.compile(r'var _0x2 = "([0-9a-f]{32})"')


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _get(url, encoding="gbk", timeout=20, retries=2, raw_dir=None, tag=""):
    """GET 并解码；失败重试。raw_dir 非空时把原始响应落盘留档。"""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": REFERER,
            })
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                body = r.read()
            if raw_dir:
                _snapshot(raw_dir, tag or "raw", body)
            return body.decode(encoding, "ignore")
        except Exception as e:  # noqa: BLE001 网络异常统一重试
            last = e
            if attempt < retries:
                time.sleep(2)
    raise last


def _snapshot(raw_dir, tag, body):
    day = datetime.date.today().isoformat()
    d = os.path.join(raw_dir, day)
    os.makedirs(d, exist_ok=True)
    ts = datetime.datetime.now().strftime("%H%M%S")
    with open(os.path.join(d, "%s-%s.bin" % (tag, ts)), "wb") as fh:
        fh.write(body)


def quotes(codes, raw_dir=None):
    """期货实时报价。codes 形如 ['M2701', 'RM2701']，返回 {code: dict}。"""
    if not codes:
        return {}
    text = _get(QUOTE_URL + ",".join("nf_" + c for c in codes),
                raw_dir=raw_dir, tag="sina_futures")
    out = {}
    for m in re.finditer(r'hq_str_nf_(\w+)="([^"]*)"', text):
        code, payload = m.group(1), m.group(2)
        f = payload.split(",")
        if len(f) < 18:
            continue
        out[code] = {
            "name": f[0], "time": f[1],
            "open": _f(f[2]), "high": _f(f[3]), "low": _f(f[4]),
            "prev_close": _f(f[5]),
            "last": _f(f[8]), "settle": _f(f[9]), "prev_settle": _f(f[10]),
            "oi": _f(f[13]), "volume": _f(f[14]),
            "exchange": f[15], "product": f[16], "date": f[17],
        }
    return out


def foreign(symbols, raw_dir=None):
    """外盘实时。symbols 形如 ['S'] -> 美豆。返回 {code: dict}。"""
    if not symbols:
        return {}
    text = _get(QUOTE_URL + ",".join("hf_" + c for c in symbols),
                raw_dir=raw_dir, tag="sina_foreign")
    out = {}
    for m in re.finditer(r'hq_str_hf_(\w+)="([^"]*)"', text):
        code, payload = m.group(1), m.group(2)
        f = payload.split(",")
        if len(f) < 14:
            continue
        out[code] = {
            "last": _f(f[0]), "open": _f(f[8]), "high": _f(f[4]), "low": _f(f[5]),
            "prev_close": _f(f[7]), "time": f[6], "date": f[12], "name": f[13],
        }
    return out


def fx_rate(symbol="susdcny", raw_dir=None):
    """外汇中间价（新浪）。默认 USD/CNY，返回 {'value','date'} 或 None。"""
    text = _get(QUOTE_URL + "fx_" + symbol, raw_dir=raw_dir, tag="sina_fx")
    m = re.search(r'hq_str_fx_\w+="([^"]*)"', text)
    if not m:
        return None
    f = m.group(1).split(",")
    if len(f) < 9:
        return None
    bid, ask = _f(f[1]), _f(f[2])
    vals = [v for v in (bid, ask) if v]
    if not vals:
        return None
    return {"value": sum(vals) / len(vals), "date": f[-1] or None}


def sina_flash(pages=1, page_size=50, raw_dir=None):
    """新浪财经 7x24 快讯（全量流，需要调用方自己按关键词过滤）。

    返回 [{'time','content','url'}]，时间格式 'YYYY-MM-DD HH:MM:SS'。
    """
    out = []
    for page in range(1, max(1, pages) + 1):
        text = _get(SINA_NEWS_URL % (page, page_size), encoding="utf-8",
                    raw_dir=raw_dir, tag="sina_news")
        try:
            data = json.loads(text)
        except ValueError:
            break
        feed = (((data.get("result") or {}).get("data") or {}).get("feed") or {})
        items = feed.get("list") or []
        if not items:
            break
        for it in items:
            content = _plain(it.get("rich_text") or "")
            if not content:
                continue
            out.append({
                "time": it.get("create_time") or "",
                "content": content,
                "url": it.get("docurl") or "",
            })
    return out


def _plain(text):
    """去掉 HTML 标签与实体。"""
    raw = re.sub(r"<[^>]+>", "", text or "")
    raw = html.unescape(raw).replace("\u3000", " ")
    return raw.strip()


def _get_bytes(url, headers=None, timeout=20, retries=2):
    """GET 原始字节，可带自定义请求头（用于带 cookie 的重试）。"""
    last = None
    for attempt in range(retries + 1):
        try:
            h = {"User-Agent": UA}
            if headers:
                h.update(headers)
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2)
    raise last


def spot_100ppi(date=None, raw_dir=None):
    """生意社「商品现货与期货价格对比表」中豆粕一行。

    页面有 JS cookie 校验（HW_CHECK），这里解一次挑战后再请求。
    返回 {'date','spot','near_code','near_price','main_code','main_price'} 或 None。

    注意：这里的 spot 是生意社的全国基准价，和手工录的华东价不是一个口径。
    """
    day = date or datetime.date.today().isoformat()
    url = SPOT_URL % day
    body = _get_bytes(url)
    text = body.decode("gbk", "ignore")
    m = _CHALLENGE.search(text)
    if m:
        body = _get_bytes(url, {"Cookie": "HW_CHECK=" + m.group(1)})
        text = body.decode("utf-8", "ignore")
    if raw_dir:
        _snapshot(raw_dir, "100ppi_sf", body)

    i = text.find(">豆粕<")
    if i < 0:
        return None
    seg = text[i:i + 4000]
    cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", seg, re.S)]
    if len(cells) < 9:
        return None
    return {
        "date": day,
        "spot": _f(cells[0]),
        "near_code": cells[1], "near_price": _f(cells[2]),
        "main_code": cells[5], "main_price": _f(cells[6]),
    }


def daily_kline(symbol, raw_dir=None):
    """历史日线（一次返回该合约全部历史）。

    返回 [{'d','o','h','l','c','v','p','s'}]，v=成交量、p=持仓量、s=结算价。
    """
    text = _get(KLINE_URL + symbol, encoding="utf-8",
                raw_dir=raw_dir, tag="kline_" + symbol)
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end < 0:
        raise ValueError("日线返回格式异常: %s" % symbol)
    data = json.loads(text[start + 1:end])
    out = []
    for item in data:
        out.append({
            "d": item.get("d"),
            "o": _f(item.get("o")), "h": _f(item.get("h")),
            "l": _f(item.get("l")), "c": _f(item.get("c")),
            "v": _f(item.get("v")), "p": _f(item.get("p")),
            "s": _f(item.get("s")),
        })
    return out
