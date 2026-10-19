# -*- coding: utf-8 -*-
"""从 cloudflared 日志中提取公网地址并写入 settings，终端只打印打码地址。

用法: python tools/set_public.py [日志路径]
"""
import os, re, sqlite3, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "data", "tunnel.log")
DB = os.path.join(BASE, "data", "arena.db")

PAT = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def mask(u):
    m = re.match(r"(https?)://([^./]+)(.*)", u)
    if not m:
        return "*" * 8
    scheme, host, rest = m.groups()
    keep = 2 if len(host) > 5 else 1
    return "%s://%s%s%s" % (scheme, host[:keep] + "*" * max(3, len(host) - keep), rest, "")


def main():
    if not os.path.exists(LOG):
        print("[失败] 日志不存在:", LOG)
        return 1
    with open(LOG, "r", encoding="utf-8", errors="ignore") as f:
        found = PAT.findall(f.read())
    if not found:
        print("[失败] 日志中未找到公网地址")
        return 1
    url = found[-1].rstrip("/")
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO settings(k,v) VALUES('public_base',?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (url,))
    conn.execute("INSERT INTO settings(k,v) VALUES('access_masked','1') "
                 "ON CONFLICT(k) DO NOTHING")
    conn.commit()
    conn.close()
    print("[完成] 公网地址已写入，界面默认显示为:", mask(url))
    print("        真实地址请到 管理端 - 访问地址 - 显示真实地址 查看")
    return 0


if __name__ == "__main__":
    sys.exit(main())
