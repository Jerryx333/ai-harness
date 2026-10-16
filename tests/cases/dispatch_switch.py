"""Global dispatch switch cases, registered by route_panel_map."""
import datetime as dt
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from support import Path, REPO, check, fake_home, json, os, subprocess, sys, tempfile


def dispatch_cli():
    import dispatch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        state = root / 'state'
        file = state / 'dispatch.json'
        # These commands must work even with broken seat configuration.
        broken = root / 'broken.json'
        broken.write_text('{bad')
        env['AIW_CONFIG'] = str(broken)
        def run(*args):
            return subprocess.run(['bash', str(REPO / 'bin/aiw'), *args], env=env,
                                  capture_output=True, text=True)
        for command in ('status', 'on', 'on'):
            r = run(command)
            check(r.returncode == 0 and r.stdout.strip() == '派单：开启', repr(r))
            check(not state.exists(), '只读状态或 on 创建了 STATE')
        for value in ('oops', '0h', '-1h', '2.5h', '999999999999999999999999999999999999d'):
            r = run('off', value)
            check(r.returncode == 2 and r.stderr and not state.exists(), repr(r))
        before = time.time()
        r = run('off', '2h')
        after = time.time()
        data = json.loads(file.read_text())
        check(r.returncode == 0 and '派单：已关闭（至 ' in r.stdout, repr(r))
        check(before + 7200 <= data['until'] <= after + 7200 and
              before <= data['since'] <= after and data['host'] and data['off'] is True, str(data))
        check(run('status').stdout == r.stdout, 'status 与 off 不一致')
        saved = file.read_bytes()
        check(run('off', 'bad').returncode == 2 and file.read_bytes() == saved, '非法时长改写了状态')
        check(run('on').stdout.strip() == '派单：开启' and not file.exists(), 'on 未删除开关')
        r = run('off', 'forever')
        check(r.returncode == 0 and '直到 aiw on' in r.stdout and
              json.loads(file.read_text())['until'] is None, repr(r))
        for value in ('30m', '3d'):
            r = run('off', value)
            seconds = {'30m': 1800, '3d': 259200}[value]
            data = json.loads(file.read_text())
            check(r.returncode == 0 and data['until'] - data['since'] == seconds, repr(r))
        r = run('off')
        check(r.returncode == 0 and json.loads(file.read_text())['until'] == dispatch.next_midnight(), repr(r))
        for content in ('{bad', '[]', '{"off": true}', '{"off": true, "until": true}',
                        '{"off": true, "until": NaN}', '{"off": true, "until": 1e300}',
                        json.dumps({'off': True, 'until': time.time() - 1})):
            file.write_text(content)
            check(run('status').stdout.strip() == '派单：开启', content)
            check(file.read_text() == content, '读取状态修改了文件')
        file.unlink()
        file.mkdir()
        check(dispatch.is_off(state) == (False, None), '读取失败应视为开启')


def dispatch_midnight():
    from dispatch import next_midnight
    wib = ZoneInfo('Asia/Jakarta')
    for now, expected in (
        (dt.datetime(2026, 10, 16, 23, 59, tzinfo=wib), dt.datetime(2026, 10, 17, tzinfo=wib)),
        (dt.datetime(2026, 12, 31, tzinfo=wib), dt.datetime(2027, 1, 1, tzinfo=wib)),
        (dt.datetime(2028, 2, 28, 12, tzinfo=wib), dt.datetime(2028, 2, 29, tzinfo=wib)),
    ):
        check(next_midnight(now) == expected.timestamp(), str(now))
    ny = ZoneInfo('America/New_York')
    now = dt.datetime(2026, 3, 8, tzinfo=ny)
    check(next_midnight(now) - now.timestamp() == 23 * 3600, '零点按日历计算，应跨夏令时')


def dispatch_hook():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        env.pop('AIW_NESTED', None)
        env['AIW_TUI_ID'] = 'dispatch-test'
        state = root / 'state'
        state.mkdir()
        transcript = root / 'transcript.jsonl'
        transcript.write_text(json.dumps({'type': 'assistant', 'message': {'usage': {'input_tokens': 220000}}}) + '\n')
        payload = {'prompt': '帮我批量给 scripts 补测试', 'cwd': str(root),
                   'session_id': 'dispatch-session', 'transcript_path': str(transcript)}
        def hook(payload=payload, override=None):
            r = subprocess.run([sys.executable, str(REPO / 'route.py'), '--hook'],
                               input=json.dumps(payload), env=dict(env, **(override or {})),
                               cwd=root, capture_output=True, text=True)
            check(r.returncode == 0, repr(r))
            return r.stdout
        opened = hook()
        check('[派单] ' in opened and '[上下文]' in opened, opened)
        # Make the buffer eligible for a hint so suppression is observable.
        settings = root / 'hint-config.json'
        settings.write_text(json.dumps({'findings': {'hint': True}}))
        env['AIW_CONFIG'] = str(settings)
        (state / 'findings').mkdir()
        (state / 'findings/count-cache.json').write_text(json.dumps({'open': 100, 'last_triage': None}))
        file = state / 'dispatch.json'
        file.write_text(json.dumps({'off': True, 'until': None}))
        closed = hook()
        check('[派单已关闭]' in closed and '直到 aiw on' in closed and
              '[派单] ' not in closed and '[缓冲区]' not in closed and '[上下文]' in closed, closed)
        log = json.loads((state / 'router-log.jsonl').read_text().splitlines()[-1])
        check(log['dispatch_off'] is True and log['rule'] == 'bulk', str(log))
        codex = hook(dict(payload, turn_id='turn'))
        check('[派单已关闭]' in codex and
              (state / 'tui/dispatch-test').read_text().splitlines()[0] == 'dispatch-session', codex)
        check(hook(override={'AIW_NESTED': '1'}) == '', '嵌套逻辑发生变化')
        direct = subprocess.run([sys.executable, str(REPO / 'route.py'), payload['prompt']],
                                env=env, cwd=root, capture_output=True, text=True)
        check(direct.returncode == 0 and json.loads(direct.stdout)['rule'] == 'bulk', repr(direct))
        for content in ('{bad', json.dumps({'off': True, 'until': 1})):
            file.write_text(content)
            check('[派单] ' in hook(), '损坏或过期状态影响钩子')
        file.unlink()
        (state / 'findings/hint.json').unlink(missing_ok=True)
        opened = hook()
        check('[派单] ' in opened and '[缓冲区]' in opened, '恢复后未派单或未提示缓冲区：' + opened)


def dispatch_tui():
    import tui
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        with patch.dict(os.environ, {'AIW_STATE': str(state)}), patch.object(tui.shutil, 'which', return_value='/fake/aiw-board'), patch.object(tui, '_mapping', return_value=['session']):
            (state / 'dispatch.json').write_text('{"off": true, "until": null}')
            check(tui._dispatch_status('') == '派单已关闭', '关闭时仍显示空闲')
            result = subprocess.CompletedProcess([], 0, stdout='write 正在运行 2m\n')
            with patch.object(tui.subprocess, 'run', return_value=result):
                line = tui._dispatch_status('valid')
                check('write 正在运行 2m' in line and '·已关闭' in line, line)
            result = subprocess.CompletedProcess([], 0, stdout='○ 派单空闲\n')
            with patch.object(tui.subprocess, 'run', return_value=result):
                check(tui._dispatch_status('valid') == '○ 派单已关闭', 'board 空闲时未显示关闭')
            with patch.object(tui.shutil, 'which', return_value=None):
                check('·已关闭' in tui._dispatch_status('valid'), '未安装 board 时丢失关闭状态')
            (state / 'dispatch.json').unlink()
            check(tui._dispatch_status('') == '○ 派单空闲', '开启时空闲显示变化')


def dispatch_doctor():
    from support import free_guard_fixture, codex_home_fixture, inprocess_sh
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = free_guard_fixture(root)
        source = codex_home_fixture(root)
        env.update(HOME=str(root), CODEX_HOME=str(source))
        state = root / 'state'
        def doctor():
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            return r.stdout
        check('派单：开启' in doctor() and not state.exists(), 'doctor 创建了 STATE')
        state.mkdir()
        file = state / 'dispatch.json'
        file.write_text('{"off": true, "until": null}')
        before = file.stat().st_mtime_ns
        check('派单：已关闭（直到 aiw on）' in doctor() and
              file.stat().st_mtime_ns == before and
              sorted(p.name for p in state.iterdir()) == ['dispatch.json'], 'doctor 改写了状态')


CASES = {fn.__name__: fn for fn in (dispatch_cli, dispatch_midnight, dispatch_hook, dispatch_tui, dispatch_doctor)}
