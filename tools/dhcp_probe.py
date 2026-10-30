#!/usr/bin/env python3
"""被动监听 DHCP，识别链路上是谁在要地址。

用途：
  - 判断 NAS 到底有没有发包；
  - 区分 RR 环境（vendor='udhcp 1.30.1'）和 DSM（host='<主机名>'）；
  - 发现只有 DISCOVER、没有后续分配的异常客户端。

需要管理员 / root 权限绑定 UDP 67。脚本只监听，不回应，不会干扰现有 DHCP。

用法：
  sudo python3 dhcp_probe.py --duration 120
  python dhcp_probe.py --duration 60 --bind 0.0.0.0
"""

from __future__ import annotations

import argparse
import datetime
import socket
import sys
import time

MESSAGE_TYPES = {
    1: "DISCOVER",
    2: "OFFER",
    3: "REQUEST",
    4: "DECLINE",
    5: "ACK",
    6: "NAK",
    7: "RELEASE",
    8: "INFORM",
}


def log(message: str) -> None:
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"{stamp} {message}", flush=True)


def parse_options(payload: bytes) -> dict[int, bytes]:
    """解析 DHCP options（从 offset 240 开始）。"""
    options: dict[int, bytes] = {}
    index = 240
    while index < len(payload):
        code = payload[index]
        if code == 255:  # END
            break
        if code == 0:  # PAD
            index += 1
            continue
        if index + 1 >= len(payload):
            break
        length = payload[index + 1]
        options[code] = payload[index + 2 : index + 2 + length]
        index += 2 + length
    return options


def text(value: bytes | None) -> str:
    if not value:
        return ""
    return value.decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="被动监听 DHCP 报文")
    parser.add_argument("--bind", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=67, help="监听端口（默认 67）")
    parser.add_argument("--duration", type=int, default=120, help="监听时长，秒（默认 120）")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind((args.bind, args.port))
    except PermissionError:
        log(f"绑定 {args.bind}:{args.port} 失败：需要管理员 / root 权限")
        return 1
    except OSError as exc:
        log(f"绑定 {args.bind}:{args.port} 失败：{exc}")
        log("提示：Windows 上如果开启了 ICS / 其他 DHCP 服务，67 端口可能已被占用。")
        return 1

    sock.settimeout(1.0)
    log(f"listening on {args.bind}:{args.port} for {args.duration}s (passive, no replies)")
    deadline = time.time() + args.duration
    count = 0

    while time.time() < deadline:
        try:
            payload, address = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as exc:
            log(f"recv error: {exc}")
            continue

        if len(payload) < 240:
            log(f"short packet from {address} len={len(payload)}")
            continue

        count += 1
        options = parse_options(payload)
        message_type = options.get(53, b"\x00")[0]
        mac = ":".join(f"{byte:02x}" for byte in payload[28:34])
        hostname = text(options.get(12))
        vendor = text(options.get(60))
        requested = options.get(50)
        requested_ip = socket.inet_ntoa(requested) if requested and len(requested) == 4 else ""

        log(
            f"pkt#{count} from {address[0]}:{address[1]} "
            f"mac={mac} type={MESSAGE_TYPES.get(message_type, message_type)} "
            f"host={hostname!r} vendor={vendor!r} requested={requested_ip or '-'}"
        )

    log(f"done, {count} packet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
