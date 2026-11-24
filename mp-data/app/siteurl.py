# -*- coding: utf-8 -*-
"""站点地址配置。

仓库里放的是占位域名；真实域名写在服务器的 /etc/mp-data/site.json（600，属主 mpdata）：
    {"site_url": "https://data.example.com"}

这样发布代码时不用再逐文件替换域名。
"""
import json
import os

CONFIG_PATH = os.environ.get("MP_SITE_CONFIG", "/etc/mp-data/site.json")
FALLBACK = "https://data.example.com"


def base_url():
    """返回站点根地址，不带结尾斜杠。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh) or {}
        url = (data.get("site_url") or "").strip()
        if url:
            return url.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return FALLBACK


def brief_url(date_text):
    return "%s/brief/%s.html" % (base_url(), date_text)


def macro_url(date_text):
    return "%s/macro/%s.html" % (base_url(), date_text)
