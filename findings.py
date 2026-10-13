#!/usr/bin/env python3
"""观察记录的本机核心：解析、清洗、公共字段与加锁追加与集中仓库同步。"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import itertools
import json
import math
import os
from repo_aliases import canonical_repo, normalize_record
from env_compat import getenv
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import NamedTuple

from harness_ids import compile_id_pattern, format_record_id

from gitenv import repository_git_env

HERE = Path(__file__).resolve().parent
STATE = Path(getenv("AIW_STATE", HERE / "state"))
HUB_LOCK_SECONDS = 60
RECORDS_LOCK_SECONDS = 10
_SEQUENCE = itertools.count(1)
_CONFIG_WARNED = set()

SUFFIX = """---
【aiw 观察记录】完成上面的任务后，在回答最末尾附下面的记录块。它不影响验收，只用于改进流程，请如实写，没有就写「无」。
修改边界：本单范围内或挡住本单验收的问题可以修，但必须在 fixed 写明改了什么、原本应是什么；范围外的文件只记不修；规则、派单模板、工具一律只记不修；安全或数据破坏问题当场停下在正文报告。
<<<AIW-FINDINGS
guess: 哪一步是你猜的、说明里没给的？
deviation: 实际步骤和原计划有什么不同，为什么？
abandoned: 试过又放弃的做法和原因
fixed: 范围内修了什么（改了什么 / 原本应是什么）
- observation: 现象 | expected: 你以为应怎样、依据是什么 | evidence: 命令、输出或 文件:行 | location: 路径:函数
AIW-FINDINGS>>>"""


from seats_config import ConfigError, load_seats, effective_hash


def _settings() -> dict:
    return load_seats(HERE, tolerate_public=True)


def config() -> dict:
    defaults = {"collect": False, "sync": False, "hint": False, "owners": [],
                "max_items": 5, "daily_cap": 300, "field_max": 300, "sync_interval": 3600,
                "trigger_count": 100, "trigger_days": 3}
    section = _settings().get("findings", {})
    result = {**defaults, **(section if isinstance(section, dict) else {})}
    for key in ('collect', 'sync', 'hint'):
        if type(result[key]) is not bool:
            result[key] = False
    for key in ('max_items', 'daily_cap', 'field_max', 'trigger_count', 'trigger_days', 'sync_interval'):
        value = result[key]
        minimum = 0 if key in ('daily_cap', 'sync_interval') else 1
        if type(value) is not int or not minimum <= value <= sys.maxsize:
            if key not in _CONFIG_WARNED:
                print(f"findings: 警告：{key} 非法，使用默认值", file=sys.stderr)
                _CONFIG_WARNED.add(key)
            result[key] = defaults[key]
    return result


def hint_text(now=None) -> str | None:
    """Build a local-only hook hint and atomically persist its four-hour throttle.

    ``now`` is an optional datetime for deterministic callers. Fail closed on
    invalid state or I/O errors so a hint can never block a user prompt.
    """
    try:
        settings = config()
        if not settings["hint"]:
            return None
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone()
        folder = STATE / "findings"
        cache = json.loads((folder / "count-cache.json").read_text(encoding="utf-8"))
        count = cache["open"]
        if type(count) is not int or count <= 0:
            return None
        last = cache["last_triage"]
        start = _timestamp(last) if last is not None else None
        if last is not None and start is None:
            return None
        if last is None and count < settings["trigger_count"]:
            start = _timestamp(cache.get("oldest_open"))
            if start is None:
                start = min((stamp for row in _rows(folder / "records.jsonl")
                             if (stamp := _timestamp(row.get("ts"))) is not None), default=None)
        days = (now - start).total_seconds() / 86400 if start is not None else None
        if count < settings["trigger_count"] and (days is None or days < settings["trigger_days"]):
            return None
        hint_path = folder / "hint.json"
        try:
            previous = json.loads(hint_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            previous = None
        if previous is not None:
            stamp = _timestamp(previous.get("ts")) if isinstance(previous, dict) else None
            if stamp is not None and (now - stamp).total_seconds() < 4 * 3600:
                return None
        age = "还没评审过" if last is None else f"上次评审 {max(0, int(days))} 天前"
        text = f"[缓冲区] 待处理 {count} 条，{age}，建议运行 aiw triage start"
        _atomic_json(hint_path, {"ts": now.isoformat()})
        return text
    except ConfigError as error:
        print(f"findings: 配置警告：{error}", file=sys.stderr)
        return None
    except Exception:
        return None


def parse(out: str) -> tuple[str, dict | None, str | None]:
    start, end = "<<<AIW-FINDINGS", "AIW-FINDINGS>>>"
    pos = out.rfind(start)
    if pos < 0:
        return out, None, None
    tail = out[pos + len(start):]
    stop = tail.find(end)
    if stop < 0:
        return out, None, "记录块缺少结束标记"
    after_end = tail[stop + len(end):]
    if after_end.split("\n", 1)[0].strip():
        return out, None, "记录块标记格式错误"
    trailing = after_end.strip()
    if trailing and (len(trailing) > 300
                     or sum(bool(line.strip()) for line in trailing.splitlines()) > 3
                     or start in trailing):
        return out, None, "记录块后还有正文"
    # 标记独占一行；坏块绝不剥离，以免吞掉执行方的回答。
    if (pos and out[pos - 1] not in "\r\n") or not tail.startswith(("\n", "\r\n")):
        return out, None, "记录块标记格式错误"
    body = tail[:stop]
    if not body.endswith(("\n", "\r\n")):
        return out, None, "记录块标记格式错误"
    result = {key: "" for key in ("guess", "deviation", "abandoned", "fixed")}
    observations = []
    recognized = False
    max_items = config()["max_items"]
    target, previous = result, None
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            target = {key: "" for key in ("observation", "expected", "evidence", "location")}
            previous = None
            for part in line[2:].split(" | "):
                pair = re.split(r"[:：]", part, maxsplit=1)
                if len(pair) == 2:
                    key, value = pair
                    if key.strip() in target:
                        if key.strip() == "observation":
                            recognized = True
                        previous = key.strip()
                        target[previous] = value.strip()
                elif previous is not None:
                    target[previous] = (target[previous] + " " + part.strip()).strip()
            observation = target["observation"].strip()
            empty_observation = not observation or re.fullmatch(r"(?:无|没有|none|n/a)[.。]?", observation, re.IGNORECASE)
            if not empty_observation and len(observations) < max_items:
                observations.append(target)
        else:
            pair = re.split(r"[:：]", line, maxsplit=1)
            if len(pair) == 2 and pair[0].strip() in result:
                recognized = True
                target, previous = result, pair[0].strip()
                target[previous] = pair[1].strip()
            elif len(pair) == 2 and target is not result and pair[0].strip() in target:
                previous = pair[0].strip()
                target[previous] = pair[1].strip()
            elif previous is not None:
                target[previous] = (target[previous] + " " + line).strip()
    if not recognized:
        return out, None, "记录块没有已知协议字段"
    # 调用方以 parsed 是否存在区分成功与坏块；第三项也可携带非致命提示。
    answer = "\n".join(part for part in (out[:pos].rstrip(), trailing) if part)
    notice = "已保留记录块后的少量正文" if trailing else None
    return answer, {**result, "observations": observations}, notice


def sanitize(text: str, limit: int) -> str:
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("```"))
    return " ".join(text.split())[:max(0, limit)]


class ScanContext(NamedTuple):
    """一次扫描调用固定的已知名称与编译好的标识符豁免规则，不可变。"""
    hosts: frozenset[str]
    seats: frozenset[str]
    id_pattern: re.Pattern[str]


def build_scan_context() -> ScanContext:
    """只从既有的可信环境来源（本机主机名、hub raw 目录、座位配置）取快照，绝不从待扫描文本取名。"""
    hosts = set()
    try:
        hosts.add(socket.gethostname().split(".")[0])
    except OSError:
        pass
    try:
        hosts.update(path.name for path in (STATE / "findings/hub/raw").iterdir() if path.is_dir())
    except OSError:
        pass
    try:
        seats = set(load_seats(HERE, tolerate_public=True).get("seats", {}))
    except (ConfigError, OSError, ValueError):
        seats = set()
    hosts, seats = frozenset(hosts), frozenset(seats)
    return ScanContext(hosts, seats, compile_id_pattern(hosts, seats))


def has_secret(text: str, context: ScanContext) -> bool:
    if re.search(r"\bsk-[A-Za-z0-9_-]{20,}|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])|\bxox[bap]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text):
        return True

    # 用单个字符占位而不是空格，夹在凭据中间的标识符被剥掉后凭据仍是一整串。
    text = context.id_pattern.sub("x", text)
    for match in re.finditer(r"[A-Za-z0-9+/=_-]{32,}", text):
        token = match.group()
        # 十六进制摘要（包括 git SHA）不是凭据；路径按组件检测，避免整条路径凑出高熵。
        if re.fullmatch(r"[a-fA-F0-9]+", token):
            continue
        if (token.startswith("/") or (match.start() and text[match.start() - 1] == ".")
                or (match.end() < len(text) and text[match.end()] == "." and "/" in token)):
            candidates = token.split("/")
        else:
            candidates = [token]
        for candidate in candidates:
            if len(candidate) < 32 or re.fullmatch(r"[a-fA-F0-9]+", candidate):
                continue
            counts = collections.Counter(candidate)
            entropy = -sum((n / len(candidate)) * math.log2(n / len(candidate)) for n in counts.values())
            if entropy >= 4.0:
                return True
    return False


def _git(workdir: str, *args: str, strip: bool = True) -> str | None:
    try:
        p = subprocess.run(["git", "-C", workdir, *args], env=repository_git_env(), capture_output=True, text=True, timeout=10)
        return ((p.stdout.strip() if strip else p.stdout) or None) if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def _repo(remote: str | None) -> str | None:
    if not remote:
        return None
    if "://" not in remote:
        m = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", remote)
        if not m:
            return None
        host, path = m.groups()
    else:
        try:
            parsed = urlsplit(remote)
            host, path = parsed.hostname, parsed.path
        except ValueError:
            return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return canonical_repo(f"{host}/{path}".lower()) if host and len(path.split("/")) >= 2 else None


def repository_identity(workdir: str) -> tuple[str | None, str | None]:
    """返回 (仓库根, 归一化 origin)；查询失败为 None。

    无 origin 或解析失败时仍保留仓库根，供相对路径元数据使用；仓库定位环境变量由 _git 剔除。
    """
    root = _git(workdir, "rev-parse", "--show-toplevel")
    repo = _repo(_git(workdir, "remote", "get-url", "origin")) if root else None
    return root, repo


def new_id() -> str:
    host = socket.gethostname().split(".")[0]
    stamp = dt.datetime.now().astimezone()
    return format_record_id(host, stamp, os.getpid(), next(_SEQUENCE))


def common_fields(workdir: str, **dispatch) -> dict:
    stamp = dt.datetime.now().astimezone()
    host = socket.gethostname().split(".")[0]
    root, repo = repository_identity(workdir)
    digest = hashlib.sha1()
    for name in ("CLAUDE.md", "AGENTS.harness.md", "router-rules.json", "seats.json"):
        try:
            digest.update((HERE / name).read_bytes())
        except OSError:
            pass
    seat = dispatch.get("seat")
    kind = dispatch.get("kind", _settings().get("seats", {}).get(seat, {}).get("kind"))
    return {"id": new_id(),
            "ts": stamp.isoformat(timespec="seconds"), "host": host,
            "session": dispatch.get("session", os.environ.get("CLAUDE_CODE_SESSION_ID") or None),
            "source": dispatch.get("source", "worker"), "type": dispatch.get("type", "answers"),
            **{key: dispatch.get(key) for key in ("seat", "actual", "via", "ro", "prompt_id", "run_dir")},
            "workdir": str(Path(workdir).resolve()) if repo is None else None,
            "repo": repo, "repo_rel": os.path.relpath(Path(workdir).resolve(), root) if root else None,
            "commit": _git(workdir, "rev-parse", "--verify", "HEAD") if root else None,
            "branch": _git(workdir, "symbolic-ref", "--quiet", "--short", "HEAD") if root else None,
            "owned": bool(repo and repo.split("/")[1] in {owner.lower() for owner in config()["owners"]}),
            "rules_hash": digest.hexdigest()[:12], "effective_hash": effective_hash(_settings()), "partial_context": kind == "agent_use"}


TEXT_FIELDS = ("guess", "deviation", "abandoned", "fixed",
               "observation", "expected", "evidence", "location")


def record(rows: list[dict], *, strict=False) -> dict:
    counts = {"written": 0, "quarantined": 0, "capped": 0}
    folder = STATE / "findings"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # 一把锁覆盖两份日志和日计数，多个 worker 不会同时领到最后一个名额。
        with (folder / "records.lock").open("a") as lock:
            if not _acquire_lock(lock, RECORDS_LOCK_SECONDS):
                print(f"[ai-worker] 警告：records.lock timeout ({RECORDS_LOCK_SECONDS}s)，放弃本次记录", file=sys.stderr)
                if strict and rows:
                    raise TimeoutError(f"records.lock timeout ({RECORDS_LOCK_SECONDS}s)")
                return counts
            daily = folder / f"daily-{dt.date.today().isoformat()}.count"
            context = build_scan_context()
            for row in rows:
                line = json.dumps(row, ensure_ascii=False) + "\n"
                secret = any(has_secret(row[key], context) for key in TEXT_FIELDS
                             if isinstance(row.get(key), str))
                if not secret and row.get("type") == "observation":
                    n = int(daily.read_text()) if daily.exists() else 0
                    daily.write_text(str(n + 1), encoding="utf-8")
                    if n >= config()["daily_cap"]:
                        counts["capped"] += 1
                        continue
                path = folder / ("quarantine.jsonl" if secret else "records.jsonl")
                with path.open("a+b") as stream:
                    complete_line(stream)
                    stream.write(line.encode("utf-8"))
                counts["quarantined" if secret else "written"] += 1
                if secret:
                    print("[ai-worker] 警告：观察记录命中密钥特征，已隔离", file=sys.stderr)
    except Exception as e:
        if strict:
            raise
        print(f"[ai-worker] 警告：观察记录写入失败（{e}）", file=sys.stderr)
    return counts


def complete_line(stream):
    """Separate an interrupted JSONL append from the next complete record."""
    stream.seek(0, os.SEEK_END)
    if stream.tell():
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) != b"\n":
            stream.write(b"\n")


def _rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # An interrupted append must not hide earlier records.
            if isinstance(row, dict):
                yield normalize_record(row)


def _timestamp(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
        return stamp.astimezone()
    except (TypeError, ValueError):
        return None


def _overlap(start, end, include_hub=True):
    folder = STATE / "findings"
    groups = collections.defaultdict(list)
    seen = set()
    for path in [folder / "records.jsonl", *(sorted((folder / "hub/raw").glob("*/*.jsonl")) if include_hub else [])]:
        for row in _rows(path):
            stamp = _timestamp(row.get("ts"))
            if (row.get("type") != "changes" or not stamp or not start <= stamp <= end
                    or not row.get("id") or row["id"] in seen):
                continue
            seen.add(row["id"])
            repo = row.get("repo")
            scope = (repo,) if repo else (None, row.get("host"), row.get("workdir") or row.get("repo_rel"))
            for file in set(row.get("files") or []):
                groups[(scope, file)].append((stamp, row))
    signals = []
    for (scope, file), items in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        items.sort(key=lambda item: item[0])
        involved = {}
        for i, (stamp, row) in enumerate(items):
            for other_stamp, other in items[i + 1:]:
                if other_stamp - stamp > dt.timedelta(hours=24):
                    break
                if row.get("run_dir") and other.get("run_dir") and row["run_dir"] != other["run_dir"]:
                    involved[row["id"]] = row
                    involved[other["id"]] = other
        if involved:
            signals.append({"repo": scope[0], "file": file,
                            **({"host": scope[1], "workdir": scope[2]} if scope[0] is None else {}),
                            "records": [{key: row.get(key) for key in ("id", "host", "ts", "run_dir")}
                                        for row in involved.values()]})
    return signals


def _rule_churn(start, end, harness):
    if not _git(str(harness), "rev-parse", "--show-toplevel"):
        return [], "规则振荡：跳过（aiw 目录不是 git 仓库）"
    signals = []
    for file in ("CLAUDE.md", "AGENTS.harness.md", "router-rules.json", "seats.json"):
        commits = _git(str(harness), "log", "--since=" + start.isoformat(),
                       "--until=" + end.isoformat(), "--format=%H", "--", file) or ""
        sections = collections.defaultdict(list)
        for sha in commits.splitlines():
            diff = _git(str(harness), "show", "-U0", "--format=", sha, "--", file) or ""
            content = (_git(str(harness), "show", f"{sha}:./{file}", strip=False) or "").splitlines()
            headings, heading = {}, file
            for number, line in enumerate(content, 1):
                if not file.endswith(".json") and line.startswith("## "):
                    heading = line[3:].strip()
                headings[number] = heading
            touched = set()
            for match in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
                line, count = int(match[1]), int(match[2] or 1)
                # A deletion has no new lines; use its surviving new-version anchor.
                for number in range(max(1, line), max(1, line) + max(1, count)):
                    touched.add(headings.get(number, file))
            for section in touched:
                sections[section].append({"sha": sha[:12],
                                          "title": _git(str(harness), "show", "-s", "--format=%s", sha) or ""})
        for section, commits in sorted(sections.items()):
            if len(commits) >= 3:
                signals.append({"file": file, "section": section, "commits": commits})
    return signals, None


def _repeated_instruction(start, end):
    seen = set()
    lines = collections.defaultdict(list)
    for path in sorted((STATE / "runs").glob("*/task.txt")):
        try:
            stamp = dt.datetime.strptime(path.parent.name[:15], "%Y%m%d-%H%M%S").astimezone()
        except ValueError:
            continue
        if not start <= stamp <= end:
            continue
        prompt = path.read_text(encoding="utf-8")
        for marker in ("\n\n---\n以下是调用方附上的文件", "---\n【aiw 观察记录】"):
            prompt = prompt.split(marker, 1)[0]
        digest = hashlib.sha1(prompt.rstrip().encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        for line in sorted({" ".join(line.split()) for line in prompt.splitlines()}):
            if len(line) >= 15:
                lines[line].append(path.parent.name)
    return [{"line": line, "count": len(runs), "examples": runs[:3]}
            for line, runs in sorted(lines.items()) if len(runs) >= 5]


def extract(days=14, harness=None, include_hub=True):
    end = dt.datetime.now().astimezone()
    start = end - dt.timedelta(days=days)
    churn, skipped = _rule_churn(start, end, HERE if harness is None else harness)
    hub_skipped = False
    if include_hub:
        with hub_read() as hub:
            hub_skipped = hub is None
            overlap = _overlap(start, end, hub is not None)
    else:
        overlap = _overlap(start, end, False)
    data = {"generated": end.isoformat(timespec="seconds"), "days": days,
            "host": socket.gethostname().split(".")[0], "overlap": overlap, "hub_skipped": hub_skipped,
            "rule_churn": churn, "repeated_instruction": _repeated_instruction(start, end)}
    folder = STATE / "findings"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / "signals.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"最近 {days} 天：文件重叠 overlap={len(data['overlap'])}，"
          f"规则振荡 rule_churn={len(churn)}，重复手写指令 repeated_instruction={len(data['repeated_instruction'])}")
    if hub_skipped:
        print("hub.lock 超时：跳过 hub，仅抽取本机记录")
    if skipped:
        print(skipped)
    for row in data["overlap"]:
        print(f"  文件重叠：{row['repo'] or row.get('workdir')} / {row['file']}（{len(row['records'])} 条记录）")
    for row in churn:
        print(f"  规则振荡：{row['file']} / {row['section']}（{len(row['commits'])} 次提交）")
    for row in data["repeated_instruction"]:
        print(f"  重复指令：{row['line']}（{row['count']} 个 prompt）")
    return data


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _command(argv, cwd=None):
    result = subprocess.run(argv, cwd=cwd, env=repository_git_env(), capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or
                           f"{argv[0]} exited {result.returncode}")
    return result.stdout.strip()


def _acquire_lock(lock, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


@contextlib.contextmanager
def hub_read():
    """Yield the protected clone, or None when its bounded lock wait fails."""
    folder = STATE / "findings"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "hub.lock").open("a") as lock:
        if not _acquire_lock(lock, HUB_LOCK_SECONDS):
            print(f"findings: 警告：hub.lock timeout ({HUB_LOCK_SECONDS}s)，跳过 hub 读取", file=sys.stderr)
            yield None
        else:
            yield folder / "hub"


def require_private_sync():
    """Shared upload gate; its cache never writes synchronization offsets."""
    cfg = config()
    if not cfg.get("hub"):
        raise ValueError("未配置 findings.hub，triage 需要集中仓库")
    if not cfg['sync']:
        raise ValueError("findings sync=false，禁止远端写入")
    path = STATE / 'findings/visibility.json'
    cache = json.loads(path.read_text()) if path.exists() else {}
    today = dt.date.today().isoformat()
    if (cache.get('date') != today or cache.get('repo') != cfg.get('hub')
            or cache.get('visibility') != 'PRIVATE'):
        visibility = json.loads(_command(['gh', 'repo', 'view', cfg['hub'], '--json', 'visibility']))
        if visibility.get('visibility') != 'PRIVATE':
            raise ValueError('hub visibility must be PRIVATE（可见性检查失败）')
        _atomic_json(path, dict(repo=cfg['hub'], date=today, visibility='PRIVATE'))


def hub_transaction(mutate, message):
    """Reset the tool-owned clone, replay mutate, and push under the shared hub lock.

    mutate must tolerate replay. Return (success, error), including on lock timeout.
    """
    if not config().get("hub"):
        return False, "未配置 findings.hub，triage 需要集中仓库"
    folder = STATE / "findings"
    hub = folder / "hub"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "hub.lock").open("a") as lock:
            if not _acquire_lock(lock, HUB_LOCK_SECONDS):
                return False, f"hub.lock timeout ({HUB_LOCK_SECONDS}s)"
            def git(*args):
                return _command(["git", "-c", "user.name=aiw", "-c",
                                 "user.email=aiw@localhost", *args], cwd=hub)
            for attempt in range(3):
                if not hub.exists():
                    _command(["gh", "repo", "clone", config()["hub"], str(hub),
                              "--", "--filter=blob:none", "--sparse"])
                git("sparse-checkout", "set", "--no-cone", "/*", "!/archive/")
                git("fetch", "--prune", "origin")
                refs = git("ls-remote", "--symref", "origin", "HEAD", "refs/heads/*")
                branch = next((line.split()[1].removeprefix("refs/heads/")
                               for line in refs.splitlines() if line.startswith("ref:")), None)
                if branch is None and refs:
                    # An empty bare origin may still have HEAD pointing to master
                    # after our initial push to main.
                    branches = [line.split()[1].removeprefix("refs/heads/")
                                for line in refs.splitlines() if "refs/heads/" in line]
                    branch = "main" if "main" in branches else branches[0]
                if branch:
                    git("reset", "--hard", "origin/" + branch)
                    git("clean", "-fdx")
                    git("checkout", "-B", branch, "origin/" + branch)
                else:
                    # Remove even an unpushed root commit from a previous failure.
                    git("symbolic-ref", "HEAD", "refs/heads/main")
                    git("update-ref", "-d", "refs/heads/main")
                    git("read-tree", "--empty")
                git("clean", "-fdx")
                mutate(hub)
                git("add", "-A")
                if not git("diff", "--cached", "--name-only"):
                    git("sparse-checkout", "reapply")
                    return True, None
                git("commit", "-m", message)
                git("sparse-checkout", "reapply")
                try:
                    git("push", "-u", "origin", "HEAD:" + (branch or "main"))
                    return True, None
                except RuntimeError as error:
                    if attempt == 2 or not any(token in str(error).lower() for token in
                                              ("non-fast-forward", "fetch first", "[rejected]")):
                        if branch:
                            git("reset", "--hard", "origin/" + branch)
                            git("clean", "-fdx")
                        raise
    except Exception as error:
        return False, str(error)
    return False, "push retries exhausted"


def _complete_records(path, offset=0):
    rows = []
    bad = 0
    if not path.exists():
        return rows, offset
    with path.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                break
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("non-object")
                rows.append(normalize_record(row))
            except (ValueError, UnicodeError):
                bad += 1
            offset = stream.tell()
    if bad:
        print(f"findings: 警告：records.jsonl 跳过 {bad} 行损坏记录", file=sys.stderr)
    return rows, offset


def _archive_hub(hub):
    """Move old raw files in the current transaction, including sparse paths."""
    today = dt.date.today()
    cutoff = f"{today.year - 1:04d}-{today.month:02d}"
    for source in sorted(hub.glob("raw/*/*.jsonl")):
        if not re.fullmatch(r"\d{4}-\d{2}", source.stem) or source.stem >= cutoff:
            continue
        target = Path("archive") / source.relative_to(hub)
        (hub / target).parent.mkdir(parents=True, exist_ok=True)
        if (hub / target).exists():
            with (hub / target).open("ab") as stream:
                stream.write(source.read_bytes())
            source.unlink()
            _command(["git", "add", "--sparse", str(target)], cwd=hub)
        else:
            _command(["git", "add", str(source.relative_to(hub))], cwd=hub)
            _command(["git", "mv", "--sparse", str(source.relative_to(hub)), str(target)], cwd=hub)


def _recover_compaction(folder, state):
    pending = state.pop("compaction", None)
    if pending:
        if (folder / "records.jsonl").stat().st_ino == pending["inode"]:
            state["offset"] = pending["offset"]
        _atomic_json(folder / "sync.json", state)


def compact_records(folder, state):
    """Caller holds sync.lock; archive durably before replacing the local buffer."""
    with (folder / "records.lock").open("a") as lock:
        if not _acquire_lock(lock, RECORDS_LOCK_SECONDS):
            return
        path = folder / "records.jsonl"
        if not path.exists():
            return
        closed = {r.get("id") for r in _rows(folder / "local-status.jsonl")}
        prefix, suffix = [], []
        archives = collections.defaultdict(list)
        bad = 0
        month = dt.date.today().strftime("%Y-%m")
        with path.open("rb") as stream:
            while line := stream.readline():
                # Bytes beyond the uploaded snapshot (including half a line) are untouched.
                if stream.tell() > state["offset"] or not line.endswith(b"\n"):
                    suffix.append(line)
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("non-object")
                except (ValueError, UnicodeError):
                    bad += 1
                    continue
                if row.get("owned") is True:
                    continue
                try:
                    record_month = dt.datetime.fromisoformat(row.get("ts")).strftime("%Y-%m")
                except (TypeError, ValueError):
                    record_month = None
                archive = row.get("owned") is False and record_month and (
                    row.get("type") == "observation" and row.get("id") in closed or
                    row.get("type") in ("answers", "changes") and record_month < month)
                if archive:
                    archives[record_month].append(line)
                else:
                    prefix.append(line)
        if bad:
            print(f"findings: 压缩跳过 {bad} 行损坏记录", file=sys.stderr)
        temporary = None
        sizes = {}
        replaced = False
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
                temporary = Path(stream.name)
                stream.writelines(prefix)
                offset = stream.tell()
                stream.writelines(suffix)
                stream.flush()
                os.fsync(stream.fileno())
                inode = os.fstat(stream.fileno()).st_ino
            for key, lines in archives.items():
                target = folder / "archive" / (key + ".jsonl.gz")
                target.parent.mkdir(parents=True, exist_ok=True)
                sizes[target] = target.stat().st_size if target.exists() else None
                with target.open("ab") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="ab") as zipped:
                        zipped.writelines(lines)
                        zipped.flush()
                    raw.flush()
                    os.fsync(raw.fileno())
            # The inode marker recovers an interruption between the two atomic replaces.
            state["compaction"] = {"inode": inode, "offset": offset}
            _atomic_json(folder / "sync.json", state)
            os.replace(temporary, path)
            replaced = True
            state["offset"] = offset
            state.pop("compaction", None)
            _atomic_json(folder / "sync.json", state)
        except Exception:
            if not replaced:
                state.pop("compaction", None)
                for target, size in sizes.items():
                    if size is None:
                        target.unlink(missing_ok=True)
                    else:
                        with target.open("r+b") as raw:
                            raw.truncate(size)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def recompute_count_cache(offset):
    folder = STATE / "findings"
    hub = folder / "hub"
    open_ids = set()
    closed = set()
    last_triage = None
    stamps = {}
    with hub_read() as hub:
        if hub is not None and (hub / ".git").exists():
            closed = {row.get("id") for row in _rows(hub / "state/status.jsonl")}
            for path in hub.glob("raw/*/*.jsonl"):
                stamps.update((r.get("id"), r.get("ts")) for r in _rows(path))
                open_ids.update(row["id"] for row in _rows(path)
                                if row.get("type") == "observation" and row.get("id") not in closed)
            for row in _rows(hub / "state/triages.jsonl"):
                last_triage = row.get("ts")
    pending, _ = _complete_records(folder / "records.jsonl", offset)
    open_ids.update(row["id"] for row in pending
                    if row.get("owned") and row.get("type") == "observation" and row.get("id") not in closed)
    local_closed = {row.get("id") for row in _rows(folder / "local-status.jsonl")}
    local, _ = _complete_records(folder / "records.jsonl")
    open_ids.update(row["id"] for row in local if not row.get("owned") and
                    row.get("type") == "observation" and row.get("id") not in local_closed)
    stamps.update((r.get("id"), r.get("ts")) for r in local)
    oldest = min((stamp for id in open_ids if (stamp := _timestamp(stamps.get(id))) is not None), default=None)
    cache = {"oldest_open": oldest.isoformat() if oldest else None, "open": len(open_ids), "last_triage": last_triage,
             "computed": dt.datetime.now(dt.timezone.utc).isoformat()}
    _atomic_json(folder / "count-cache.json", cache)
    return cache


def sync(quiet=False):
    if not config().get("hub"):
        return 0
    folder = STATE / "findings"
    state = {"last_attempt": None, "offset": 0, "last_ok": None,
             "last_error": None}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "sync.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            path = folder / "sync.json"
            if path.exists():
                state.update(json.loads(path.read_text()))
            _recover_compaction(folder, state)
            state.pop("visibility_checked", None)
            state["last_attempt"] = dt.datetime.now(dt.timezone.utc).isoformat()
            _atomic_json(path, state)
            uploaded = 0
            try:
                if config()["sync"]:
                    require_private_sync()
                    rows, end = _complete_records(folder / "records.jsonl", state["offset"])
                    owned = [row for row in rows if row.get("owned")]
                    def mutate(hub):
                        nonlocal uploaded
                        uploaded = 0
                        known = {row.get("id") for path in hub.glob("raw/*/*.jsonl")
                                 for row in _rows(path)}
                        archived_paths = set(_command(
                            ["git", "ls-files", "archive/raw/"], cwd=hub).splitlines())
                        restored = set()
                        for row in owned:
                            host = row["host"]
                            if not isinstance(host, str) or not re.fullmatch(r"[\w.-]+", host) or host in (".", ".."):
                                raise ValueError("invalid record host")
                            month = dt.datetime.fromisoformat(row["ts"]).strftime("%Y-%m")
                            archived = f"archive/raw/{host}/{month}.jsonl"
                            if archived in archived_paths and archived not in restored:
                                _command(["git", "checkout", "--ignore-skip-worktree-bits", "--", archived], cwd=hub)
                                known.update(r.get("id") for r in _rows(hub / archived))
                                restored.add(archived)
                            if row["id"] in known:
                                continue
                            target = hub / "raw" / host / (month + ".jsonl")
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with target.open("a", encoding="utf-8") as stream:
                                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                            known.add(row["id"])
                            uploaded += 1
                        _archive_hub(hub)
                    hosts = ",".join(sorted({row["host"] for row in owned})) or socket.gethostname().split(".")[0]
                    ok, error = hub_transaction(mutate, f"raw: {hosts} +{len(owned)}")
                    if not ok:
                        raise RuntimeError(error)
                    state["offset"] = end
                    state["last_ok"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    state["last_error"] = None
                    _atomic_json(path, state)
                    try:
                        compact_records(folder, state)
                    except Exception as error:
                        state["last_error"] = f"compaction: {error}"
                        _atomic_json(path, state)
                cache = recompute_count_cache(state["offset"])
            except Exception as error:
                state["last_error"] = str(error)
                _atomic_json(path, state)
                if not quiet:
                    print(f"findings sync: {error}", file=sys.stderr)
                return 1
            if not quiet:
                print(f"uploaded: {uploaded}; open: {cache['open']}")
            return 0
    except Exception as error:
        if not quiet:
            print(f"findings sync: {error}", file=sys.stderr)
        return 1


def status():
    folder = STATE / "findings"
    counts = collections.Counter(row.get("type", "unknown") for row in _rows(folder / "records.jsonl"))
    for kind in sorted({"answers", "observation", "changes"} | counts.keys()):
        print(f"{kind}: {counts[kind]}")
    print(f"quarantine: {sum(1 for _ in _rows(folder / 'quarantine.jsonl'))}")
    archives = list((folder / "archive").glob("*.jsonl.gz"))
    print(f"archive: {len(archives)} files; {sum(p.stat().st_size for p in archives)} bytes")
    daily = folder / f"daily-{dt.date.today().isoformat()}.count"
    print(f"今天 daily: {daily.read_text().strip() if daily.exists() else '0'}")
    for name, label, missing in (("sync.json", "同步（偏移 / 上次同步 / 最近错误）", "未同步过"),
                                 ("count-cache.json", "待处理数 / 上次评审时间", "无")):
        path = folder / name
        print(f"{label}: {path.read_text().strip() if path.exists() else missing}")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"{self.prog}: {message}\n")


def main(argv=None):
    try:
        _settings()
    except ConfigError as error:
        print(f"findings: 配置错误：{error}", file=sys.stderr)
        return 1
    if os.environ.get("AIW_NESTED") == "1":
        print("执行方不得调用 aiw 命令", file=sys.stderr)
        return 64
    parser = _Parser(description="本机观察记录与离线信号抽取")
    commands = parser.add_subparsers(dest="command", required=True)
    note = commands.add_parser("note")
    note.add_argument("workdir")
    note.add_argument("observation")
    for field in ("expected", "evidence", "location"):
        note.add_argument("--" + field, default="")
    extraction = commands.add_parser("extract")
    extraction.add_argument("--days", type=int, default=14)
    commands.add_parser("status")
    syncing = commands.add_parser("sync")
    syncing.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "note":
            fields = {field: sanitize(getattr(args, field), config()["field_max"])
                      for field in ("observation", "expected", "evidence", "location")}
            if not Path(args.workdir).is_dir() or not fields["observation"]:
                parser.error("workdir 必须存在且现象不能为空")
            row = {**common_fields(args.workdir, source="main", type="observation", seat="main",
                                   actual="main", via="note", kind=None), **fields}
            result = record([row])
            if result["written"] + result["quarantined"]:
                print(row["id"])
            else:
                print("记录未写入（达到 daily_cap 或写入失败）", file=sys.stderr)
                return 1
        elif args.command == "sync":
            return sync(args.quiet)
        elif args.command == "extract":
            if args.days <= 0:
                parser.error("--days 必须为正整数")
            extract(args.days)
        else:
            status()
    except (OSError, ValueError) as error:
        print(f"findings: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
