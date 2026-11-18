# -*- coding: utf-8 -*-
"""极简 MCP (Streamable HTTP) 客户端，只用标准库。

协议要点：
- 全部通过 POST <endpoint> 发送 JSON-RPC 2.0；
- 请求头同时声明接受 application/json 和 text/event-stream；
- initialize 的响应头可能带 Mcp-Session-Id，后续请求要回传；
- 响应可能是单个 JSON，也可能是 SSE。
"""
import json
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
CLIENT_INFO = {"name": "mp-data", "version": "1.0.0"}
_SSE_EVENTS = {"message", ""}


class McpError(RuntimeError):
    pass


def _parse_sse(text, want_id):
    """从 SSE 文本里取出 JSON-RPC 响应。"""
    fallback = None
    event = ""
    data_lines = []

    def flush():
        nonlocal event, data_lines
        payload = None
        if event in _SSE_EVENTS and data_lines:
            raw = "\n".join(data_lines).strip()
            if raw and raw != "[DONE]":
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = None
        event = ""
        data_lines = []
        return payload

    for line in text.splitlines():
        if line.startswith(":"):
            continue
        if line == "":
            payload = flush()
            if isinstance(payload, dict):
                if want_id is None or payload.get("id") == want_id:
                    return payload
                if fallback is None:
                    fallback = payload
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value.strip()
        elif field == "data":
            data_lines.append(value)

    payload = flush()
    if isinstance(payload, dict):
        if want_id is None or payload.get("id") == want_id:
            return payload
        if fallback is None:
            fallback = payload
    return fallback


class McpClient:
    def __init__(self, url, token="", timeout=40.0):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self._next_id = 0
        self._initialized = False

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "mp-data/1.0",
        }
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self._initialized:
            h["MCP-Protocol-Version"] = PROTOCOL
        return h

    def _post(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data,
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                status = resp.status
                ctype = (resp.headers.get("Content-Type") or "").lower()
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise McpError("HTTP %s: %s" % (e.code, detail[:300]))
        except Exception as e:  # noqa: BLE001
            raise McpError("请求失败: %s" % e)

        if status in (202, 204) or not body:
            return None
        text = body.decode("utf-8", "ignore")
        if "text/event-stream" in ctype:
            return _parse_sse(text, payload.get("id"))
        try:
            return json.loads(text)
        except ValueError:
            parsed = _parse_sse(text, payload.get("id"))
            if parsed is not None:
                return parsed
            raise McpError("响应既不是 JSON 也不是 SSE: %r" % text[:200])

    def _rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        resp = self._post(payload)
        if notify or resp is None:
            return None
        if resp.get("error"):
            err = resp["error"]
            raise McpError("%s 返回错误 %s: %s" % (method, err.get("code"), err.get("message")))
        return resp.get("result")

    def initialize(self):
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self._initialized = True
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except McpError:
            pass
        return self

    def call(self, name, arguments=None):
        if not self._initialized:
            self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        if result.get("isError"):
            raise McpError("工具 %s 返回错误" % name)
        contents = result.get("content") or []
        texts = [c.get("text", "") for c in contents
                 if isinstance(c, dict) and c.get("type") == "text"]
        if not texts:
            return result.get("structuredContent") or result
        raw = "\n".join(texts)
        try:
            return json.loads(raw)
        except ValueError:
            return raw
