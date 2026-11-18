# -*- coding: utf-8 -*-
"""极简 OpenAI 兼容 chat/completions 客户端（urllib 实现，零依赖）。

凭据放在 /etc/mp-data/llm.json（权限 600）：
{"api_key": "sk-...", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}
"""
import json
import os
import urllib.error
import urllib.request

CFG_PATH = os.environ.get("MP_LLM_CONFIG", "/etc/mp-data/llm.json")


def load_config():
    try:
        with open(CFG_PATH, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def configured():
    return bool(load_config().get("api_key"))


def chat(messages, max_tokens=400, temperature=0.3, timeout=60):
    """发一轮对话，返回文本。失败抛异常，由调用方决定降级策略。"""
    cfg = load_config()
    key = cfg.get("api_key")
    if not key:
        raise RuntimeError("未配置 LLM api_key")

    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise RuntimeError("HTTP %s: %s" % (e.code, detail[:200]))
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("响应里没有 choices")
    return (choices[0].get("message") or {}).get("content", "").strip()
