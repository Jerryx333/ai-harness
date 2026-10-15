#!/usr/bin/env python3
"""主会话给外包派单评分；weekly 共用只读解析。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import socket
import sys

from env_compat import getenv, session_id
from findings import _acquire_lock, complete_line

STATE = Path(getenv('AIW_STATE', Path(__file__).resolve().parent / 'state'))
RATINGS = ('ok', 'fixed', 'rework', 'discard')


def read_rows(path):
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        yield row
                except ValueError:
                    continue
    except FileNotFoundError:
        return


def run_id(value):
    path = Path(value)
    return path.parent.name if path.name == 'output.md' else path.name


def task_class(row):
    if row.get('task_class') in ('write', 'review', 'draft'):
        return row['task_class']
    if 'ro' not in row:
        return 'unknown'
    return 'write' if row['ro'] is False else 'draft' if row.get('text') else 'review'


def latest_ratings(state):
    return {r['run_id']: r for r in read_rows(state / 'seat-ratings.jsonl')
            if r.get('run_id') and r.get('rating') in RATINGS}


def main():
    if os.environ.get('AIW_NESTED') == '1':
        print('已在被外包的会话里，不得给派单评分（aiw rate）', file=sys.stderr)
        return 64
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', nargs='?')
    parser.add_argument('rating', nargs='?', choices=RATINGS)
    parser.add_argument('--note', default='')
    parser.add_argument('--pending', action='store_true')
    parser.add_argument('--days', type=int, default=2)
    parser.add_argument('--session', default=session_id())
    args = parser.parse_args()
    if args.days < 1:
        parser.error('天数须大于 0')
    executions = {run_id(r['run_dir']): r for r in read_rows(STATE / 'seat-ledger.jsonl')
                  if r.get('run_dir') and r.get('rc') is not None}
    if args.pending:
        from ai_worker import strip_auto_prompt_prefix

        if args.run or args.rating:
            parser.error('--pending 不能与评分参数同用')
        rated = latest_ratings(STATE)
        since = dt.datetime.now().astimezone() - dt.timedelta(days=args.days)
        count = 0
        for rid, row in executions.items():
            try:
                stamp = dt.datetime.fromisoformat(row['ts']).astimezone()
            except (ValueError, KeyError, TypeError):
                continue
            if (row.get('kind') != 'ok' or row.get('freecheck') or rid in rated or stamp < since
                    or (args.session and row.get('session') != args.session)):
                continue
            try:
                prompt = (Path(row['run_dir']) / 'task.txt').read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                prompt = str(row.get('prompt', ''))
            prompt = ' '.join(strip_auto_prompt_prefix(prompt).split())[:100]
            print(f"{rid}\t{row.get('actual')}\t{task_class(row)}\t{row['ts']}\t{prompt}")
            count += 1
        if not count:
            print('无待评分派单')
        return 0
    if not args.run or not args.rating:
        parser.error('需要 <run> ok|fixed|rework|discard，或 --pending')
    rid = run_id(args.run)
    row = executions.get(rid)
    if row is None:
        print(f'找不到派单：{rid}', file=sys.stderr)
        return 2
    record = dict(ts=dt.datetime.now().astimezone().isoformat(), host=socket.gethostname(),
                  session=session_id(), run_id=rid, seat=row.get('seat'), actual=row.get('actual'),
                  role=row.get('role'), task_class=task_class(row), rating=args.rating, note=args.note)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / 'seat-ratings.lock').open('a') as lock:
            if not _acquire_lock(lock, 5):
                raise OSError('评分文件锁等待超时')
            with (STATE / 'seat-ratings.jsonl').open('a+b') as stream:
                complete_line(stream)
                stream.write((json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
    except OSError as error:
        print(f'评分写入失败：{error}', file=sys.stderr)
        return 2
    print(f"已评分：{rid} · {row.get('actual')} · {args.rating}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
