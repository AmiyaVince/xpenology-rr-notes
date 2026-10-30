#!/usr/bin/env python3
"""等待 RR 环境的 SSH 端口，登录后抓取诊断信息。

RR 环境默认：root / rr，SSH 22 端口，bootwait 窗口内可访问。
建立 SSH 会话本身会中断 `_bootwait` 倒计时，让机器停在 RR 环境。

依赖：
  pip install paramiko

用法：
  python wait_rr_ssh.py --host 169.254.10.3
  python wait_rr_ssh.py --host 169.254.10.3 --timeout 300 --password rr
  python wait_rr_ssh.py --host 169.254.10.3 --command "lsblk; ip -4 addr"
"""

from __future__ import annotations

import argparse
import datetime
import os
import socket
import sys
import time

DEFAULT_COMMAND = """
uname -a
id
cat /proc/cmdline
echo ---W---
w
echo ---DF---
df -h
echo ---RR---
ls /opt/rr 2>/dev/null | head -30
echo ---PART---
cat /proc/partitions
echo ---BLK---
lsblk -o NAME,SIZE,TYPE,MODEL,SERIAL 2>/dev/null
echo ---NET---
ip -4 addr
echo ---END---
""".strip()


def stamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="等待 RR 环境 SSH 并抓取诊断信息")
    parser.add_argument("--host", required=True, help="NAS 地址")
    parser.add_argument("--port", type=int, default=22, help="SSH 端口（默认 22）")
    parser.add_argument("--user", default="root", help="SSH 用户（RR 默认 root）")
    parser.add_argument(
        "--password",
        default=os.environ.get("RR_PASSWORD", "rr"),
        help="SSH 密码（默认取 RR_PASSWORD 环境变量，否则 rr）",
    )
    parser.add_argument("--timeout", type=int, default=300, help="等待端口打开的秒数（默认 300）")
    parser.add_argument("--command", default=DEFAULT_COMMAND, help="登录后执行的命令")
    args = parser.parse_args()

    try:
        import paramiko
    except ImportError:
        print("缺少 paramiko：pip install paramiko", file=sys.stderr)
        return 1

    print(f"{stamp()} waiting up to {args.timeout}s for {args.host}:{args.port} ...", flush=True)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if port_open(args.host, args.port):
            print(f"{stamp()} port {args.port} OPEN", flush=True)
            break
        time.sleep(1.5)
    else:
        print(f"{stamp()} timeout: port {args.port} never opened", flush=True)
        return 2

    time.sleep(1)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            args.host,
            port=args.port,
            username=args.user,
            password=args.password,
            timeout=8,
            banner_timeout=8,
            auth_timeout=8,
        )
        print(f"{stamp()} SSH connected", flush=True)
        _, stdout, stderr = client.exec_command(args.command, timeout=30)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        print("=== STDOUT ===")
        print(out)
        if err.strip():
            print("=== STDERR ===")
            print(err)
        return 0
    except Exception as exc:  # noqa: BLE001 - 需要把认证/网络错误原样告诉用户
        print(f"{stamp()} SSH failed: {type(exc).__name__}: {exc}", flush=True)
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
