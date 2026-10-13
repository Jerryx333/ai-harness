#!/usr/bin/env python3
"""Isolated harness tests: all cases run in parallel; named cases run in order.

    python3 tests/test_harness.py [-j N] [--durations] [case ...]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from support import fake_agent_use, fake_bins, write_legacy_local


CASE_MODULES = (
    "worker",
    "worker_2",
    "route_panel_map",
    "install_doctor",
    "track_snapshot",
    "goal",
    "misc",
    "free_guard",
    "weekly_tokens",
    "config_errands",
    "findings",
    "triage",
    "codex_home",
    "tui",
)
CASES = {}
for module_name in CASE_MODULES:
    module = importlib.import_module(f"cases.{module_name}")
    duplicates = CASES.keys() & module.CASES.keys()
    if duplicates:
        raise ValueError(f"Duplicate cases: {sorted(duplicates)}")
    CASES.update(module.CASES)

# This guard checked the complete registry in the original single-file suite.
# Bind its module global after aggregation without changing the guard assertion.
sys.modules["cases.misc"].CASES = CASES

# Audit: fixture writes use TemporaryDirectory / fake HOME; no fixed ports,
# shared /tmp files, or mutable repository files. Add discovered conflicts here.
SERIAL_CASES = frozenset()


def default_jobs():
    return max(1, (os.cpu_count() or 1) // 4)


def clamp_jobs(jobs):
    limit = max(1, (os.cpu_count() or 1) // 2)
    if jobs > limit:
        print(f"Concurrency clamped: -j {jobs} -> {limit} (half the CPU count)",
              file=sys.stderr)
    return min(jobs, limit)


def lower_priority():
    try:
        os.nice(10)
    except OSError:
        pass


def run_named(names, durations):
    failed = 0
    for n in names:
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as isolated:
                saved_env = dict(os.environ)
                try:
                    for key in ("CODEX_THREAD_ID", "AIW_TUI_ID", "TMUX", "CLAUDE_CODE_SESSION_ID", "CODEX_HOME", "AIW_CODEX_SHARED_HOME", "XDG_CONFIG_HOME", "AGENT_USE_CONFIG", "AIW_CONFIG", "AIW_ROUTER_RULES", "AGENT_USE_ERRAND_HOME", "CODEX_BIN", "CLAUDE_BIN", "AIW_PROVIDER_FILE", "AIW_MAIN", "AIW_NESTED", "CLAUDECODE", "AIW_NO_CODEX", "AIW_PANEL_GRACE", "DISCORD_TOKEN", "AIW_DISCORD_USE_BIN", "AIW_DISCORD_CHANNEL", "AIW_ZEN_MODELS_URL", "AIW_LAUNCH_AGENTS_DIR"):
                        os.environ.pop(key, None)
                    root = Path(isolated)
                    os.environ.update(HOME=str(root), AIW_STATE=str(root / "state"),
                                      PATH=f"{fake_bins(root)}:{os.environ['PATH']}",
                                      AGENT_USE_HOME=str(fake_agent_use(root)))
                    local = root / "test-seats.json"
                    write_legacy_local(local, {})
                    if n not in {"local_config_contract", "local_config_empty_defaults", "public_config_contract"}:
                        os.environ["AIW_CONFIG"] = str(local)
                    CASES[n]()
                finally:
                    os.environ.clear()
                    os.environ.update(saved_env)
            print(f"PASS {n}")
        except Exception as e:
            failed += 1
            print(f"FAIL {n}: {e}")
        durations.append((n, time.monotonic() - started))
    return 1 if failed else 0


def run_child(name):
    started = time.monotonic()
    try:
        command = [sys.executable, str(Path(__file__).resolve()), name]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            preexec_fn=lower_priority,
        )
        return name, result.returncode, result.stdout, time.monotonic() - started
    except Exception as exc:
        return name, 1, f"{type(exc).__name__}: {exc}\n", time.monotonic() - started


def run_parallel(jobs, durations):
    names = sorted(CASES)
    parallel = [name for name in names if name not in SERIAL_CASES]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_child, parallel))
    # Conflicting cases run only after every parallel child has exited.
    results.extend(run_child(name) for name in names if name in SERIAL_CASES)
    failed = 0
    for name, returncode, output, elapsed in sorted(results):
        durations.append((name, elapsed))
        if returncode:
            failed += 1
            print(f"FAIL {name}: child exited {returncode}", flush=True)
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
        else:
            print(f"PASS {name}", flush=True)
    return 1 if failed else 0


def main(argv=None):
    lower_priority()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-j", type=int, default=default_jobs(),
                        help="parallel children (default: quarter of CPUs; capped at half)")
    parser.add_argument("--durations", action="store_true", help="show slowest 15 cases")
    parser.add_argument("cases", nargs="*", help="named cases run serially in the given order")
    args = parser.parse_args(argv)
    if args.j < 1:
        parser.error("-j must be at least 1")
    args.j = clamp_jobs(args.j)
    durations = []
    if args.cases or args.j == 1:
        status = run_named(args.cases or list(CASES), durations)
    else:
        status = run_parallel(args.j, durations)
    if args.durations:
        print("Slowest 15 cases (seconds):")
        for name, elapsed in sorted(durations, key=lambda row: (-row[1], row[0]))[:15]:
            print(f"{elapsed:8.3f}s {name}")
    return status


if __name__ == "__main__":
    sys.exit(main())
