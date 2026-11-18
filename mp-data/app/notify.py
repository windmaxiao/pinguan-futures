# -*- coding: utf-8 -*-
"""告警发送：邮件（SMTP_SSL），可选 Server酱。

凭据不写在代码里，从 /etc/mp-data/notify.json 读取（权限 600，属主 mpdata）。
结构：
{
  "mail": {"enabled": true, "host": "smtp.qq.com", "port": 465,
           "user": "xxx@qq.com", "password": "授权码", "to": ["xxx@qq.com"]},
  "serverchan": {"enabled": false, "sendkey": ""}
}
"""
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText

CFG_PATH = os.environ.get("MP_NOTIFY_CONFIG", "/etc/mp-data/notify.json")


def load_config():
    try:
        with open(CFG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _send_mail(cfg, subject, body):
    mail = cfg.get("mail") or {}
    if not mail.get("enabled"):
        return False, "邮件未启用"
    host = mail.get("host", "smtp.qq.com")
    port = int(mail.get("port", 465))
    user = mail.get("user")
    password = mail.get("password")
    to = mail.get("to") or ([user] if user else [])
    if not user or not password or not to:
        return False, "邮件配置不完整"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(to)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as smtp:
            smtp.login(user, password)
            smtp.sendmail(user, to, msg.as_string())
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, "邮件失败: %s" % e


def _send_serverchan(cfg, subject, body):
    sc = cfg.get("serverchan") or {}
    if not sc.get("enabled") or not sc.get("sendkey"):
        return False, "Server酱未启用"
    url = "https://sctapi.ftqq.com/%s.send" % sc["sendkey"]
    data = urllib.parse.urlencode({"title": subject[:60], "desp": body}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        return ('"code":0' in raw.replace(" ", "")), raw[:80]
    except Exception as e:  # noqa: BLE001
        return False, "Server酱失败: %s" % e


def send(subject, body, channels=None):
    """多渠道发送，任一成功即返回 True。

    channels 指定要用的通道，默认全部启用中的通道。
    Server酱 免费额度只有 5 条/天，日常推送应显式传 ("mail",)，
    只有系统故障这类必须立刻知道的才走默认（含 Server酱）。
    """
    cfg = load_config()
    wanted = channels or ("mail", "serverchan")
    results = []
    ok_any = False
    for name in wanted:
        if name not in ("mail", "serverchan"):
            continue
        if not (cfg.get(name) or {}).get("enabled"):
            continue
        fn = _send_mail if name == "mail" else _send_serverchan
        ok, msg = fn(cfg, subject, body)
        results.append("%s=%s" % (name, msg))
        ok_any = ok_any or ok
    if not results:
        print("[notify] 没有启用任何通道，跳过")
        return False
    print("[notify] %s | %s" % ("成功" if ok_any else "失败", "; ".join(results)))
    return ok_any


if __name__ == "__main__":
    send("[测试] 豆粕数据平台告警通道", "这是一条测试消息。\n\n如果你看到它，说明告警通道已经打通。")
