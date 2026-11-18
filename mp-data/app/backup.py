# -*- coding: utf-8 -*-
"""数据库每日备份，保留最近 30 份。只用标准库。"""
import datetime
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "mp.sqlite3")
BACKUP_DIR = os.path.join(ROOT, "backup")
KEEP = 30


def main():
    if not os.path.exists(DB_PATH):
        print("[backup] 数据库不存在，跳过")
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, "mp-%s.sqlite3" % stamp)
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()
    print("[backup] %s" % dst)

    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("mp-"))
    for name in files[:-KEEP]:
        os.remove(os.path.join(BACKUP_DIR, name))
        print("[backup] 清理 %s" % name)


if __name__ == "__main__":
    main()
