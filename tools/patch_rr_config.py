#!/usr/bin/env python3
r"""安全修改 RR 引导 U 盘里的 user-config.yml。

只改指定键，自动备份，写回后逐行 diff 校验：
  - 除目标行外不允许有任何变化；
  - 每个目标键必须恰好命中一行；
  - 保留原有的引号风格、缩进和行尾注释。

默认是 dry-run，只打印将要发生的修改；确认无误后加 --apply 才写入。

用法：
  python patch_rr_config.py --config "E:/path/user-config.yml" --directboot false --bootwait 300
  python patch_rr_config.py --config "E:/path/user-config.yml" --directboot false --bootwait 300 --apply

Windows 上 U 盘可能没有盘符，可以这样拿到路径：
  Get-Volume -FileSystemLabel RR1 | Select-Object Path
  # 然后用 \\?\Volume{GUID}\user-config.yml
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

SUPPORTED_KEYS = ("directboot", "bootwait", "bootipwait")


def find_key_line(lines: list[str], key: str) -> int:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:")
    matches = [index for index, line in enumerate(lines) if pattern.match(line)]
    if not matches:
        raise ValueError(f"找不到键 {key!r}")
    if len(matches) > 1:
        raise ValueError(f"键 {key!r} 出现了 {len(matches)} 次，拒绝猜测要改哪一行")
    return matches[0]


def replace_value(line: str, key: str, value: str) -> str:
    match = re.match(rf"^(\s*{re.escape(key)}\s*:\s*)(.*)$", line)
    if not match:
        raise ValueError(f"无法解析行：{line!r}")
    prefix, rest = match.groups()

    comment = ""
    if "#" in rest:
        comment_index = rest.index("#")
        gap_start = comment_index
        while gap_start > 0 and rest[gap_start - 1] in (" ", "\t"):
            gap_start -= 1
        comment = rest[gap_start:]
        rest = rest[:gap_start]

    stripped = rest.strip()
    quote = ""
    if len(stripped) >= 2 and stripped[0] in ("'", '"') and stripped[-1] == stripped[0]:
        quote = stripped[0]

    return f"{prefix}{quote}{value}{quote}{comment}"


def next_backup_path(config: Path) -> Path:
    candidate = config.with_name(config.name + ".bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = config.with_name(f"{config.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="安全修改 RR 的 user-config.yml")
    parser.add_argument("--config", required=True, help="user-config.yml 的完整路径")
    parser.add_argument("--directboot", choices=["true", "false"], help="是否直接引导 DSM")
    parser.add_argument("--bootwait", type=int, help="RR 环境倒计时秒数")
    parser.add_argument("--bootipwait", type=int, help="RR 环境等待 IP 的秒数")
    parser.add_argument("--apply", action="store_true", help="真正写入（默认只做 dry-run）")
    args = parser.parse_args()

    targets: dict[str, str] = {}
    if args.directboot is not None:
        targets["directboot"] = args.directboot
    if args.bootwait is not None:
        if args.bootwait < 0:
            parser.error("--bootwait 不能为负数")
        targets["bootwait"] = str(args.bootwait)
    if args.bootipwait is not None:
        if args.bootipwait < 0:
            parser.error("--bootipwait 不能为负数")
        targets["bootipwait"] = str(args.bootipwait)

    if not targets:
        parser.error("至少指定一个要修改的键：--directboot / --bootwait / --bootipwait")

    config = Path(args.config)
    if not config.is_file():
        print(f"错误：文件不存在：{config}", file=sys.stderr)
        return 1

    raw = config.read_bytes()
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)

    original_lines = list(lines)
    changes: list[tuple[int, str, str, str]] = []

    for key, value in targets.items():
        index = find_key_line([line.rstrip("\r\n") for line in lines], key)
        old_line = lines[index]
        new_line = replace_value(old_line.rstrip("\r\n"), key, value)
        new_line += "\r\n" if old_line.endswith("\r\n") else "\n" if old_line.endswith("\n") else ""
        lines[index] = new_line
        changes.append((index + 1, key, old_line.rstrip("\r\n"), new_line.rstrip("\r\n")))

    # 逐行校验：只有目标行允许变化
    if len(lines) != len(original_lines):
        print("错误：行数发生变化，拒绝写入", file=sys.stderr)
        return 1

    changed_indexes = {index for index, _, _, _ in changes}
    for index, (before, after) in enumerate(zip(original_lines, lines), start=1):
        if before != after and index not in changed_indexes:
            print(f"错误：第 {index} 行发生了非预期变化，拒绝写入", file=sys.stderr)
            print(f"  before: {before!r}", file=sys.stderr)
            print(f"  after : {after!r}", file=sys.stderr)
            return 1

    print(f"配置文件：{config}")
    print(f"共 {len(lines)} 行，计划修改 {len(changes)} 行：")
    for line_number, key, before, after in changes:
        print(f"  L{line_number} [{key}]")
        print(f"    - {before}")
        print(f"    + {after}")

    if not args.apply:
        print("\n这是 dry-run，没有写入。确认无误后加 --apply。")
        return 0

    backup = next_backup_path(config)
    shutil.copy2(config, backup)
    print(f"\n已备份：{backup}")

    new_text = "".join(lines)
    config.write_bytes(new_text.encode("utf-8"))

    # 回读校验
    verify_text = config.read_text(encoding="utf-8-sig")
    verify_lines = verify_text.splitlines(keepends=True)
    if len(verify_lines) != len(lines):
        print("错误：回读行数不一致", file=sys.stderr)
        return 1
    for index, (expected, actual) in enumerate(zip(lines, verify_lines), start=1):
        if expected != actual:
            print(f"错误：回读第 {index} 行不一致", file=sys.stderr)
            return 1

    print("已写入并回读校验通过。")
    print("下一步：U 盘插回 NAS，冷启动，并在 bootwait 窗口内建立 SSH / 打开 7681。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
