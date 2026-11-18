# -*- coding: utf-8 -*-
"""合约滚动：根据当天日期，算出"最近的 N 个合约"。

规则：
- 只保留交割月 strictly 晚于当前月的合约（进入交割月即视为过期，不再展示）；
- 按交割月从近到远排序；
- 交易所没挂牌的合约自然抓不到数据，不会显示。
"""
import datetime
import re


def _parse(code):
    m = re.match(r"^([A-Z]+)(\d{4})$", code)
    if not m:
        return None
    return m.group(1), 2000 + int(m.group(2)[:2]), int(m.group(2)[2:])


def active_codes(prefix, months, count, today=None):
    """返回形如 ['M2701', 'M2703', ...]，按交割月由近及远。"""
    today = today or datetime.date.today()
    out = []
    for k in range(1, 60):                       # k=0 是当月，跳过
        total = (today.year * 12 + today.month - 1) + k
        year, month = total // 12, total % 12 + 1
        if month in months:
            out.append("%s%02d%02d" % (prefix, year % 100, month))
            if len(out) >= count:
                break
    return out


def groups(cfg):
    """{'M': ['M2701', ...], 'RM': [...], 'Y': [...]}，按交割月由近及远。"""
    out = {}
    for prefix, spec in cfg["contracts"].items():
        out[prefix] = active_codes(prefix, spec["months"], spec["count"])
    return out


def sort_key(code):
    parsed = _parse(code)
    if not parsed:
        return (0, 0)
    return (parsed[1], parsed[2])


def is_expired(code, today=None):
    """合约是否已进入交割月（含已过）。"""
    parsed = _parse(code)
    if not parsed:
        return False
    today = today or datetime.date.today()
    return (parsed[1], parsed[2]) <= (today.year, today.month)
