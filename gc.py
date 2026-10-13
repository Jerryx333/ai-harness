#!/usr/bin/env python3
# 用途：清理过期 runs/panels/maps 子目录；运行：python3 gc.py [天数] [--dry-run]
# 依赖：Python 3 标准库；状态目录由 AI_HARNESS_STATE 指定。
import os
import shutil
import sys
import time
from pathlib import Path


def directory_size(path: Path) -> int:
    total = path.lstat().st_size
    for root, dirs, files in os.walk(path, followlinks=False):
        total += sum((Path(root) / name).lstat().st_size for name in dirs + files)
    return total


def main() -> int:
    args = sys.argv[1:]
    days = [a for a in args if a != "--dry-run"]
    if len(days) > 1 or (days and not days[0].isdigit()):
        print("用法：aiw gc [非负整数天数] [--dry-run]", file=sys.stderr)
        return 64
    cutoff = time.time() - int(days[0] if days else 30) * 86400
    dry = "--dry-run" in args
    state = Path(os.environ.get("AI_HARNESS_STATE", Path(__file__).resolve().parent / "state"))
    count = size = 0
    for name in ("runs", "panels", "maps"):
        parent = state / name
        if parent.is_symlink() or not parent.exists():
            continue
        for path in sorted(parent.iterdir()):
            if path.is_symlink() or not path.is_dir() or path.stat().st_mtime >= cutoff:
                continue
            amount = directory_size(path)
            print(f"{'将删除' if dry else '删除'} {path} ({amount} 字节)")
            if not dry:
                shutil.rmtree(path)
            count += 1
            size += amount
    print(f"{'预计删除' if dry else '已删除'} {count} 个目录，{'预计释放' if dry else '释放'} {size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
