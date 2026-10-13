"""track snapshot regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    Path,
    REPO,
    check,
    fake_home,
    json,
    os,
    sh,
    subprocess,
    sys,
    tempfile,
    wait_until,
)


def snapshot():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        sh(["bash", str(home / ".claude/ai-harness/install.sh")], env=env)
        st = json.loads((home / ".claude/settings.json").read_text())
        st["env"] = {"ANTHROPIC_AUTH_TOKEN": "sk-should-not-leak-1234567890"}
        st["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "notify --api-key sk-should-not-leak-1234567890"}]}]
        st["apiKeyHelper"] = "secret-helper"
        (home / ".claude/settings.json").write_text(json.dumps(st))
        h = home / ".claude/ai-harness"
        p = inprocess_sh([sys.executable, str(h / "snapshot.py")], env=env)
        check(p.returncode == 0, f"snapshot 失败: {p.stderr}")
        files = list((h / "snapshots").glob("*.json"))
        check(len(files) == 1, f"应生成一份快照: {files}")
        text = files[0].read_text()
        snap = json.loads(text)
        check("sk-should-not-leak" not in text and "secret-helper" not in text, "快照泄露了密钥")
        check(str(home) not in text, "快照里有写死的家目录路径")
        check(snap["local_config"] is True and len(snap["effective_hash"]) == 12, "缺少配置指纹")
        import doctor_config as diagnostics
        from seats_config import load_seats
        from unittest.mock import patch
        with patch.dict(os.environ, env, clear=True):
            check(snap['fingerprint'] == diagnostics.fingerprint(h, load_seats(h)), 'snapshot fingerprint mismatch')
        check(snap["claude_md_imports_harness"] is True and snap["cli"]["workbuddy"]["kind"] == "agent_use" and snap["cli"]["codex"]["version"] == "codex-cli fake" and snap["agent_use"]["entry_exists"] is True, f"快照字段不对: {snap}")



def snapshot_hooks_minimal():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        sh(["bash", str(home / ".claude/ai-harness/install.sh")], env=env)
        st = json.loads((home / ".claude/settings.json").read_text())
        st["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "mytool --weird-secret-format abc:def:ghi"}]}]
        (home / ".claude/settings.json").write_text(json.dumps(st))
        h = home / ".claude/ai-harness"
        inprocess_sh([sys.executable, str(h / "snapshot.py")], env=env)
        text = next((h / "snapshots").glob("*.json")).read_text()
        check("abc:def:ghi" not in text and "mytool" not in text, "非 harness 钩子命令不应进快照")
        snap = json.loads(text)["settings"]
        check(snap["other_hooks"]["Stop"]["count"] == 1, "其它钩子数量错误")
        check(snap["harness_hooks"]["UserPromptSubmit"]["hooks"][0]["matches_expected"], "标准命令未识别")
        check(all(h["command"] not in text for groups in st["hooks"].values() for group in groups for h in group["hooks"]), "快照包含命令原文")
        check("ai-harness/route.py" not in text and '"command":' not in text, "钩子命令泄露")



def workspace_snapshot_git_env():
    from unittest.mock import patch
    from ai_worker import workspace_snapshot
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work, other = root / 'work', root / 'other'
        for repo in (work, other):
            check(sh(['git', 'init', '-q', str(repo)]).returncode == 0, 'git init')
        (other / 'foreign').write_text('foreign')
        (work / 'local').write_text('local')
        # Unborn HEAD exercises the direct show-ref call as well as the git helper.
        before = workspace_snapshot(str(work))
        with patch.dict(os.environ, GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other)):
            check(workspace_snapshot(str(work)) == before, '异仓库环境污染未提交快照')
        check(set(before['files']) == {'local'} and before['head'] is None, str(before))
        check(sh(['git', 'add', '.'], cwd=work).returncode == 0, 'git add')
        check(sh(['git', '-c', 'user.name=t', '-c', 'user.email=t@t',
                  'commit', '-qm', 'initial'], cwd=work).returncode == 0, 'git commit')
        (work / 'local').write_text('changed')
        expected = workspace_snapshot(str(work), before)
        with patch.dict(os.environ, GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other)):
            check(workspace_snapshot(str(work), before) == expected, '异仓库环境污染已提交快照')
        check(expected['head'] is not None and expected['committed'] == {'local': '提交A'}, str(expected))



def track_contract():
    import datetime
    import fcntl
    import socket
    import time
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = {**os.environ, 'AI_HARNESS_STATE': td, 'AIW_TRACK_FLUSH_SECS': '0.03',
               'CLAUDE_CODE_SESSION_ID': 'track-session'}
        payload = b'first\r\n\x1b[31m' + ('长' * 100).encode() + b'\x1b[0m\x00\t\n\n'
        code = ('import os,time; os.write(1,' + repr(payload[:7]) + '); '
                'os.write(2,' + repr(payload[7:]) + '); time.sleep(30)')
        p = subprocess.Popen(['bash', str(REPO / 'bin/aiw'), 'track', '--host', 'remote-mac',
                              '--timeout', '0.01', 'demo', '--', sys.executable, '-c', code],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        path = root / 'tracks' / f'{p.pid}.json'
        lock = path.with_suffix('.lock')
        try:
            def published_progress():
                if path.exists():
                    data = json.loads(path.read_text())
                    if data['progress']:
                        return data
                return None
            data = wait_until(published_progress, 'track 未发布进度', timeout=120)
            check(set(data) == {'pid', 'host', 'target', 'session', 'label', 'timeout',
                                'started', 'progress', 'updated'}, 'track 字段集合错误')
            check(data['pid'] == p.pid and data['host'] == socket.gethostname(), 'track 本机身份错误')
            check(data['target'] == 'remote-mac' and data['session'] == 'track-session', 'track 来源错误')
            check(data['label'] == 'demo' and data['timeout'] == 0.01, 'track 展示字段错误')
            check(data['progress'] == '长' * 80, 'track 非空行/控制字符/截断错误')
            for key in ('started', 'updated'):
                check(datetime.datetime.fromisoformat(data[key]).tzinfo is not None, 'track 缺时区')
            with lock.open() as fd:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError('track 未持有独占锁')
            before = path.stat().st_mtime_ns
            time.sleep(0.12)
            check(p.poll() is None, '展示 timeout 杀死命令')
            check(path.stat().st_mtime_ns == before, '未变化进度重复写盘')
            p.terminate()
            out, err = p.communicate(timeout=5)
            check(p.returncode == -15, 'SIGTERM 状态未透传')
            check(out == payload, 'track 改变输出字节或顺序: ' + repr(out))
            check(not path.exists() and not lock.exists(), 'track 清理失败')
            check(not (root / 'seat-ledger.jsonl').exists(), 'track 写入账本')
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()



def track_cli():
    import time
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, 'AI_HARNESS_STATE': td, 'AIW_TRACK_FLUSH_SECS': '5'}
        cmd = ['bash', str(REPO / 'bin/aiw'), 'track']
        r = sh(cmd + ['demo', '--', 'sh', '-c', 'echo a; echo b'], env=env)
        check(r.returncode == 0 and r.stdout == 'a\nb\n' and not r.stderr, 'track smoke 失败')
        r = sh(cmd + ['demo', '--', 'sh', '-c', 'exit 7'], env=env)
        check(r.returncode == 7, 'track 退出码错误')
        r = sh(cmd + ['demo', '--', 'sh', '-c', 'kill -KILL $$'], env=env)
        check(r.returncode == -9, 'track SIGKILL 状态错误')
        r = subprocess.run(cmd + ['bytes', '--', sys.executable, '-c',
                                  'import os; os.write(1, bytes([255, 13, 10, 0, 65]))'],
                           env=env, capture_output=True, timeout=5)
        check(r.returncode == 0 and r.stdout == bytes([255, 13, 10, 0, 65]),
              'track 改变非UTF8/无末尾换行字节')
        for args in ([], ['demo'], ['--', 'echo'], ['demo', '--'],
                     ['--timeout', 'nan', 'demo', '--', 'echo'], ['demo', 'echo']):
            r = sh(cmd + args, env=env)
            check(r.returncode == 64, 'track 用法错误未返回64: ' + repr(args))
        ledger = Path(td) / 'seat-ledger.jsonl'
        ledger.write_text('existing\n')
        env['AIW_TRACK_TEST_WRITE_COUNT'] = '1'
        start = time.monotonic()
        r = subprocess.run(cmd + ['fast', '--', sys.executable, '-c',
                                  'import sys; sys.stdout.write("".join(str(i)+"\\n" for i in range(20000)))'],
                           env=env, capture_output=True, timeout=5)
        elapsed = time.monotonic() - start
        check(r.returncode == 0, 'track 高吞吐运行失败')
        check(r.stdout == ''.join(str(i)+'\n' for i in range(20000)).encode(), 'track 丢失快速输出')
        writes = int(r.stderr.decode().strip().split('writes=')[-1])
        check(1 <= writes <= 3, 'track 未节流: ' + str(writes))
        check(elapsed < 3, 'track 20000行超过3秒: ' + str(elapsed))
        check(ledger.read_text() == 'existing\n', 'track 改变账本')
        check(not list((Path(td) / 'tracks').iterdir()), 'track 留下残留')
        print(f'  track 20000行: {elapsed:.3f}s, 原子写入 {writes} 次')



def track_failures_and_signals():
    import signal
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = {**os.environ, 'AI_HARNESS_STATE': str(root / 'state'), 'AIW_TRACK_FLUSH_SECS': '0.02'}
        # STATE 是普通文件时，拿锁和写盘失败也不得影响命令。
        (root / 'state').write_text('not a directory')
        cmd = [sys.executable, str(REPO / 'track.py')]
        r = sh(cmd + ['demo', '--', 'sh', '-c', 'echo intact; exit 7'], env=env)
        check(r.returncode == 7 and r.stdout == 'intact\n' and '警告' in r.stderr, '状态失败影响执行')
        (root / 'state').unlink()
        for sig in (signal.SIGINT, signal.SIGTERM):
            code = ('import signal,sys; '
                    'signal.signal(signal.SIGINT, lambda *a: sys.exit(23)); '
                    'signal.signal(signal.SIGTERM, lambda *a: sys.exit(24)); '
                    'print("ready",flush=True); signal.pause()')
            p = subprocess.Popen(cmd + ['signal', '--', sys.executable, '-c', code],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(p.stdout, selectors.EVENT_READ)
                    check(bool(selector.select(120)), 'track 信号测试启动超时')
                    check(p.stdout.readline() == b'ready\n', 'track 未就绪')
                p.send_signal(sig)
                p.communicate(timeout=120)
                check(p.returncode == (23 if sig == signal.SIGINT else 24), 'track 未等待信号处理退出码')
                check(not list((root / 'state/tracks').iterdir()), '信号退出残留状态')
            finally:
                if p.poll() is None:
                    p.kill()
                    p.communicate()
        # 直接验证描述符不可继承；写盘失败不使线程挂死。
        script = '''import os, pathlib, track
p = pathlib.Path(os.environ['AI_HARNESS_STATE']) / 'lock'
f = track.acquire_liveness_lock(p)
assert not os.get_inheritable(f.fileno())
p.unlink(); f.close()
bad = p / 'bad.json'
progress = track.Progress(bad, {}, 5)
progress.start(); progress.stop()
assert progress.writes == 0
'''
        r = sh([sys.executable, '-c', script], env=env, cwd=REPO)
        check(r.returncode == 0 and '警告' in r.stderr, 'track 失败路径/描述符测试失败: ' + r.stderr)



def git_locator_env():
    from unittest.mock import patch
    import ai_worker
    import findings

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        a, b, linked = t / 'a', t / 'b', t / 'linked'
        # Fixture setup must not inherit any caller's repository locators.
        clean_env = ai_worker.repository_git_env()
        for repo, name in ((a, 'a'), (b, 'b')):
            r = sh(['git', 'init', '-q', str(repo)], env=clean_env)
            check(r.returncode == 0, r.stderr)
            (repo / (name + '-tracked')).write_text(name)
            for args in (['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t',
                                       'commit', '-qm', name],
                         ['remote', 'add', 'origin', f'git@github.com:owner/{name}.git']):
                r = sh(['git', '-C', str(repo), *args], env=clean_env)
                check(r.returncode == 0, r.stderr)
        r = sh(['git', '-C', str(b), 'worktree', 'add', '-qb', 'linked', str(linked)], env=clean_env)
        check(r.returncode == 0, r.stderr)
        b_commit = sh(['git', '-C', str(b), 'rev-parse', 'HEAD'], env=clean_env).stdout.strip()
        (a / 'a-only').write_text('only A')
        (b / 'b-only').write_text('only B')
        locators = dict(GIT_DIR=str(a / '.git'), GIT_WORK_TREE=str(a))
        with patch.dict(os.environ, locators):
            snapshot = ai_worker.workspace_snapshot(str(b))
            check('b-only' in snapshot['files'] and not any(Path(name).name == 'a-only' for name in snapshot['files']),
                  f'workspace_snapshot 定位错误: {snapshot}')
            prompt = t / 'prompt.txt'
            prompt.write_text('test')
            env = dict(os.environ, AI_HARNESS_STATE=str(t / 'state'))
            r = sh([sys.executable, str(REPO / 'ai_worker.py'), 'codex', str(b), '10',
                    str(prompt), '--no-fallback', '--no-findings'], env=env)
            check(r.returncode == 0, r.stderr)
            row = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
            check(row['repo'] == 'github.com/owner/b', str(row))
            fields = findings.common_fields(str(b))
            check(fields['repo'] == 'github.com/owner/b' and fields['commit'] == b_commit, str(fields))
            roots = ai_worker.worktree_writable_roots(str(linked))
            common = (b / '.git').resolve()
            check(len(roots) == 2 and all(Path(root).is_relative_to(common) for root in roots)
                  and not any(Path(root).is_relative_to(a.resolve()) for root in roots), str(roots))
            check(findings._command(['git', 'rev-parse', '--show-toplevel'], cwd=str(b)) == str(b.resolve()),
                  '_command 定位错误')
            with patch.dict(os.environ, {'GIT_SSH_COMMAND': 'ssh test', 'GIT_CONFIG_COUNT': '0',
                                         'GIT_CONFIG_GLOBAL': str(t / 'config')}):
                filtered = ai_worker.repository_git_env()
                for key in ('GIT_SSH_COMMAND', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_GLOBAL', 'HOME'):
                    check(filtered[key] == os.environ[key], f'{key} 不应被清除')
                check('GIT_DIR' not in filtered and 'GIT_WORK_TREE' not in filtered, '定位变量未清除')



def git_locator_env_snapshot_doctor():
    import contextlib
    import io
    from unittest.mock import patch
    import doctor_config as diagnostics
    import snapshot as snapshots
    from gitenv import repository_git_env
    from seats_config import load_seats

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        a, b = t / 'a', t / 'b'
        clean_env = repository_git_env()
        for repo, name in ((a, 'a'), (b, 'b')):
            r = sh(['git', 'init', '-q', str(repo)], env=clean_env)
            check(r.returncode == 0, r.stderr)
            (repo / 'tracked').write_text(name)
            for args in (['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t',
                                       'commit', '-qm', name]):
                r = sh(['git', '-C', str(repo), *args], env=clean_env)
                check(r.returncode == 0, r.stderr)
        commits = [sh(['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'],
                      env=clean_env).stdout.strip() for repo in (a, b)]
        check(all(commits) and commits[0] != commits[1], f'提交夹具不区分 A/B: {commits}')
        for name in ('b-only-1', 'b-only-2'):
            (b / name).write_text('only B')
        seats = load_seats(REPO)
        with patch.dict(os.environ, {'GIT_DIR': str(a / '.git'), 'GIT_WORK_TREE': str(a),
                                     'AGENT_USE_HOME': str(b)}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostics.repository(b)
            check('未提交改动 2 项' in output.getvalue(),
                  f'doctor repository 未查询 B: {output.getvalue()}')
            with patch.object(snapshots, 'HERE', b), patch.object(snapshots, 'HOME', t / 'home'), \
                    patch.object(snapshots, 'load_seats', return_value=seats), \
                    patch.object(snapshots, 'seat_shape', return_value={}), \
                    patch.object(snapshots, 'version', return_value=None), \
                    contextlib.redirect_stdout(io.StringIO()):
                check(snapshots.main() == 0, 'snapshot 失败')
            files = list((b / 'snapshots').glob('*.json'))
            check(len(files) == 1, f'应在 B 写入快照: {files}')
            data = json.loads(files[0].read_text())
            check(data['harness_commit'] == commits[1], f'harness_commit 未查询 B: {data}')
            check(data['agent_use']['head'] == commits[1] and data['agent_use']['dirty'],
                  f'agent_use_shape 未查询 B: {data["agent_use"]}')
            root, head = diagnostics.git_fingerprint(b)
            check(root == str(b.resolve()) and head == commits[1] + '*',
                  f'git_fingerprint 未查询 B: {(root, head)}')



CASES = {case.__name__: case for case in (
    snapshot,
    snapshot_hooks_minimal,
    workspace_snapshot_git_env,
    track_contract,
    track_cli,
    track_failures_and_signals,
    git_locator_env,
    git_locator_env_snapshot_doctor,
)}
