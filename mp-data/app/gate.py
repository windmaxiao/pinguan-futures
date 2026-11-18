# -*- coding: utf-8 -*-
"""访问门禁：解锁码校验、签名 cookie、门禁开关。

状态存在 /etc/mp-data/auth.json（权限 600）。只存哈希，不存明文。
"""
import hashlib
import hmac
import json
import os
import secrets
import time

AUTH_PATH = os.environ.get("MP_AUTH_CONFIG", "/etc/mp-data/auth.json")
COOKIE_AUTH = "pinguan_auth"
COOKIE_ADMIN = "pinguan_admin"

DEFAULTS = {
    "access_sha256": "",
    "access_hint": "",
    "admin_sha256": "",
    "secret": "",
    "gate_enabled": False,
    "session_days": 7,
    "admin_session_hours": 24,
}


def load():
    data = {}
    try:
        with open(AUTH_PATH, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception:  # noqa: BLE001
        data = {}
    out = dict(DEFAULTS)
    out.update(data)
    if not out.get("secret"):
        out["secret"] = secrets.token_hex(32)
        save(out)
    return out


def save(data):
    path = os.path.dirname(os.path.abspath(AUTH_PATH))
    if path:
        os.makedirs(path, exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, AUTH_PATH)
    try:
        os.chmod(AUTH_PATH, 0o600)
    except Exception:  # noqa: BLE001
        pass


def hash_code(code):
    return hashlib.sha256(("pinguan:" + (code or "").strip()).encode("utf-8")).hexdigest()


def check_code(code, digest):
    if not digest:
        return False
    return hmac.compare_digest(hash_code(code), digest)


def sign(scope, expires_at, secret):
    """签名格式：scope.expires.sig —— scope 防止访问码 cookie 冒充管理码。"""
    msg = "%s.%d" % (scope, int(expires_at))
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return msg + "." + sig


def verify(value, scope, secret):
    if not value:
        return False
    parts = value.rsplit(".", 1)
    if len(parts) != 2:
        return False
    msg, sig = parts
    if not msg.startswith(scope + "."):
        return False
    want = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return False
    try:
        return int(msg.split(".", 1)[1]) > time.time()
    except ValueError:
        return False


def new_access_code():
    """生成 XXXX-XXXX-XXXX-XXXX 形式的访问码（去除易混字符）。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)


def set_access_code(code):
    data = load()
    data["access_sha256"] = hash_code(code)
    data["access_hint"] = (code or "")[:4].upper()
    save(data)
    return data


def set_admin_code(code):
    data = load()
    data["admin_sha256"] = hash_code(code)
    save(data)
    return data


def issue_auth_cookie(data):
    return sign("auth", time.time() + int(data["session_days"]) * 86400, data["secret"])


def issue_admin_cookie(data):
    return sign("admin", time.time() + int(data["admin_session_hours"]) * 3600, data["secret"])


def auth_ok(cookie_value):
    data = load()
    return verify(cookie_value, "auth", data["secret"])


def admin_ok(cookie_value):
    data = load()
    return verify(cookie_value, "admin", data["secret"])


def gate_enabled():
    return bool(load().get("gate_enabled"))
