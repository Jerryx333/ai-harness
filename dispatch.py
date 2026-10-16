#!/usr/bin/env python3
"""全局派单提示开关；独立于座位配置。"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import re
import socket
import sys
import tempfile
import time

from env_compat import getenv

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))


def is_off(state_dir) -> tuple[bool, float | None]:
    """只读检查；无法读取、无效或到期均按开启处理。"""
    try:
        data = json.loads((Path(state_dir) / "dispatch.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("off") is not True:
            return False, None
        until = data["until"]
        if until is not None:
            if type(until) not in (int, float) or not math.isfinite(until) or until <= time.time():
                return False, None
            # Also reject timestamps that cannot be displayed on this host.
            dt.datetime.fromtimestamp(until)
        return True, until
    except Exception:
        return False, None


def until_text(until) -> str:
    return "直到 aiw on" if until is None else "至 " + dt.datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")


def status_text(state_dir) -> str:
    off, until = is_off(state_dir)
    return f"派单：已关闭（{until_text(until)}）" if off else "派单：开启"


def next_midnight(now: dt.datetime | None = None) -> float:
    # Keep a naive local datetime naive: timestamp() applies the timezone rules
    # for the target date, including daylight saving changes.
    now = now if now is not None else dt.datetime.now()
    tomorrow = now.date() + dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow, dt.time(), tzinfo=now.tzinfo).timestamp()


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in ("off", "on", "status") or len(args) > (2 if args[0] == "off" else 1):
        print("用法：aiw off [30m|2h|3d|forever] | on | status", file=sys.stderr)
        return 2
    temporary = None
    try:
        if args[0] == "off":
            since = time.time()
            until = None
            if len(args) == 1:
                until = next_midnight()
            elif args[1] != "forever":
                match = re.fullmatch(r"([1-9][0-9]*)([mhd])", args[1])
                try:
                    if match is None:
                        raise ValueError
                    until = since + int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
                    dt.datetime.fromtimestamp(until)
                except (ValueError, OverflowError, OSError):
                    print("非法时长：请使用 30m、2h、3d 或 forever", file=sys.stderr)
                    return 2
            STATE.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE, prefix=".dispatch-", delete=False) as out:
                temporary = Path(out.name)
                json.dump({"off": True, "until": until, "since": since, "host": socket.gethostname()}, out)
                out.write("\n")
            temporary.replace(STATE / "dispatch.json")
            temporary = None
        elif args[0] == "on":
            (STATE / "dispatch.json").unlink(missing_ok=True)
        print(status_text(STATE))
        return 0
    except OSError as error:
        print(f"派单开关操作失败：{error}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
