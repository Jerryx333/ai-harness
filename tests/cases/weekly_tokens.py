"""weekly tokens regression cases."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    json,
    os,
    inprocess_sh as sh,
    sys,
    tempfile,
    token_rollout,
    token_worker,
    weekly_repo_fixture,
    worker_call,
)


def codex_tokens_rollout():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); sid, path = token_rollout(t)
        def event(info):
            return json.dumps(dict(type='event_msg', payload=dict(type='token_count', info=info)))
        usage = dict(input_tokens=1000, cached_input_tokens=600, cache_write_input_tokens=20,
                     output_tokens=100, reasoning_output_tokens=30, total_tokens=1100)
        path.write_text('unrelated non-json line\n' + event(None) + '\n' +
                        event(dict(total_token_usage=dict(usage, input_tokens=100))) + '\n' +
                        event(dict(total_token_usage=usage)) + '\n' + event(None) + '\n')
        expected = dict(input=1000, cached=600, cache_write=20, output=100, reasoning=30,
                        calls=2, source='rollout')
        for seat in ('codex', 'codex-sol'):
            result, row = token_worker(t, f'session id: {sid}\ntokens used\n500\n', seat)
            check(result.returncode == 0 and row['tokens'] == expected and 'tokens_null_reason' not in row, str(row))
        result, row = token_worker(t, '', 'qoder')
        check(result.returncode == 0 and 'tokens' not in row and 'tokens_null_reason' not in row, str(row))
        (t / 'state/disabled.json').write_text(json.dumps({'codex': {'reason': 'test'}}))
        result, row = token_worker(t, f'session id: {sid}\n')
        check(row['kind'].startswith('skipped_') and row['rc'] is None and 'tokens' not in row and 'tokens_null_reason' not in row, str(row))
        # Failed actual attempts also retain rollout measurements.
        script = "import ai_worker as w; w.run_seat=lambda *a: dict(out='', err='session id: " + sid + "\\n', kind='other', rc=7, secs=0); raise SystemExit(w.main())"
        (t / 'state/disabled.json').unlink()
        result, row = token_worker(t, '', script=script)
        check(row['rc'] == 7 and row['tokens'] == expected, str(row))

        # A later filename with smaller totals must not replace the larger rollout.
        second = path.with_name('rollout-zz-' + sid + '.jsonl')
        path.write_text(event(dict(total_token_usage=dict(input_tokens=1000, total_tokens=1000))) + '\n')
        second.write_text((event(dict(total_token_usage=dict(input_tokens=100, total_tokens=100))) + '\n') * 3)
        result, row = token_worker(t, f'session id: {sid}\n')
        check(row['tokens']['input'] == 1000 and row['tokens']['calls'] == 1, str(row))

        # Isolated worker CODEX_HOME rollouts also live under the state directory.
        state_path = t / 'state/codex-home/sessions/2020/01/01' / path.name
        state_path.parent.mkdir(parents=True)
        state_path.write_text(event(dict(total_token_usage=usage)) + '\n')
        path.unlink()
        second.unlink()
        result, row = token_worker(t, f'session id: {sid}\n')
        check(result.returncode == 0 and row['tokens'] == dict(expected, calls=1)
              and 'tokens_null_reason' not in row, str(row))



def codex_tokens_fallback():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); sid, path = token_rollout(t)
        stderr = f'session id: {sid}\ntokens used\n24,324\n'
        result, row = token_worker(t, stderr)
        check(result.returncode == 0 and row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
              and row['tokens_null_reason'] == 'rollout_missing', str(row))
        for err in ('', 'session id: ../../bad\n'):
            result, row = token_worker(t, err)
            check(result.returncode == 0 and row['tokens'] is None and row['tokens_null_reason'] == 'no_session_id', str(row))
        path.write_text('{"token_count": broken\n')
        for err in (stderr, f'session id: {sid}\n'):
            result, row = token_worker(t, err)
            check(result.returncode == 0 and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
            check(row['tokens'] == (dict(source='stderr', uncached_plus_output=24324) if err == stderr else None), str(row))
        path.write_bytes(b'{"token_count": \xff}\n')
        result, row = token_worker(t, stderr)
        check(row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
              and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
        path.write_text('{"token_count": broken\n')
        path.chmod(0)
        try:
            if os.geteuid() != 0:
                result, row = token_worker(t, stderr)
                check(row['tokens'] == dict(source='stderr', uncached_plus_output=24324)
                      and row['tokens_null_reason'] == 'rollout_unparsed', str(row))
        finally:
            path.chmod(0o600)
        path.write_text('{"type":"event_msg","payload":{"type":"token_count","info":null}}\n')
        # Inject at run_seat, after configuration/state parsing, so only token parsing raises.
        script = "import ai_worker as w; original=w.run_seat\ndef run(*a):\n r=original(*a); w.json.loads=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('test')); return r\nw.run_seat=run\nraise SystemExit(w.main())"
        result, row = token_worker(t, stderr, script=script)
        check(result.returncode == 0 and row['rc'] == 0 and row['tokens'] is None and row['tokens_null_reason'] == 'error', str(row) + result.stderr)

        path.write_text(json.dumps(dict(type='event_msg', payload=dict(type='token_count',
                        info=dict(total_token_usage=dict(input_tokens=900, cached_input_tokens=800, output_tokens=10))))) + '\n')
        # Inject a timeout carrying captured stderr; token extraction must survive timeout normalization.
        script = ("import ai_worker as w; import os; from pathlib import Path\n"
                  "def run(*a, **k):\n"
                  " raise w.subprocess.TimeoutExpired(a[0], a[1], output='', "
                  "stderr=Path(os.environ['FAKE_CODEX_STDERR']).read_text())\n"
                  "w.run_group=run\nraise SystemExit(w.main())")
        result, row = token_worker(t, f'session id: {sid}\n', script=script)
        check(result.returncode == 124 and row['kind'] == 'timeout' and row['err_tail'] == 'timeout'
              and row['tokens']['source'] == 'rollout' and row['tokens']['input'] == 900, str(row) + result.stderr)



def tokens_report():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); projects = t / 'projects'; project = projects / 'fake-project'
        project.mkdir(parents=True)
        now = dt.datetime.now(dt.timezone.utc)
        sid, other = '11111111-fake-session', '22222222-fake-session'
        sentinel = 'PRIVATE_BODY_SENTINEL'
        def assistant(mid, ctx, offset=1, **kw):
            return dict(type='assistant', timestamp=(now - dt.timedelta(hours=offset)).isoformat(),
                        sessionId=sid, cwd='/fake/example', message=dict(id=mid, content=sentinel,
                        usage=dict(input_tokens=100 if ctx else 0, cache_read_input_tokens=max(0, ctx-100),
                                   cache_creation_input_tokens=0, output_tokens=10 if ctx else 0)), **kw)
        first = assistant('one', 100000, 3)
        image = dict(type='user', timestamp=now.isoformat(), sessionId=sid,
                     message=dict(content=[dict(type='tool_result', content=[dict(type='image'), dict(type='text', text=sentinel)])]))
        rows = [assistant('two', 500000, 2), first, first, assistant('old', 900000, 240),
                assistant('zero', 0), dict(assistant('side', 20000, isSidechain=True), sessionId='child-fake-session'), image]
        rows[-1]['timestamp'] = (now - dt.timedelta(minutes=1)).isoformat()
        (project / (sid + '.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows) + '\n{"assistant": broken\n')
        third = assistant('three', 200000, 1); third['sessionId'] = other
        third['message'].pop('id'); third['uuid'] = 'fake-uuid'; third.pop('cwd')
        (project / (other + '.jsonl')).write_text(json.dumps(third) + '\n')
        sub = project / sid / 'subagents'; sub.mkdir(parents=True)
        (sub / 'agent-fake.jsonl').write_text(json.dumps(assistant('child', 30000)) + '\n')
        state = t / 'state'; state.mkdir()
        base = dict(ts=(now-dt.timedelta(minutes=1)).isoformat(), actual='codex', rc=0, session=sid,
                    tokens=dict(source='rollout', input=1000, cached=700, output=20))
        led = [base, dict(base, actual='codex-sol', rc=124), dict(base, actual='qoder', tokens=None),
               dict(base, session=None, tokens=dict(source='stderr')), dict(base, rc=None),
               dict(base, ts=(now-dt.timedelta(days=20)).isoformat())]
        (state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in led))
        env = dict(os.environ, AIW_CLAUDE_PROJECTS=str(projects), AIW_STATE=str(state))
        cmd = [str(REPO / 'bin/aiw'), 'tokens', '7']
        result = sh(cmd + ['--json'], env=env); check(result.returncode == 0, result.stderr)
        d = json.loads(result.stdout)
        check({'window_days', 'totals', 'weighted', 'context', 'concentration', 'sessions', 'subagents', 'dispatch'} <= set(d), str(d))
        check(d['totals']['total'] == 800030 and d['totals']['input'] == 300, str(d))
        check(d['weighted']['total'] == 80420 and d['weighted']['output'] == 150, str(d['weighted']))
        c = d['context']
        check(c['rounds'] == 3 and c['ctx_median'] == 200000 and c['ctx_p90'] == 500000, str(c))
        check(c['thresholds']['400000']['round_share'] == 1/3 and c['thresholds']['400000']['token_share'] == 500010/800030, str(c))
        check(c['fixed_estimate'] == 150000 and c['fixed_share'] == .5 and c['growth_share'] == .5, str(c))
        a = d['sessions'][0]
        check(a['session'] == sid[:8] and a['screenshots'] == 1 and a['dispatches'] == 3 and a['codex_input'] == 2000, str(a))
        check(d['sessions'][1]['project'] == 'fake-project', str(d))
        check(d['subagents']['rounds'] == 2 and d['subagents']['tokens'] == 50020 and len(d['subagents']['parents']) == 1, str(d))
        check(d['dispatch']['count'] == 4 and d['dispatch']['session_share'] == .75 and d['dispatch']['rollout']['codex']['cached'] == 700, str(d))
        check(d['malformed_lines'] == 1 and '1 行解析不了' in result.stderr, result.stderr)
        bool_state = t / 'bool-state'; bool_state.mkdir()
        bool_ledger = [dict(base, tokens=dict(source='rollout', input=True, cached=0, output=0)), base]
        (bool_state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in bool_ledger))
        bool_env = dict(env, AIW_CLAUDE_PROJECTS=str(bool_state), AIW_STATE=str(bool_state))
        bool_result = sh(cmd + ['--json'], env=bool_env)
        check(bool_result.returncode == 0, bool_result.stderr)
        bool_data = json.loads(bool_result.stdout)
        check(bool_data['malformed_lines'] == 1 and bool_data['dispatch']['count'] == 1
              and bool_data['dispatch']['rollout']['codex']['input'] == 1000, str(bool_data))
        text = sh(cmd, env=env)
        for expected in ('主会话 3 轮', 'ctx 中位 200000', '轮次 33.33%', '固定 50.00%', '增长 50.00%',
                         '600020 1 3 2000', 'codex rollout: input 1000 · cached 700 · output 20'):
            check(expected in text.stdout, text.stdout)
        check(sentinel not in result.stdout + result.stderr + text.stdout + text.stderr and sid not in result.stdout + text.stdout, 'privacy leak')
        limited = json.loads(sh(cmd + ['--json', '--top', '1'], env=env).stdout)
        check(len(limited['sessions']) == 1 and limited['totals'] == d['totals'], str(limited))
        duplicate = assistant('updated', 100)
        duplicate['message']['usage']['output_tokens'] = 1
        transcript = project / (sid + '.jsonl')
        with transcript.open('a') as stream:
            stream.write(json.dumps(duplicate) + '\n')
            duplicate['message']['usage']['output_tokens'] = 20
            stream.write(json.dumps(duplicate) + '\n')
        updated = json.loads(sh(cmd + ['--json'], env=env).stdout)
        check(updated['totals']['output'] == d['totals']['output'] + 20
              and updated['context']['rounds'] == c['rounds'] + 1, str(updated))
        empty = t / 'empty'; empty.mkdir()
        env.update(AIW_CLAUDE_PROJECTS=str(empty), AIW_STATE=str(empty))
        result = sh(cmd, env=env)
        check(result.returncode == 0 and result.stdout.strip() == '无数据', result.stdout)



def weekly_repo_section():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        weekly_repo_fixture(t)
        r = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0, r.stderr)
        section = r.stdout.split('## 按仓库\n')[1].split('\n\n')[0]
        expected = ('- owner/a: 尝试 3 · 成功 2 · 成功率 66.7% · 超时 1（33.3%） · 成功单中位耗时 3s · 写任务改动文件中位数 4\n'
                    '- /tmp/local: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 0s · 写任务改动文件中位数 0\n'
                    '- owner/b: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 0（0.0%） · 成功单中位耗时 无数据 · 写任务改动文件中位数 无数据\n'
                    '- 未记录: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 0（0.0%） · 成功单中位耗时 无数据 · 写任务改动文件中位数 无数据')
        check(section == expected, section)
        check(r.stdout.index('## 按座位成功率') < r.stdout.index('## 按仓库') < r.stdout.index('被顶替但记成功'), r.stdout)
        check('外包调用 8 次；派单判定 1 条，命中 1，逃生口 0' in r.stdout, r.stdout)
        check('- codex: 尝试 6 · 成功 3 · 成功率 50.0% · 超时 1（16.7%） · 成功单中位耗时 2s · 失败分布 timeout 1、auth 1、format 1 · direct 5 · panel 1 · map 0 · 未记录 0' in r.stdout, r.stdout)
        check('被顶替但记成功：1 次' in r.stdout and '## token（codex / codex-sol）' in r.stdout and '## 改进缓冲区' in r.stdout, r.stdout)



def weekly_json():
    import datetime as dt
    import socket
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        rows = weekly_repo_fixture(t)
        rows.append(dict(rows[0], ts=(dt.datetime.now().astimezone() - dt.timedelta(days=5)).isoformat(), repo='old', kind='other', rc=1))
        with (t / 'seat-ledger.jsonl').open('a') as f:
            f.write(json.dumps(rows[-1]) + '\n')
        env = dict(os.environ, AIW_STATE=str(t))
        command = [sys.executable, str(REPO / 'weekly.py')]
        r = sh(command + ['--json'], env=env)
        check(r.returncode == 0 and '有 1 行解析不了' in r.stderr, repr(r))
        data = json.loads(r.stdout)
        check(set(data) == {'host', 'days', 'since', 'generated_at', 'calls', 'router', 'kinds', 'efforts', 'seats', 'repos', 'substituted_ok', 'substitute_limits'}, str(data))
        check(data['host'] == socket.gethostname() and data['days'] == 7 and data['calls'] == 9, str(data))
        since, generated = (dt.datetime.fromisoformat(data[k]) for k in ('since', 'generated_at'))
        check(since.tzinfo is not None and generated.tzinfo is not None and generated - since == dt.timedelta(days=7), str(data))
        check(data['router'] == dict(total=1, hit=1, escape=0) and data['efforts'] == [dict(seat='codex', effort='high', n=9)], str(data))
        seat = data['seats'][0]
        check(seat == dict(actual='codex', attempts=7, ok=3, success_rate=3/7, timeouts=1, timeout_rate=1/7,
                           median_ok_secs=2, failures=dict(timeout=1, auth=1, format=1, other=1),
                           via=dict(direct=6, panel=1, map=0, unknown=0)), str(seat))
        check(data['kinds'] == dict(ok=4, timeout=1, auth=1, format=1, skipped_disabled=1, other=1), str(data['kinds']))
        check(data['substituted_ok'] == 1, str(data))
        check(data['repos'][0] == dict(repo='owner/a', key_type='repo', attempts=3, ok=2, success_rate=2/3,
                                     timeouts=1, timeout_rate=1/3, median_ok_secs=3, median_changed_files=4), str(data['repos']))
        check([(r['repo'], r['key_type']) for r in data['repos']] == [('owner/a', 'repo'), ('/tmp/local', 'workdir'), ('old', 'repo'), ('owner/b', 'repo'), (None, 'unknown')], str(data['repos']))
        text = sh(command, env=env)
        check(text.returncode == 0 and text.stdout.splitlines()[0].endswith(str(data['substitute_limits'])), text.stdout)
        check('外包调用 9 次' in text.stdout and '成功率 42.9%' in text.stdout and 'owner/a: 尝试 3 · 成功 2 · 成功率 66.7%' in text.stdout, text.stdout)
        short = sh(command + ['3', '--json'], env=env)
        short_data = json.loads(short.stdout)
        check(short.returncode == 0 and short_data['days'] == 3 and short_data['calls'] == 8 and len(short_data['repos']) == 4, str(short_data))
        for days in ('0', '-1'):
            invalid = sh(command + [days, '--json'], env=env)
            check(invalid.returncode != 0 and 'usage:' in invalid.stderr and invalid.stdout == '', repr(invalid))
        (t / 'seat-ledger.jsonl').write_text('')
        empty = json.loads(sh(command + ['--json'], env=env).stdout)
        check(empty['calls'] == 0 and empty['seats'] == [] and empty['repos'] == [], str(empty))



def weekly_tokens():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake', seat='requested', actual='codex', rc=0, kind='ok', secs=1)
        usage = dict(input=1000, cached=600, cache_write=0, output=100, reasoning=0, calls=2, source='rollout')
        rows = [dict(row, tokens=usage), dict(row, tokens=dict(usage, input=3000, cached=1400, output=300, calls=4)),
                dict(row, tokens=dict(source='stderr', uncached_plus_output=24324)),
                dict(row, tokens=None, tokens_null_reason='no_session_id'),
                dict(row, tokens=None, tokens_null_reason='error', rc=1, kind='other'), row,
                dict(row, tokens=usage, rc=None, kind='skipped_breaker'),
                dict(row, actual='qoder', tokens=usage), dict(row, actual='codex-sol', tokens=usage)]
        def report(data):
            (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in data) + '\n')
            result = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
            check(result.returncode == 0, result.stderr)
            return result.stdout
        output = report(rows)
        section = output.split('## token（codex / codex-sol）')[1].split('## 派单规则命中')[0]
        for expected in ('codex: 总尝试 6', 'rollout 2（覆盖率 33.3%）', 'stderr 兜底 1',
                         'tokens=null 2（error 1、no_session_id 1）', '旧记录 1',
                         '2000 / 1000 / 200 / 3', '合计 input 4000、cached 2000（缓存占比 50.0%）、output 400',
                         'codex-sol: 总尝试 1', 'Claude 主会话用量见 aiw tokens'):
            check(expected in section, section)
        check('qoder' not in section, section)
        check(output.index('## 按座位成功率') < output.index('## token（codex / codex-sol）') < output.index('## 派单规则命中'), output)
        empty = report([])
        check('- codex: 无数据' in empty and '- codex-sol: 无数据' in empty, empty)
        zero = report([dict(row, tokens=dict(usage, input=0, cached=0))])
        check('缓存占比 无数据' in zero, zero)



def weekly_via():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake',
                   seat='requested', actual='qoder', rc=0, kind='ok', secs=1)
        rows = [dict(row, via='direct'), dict(row, via='panel'),
                dict(row, via='map', rc=1, kind='other'), dict(row),
                dict(row, via='direct', rc=None, kind='skipped_breaker'),
                dict(row, actual='workbuddy', via='panel'),
                dict(row, actual='antigravity', via='map'),
                dict(row, actual='skipped-only', rc=None, kind='skipped_disabled')]
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        r = sh([sys.executable, str(REPO / 'weekly.py')],
               env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0, r.stdout + r.stderr)
        section = r.stdout.split('## 按座位成功率\n')[1].split('\n\n')[0]
        expected = ('口径：按实际执行座位；不计 freecheck=true 和 skipped_*；成功与超时按 kind 判断；失败分布取前三项。\n'
                    '- qoder: 尝试 4 · 成功 3 · 成功率 75.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 other 1 · direct 1 · panel 1 · map 1 · 未记录 1\n'
                    '- antigravity: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 无 · direct 0 · panel 0 · map 1 · 未记录 0\n'
                    '- workbuddy: 尝试 1 · 成功 1 · 成功率 100.0% · 超时 0（0.0%） · 成功单中位耗时 1s · 失败分布 无 · direct 0 · panel 1 · map 0 · 未记录 0')
        check(section == expected, f'成功率/来源统计错误: {section}')
        check(r.stdout.index('## 结果分类') < r.stdout.index('## 按座位成功率'), '小节位置错误')

    for panel_dir, map_dir, expected in ((None, None, 'direct'), ('panel', None, 'panel'),
                                         (None, 'map', 'map'), ('panel', 'map', 'panel'),
                                         ('', '', 'direct'), ('', 'map', 'map')):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            cmd, env, wd = worker_call(t, AIW_STATE=str(t / 'state'))
            for key, value in (('AIW_PANEL_DIR', panel_dir), ('AIW_MAP_DIR', map_dir)):
                env.pop(key, None)
                if value is not None:
                    env[key] = value
            r = sh(cmd + ['--no-fallback'], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            ledger = t / 'state/seat-ledger.jsonl'
            last = json.loads(ledger.read_text().splitlines()[-1])
            check(last['via'] == expected and last['rc'] == 0, f'调用来源错误: {last}')
            (t / 'state/disabled.json').write_text(json.dumps({'qoder': {'reason': 'paid'}}))
            r = sh(cmd + ['--no-fallback'], env=env)
            last = json.loads(ledger.read_text().splitlines()[-1])
            check(r.returncode == 1 and last['kind'] == 'skipped_disabled'
                  and last['rc'] is None and last['via'] == expected, f'跳过来源错误: {last}')



def weekly_seat_stats():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        row = dict(ts=dt.datetime.now().astimezone().isoformat(), host='fake',
                   seat='requested', actual='qoder', rc=0, kind='ok', secs=10)
        rows = [row, dict(row, secs=30), dict(row, kind='timeout', rc=1, secs=500),
                dict(row, kind='format', rc=0, secs=1000),
                dict(row, freecheck=True, secs=9000),
                dict(row, freecheck=True, kind='timeout', rc=1),
                dict(row, actual='probe-only', freecheck=True),
                dict(row, actual='skipped-only', kind='skipped_disabled', rc=None),
                dict(row, kind='skipped_breaker', rc=1),
                dict(row, actual='failed-only', kind='timeout', rc=1),
                dict(row, actual='odd-success', rc=1)]
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        result = sh([sys.executable, str(REPO / 'weekly.py')],
                    env=dict(os.environ, AIW_STATE=str(t)))
        check(result.returncode == 0, result.stderr)
        section = result.stdout.split('## 按座位成功率\n')[1].split('\n\n')[0]
        check('- qoder: 尝试 4 · 成功 2 · 成功率 50.0% · 超时 1（25.0%）'
              ' · 成功单中位耗时 20s · 失败分布 timeout 1、format 1' in section, section)
        check('- failed-only: 尝试 1 · 成功 0 · 成功率 0.0% · 超时 1（100.0%）'
              ' · 成功单中位耗时 无数据' in section, section)
        check('- odd-success: 尝试 1 · 成功 1 · 成功率 100.0%' in section, section)
        check('probe-only' not in section and 'skipped-only' not in section and 'requested' not in section, section)
        check('不计 freecheck=true 和 skipped_*' in section, section)



def weekly_mixed_ledger():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); row = dict(host='fake', seat='qoder', actual='qoder', rc=0, kind='ok', secs=1)
        rows = [dict(row, ts=dt.datetime.now().astimezone().isoformat()), dict(row, ts=dt.datetime.now().isoformat())]
        rows += [dict(row, seat='codex', actual='codex', effort=effort, ts=rows[0]['ts']) for effort in ('high', 'high', 'low')]
        rows.append(dict(rows[-1], rc=None, kind='skipped_breaker'))
        (t / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n{bad\n')
        r = sh([sys.executable, str(REPO / 'weekly.py')], env=dict(os.environ, AIW_STATE=str(t)))
        check(r.returncode == 0 and '外包调用 5 次' in r.stdout and '有 1 行解析不了' in r.stderr, f'周报错误: {r.stdout}{r.stderr}')
        section = r.stdout.split('## 推理档位\n')[1].split('\n## 结果分类')[0]
        check(section.strip() == '- codex / high: 2\n- codex / low: 1', f'档位计数错误: {section}')



CASES = {case.__name__: case for case in (
    codex_tokens_rollout,
    codex_tokens_fallback,
    tokens_report,
    weekly_repo_section,
    weekly_json,
    weekly_tokens,
    weekly_via,
    weekly_seat_stats,
    weekly_mixed_ledger,
)}
