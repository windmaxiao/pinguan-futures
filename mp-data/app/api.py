# -*- coding: utf-8 -*-
"""写入接口 + 门禁鉴权：只监听 127.0.0.1，由 nginx 反代。

安全措施：
- 只绑回环地址，外网无法直连；
- 强制 Content-Type: application/json（跨站请求会触发预检，这里不返回 CORS 头 → 被浏览器拦下）；
- 校验 Origin；
- 指标名必须在 config 白名单里，数值必须落在 min/max 区间；
- 门禁 cookie 用 HMAC 签名，访问码与管理码分属不同 scope，不能互相冒充。
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
import gate    # noqa: E402
import journal # noqa: E402
import render  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
PORT = int(os.environ.get("MP_API_PORT", "8765"))
ALLOWED_ORIGIN = os.environ.get("MP_ALLOWED_ORIGIN", "https://data.example.com")


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

    # ---------------- 基础

    def _send(self, code, obj, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            name, value, max_age = cookie
            self.send_header("Set-Cookie",
                             "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax; Secure"
                             % (name, value, max_age))
        self.end_headers()
        self.wfile.write(body)

    def _cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, val = part.strip().partition("=")
            if key == name:
                return val
        return ""

    def _origin_ok(self):
        origin = self.headers.get("Origin", "")
        return (not origin) or origin == ALLOWED_ORIGIN

    def _json_body(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("需要 application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            raise ValueError("请求体为空或过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---------------- 门禁

    def _is_authed(self):
        data = gate.load()
        if not data.get("gate_enabled"):
            return True
        return gate.auth_ok(self._cookie(gate.COOKIE_AUTH))

    def _is_admin(self):
        return gate.admin_ok(self._cookie(gate.COOKIE_ADMIN))

    def do_OPTIONS(self):
        self._send(405, {"ok": False, "error": "OPTIONS not allowed"})

    def do_GET(self):
        if self.path == "/auth/check":
            self._send(200 if self._is_authed() else 401,
                       {"ok": self._is_authed()})
            return
        if self.path == "/auth/admin/status":
            if not self._is_admin():
                self._send(401, {"ok": False, "error": "需要管理码"})
                return
            data = gate.load()
            self._send(200, {
                "ok": True,
                "gate_enabled": bool(data.get("gate_enabled")),
                "access_set": bool(data.get("access_sha256")),
                "access_hint": data.get("access_hint") or "",
                "admin_set": bool(data.get("admin_sha256")),
                "session_days": data.get("session_days"),
                "admin_session_hours": data.get("admin_session_hours"),
            })
            return
        if self.path == "/api/journal":
            if not self._is_admin():
                self._send(401, {"ok": False, "error": "需要管理码"})
                return
            conn = db.connect(DB_PATH)
            try:
                payload = journal.summary(conn)
            finally:
                conn.close()
            self._send(200, {"ok": True, **payload})
            return
        if self.path != "/api/latest":
            self._send(404, {"ok": False, "error": "not found"})
            return
        # 纵深防御：不只依赖 nginx 的 auth_request
        if not self._is_authed():
            self._send(401, {"ok": False, "error": "未解锁"})
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
        path = self.path
        if path.startswith("/auth/") and not self._origin_ok():
            self._send(403, {"ok": False, "error": "来源不合法"})
            return

        if path == "/auth/login":
            self._login(admin=False)
            return
        if path == "/auth/admin/login":
            self._login(admin=True)
            return
        if path == "/auth/admin/gen":
            self._admin_gen()
            return
        if path == "/auth/admin/toggle":
            self._admin_toggle()
            return

        if path.startswith("/api/journal") or path.startswith("/api/trade"):
            self._journal_write(path)
            return

        if path != "/api/manual":
            self._send(404, {"ok": False, "error": "not found"})
            return

        if not self._is_admin():
            self._send(401, {"ok": False, "error": "录入数据需要管理码"})
            return
        if not self._origin_ok():
            self._send(403, {"ok": False, "error": "来源不合法"})
            return
        try:
            payload = self._json_body()
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

    # ---------------- 门禁内部

    def _login(self, admin):
        try:
            payload = self._json_body()
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": "请求解析失败：%s" % e})
            return
        code = (payload.get("code") or "").strip()
        if not code:
            self._send(400, {"ok": False, "error": "请输入访问码"})
            return

        data = gate.load()
        if admin:
            if not gate.check_code(code, data.get("admin_sha256")):
                self._send(401, {"ok": False, "error": "管理码不正确"})
                return
            max_age = int(data["admin_session_hours"]) * 3600
            self._send(200, {"ok": True},
                       cookie=(gate.COOKIE_ADMIN, gate.issue_admin_cookie(data), max_age))
            return

        if not data.get("access_sha256"):
            self._send(503, {"ok": False, "error": "访问码尚未设置，请先在服务器上生成"})
            return
        if not gate.check_code(code, data.get("access_sha256")):
            self._send(401, {"ok": False, "error": "访问码不正确"})
            return
        max_age = int(data["session_days"]) * 86400
        self._send(200, {"ok": True},
                   cookie=(gate.COOKIE_AUTH, gate.issue_auth_cookie(data), max_age))

    def _admin_gen(self):
        if not self._is_admin():
            self._send(401, {"ok": False, "error": "需要管理码"})
            return
        code = gate.new_access_code()
        data = gate.set_access_code(code)
        data["gate_enabled"] = True
        data["secret"] = os.urandom(32).hex()      # 旧会话全部失效
        gate.save(data)
        # 用新密钥重新发一个管理 cookie，免得把自己也踢下线
        max_age = int(data["admin_session_hours"]) * 3600
        self._send(200, {"ok": True, "code": code},
                   cookie=(gate.COOKIE_ADMIN, gate.issue_admin_cookie(data), max_age))

    def _admin_toggle(self):
        if not self._is_admin():
            self._send(401, {"ok": False, "error": "需要管理码"})
            return
        try:
            payload = self._json_body()
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": "请求解析失败：%s" % e})
            return
        enabled = bool(payload.get("enabled"))
        data = gate.load()
        data["gate_enabled"] = enabled
        gate.save(data)
        self._send(200, {"ok": True, "gate_enabled": enabled})

    # ---------------- 交易记录

    def _journal_write(self, path):
        if not self._is_admin():
            self._send(401, {"ok": False, "error": "需要管理码"})
            return
        if not self._origin_ok():
            self._send(403, {"ok": False, "error": "来源不合法"})
            return
        try:
            p = self._json_body()
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": "请求解析失败：%s" % e})
            return

        conn = db.connect(DB_PATH)
        try:
            if path == "/api/journal":
                date = (p.get("date") or "").strip()
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                    raise ValueError("日期格式应为 YYYY-MM-DD")
                direction = (p.get("direction") or "").strip()
                if direction not in ("多", "空", "观望"):
                    raise ValueError("方向应为 多 / 空 / 观望")
                journal.save_journal(conn, date, direction,
                                     (p.get("reason") or "")[:300],
                                     (p.get("risk") or "")[:300],
                                     (p.get("note") or "").strip()[:12])
                self._send(200, {"ok": True})

            elif path == "/api/trade":
                contract = (p.get("contract") or "").strip()[:12]
                side = (p.get("side") or "").strip()
                if not contract:
                    raise ValueError("合约不能为空")
                if side not in ("多", "空"):
                    raise ValueError("方向应为 多 / 空")
                lots = int(p.get("lots") or 1)
                if not 1 <= lots <= 100:
                    raise ValueError("手数不合法")
                open_price = float(p.get("open_price") or 0)
                if open_price <= 0:
                    raise ValueError("开仓价不合法")
                tid = journal.save_trade(conn, contract, side, lots,
                                         (p.get("open_date") or
                                          datetime.date.today().isoformat()),
                                         open_price, (p.get("reason") or "")[:300])
                self._send(200, {"ok": True, "id": tid})

            elif path == "/api/trade/close":
                tid = int(p.get("id") or 0)
                if tid <= 0:
                    raise ValueError("交易编号不合法")
                close_price = float(p.get("close_price") or 0)
                if close_price <= 0:
                    raise ValueError("平仓价不合法")
                pnl = journal.close_trade(
                    conn, tid,
                    (p.get("close_date") or datetime.date.today().isoformat()),
                    close_price, p.get("fee") or 0, (p.get("review") or "")[:300])
                self._send(200, {"ok": True, "pnl": pnl})

            elif path == "/api/trade/delete":
                journal.delete_trade(conn, int(p.get("id") or 0))
                self._send(200, {"ok": True})

            else:
                self._send(404, {"ok": False, "error": "not found"})
        except (ValueError, TypeError) as e:
            self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"ok": False, "error": "保存失败：%s" % e})
        finally:
            conn.close()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("[api] listening on 127.0.0.1:%d (origin=%s)" % (PORT, ALLOWED_ORIGIN))
    server.serve_forever()


if __name__ == "__main__":
    main()
