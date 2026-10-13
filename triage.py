#!/usr/bin/env python3
"""Build local review batches and commit validated decisions to the findings hub."""
from __future__ import annotations

import collections
import datetime as dt
import fcntl
import json
import os
from repo_aliases import canonical_repo, canonical_key, normalize_record
import re
import socket
import sys
from pathlib import Path

import findings as f

STATUSES = {'explained', 'unexplained', 'stale', 'wontfix', 'unverifiable'}
HYP_STATUSES = {'待补证', '支持', '反驳', '试修', '验证中', '已验证', '已放弃'}
LAYERS = {'机制', '项目规则', '代码'}
KEYWORDS = '找不到 不一致 被覆盖 冲突 超时 格式 猜 手动 重复 过期 权限 绕过'.split()
TEMPLATE = {
    'self_cause_considered': '本批是否有症状由主会话自己的规则或派单模板引起：……',
    'hypotheses': [{'id': 'H-YYYYMMDD-1', 'title': '', 'mechanism': '',
                    'explains': ['记录id'], 'predicts': ['若成立还应看到'],
                    'refuted_by': ['看到什么说明它错了'], 'competing': [],
                    'status': '待补证', 'layer': '机制', 'fix': {'commit': None, 'issue': None},
                    'probe': '', 'expect_down': [], 'expect_flat': [], 'window_days': 7}],
    'records': [{'id': '记录id', 'status': 'unexplained', 'hypothesis': '', 'note': ''}],
    'issues': [{'hypothesis': '', 'key': '', 'title': '', 'body': '', 'labels': []}],
    'project_issues': [{'key': '项目缺陷键', 'repo': 'owner/name', 'records': ['记录id'],
                        'title': '', 'body': '', 'labels': []}],
}


def now():
    return dt.datetime.now(dt.timezone.utc)


def host():
    return socket.gethostname().split('.')[0]


def folder():
    return f.STATE / 'findings'


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def append(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        f.complete_line(stream)
        for row in rows:
            stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())


def transaction(mutate, message):
    """Preserve lock-conflict exit status across hub_transaction's error boundary."""
    conflict = False
    def checked(hub):
        nonlocal conflict
        try:
            mutate(hub)
        except LockConflict:
            conflict = True
            raise
    ok, error = f.hub_transaction(checked, message)
    if not ok:
        print(f'triage: {error}', file=sys.stderr)
        return 75 if conflict else 1
    return 0


class LockConflict(RuntimeError):
    pass


def require_lock(hub, lock):
    current = read(hub / 'state/triage.lock', {})
    if any(current.get(k) != lock.get(k) for k in ('host', 'triage_id', 'started')):
        raise LockConflict(f"锁不属于本次 triage；持锁主机 {current.get('host')}，过期时间 {current.get('expires')}")


def latest():
    locks = sorted((folder() / 'triage').glob('*/lock.json'), reverse=True)
    if not locks:
        print('请先运行 aiw triage start', file=sys.stderr)
        return None
    path = locks[0]
    lock = read(path)
    if lock.get('host') != host():
        raise LockConflict('本地锁不属于本机')
    return path, lock


def collect(hub=None):
    base = folder()
    rows = {}
    for path in sorted(hub.glob('raw/*/*.jsonl')) if hub is not None else []:
        rows.update((r['id'], r) for r in f._rows(path))
    offset = read(base / 'sync.json', {}).get('offset', 0)
    pending, _ = f._complete_records(base / 'records.jsonl', offset)
    local, _ = f._complete_records(base / 'records.jsonl')
    for r in [r for r in local if not r.get('owned')] + pending:
        rows.setdefault(r['id'], r)
    # Each file is append-only; timestamp orders updates between the two streams.
    statuses = (list(f._rows(hub / 'state/status.jsonl')) if hub is not None else []) + list(f._rows(base / 'local-status.jsonl'))
    statuses.sort(key=lambda r: f._timestamp(r.get('ts')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return list(rows.values()), {r['id']: r for r in statuses}


def overview(rows):
    result = {'hosts': dict(collections.Counter(r.get('host') for r in rows))}
    for dimension in ('seat', 'repo'):
        groups = {}
        for r in rows:
            key = (canonical_repo(r.get(dimension)) if dimension == 'repo' else r.get(dimension)) or '(本机)'
            counts = groups.setdefault(key, {'answers': 0, 'observation': 0})
            if r.get('type') in counts:
                counts[r['type']] += 1
        for counts in groups.values():
            counts['per_100'] = 100 * counts['observation'] / counts['answers'] if counts['answers'] else None
        result[dimension] = groups
    answers = [r for r in rows if r.get('type') == 'answers']
    result['empty_answers'] = {key: {'empty': sum(str(r.get(key) or '').strip() in ('', '无') for r in answers),
                                   'total': len(answers)} for key in ('guess', 'deviation', 'abandoned', 'fixed')}
    latest_rows = {}
    for r in sorted(rows, key=lambda r: f._timestamp(r.get('ts')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):
        latest_rows[r.get('host')] = r
    result['rules_hash'] = {h: r.get('rules_hash') for h, r in latest_rows.items()}
    result['rules_mismatch'] = len(set(result['rules_hash'].values())) > 1
    return result


def project_records(hub):
    """Include synced local records without changing batch collection semantics."""
    rows, _ = collect(hub)
    indexed = {r['id']: r for r in rows}
    local, _ = f._complete_records(folder() / 'records.jsonl')
    for row in local:
        indexed.setdefault(row['id'], row)
    return list(indexed.values())


def grouping(rows):
    data = {}
    for key in ('repo', 'repo_rel', 'seat', 'host', 'rules_hash'):
        data[key] = collections.Counter((canonical_repo(r.get(key)) if key == 'repo' else r.get(key)) or '(空)' for r in rows).most_common(15)
    data['keywords'] = {}
    for word in KEYWORDS:
        ids = [r['id'] for r in rows if word in ' '.join(str(r.get(k) or '') for k in ('observation', 'expected', 'evidence'))]
        data['keywords'][word] = {'count': len(ids), 'ids': ids[:5]}
    return data


def render(batch):
    def dump(value):
        return json.dumps(value, ensure_ascii=False, indent=2)
    def cell(value):
        return str(value or '').replace('\n', ' ').replace('\r', ' ').replace('|', '\\|')
    transfers = collections.defaultdict(list)
    for issue in read(folder() / 'project-issues.json', {}).values():
        for rid in issue['records']:
            transfers[rid].append(f"→ {issue['target']}#{issue['number']}")
    o = batch['overview']
    lines = [f"# Triage {batch['triage_id']}", '', '## 1. 概况', '各主机记录数：' + dump(o['hosts'])]
    for dim in ('seat', 'repo'):
        lines.append(f'### 按 {dim}（answers 为分母）')
        for key, c in o[dim].items():
            rate = f"{c['per_100']:.2f}" if c['per_100'] is not None else 'N/A（无分母）'
            lines.append(f"- {key}: answers={c['answers']}, observation={c['observation']}, 每 100 单观察数={rate}")
    for key, c in o['empty_answers'].items():
        rate = f"{100*c['empty']/c['total']:.2f}%" if c['total'] else 'N/A'
        lines.append(f"- {key} 无或空：{c['empty']}/{c['total']} ({rate})")
    lines.append('各主机最新 rules_hash：' + dump(o['rules_hash']))
    if o['rules_mismatch']:
        lines.append('⚠ 两台规则版本不一致')
    lines.append('## 2. 假设台账')
    for h in batch['hypotheses']:
        lines.append(('(本机) ' if h.get('local') else '') + h['id'])
        lines.extend(['```json', dump(h), '```'])
        if h['due']:
            lines.append('⏰ 到期：跑探针并核对两台 rules_hash')
    lines.append('## 3. 机械信号')
    for signal in batch['signals'].get('overlap', []):
        hosts = '、'.join(sorted({str(r.get('host') or '') for r in signal['records']}))
        lines.append(f"- {cell(signal.get('repo') or signal.get('workdir'))} {cell(signal['file'])} — {len(signal['records'])} 次（主机 {hosts}）")
    for signal in batch['signals'].get('rule_churn', []):
        lines.append(f"- {cell(signal['file'])} / {cell(signal['section'])} — {len(signal['commits'])} 次提交")
        for commit in signal['commits'][:3]:
            lines.append('  - ' + cell(commit['title']))
    for signal in batch['signals'].get('repeated_instruction', []):
        lines.append(f"- 出现 {signal['count']} 次：{cell(signal['line'])}")
    lines.append('## 4. 机械分组')
    for dimension, groups in batch['groups'].items():
        if dimension == 'keywords':
            groups = [(key, value['count']) for key, value in groups.items()]
        for key, count in groups:
            lines.append(f"- {dimension} / {cell(key)} — {count} 条")
    lines.append('## 5. 记录')
    for key, title in (('pool', '未解释池'), ('pending', '本批待处理')):
        lines.append('### ' + title)
        for r in batch[key]:
            lines.append(' | '.join(cell(r.get(k)) for k in ('id', 'source', 'seat')) + ' | ' +
                         cell(r.get('repo') or '(本机)') + ' | ' +
                         ' ／ '.join(cell(r.get(k)) for k in ('observation', 'expected', 'evidence', 'location')) +
                         (' ' + ' '.join(cell(x) for x in transfers[r['id']])
                          if key == 'pool' and transfers[r['id']] else ''))
    lines.extend(['## decisions.json 模板', '```json', dump(TEMPLATE), '```',
                  '写好后运行 aiw triage apply <decisions.json>'])
    return '\n'.join(lines) + '\n'


def start(days=60):
    f.require_private_sync()
    if f.sync(quiet=False):
        print('警告：sync 失败，继续使用已有数据', file=sys.stderr)
    stamp = now()
    tid = host() + '-' + stamp.strftime('%Y%m%d-%H%M%S')
    target = folder() / 'triage' / tid
    if target.exists():
        print('同秒批次已存在，请稍后 start', file=sys.stderr)
        return 75
    lock = {'host': host(), 'pid': os.getpid(), 'triage_id': tid, 'started': stamp.isoformat(),
            'expires': (stamp + dt.timedelta(hours=2)).isoformat()}
    takeover = False
    def acquire(hub):
        nonlocal takeover
        old = read(hub / 'state/triage.lock', {})
        expires = f._timestamp(old.get('expires'))
        if old and (expires is None or expires > now()):
            raise LockConflict(f"持锁主机 {old.get('host')}，过期时间 {old.get('expires')}")
        takeover = bool(old)
        f._atomic_json(hub / 'state/triage.lock', lock)
    rc = transaction(acquire, f'triage start: {tid}')
    if rc:
        return rc
    f._atomic_json(target / 'lock.json', lock)
    if takeover:
        print('已接管过期锁')
    with f.hub_read() as hub:
        rows, statuses = collect(hub)
        hub_hypotheses = read(hub / 'state/hypotheses.json', {}) if hub is not None else {}
    signals = f.extract(14)
    local_hypotheses = read_local_hypotheses()
    pending = [r for r in rows if r.get('type') == 'observation' and r['id'] not in statuses]
    pool = [r for r in rows if r.get('type') == 'observation' and
            statuses.get(r['id'], {}).get('status') in ('unexplained', 'unverifiable') and
            (f._timestamp(r.get('ts')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= stamp - dt.timedelta(days=days)]
    hypotheses = []
    for hid, value in {**hub_hypotheses, **{key: dict(value, local=True) for key, value in local_hypotheses.items()}}.items():
        if value.get('status') in ('已验证', '已放弃'):
            continue
        h = dict(value, id=hid, due=False)
        updated = f._timestamp(h.get('updated'))
        window = h.get('window_days')
        if h.get('status') in ('试修', '验证中') and updated and type(window) is int and window > 0:
            h['due'] = updated + dt.timedelta(days=window) <= stamp
        hypotheses.append(h)
    batch = {'triage_id': tid, 'created': stamp.isoformat(), 'days': days, 'records': rows,
             'pending': pending, 'pool': pool, 'overview': overview(rows), 'hypotheses': hypotheses,
             'signals': signals, 'groups': grouping(pending + pool)}
    f._atomic_json(target / 'batch.json', batch)
    (target / 'batch.md').write_text(render(batch), encoding='utf-8')
    print(f"{target / 'batch.md'}\n本批待处理 {len(pending)}；未解释池 {len(pool)}")
    return 0


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def secret_paths(value, path, context):
    if isinstance(value, str):
        return [path] if f.has_secret(value, context) else []
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            # Keys can themselves contain user-provided secrets; never echo them.
            if f.has_secret(str(key), context):
                result.append(path + '.<key>')
            else:
                result.extend(secret_paths(item, path + '.' + str(key), context))
        return result
    if isinstance(value, list):
        return [hit for i, item in enumerate(value) for hit in secret_paths(item, f'{path}[{i}]', context)]
    return []


def local_hypothesis(h, records):
    explains = h.get('explains')
    return (not isinstance(explains, list) or not explains or
            any(not isinstance(rid, str) or not records.get(rid, {}).get('owned') for rid in explains))


def validate(data, batch=None, existing=None, local_existing=None, available_records=None, *, context=None):
    errors = []
    if not isinstance(data, dict):
        return ['decisions 必须为对象']
    keys = ('issues', 'project_issues') if batch is None else ('hypotheses', 'records', 'issues', 'project_issues')
    groups = {}
    for key in keys:
        value = data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            errors.append(f'{key} 必须为对象列表')
            value = []
        groups[key] = value
    if batch is not None:
        if not nonempty(data.get('self_cause_considered')):
            errors.append('self_cause_considered 必须为非空字符串')
        ids = {r['id'] for r in batch['pending'] + batch['pool']}
        record_ids = {r['id'] for r in batch['records']}
        known = set(existing or {}) | set(local_existing or {}) | {h['id'] for h in groups['hypotheses'] if nonempty(h.get('id'))}
        for h in groups['hypotheses']:
            for key in ('id', 'title', 'mechanism'):
                if not nonempty(h.get(key)):
                    errors.append(f'假设 {key} 必须为非空字符串')
            for key in ('explains', 'predicts', 'refuted_by'):
                if not isinstance(h.get(key), list) or not h[key] or not all(nonempty(x) for x in h[key]):
                    errors.append(f'假设 {key} 必须为非空字符串列表')
            if isinstance(h.get('explains'), list):
                for rid in h['explains']:
                    if not isinstance(rid, str) or rid not in record_ids:
                        errors.append('explains 引用不在本批')
            if not isinstance(h.get('status'), str) or h['status'] not in HYP_STATUSES:
                errors.append('假设 status 不合法')
            if not isinstance(h.get('layer'), str) or h['layer'] not in LAYERS:
                errors.append('假设 layer 不合法')
            if h.get('status') in ('试修', '验证中'):
                if not nonempty(h.get('probe')):
                    errors.append('试修/验证中必须有非空 probe')
                if not isinstance(h.get('expect_down'), list) or not h['expect_down'] or not all(nonempty(x) for x in h['expect_down']):
                    errors.append('试修/验证中必须有非空 expect_down')
                if type(h.get('window_days')) is not int or h['window_days'] <= 0:
                    errors.append('window_days 必须为正整数')
        for r in groups['records']:
            if not isinstance(r.get('id'), str) or r['id'] not in ids:
                errors.append('记录 id 不在本批')
            if not isinstance(r.get('status'), str) or r['status'] not in STATUSES:
                errors.append('记录 status 不合法')
            if (r.get('status') == 'explained' or r.get('hypothesis') not in (None, '')) and (
                    not isinstance(r.get('hypothesis'), str) or r['hypothesis'] not in known):
                errors.append('记录必须引用存在的 hypothesis')
    if context is None:
        context = f.build_scan_context()
    records = {r['id']: r for r in batch['records']} if batch is not None else {}
    hub_ids = set(existing or {})
    for i, h in enumerate(groups.get('hypotheses', [])):
        if local_hypothesis(h, records):
            if isinstance(h.get('id'), str):
                hub_ids.discard(h['id'])
        else:
            if isinstance(h.get('id'), str):
                hub_ids.add(h['id'])
            # explains contains record IDs already validated against this batch.
            scanned = {key: value for key, value in h.items() if key != 'explains'}
            errors.extend('密钥特征：' + hit for hit in secret_paths(scanned, f'hypotheses[{i}]', context))
    for i, r in enumerate(groups.get('records', [])):
        if records.get(r.get('id') if isinstance(r.get('id'), str) else '', {}).get('owned'):
            for key in ('note', 'hypothesis'):
                errors.extend('密钥特征：' + hit for hit in secret_paths(r.get(key, ''), f'records[{i}].{key}', context))
    for issue in groups['issues']:
        if not isinstance(issue.get('hypothesis'), str) or issue['hypothesis'] not in hub_ids:
            errors.append('issue hypothesis 必须引用 hub 假设')
        for key in ('key', 'title', 'body'):
            if not nonempty(issue.get(key)):
                errors.append(f'issue {key} 必须非空')
        if not isinstance(issue.get('labels'), list) or not all(isinstance(x, str) for x in issue['labels']):
            errors.append('issue labels 必须为字符串列表')
        if isinstance(issue.get('key'), str) and ('\n' in issue['key'] or '\r' in issue['key']):
            errors.append('issue key 不得换行')
        for key in ('key', 'title', 'body', 'labels'):
            errors.extend('密钥特征：' + hit for hit in secret_paths(issue.get(key), f"issues[{groups['issues'].index(issue)}].{key}", context))
    eligible = {r['id']: r for r in batch['pending'] + batch['pool']} if batch is not None else {}
    if batch is None and groups['project_issues']:
        if available_records is None:
            with f.hub_read() as hub:
                available_records = project_records(hub)
        eligible = {r['id']: r for r in available_records}
    for i, issue in enumerate(groups['project_issues']):
        for key in ('key', 'repo', 'title', 'body'):
            if not nonempty(issue.get(key)):
                errors.append(f'project_issue {key} 必须非空')
        repo = issue.get('repo')
        valid_repo = isinstance(repo, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
        if not valid_repo:
            errors.append('project_issue repo 格式必须为 owner/name')
        if isinstance(issue.get('key'), str) and any(c in issue['key'] for c in '\r\n'):
            errors.append('project_issue key 不得换行')
        if not isinstance(issue.get('labels'), list) or not all(isinstance(x, str) for x in issue['labels']):
            errors.append('project_issue labels 必须为字符串列表')
        refs = issue.get('records')
        if not isinstance(refs, list) or not refs or not all(nonempty(x) for x in refs):
            errors.append('project_issue records 必须为非空字符串列表')
        else:
            for rid in refs:
                record = eligible.get(rid)
                if record is None:
                    errors.append('project_issue 记录 id 不在本批 pending 或 pool' if batch is not None
                                  else 'project_issue 记录 id 不存在')
                    continue
                if record.get('owned') is not True:
                    errors.append('project_issue 记录必须 owned=true')
                if batch is not None and any(r.get('id') == rid and r.get('status') != 'unexplained' for r in groups['records']):
                    errors.append('project_issue 记录 status 必须为 unexplained')
        for key in ('key', 'title', 'body', 'labels'):
            errors.extend('密钥特征：' + hit for hit in secret_paths(issue.get(key), f'project_issues[{i}].{key}', context))
    return errors


def persist_issue(mapping_path, mapping, mapping_key, repo, title, body, labels, marker):
    """查映射、按完整 aiw-key 行搜索、必要时创建，并立即落盘编号；调用方须持有 issues.lock。"""
    repo = canonical_repo(repo)
    mapping_key = canonical_key(mapping_key)
    normalized = {canonical_key(k): v for k, v in mapping.items()}
    mapping.clear()
    mapping.update(normalized)
    if mapping_key in mapping:
        return mapping[mapping_key]
    found = json.loads(f._command(['gh', 'issue', 'list', '--repo', repo, '--state', 'all',
                                  '--search', marker + ' in:body', '--json', 'number,title,body']))
    match = next((row for row in found if marker in [canonical_key(line) for line in (row.get('body') or '').splitlines()]), None)
    if match is None:
        from repo_aliases import ALIASES
        for old, new in ALIASES.items():
            legacy_marker = marker.replace(new.lower(), old.lower())
            if legacy_marker == marker:
                continue
            legacy = json.loads(f._command(['gh', 'issue', 'list', '--repo', repo, '--state', 'all',
                                           '--search', legacy_marker + ' in:body', '--json', 'number,title,body']))
            match = next((row for row in legacy if marker in [canonical_key(line) for line in (row.get('body') or '').splitlines()]), None)
            if match is not None:
                break
    if match:
        mapping[mapping_key] = match['number']
    else:
        for label in labels:
            try:
                f._command(['gh', 'label', 'create', label, '--repo', repo, '--force'])
            except Exception:
                pass
        command = ['gh', 'issue', 'create', '--repo', repo, '--title', title, '--body', body]
        for label in labels:
            command.extend(['--label', label])
        result = f._command(command)
        number = re.search(r'/issues/(\d+)\s*$', result)
        if not number:
            raise ValueError('issue create 未返回编号')
        mapping[mapping_key] = int(number[1])
    f._atomic_json(mapping_path, mapping)
    return mapping[mapping_key]


def issues(data):
    f.require_private_sync()
    with (folder() / 'issues.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with f.hub_read() as hub:
            existing = read(hub / 'state/hypotheses.json', {}) if hub is not None else {}
            available_records = project_records(hub) if isinstance(data, dict) and data.get('project_issues') else []
        errors = validate(data, existing=existing, available_records=available_records)
        if errors:
            print('\n'.join(errors), file=sys.stderr)
            return 2
        mapping_path = folder() / 'issues.json'
        mapping = read(mapping_path, {})
        repo = canonical_repo(f.config()['hub'])
        # Bare legacy keys have no repository identity. Only migrate with provenance.
        legacy_hub = read(folder() / 'sync.json', {}).get('visibility_hub')
        mapping = {(f'{repo}#{key}' if '#' not in key else canonical_key(key)): value
                   for key, value in mapping.items() if '#' in key or canonical_repo(legacy_hub) == repo}
        if mapping != read(mapping_path, {}):
            f._atomic_json(mapping_path, mapping)
        failed = False
        for issue in data.get('issues', []):
            try:
                key = issue['key']
                mapping_key = f'{repo}#{key}'
                if mapping_key in mapping:
                    print(f"issue {key} 已存在：#{mapping[mapping_key]}")
                    continue
                marker = 'aiw-key: ' + key
                persist_issue(mapping_path, mapping, mapping_key, repo, issue['title'],
                              issue['body'] + '\n\n' + marker, issue['labels'], marker)
                print(f"issue {key}: #{mapping[mapping_key]}")
            except Exception as error:
                failed = True
                print(f"issue 创建失败：{error}；请用 aiw triage issues <decisions.json> 重试", file=sys.stderr)
        project_path = folder() / 'project-issues.json'
        projects = read(project_path, {})
        hub_checked = False
        hub_error = None
        for issue in data.get('project_issues', []):
            try:
                project = canonical_repo(issue['repo']).lower()
                name = canonical_repo(issue['repo']).split('/')[1]
                target = repo
                reason = ''
                try:
                    info = json.loads(f._command(['gh', 'repo', 'view', issue['repo'], '--json',
                                                  'visibility,hasIssuesEnabled,nameWithOwner']))
                    canonical = canonical_repo(info['nameWithOwner'])
                    if not isinstance(canonical, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', canonical):
                        raise ValueError('仓库规范名无效')
                    project = canonical.lower()
                    name = canonical.split('/')[1]
                    if info.get('visibility') != 'PRIVATE':
                        reason = '目标仓库非 PRIVATE'
                    elif info.get('hasIssuesEnabled') is not True:
                        reason = '目标仓库 issues 未开启'
                    else:
                        target = canonical
                except Exception:
                    reason = '目标仓库查询失败'
                if reason:
                    print(f"project_issue {issue['key']}：{reason}，改开在集中仓库 {repo}")
                if reason or canonical_repo(issue['repo']).lower() == repo.lower() or target.lower() == repo.lower():
                    if not hub_checked:
                        hub_checked = True
                        try:
                            visibility = json.loads(f._command(['gh', 'repo', 'view', repo, '--json', 'visibility']))
                            if visibility.get('visibility') != 'PRIVATE':
                                raise ValueError('visibility 必须为 PRIVATE')
                        except Exception as error:
                            hub_error = f'集中仓库 {repo} 当前可见性检查失败：{error}'
                    if hub_error:
                        raise ValueError(hub_error)
                key = f"project:{project}:{issue['key']}"
                mapping_key = f'{target.lower()}#{key}'
                marker = 'aiw-key: ' + key
                labels = issue['labels'] + ([f'project:{name}'] if reason else [])
                title = (f'[{name}] ' if reason else '') + issue['title']
                body = issue['body'] + '\n\n' + marker + '\naiw-records: ' + ', '.join(issue['records'])
                persist_issue(mapping_path, mapping, mapping_key, target, title, body, labels, marker)
                projects[mapping_key] = dict(target=target, number=mapping[mapping_key], records=issue['records'])
                f._atomic_json(project_path, projects)
                print(f"project_issue {issue['key']}: {target}#{mapping[mapping_key]}")
            except Exception as error:
                failed = True
                print(f"project_issue 创建失败：{error}；请用 aiw triage issues <decisions.json> 重试", file=sys.stderr)
        return 1 if failed else 0


def read_local_hypotheses():
    hypotheses = read(folder() / 'local-hypotheses.json', {})
    for row in f._rows(folder() / 'local-hypotheses-log.jsonl'):
        if not isinstance(row.get('id'), str):
            continue
        if row.get('deleted') is True:
            hypotheses.pop(row['id'], None)
        elif isinstance(row.get('after'), dict) and isinstance(row.get('id'), str):
            hypotheses[row['id']] = row['after']
    return hypotheses


def write_hypotheses(base, name, rows, stamp, lock):
    hypotheses = read_local_hypotheses() if name == 'local-hypotheses' else read(base / (name + '.json'), {})
    for h in rows:
        before = hypotheses.get(h['id'])
        after = dict(h, created=before['created'] if before else stamp, updated=stamp)
        append(base / (name + '-log.jsonl'), [dict(ts=stamp, triage_id=lock['triage_id'],
                                                id=h['id'], before=before, after=after)])
        hypotheses[h['id']] = after
    f._atomic_json(base / (name + '.json'), hypotheses)


def apply(data):
    f.require_private_sync()
    current = latest()
    if current is None:
        return 64
    path, lock = current
    batch = read(path.parent / 'batch.json')
    with f.hub_read() as hub:
        existing = read(hub / 'state/hypotheses.json', {}) if hub is not None else {}
        if hub is not None:
            require_lock(hub, lock)
    local_existing = read_local_hypotheses()
    errors = validate(data, batch, existing, local_existing)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 2
    stamp = now().isoformat()
    records = {r['id']: r for r in batch['pending'] + batch['pool']}
    for issue in data.get('project_issues', []):
        for rid in issue['records']:
            record_repo = canonical_repo(records[rid].get('repo'))
            if (not isinstance(record_repo, str) or
                    record_repo.rsplit('/', 1)[-1].lower() != canonical_repo(issue['repo']).split('/')[1].lower()):
                print(f"提示：project_issue 记录 {rid} 的记录仓库 {record_repo} 与目标仓库 {issue['repo']} 仓库名不一致")
    statuses = [dict(id=r['id'], status=r['status'], hypothesis=r.get('hypothesis'), note=r.get('note', ''),
                     triage_id=lock['triage_id'], ts=stamp) for r in data.get('records', [])]
    all_records = {r['id']: r for r in batch['records']}
    local_hypotheses = [h for h in data.get('hypotheses', []) if local_hypothesis(h, all_records)]
    hub_hypotheses = [h for h in data.get('hypotheses', []) if not local_hypothesis(h, all_records)]
    promoted = {h['id'] for h in hub_hypotheses}
    local_ids = (set(local_existing) | {h['id'] for h in local_hypotheses}) - promoted
    append(folder() / 'local-status.jsonl', [r for r in statuses if not records[r['id']].get('owned')])
    write_hypotheses(folder(), 'local-hypotheses', local_hypotheses, stamp, lock)
    def mutate(hub):
        require_lock(hub, lock)
        state = hub / 'state'
        write_hypotheses(state, 'hypotheses', hub_hypotheses, stamp, lock)
        # A hypothesis becoming local must no longer be offered as a hub hypothesis.
        hypotheses = read(state / 'hypotheses.json', {})
        for h in local_hypotheses:
            hypotheses.pop(h['id'], None)
        f._atomic_json(state / 'hypotheses.json', hypotheses)
        append(state / 'status.jsonl', [dict(r, hypothesis=None) if r.get('hypothesis') in local_ids else r
                                      for r in statuses if records[r['id']].get('owned')])
        append(state / 'triages.jsonl', [dict(ts=stamp, triage_id=lock['triage_id'], host=host(),
                                             records=len(statuses), hypotheses=len(data.get('hypotheses', [])),
                                             issues=len(data.get('issues', [])))])
        (state / 'triage.lock').unlink()
    rc = transaction(mutate, f"triage apply: {lock['triage_id']}")
    if rc:
        return rc
    local = read_local_hypotheses()
    for hid in promoted & set(local):
        append(folder() / 'local-hypotheses-log.jsonl',
               [dict(id=hid, deleted=True, ts=stamp, triage_id=lock['triage_id'])])
        local.pop(hid)
    f._atomic_json(folder() / 'local-hypotheses.json', local)
    path.unlink()
    rc = issues(data)
    cache = f.recompute_count_cache(read(folder() / 'sync.json', {}).get('offset', 0))
    print(f"已处理 {len(statuses)} 条；假设 {len(data.get('hypotheses', []))}；open={cache['open']}")
    transferred = {rid for issue in data.get('project_issues', []) for rid in issue['records']}
    for r in data.get('records', []):
        if r['status'] == 'unexplained' and records[r['id']].get('owned') is True and r['id'] not in transferred:
            print(f"提示：未解释的自有记录 {r['id']}（{records[r['id']].get('repo')}）尚未用 project_issues 转交")
    return rc


def abort():
    f.require_private_sync()
    current = latest()
    if current is None:
        return 64
    path, lock = current
    def mutate(hub):
        require_lock(hub, lock)
        (hub / 'state/triage.lock').unlink()
    rc = transaction(mutate, f"triage abort: {lock['triage_id']}")
    if not rc:
        path.unlink()
        print('已释放 triage 锁')
    return rc


def main(argv=None):
    if os.environ.get('AIW_NESTED') == '1':
        print('执行方不得调用 aiw triage 命令', file=sys.stderr)
        return 64
    parser = f._Parser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('start')
    p.add_argument('--days', type=int, default=60)
    for name in ('apply', 'issues'):
        sub.add_parser(name).add_argument('decisions', type=Path)
    sub.add_parser('abort')
    args = parser.parse_args(argv)
    try:
        if args.command == 'start':
            if args.days <= 0:
                parser.error('--days 必须为正整数')
            return start(args.days)
        if args.command == 'abort':
            return abort()
        try:
            data = json.loads(args.decisions.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            print(f'decisions: {error}', file=sys.stderr)
            return 2
        return apply(data) if args.command == 'apply' else issues(data)
    except LockConflict as error:
        print(str(error), file=sys.stderr)
        return 75
    except Exception as error:
        print(f'triage: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
