"""Offline agent-use workspace seat contracts."""
from support import (Path, REPO, check, fake_agent_use, git_template, json, os,
                     sys, tempfile, worker_call, inprocess_sh as sh)
from unittest.mock import patch


def fixture(t, **extra):
    local = t / 'local.json'
    local.write_text(json.dumps({'seats': {
        'workspace': {'kind': 'agent_use', 'provider': 'generic', 'workspace': True},
        'text': {'kind': 'agent_use', 'provider': 'text', 'require_no_tools': True}},
        'roles': {'default': {'write': ['workspace'], 'review': ['workspace'], 'draft': ['workspace']}},
        'fallback': {'workspace': [], 'codex': ['text', 'workspace']}}))
    cmd, env, wd = worker_call(t, AIW_CONFIG=str(local), AIW_STATE=str(t / 'state'),
                               AGENT_USE_HOME=str(fake_agent_use(t)),
                               FAKE_AU_LOG=str(t / 'calls'), FAKE_AU_WORKSPACE_LOG='1', AIW_MAIN='', **extra)
    cmd[2] = 'workspace'
    return cmd, env, wd


def calls(t):
    return [json.loads(line) for line in (t / 'calls').read_text().splitlines()]


def workspace_role_write():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_AU_GENERIC='write')
        git_template(wd, {'tracked': 'initial'})
        cmd[2] = 'write'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '角色 write → 座位 workspace' in r.stderr, r.stderr)
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['changed_files'] == 2 and row['actual'] == 'workspace' and row['ro'] is False, row)
        check((Path(row['run_dir']) / 'baseline-status.txt').exists(), row)
        check('tracked' in r.stderr and 'created' in r.stderr, r.stderr)
        call = calls(t)[0]
        check(call['workdir'] == str(wd.resolve()) and call['access'] == 'rw' and not call['no_tools'], call)


def workspace_access_ro_rw():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        for role, readonly in [('workspace', True), ('review', True), ('workspace', False)]:
            cmd[2] = role
            flags = ['--ro'] if readonly and role == 'workspace' else []
            r = sh(cmd[:-1] + flags + ['--net', '--attach', 'code.txt', '--no-findings'], env=env)
            check(r.returncode == 0 and '--net 不生效' in r.stderr, r.stderr)
            run_dir = sorted((t / 'state/runs').iterdir(), key=lambda p: p.stat().st_mtime_ns)[-1]
            prompt = (run_dir / 'task.txt').read_text()
            check('code.txt |' in prompt and 'repository material' not in prompt, prompt)
            call = calls(t)[-1]; argv = call['argv']
            check(call['access'] == ('ro' if readonly else 'rw') and call['workdir'] == str(wd.resolve()), call)
            check('--workdir' in argv and '--access' in argv and '--require-no-tools' not in argv, call)
        import ai_worker as w
        with patch.object(w, 'SEATS', {'seats': {'workspace': {'kind': 'agent_use', 'workspace': True, 'ro_only': True}}}):
            check(w.role_candidate_reason('write', 'workspace') is not None, 'ro_only allowed write')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace']['ro_only'] = True; local.write_text(json.dumps(cfg))
        r = sh(cmd[:-1] + ['--no-findings'], env=env)
        check(r.returncode == 0 and calls(t)[-1]['access'] == 'ro', r.stderr)
        run_dir = sorted((t / 'state/runs').iterdir(), key=lambda p: p.stat().st_mtime_ns)[-1]
        prompt = (run_dir / 'task.txt').read_text()
        check('code.txt |' not in prompt, prompt)  # last call has no attachments


def workspace_text_unchanged():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t); cmd[2] = 'text'
        r = sh(cmd, env=env)
        check(r.returncode == 0, r.stderr)
        call = calls(t)[0]
        check(call['no_tools'] and call['access'] == 'none' and call['workdir'] is None, call)
        check('--workdir' not in call['argv'] and '--access' not in call['argv'], call)
        r = sh(cmd[:-1], env=env)
        check(r.returncode == 64 and '只生成文本' in r.stderr and len(calls(t)) == 1, r.stderr)
        import ai_worker as w
        with patch.object(w, 'SEATS', {'seats': {'text': {'kind': 'agent_use'}}}):
            result = w._run_seat('text', 'test', str(wd), 10, False)
            check(result['kind'] == 'invalid_argv', result)


def assert_text_call(call):
    check(call['no_tools'] and call['access'] == 'none' and call['workdir'] is None, call)
    check('--require-no-tools' in call['argv'] and '--workdir' not in call['argv']
          and '--access' not in call['argv'], call)


def workspace_draft_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        cmd[2] = 'draft'; cmd.remove('--ro')
        r = sh(cmd + ['--attach', 'code.txt', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        assert_text_call(calls(t)[0])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['text'] is True and row['ro'] is True, row)
        prompt = (Path(row['run_dir']) / 'task.txt').read_text()
        check('repository material' in prompt, prompt)


def workspace_explicit_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t); cmd.remove('--ro')
        r = sh(cmd + ['--text', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        assert_text_call(calls(t)[0])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['text'] is True and row['ro'] is True, row)
        # Text does not add a tool restriction to a non-workspace seat.
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['text']['require_no_tools'] = False; local.write_text(json.dumps(cfg))
        cmd[2] = 'text'
        r = sh(cmd + ['--text'], env=env)
        check(r.returncode == 0 and not calls(t)[-1]['no_tools'], r.stderr)
        cmd[2] = 'codex'; marker = t / 'cli-argv'; env['FAKE_CODEX_ARGS'] = str(marker)
        r = sh(cmd + ['--text'], env=env)
        check(r.returncode == 0 and 'read-only' in marker.read_text()
              and '--require-no-tools' not in marker.read_text(), r.stderr)


def workspace_text_net_conflict():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t); cmd.remove('--ro')
        for seat, flags in [('workspace', ['--text']), ('draft', []),
                            ('text', ['--text']), ('codex', ['--text'])]:
            cmd[2] = seat
            r = sh(cmd + flags + ['--net'], env=env)
            check(r.returncode == 64 and '不能与 --ro' in r.stderr, r.stderr)
        check(not (t / 'calls').exists(), 'conflicting dispatch executed')


def workspace_text_fallback():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t, FAKE_CODEX='quota', FAKE_AU_TEXT='quota')
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace2'] = dict(cfg['seats']['workspace'], provider='generic2')
        cfg['fallback']['codex'] = ['text', 'workspace', 'workspace2']
        local.write_text(json.dumps(cfg)); env['FAKE_AU_GENERIC'] = 'quota'
        cmd[2] = 'codex'; cmd.remove('--ro')
        r = sh(cmd + ['--text', '--no-findings'], env=env)
        check(r.returncode == 0, r.stderr)
        check([c['order'] for c in calls(t)] == ['text', 'generic', 'generic2'], calls(t))
        for call in calls(t):
            assert_text_call(call)
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 4 and all(row['text'] is True and row['ro'] is True for row in rows), rows)


def workspace_config_conflict():
    import seats_config as c
    cfg = c.load_public()
    cfg['seats']['workspace'] = {'kind': 'agent_use', 'provider': 'generic', 'workspace': True, 'require_no_tools': True}
    check(any('workspace 与 require_no_tools' in e for e in c.validate(cfg)), c.validate(cfg))
    cfg['seats']['workspace']['require_no_tools'] = False
    for value in ('true', 1, None):
        cfg['seats']['workspace']['workspace'] = value
        check(any('workspace 必须是布尔值' in e for e in c.validate(cfg)), value)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, _ = fixture(t)
        local = Path(env['AIW_CONFIG']); cfg = json.loads(local.read_text())
        cfg['seats']['workspace']['require_no_tools'] = True; local.write_text(json.dumps(cfg))
        r = sh(cmd, env=env)
        check(r.returncode != 0 and 'workspace 与 require_no_tools' in r.stderr and not (t / 'calls').exists(), r.stderr)
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check(r.returncode != 0 and 'workspace 与 require_no_tools' in r.stdout + r.stderr, r.stdout + r.stderr)


def workspace_fallback_skips_text():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t, FAKE_CODEX='quota', FAKE_AU_GENERIC='write')
        git_template(wd, {'tracked': 'initial'}); cmd[2] = 'codex'; cmd.remove('--ro')
        r = sh(cmd + ['--no-findings'], env=env)
        check(r.returncode == 0 and '[text] 顶替跳过：文本座位不能改文件' in r.stderr, r.stderr)
        check([call['order'] for call in calls(t)] == ['generic'] and calls(t)[0]['access'] == 'rw', calls(t))
        rows = [json.loads(line) for line in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(rows[-1]['actual'] == 'workspace' and rows[-1]['changed_files'] == 2, rows)


def workspace_panel_map():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); cmd, env, wd = fixture(t)
        (wd / 'code.txt').write_text('repository material')
        r = sh([sys.executable, str(REPO / 'panel.py'), str(wd), '10', cmd[5],
                'workspace', 'text', '--attach', 'code.txt', '--full'], env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        check(all(c['access'] == ('ro' if c['order'] == 'generic' else 'none') for c in calls(t)), calls(t))
        prompt = next((t / 'state/panels').glob('*/prompt.md')).read_text()
        check('repository material' in prompt and '不要假设还能读到其它文件' not in prompt, prompt)
        r = sh([sys.executable, str(REPO / 'map.py'), str(wd), '10', cmd[5],
                'code.txt', '--seat', 'workspace', '--jobs', '1'], env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        assert_text_call(calls(t)[-1])
        row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
        check(row['via'] == 'map' and row['text'] is True and row['ro'] is True, row)
        check('repository material' in (Path(row['run_dir']) / 'task.txt').read_text(), row)
        r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
        check('可读写仓库' in r.stdout and 'seats.workspace.workspace' not in r.stdout, r.stdout + r.stderr)


def workspace_findings_context():
    import findings
    import snapshot
    cfg = {'seats': {'text': {'kind': 'agent_use'},
                     'workspace': {'kind': 'agent_use', 'workspace': True}}}
    with tempfile.TemporaryDirectory() as td, patch.object(findings, '_settings', return_value=cfg):
        check(not findings.common_fields(td, seat='text', actual='workspace')['partial_context'], 'workspace partial context')
        check(findings.common_fields(td, seat='workspace', actual='text')['partial_context'], 'text full context')
    check(snapshot.seat_shape({'kind': 'agent_use', 'provider': 'generic', 'workspace': True})['workspace'] is True, 'snapshot missing workspace')
    check('workspace' not in snapshot.seat_shape({'kind': 'agent_use', 'provider': 'text'}), 'text snapshot changed')


CASES = {fn.__name__: fn for fn in (workspace_role_write, workspace_access_ro_rw,
    workspace_text_unchanged, workspace_draft_text, workspace_explicit_text,
    workspace_text_net_conflict, workspace_text_fallback, workspace_config_conflict, workspace_fallback_skips_text,
    workspace_panel_map, workspace_findings_context)}
