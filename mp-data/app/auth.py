# -*- coding: utf-8 -*-
"""门禁管理命令行。

    python3 auth.py status            # 查看门禁状态与码指纹
    python3 auth.py gen               # 生成新访问码并开启门禁
    python3 auth.py set <访问码>       # 指定访问码
    python3 auth.py admin <管理码>     # 设置管理码
    python3 auth.py on | off          # 开关门禁
    python3 auth.py secret            # 轮换签名密钥（所有会话立即失效）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate  # noqa: E402


def cmd_status():
    d = gate.load()
    print("门禁开关   :", "开启" if d["gate_enabled"] else "关闭")
    print("访问码     :", ("已设置，指纹 %s****" % d["access_hint"]) if d["access_sha256"] else "未设置")
    print("管理码     :", "已设置" if d["admin_sha256"] else "未设置")
    print("访问会话   :", "%d 天" % d["session_days"])
    print("管理会话   :", "%d 小时" % d["admin_session_hours"])
    print("配置文件   :", gate.AUTH_PATH)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "status":
        cmd_status()

    elif cmd == "gen":
        code = gate.new_access_code()
        data = gate.set_access_code(code)
        data["gate_enabled"] = True
        gate.save(data)
        print("新访问码：%s" % code)
        print("（只显示这一次；门禁已开启，旧 cookie 全部失效）")

    elif cmd == "set":
        if len(args) < 2:
            print("用法：auth.py set <访问码>")
            return
        code = args[1]
        data = gate.set_access_code(code)
        data["gate_enabled"] = True
        gate.save(data)
        print("访问码已设置，指纹 %s****，门禁已开启" % data["access_hint"])

    elif cmd == "admin":
        if len(args) < 2:
            print("用法：auth.py admin <管理码>")
            return
        gate.set_admin_code(args[1])
        print("管理码已设置")

    elif cmd in ("on", "off"):
        data = gate.load()
        data["gate_enabled"] = (cmd == "on")
        gate.save(data)
        print("门禁已%s" % ("开启" if data["gate_enabled"] else "关闭"))

    elif cmd == "secret":
        data = gate.load()
        data["secret"] = os.urandom(32).hex()
        gate.save(data)
        print("签名密钥已轮换，所有已解锁会话立即失效")

    else:
        print("未知命令：%s" % cmd)
        print(__doc__)


if __name__ == "__main__":
    main()
