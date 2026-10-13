#!/usr/bin/env python3
"""写本机脱敏快照到 snapshots/<host>.json，不提交到仓库，可对比多台机器各自生成的文件检查漂移。

只记录「形状」：CLI 版本、seats.json 哈希、settings.json 里的钩子与模型、CLAUDE.md 是否引入 aiw。
不记录任何 env 值、token、key。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

from env_compat import agent_use_home
from gitenv import repository_git_env

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("HOME", "~")).expanduser()
SECRET_KEY = re.compile(r"(token|key|secret|password|auth|cookie|credential|helper)", re.I)
# 字符串里内嵌的密钥：sk-/rc- 前缀、Bearer、--api-key xxx、KEY=xxx
SECRET_VAL = re.compile(r"((?:sk|rc|ghp|gho|github_pat)[-_][A-Za-z0-9_-]{12,}|Bearer\s+\S+|(--?[A-Za-z-]*(?:key|token|secret|password)[A-Za-z-]*)[ =]\S+|[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=\S+)", re.I)


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else None


def scrub(o):
    if isinstance(o, dict):
        return {k: ("<redacted>" if SECRET_KEY.search(k) or k == "env" else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    if isinstance(o, str):
        for h in {str(HOME), str(HOME.resolve())}:
            o = o.replace(h, "$HOME")
        return SECRET_VAL.sub("<redacted>", o)
    return o


def version(cmd: str) -> str | None:
    path = shutil.which(cmd)
    if not path:
        return None
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        out = (r.stdout or r.stderr).strip().splitlines()
        return out[0] if out else f"rc={r.returncode}"
    except Exception as e:
        return f"error:{e.__class__.__name__}"


def seat_shape(c: dict) -> dict:
    if c.get("kind") == "agent_use":
        return {"kind": "agent_use", "provider": c["provider"], "model": c.get("model") or "agent-use-default",
                "require_no_tools": bool(c.get("require_no_tools"))}
    b = os.environ.get(c.get("bin_env", "")) or c["bin"]
    return {"kind": "cli", "bin": os.environ.get(c.get("bin_env", "")) and "<env override>" or c["bin"],
            "version": version(b), "model": c.get("model") or "cli-default"}


def agent_use_shape() -> dict:
    home = agent_use_home()
    entry = home / "scripts" / "run_agent.py"
    head = subprocess.run(["git", "-C", str(home), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, env=repository_git_env()).stdout.strip() if home.exists() else ""
    dirty = subprocess.run(["git", "-C", str(home), "status", "--porcelain"], capture_output=True, text=True, env=repository_git_env()).stdout.strip() if home.exists() else ""
    return {"entry_exists": entry.exists(), "head": head or None, "dirty": bool(dirty), "entry_sha": sha(entry)}


def hook_shapes(hooks: dict) -> tuple[dict, dict]:
    expected = 'python3 "$HOME/.claude/aiw/route.py" --hook'
    mine, others = {}, {}
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                harness = "aiw" in command
                shape = {"type": hook.get("type"), "timeout": hook.get("timeout"),
                         "cmd_sha": hashlib.sha256(command.encode()).hexdigest()[:12]}
                if harness:
                    shape["matches_expected"] = command == expected
                entry = (mine if harness else others).setdefault(event, {"count": 0, "hooks": []})
                entry["count"] += 1
                entry["hooks"].append(shape)
    return mine, others


from seats_config import ConfigError, load_seats, effective_hash, local_config_path


def main() -> int:
    try:
        seats = load_seats(HERE)
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 1
    settings_p = HOME / ".claude" / "settings.json"
    settings = {}
    if settings_p.exists():
        try:
            s = json.loads(settings_p.read_text(encoding="utf-8"))
            hooks = s.get("hooks") or {}
            mine, others = hook_shapes(hooks)
            settings = scrub({"model": s.get("model"), "harness_hooks": mine, "other_hooks": others})
        except Exception:
            settings = {"error": "settings.json 解析失败"}
    claude_md = HOME / ".claude" / "CLAUDE.md"
    from doctor_config import fingerprint
    drift = fingerprint(HERE, seats)
    snap = {
        "fingerprint": drift,
        "host": socket.gethostname(),
        "taken": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "harness_commit": (drift["harness"].removesuffix("*")
                           if drift["harness"] != "-" else
                           subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
                                          capture_output=True, text=True, env=repository_git_env()).stdout.strip() or None),
        "effective_hash": effective_hash(seats),
        "local_config": local_config_path() is not None,
        "seats_json_sha": sha(HERE / "seats.json"),
        "router_rules_sha": sha(HERE / "router-rules.json"),
        "cli": {name: seat_shape(c) for name, c in seats["seats"].items()},
        "agent_use": agent_use_shape(),
        "claude_cli": version(os.environ.get("CLAUDE_BIN") or "claude"),
        "claude_md_imports_harness": claude_md.exists() and "@~/.claude/aiw/CLAUDE.md" in claude_md.read_text(encoding="utf-8", errors="ignore"),
        "settings": settings,
    }
    out = HERE / "snapshots" / f"{snap['host']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {out}；不提交到仓库；收集另一台生成的文件后对比：git diff --no-index snapshots/<A>.json snapshots/<B>.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
