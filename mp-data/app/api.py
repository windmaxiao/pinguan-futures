# -*- coding: utf-8 -*-
"""写入接口：只监听 127.0.0.1，由 nginx 反代并复用 basic auth 保护。

安全措施：
- 只绑回环地址，外网无法直连；
- 强制 Content-Type: application/json（跨站请求会触发预检，而这里不返回 CORS 头 → 被浏览器拦下）；
- 校验 Origin；
- 指标名必须在 config 白名单里，数值必须落在 min/max 区间；
- 请求体上限 4KB。
"""
import datetime
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db      # noqa: E402
import render  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
PORT = int(os.environ.get("MP_API_PORT", "8765"))
ALLOWED_ORIGIN = os.environ.get("MP_ALLOWED_ORIGIN", "https://data.windxiao.site")


def load_config():
    with open(os.path.join(APP, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def validate(payload):
    cfg = load_config()
    sid = (payload.get("series") or "").strip()
    if sid not in cfg["manual_series"]:
        raise ValueError("未知指标：%s" % sid)
    meta = cfg["manual_series"][sid]

    try:
        value = float(payload.get("value"))
    except (TypeError, ValueError):
        raise ValueError("数值无效")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("数值无效")
    lo, hi = meta.get("min"), meta.get("max")
    if lo is not None and value < lo:
        raise ValueError("%s 不应低于 %s" % (meta["name"], lo))
    if hi is not None and value > hi:
        raise ValueError("%s 不应高于 %s" % (meta["name"], hi))

    date = (payload.get("date") or "").strip() or datetime.date.today().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError("日期无效")
    today = datetime.date.today()
    if d > today + datetime.timedelta(days=7) or d < today - datetime.timedelta(days=400):
        raise ValueError("日期超出允许范围")

    note = (payload.get("note") or "").strip()[:100]
    return sid, date, value, note


class Handler(BaseHTTPRequestHandler):
    server_version = "mpdata/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[api] %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # 故意不返回 CORS 头：跨站 POST 会卡在预检
        self._send(405, {"ok": False, "error": "OPTIONS not allowed"})

    def do_GET(self):
        if self.path != "/api/latest":
            self._send(404, {"ok": False, "error": "not found"})
            return
        cfg = load_config()
        conn = db.connect(DB_PATH)
        out = {}
        for sid, meta in cfg["manual_series"].items():
            row = db.latest(conn, sid)
            out[sid] = {
                "name": meta["name"], "unit": meta["unit"],
                "value": row["value"] if row else None,
                "date": row["date"] if row else None,
                "hint": meta.get("hint", ""),
            }
        conn.close()
        self._send(200, {"ok": True, "series": out})

    def do_POST(self):
        if self.path != "/api/manual":
            self._send(404, {"ok": False, "error": "not found"})
            return

        origin = self.headers.get("Origin", "")
        if origin and origin != ALLOWED_ORIGIN:
            self._send(403, {"ok": False, "error": "来源不合法"})
            return

        if "application/json" not in self.headers.get("Content-Type", ""):
            self._send(415, {"ok": False, "error": "需要 application/json"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("请求体为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": "请求解析失败：%s" % e})
            return

        try:
            sid, date, value, note = validate(payload)
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return

        try:
            cfg = load_config()
            meta = cfg["manual_series"][sid]
            conn = db.connect(DB_PATH)
            db.upsert(conn, sid, date, value, unit=meta["unit"],
                      freq=meta.get("freq", "D"), source="web",
                      sample="", quality="manual", note=note)
            conn.commit()
            conn.close()
            render.main()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"ok": False, "error": "保存失败：%s" % e})
            return

        self._send(200, {"ok": True, "series": sid, "date": date,
                         "value": value, "name": meta["name"]})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("[api] listening on 127.0.0.1:%d (origin=%s)" % (PORT, ALLOWED_ORIGIN))
    server.serve_forever()


if __name__ == "__main__":
    main()
