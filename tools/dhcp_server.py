#!/usr/bin/env python3
"""最小 DHCP 服务：给直连的 NAS 发地址，把它拉进可访问状态。

典型场景：
  电脑 Wi-Fi 上网，有线网口直连 NAS；NAS 的 RR 环境 / DSM 发 DHCP DISCOVER，
  本脚本回 OFFER/ACK，让 NAS 拿到一个可路由的地址，然后就能 SSH / 打开 7681。

重要：
  - 只应在“电脑与 NAS 直连”时使用；接回主网络后立即停止，避免和主网关抢答。
  - offer IP 必须避开 ZeroTier / Hyper-V / WSL / Docker 的虚拟网卡地址。
    分配前用 `ip route` / `Find-NetRoute` 确认目标地址走物理网口。
  - 需要管理员 / root 权限绑定 UDP 67。

用法：
  sudo python3 dhcp_server.py --server-ip 169.254.10.1 --offer-ip 169.254.10.3
  python dhcp_server.py --server-ip 169.254.10.1 --offer-ip 169.254.10.3 --duration 3600
"""

from __future__ import annotations

import argparse
import datetime
import socket
import struct
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


class Logger:
    def __init__(self, path: str | None) -> None:
        self.path = path

    def __call__(self, message: str) -> None:
        line = f"{datetime.datetime.now().strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass


def parse_options(payload: bytes) -> dict[int, bytes]:
    options: dict[int, bytes] = {}
    index = 240
    while index < len(payload):
        code = payload[index]
        if code == 255:
            break
        if code == 0:
            index += 1
            continue
        if index + 1 >= len(payload):
            break
        length = payload[index + 1]
        options[code] = payload[index + 2 : index + 2 + length]
        index += 2 + length
    return options


def build_reply(
    xid: int,
    chaddr16: bytes,
    message_type: int,
    request_options: dict[int, bytes],
    server_ip: str,
    offer_ip: str,
    netmask: str,
    lease: int,
) -> bytes:
    packet = struct.pack("!BBBBIHH", 2, 1, 6, 0, xid, 0, 0x8000)
    packet += socket.inet_aton("0.0.0.0")
    packet += socket.inet_aton(offer_ip)
    packet += socket.inet_aton(server_ip)
    packet += socket.inet_aton("0.0.0.0")
    packet += chaddr16.ljust(16, b"\x00")[:16]
    packet += b"\x00" * 64
    packet += b"\x00" * 128
    packet += b"\x63\x82\x53\x63"  # magic cookie

    packet += bytes([53, 1, message_type])
    packet += bytes([54, 4]) + socket.inet_aton(server_ip)
    packet += bytes([51, 4]) + struct.pack("!I", lease)
    packet += bytes([58, 4]) + struct.pack("!I", lease // 2)
    packet += bytes([59, 4]) + struct.pack("!I", lease * 7 // 8)
    packet += bytes([1, 4]) + socket.inet_aton(netmask)
    packet += bytes([3, 4]) + socket.inet_aton(server_ip)
    packet += bytes([6, 4]) + socket.inet_aton(server_ip)
    packet += bytes([28, 4]) + socket.inet_aton("169.254.255.255")

    # 回显 client identifier，部分客户端要求
    client_id = request_options.get(61)
    if client_id and len(client_id) <= 255:
        packet += bytes([61, len(client_id)]) + client_id

    packet += b"\xff"
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description="最小 DHCP 服务（直连救援用）")
    parser.add_argument("--server-ip", required=True, help="本机有线网口地址，例如 169.254.10.1")
    parser.add_argument("--offer-ip", required=True, help="分配给 NAS 的地址，例如 169.254.10.3")
    parser.add_argument("--netmask", default="255.255.0.0", help="子网掩码（默认 255.255.0.0）")
    parser.add_argument("--lease", type=int, default=3600, help="租约秒数（默认 3600）")
    parser.add_argument("--duration", type=int, default=14400, help="服务运行秒数（默认 14400）")
    parser.add_argument("--ignore-mac", action="append", default=[], help="忽略的 MAC，可重复")
    parser.add_argument("--log", default=None, help="日志文件路径")
    args = parser.parse_args()

    log = Logger(args.log)
    ignored = {mac.lower() for mac in args.ignore_mac}

    send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        send.bind((args.server_ip, 67))
        log(f"send socket bound {args.server_ip}:67")
    except OSError as exc:
        log(f"send bind {args.server_ip}:67 failed: {exc}; trying ephemeral port")
        send.bind((args.server_ip, 0))
        log(f"send socket bound {args.server_ip}:{send.getsockname()[1]}")

    receive = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    receive.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        receive.bind(("0.0.0.0", 67))
    except PermissionError:
        log("recv bind 0.0.0.0:67 failed: 需要管理员 / root 权限")
        return 1
    except OSError as exc:
        log(f"recv bind 0.0.0.0:67 failed: {exc}")
        log("提示：Windows 上如果开启了 ICS / 其他 DHCP 服务，67 端口可能已被占用。")
        return 1
    receive.settimeout(2.0)

    log(
        f"DHCP server up: offer={args.offer_ip} server={args.server_ip} "
        f"mask={args.netmask} duration={args.duration}s"
    )

    deadline = time.time() + args.duration
    count = 0
    while time.time() < deadline:
        try:
            payload, address = receive.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError as exc:
            log(f"recv error: {exc}")
            continue

        if len(payload) < 240:
            log(f"short packet from {address} len={len(payload)}")
            continue

        xid = struct.unpack("!I", payload[4:8])[0]
        chaddr16 = payload[28:44]
        mac = ":".join(f"{byte:02x}" for byte in chaddr16[:6])
        options = parse_options(payload)
        message_type = options.get(53, b"\x00")[0]
        hostname = options.get(12, b"").decode("utf-8", "replace")
        vendor = options.get(60, b"").decode("utf-8", "replace")
        client_id = options.get(61, b"").hex()

        count += 1
        log(
            f"pkt#{count} from {address[0]}:{address[1]} mac={mac} "
            f"type={MESSAGE_TYPES.get(message_type, message_type)} "
            f"xid={xid:08x} host={hostname!r} vendor={vendor!r} cid={client_id}"
        )

        if mac.lower() in ignored:
            log("  (ignored by --ignore-mac)")
            continue

        if message_type == 1:  # DISCOVER -> OFFER
            reply = build_reply(
                xid, chaddr16, 2, options, args.server_ip, args.offer_ip, args.netmask, args.lease
            )
            try:
                send.sendto(reply, ("255.255.255.255", 68))
                log(f"  -> OFFER {args.offer_ip} sent")
            except OSError as exc:
                log(f"  -> OFFER send error: {exc}")

        elif message_type == 3:  # REQUEST -> ACK / NAK
            requested = options.get(50)
            requested_ip = (
                socket.inet_ntoa(requested) if requested and len(requested) == 4 else ""
            )
            if requested_ip and requested_ip != args.offer_ip:
                reply = build_reply(
                    xid, chaddr16, 6, options, args.server_ip, "0.0.0.0", args.netmask, args.lease
                )
                try:
                    send.sendto(reply, ("255.255.255.255", 68))
                    log(f"  -> NAK (asked {requested_ip}, we offer {args.offer_ip})")
                except OSError as exc:
                    log(f"  -> NAK send error: {exc}")
            else:
                reply = build_reply(
                    xid, chaddr16, 5, options, args.server_ip, args.offer_ip, args.netmask, args.lease
                )
                try:
                    send.sendto(reply, ("255.255.255.255", 68))
                    log(f"  -> ACK {args.offer_ip} sent")
                except OSError as exc:
                    log(f"  -> ACK send error: {exc}")
        else:
            log(f"  ignored message type {message_type}")

    log(f"done, {count} packet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
