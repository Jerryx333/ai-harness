"""worker 2 regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    git_template,
    FAKE_AGENT_USE,
    FINDINGS_BLOCK,
    Path,
    REPO,
    check,
    fake_agent_use,
    fake_home,
    free_guard_fixture,
    json,
    os,
    sh,
    subprocess,
    sys,
    tempfile,
    worker_call,
)


def inflight_liveness_lock():
    import fcntl
    import signal
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("worker_lock_test", REPO / "ai_worker.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        stale = t / "stale.lock"
        stale.touch()
        stale.chmod(0o444)
        held = worker.acquire_liveness_lock(stale)
        check(held is not None, "只读残留锁未重建")
        try:
            with stale.open("r") as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("重建锁未被持有")
        finally:
            held.close()
        with patch.object(worker.fcntl, "flock", side_effect=OSError("模拟拿锁失败")):
            check(worker.acquire_liveness_lock(stale) is None, "拿锁失败应返回 None")
        check(not stale.exists(), "拿锁失败留下假锁文件")

        backend = fake_agent_use(t)
        pidfile, release = t / 'seat.pid', t / 'release'
        (backend / 'scripts/run_agent.py').write_text(
            "import os, time\nfrom pathlib import Path\n"
            "Path(os.environ['SEAT_PID']).write_text(str(os.getpid()))\n"
            "while not Path(os.environ['SEAT_RELEASE']).exists(): time.sleep(.02)\n"
            + FAKE_AGENT_USE)
        cmd, env, _ = worker_call(t, AGENT_USE_HOME=str(backend),
                                  SEAT_PID=str(pidfile), SEAT_RELEASE=str(release))
        for killed in (False, True):
            pidfile.unlink(missing_ok=True)
            release.unlink(missing_ok=True)
            p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            path = Path(env['AI_HARNESS_STATE']) / 'inflight' / f'{p.pid}.json'
            lock = path.with_suffix('.lock')
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and p.poll() is None:
                    if pidfile.exists() and pidfile.read_text() and path.exists():
                        break
                    time.sleep(.02)
                check(path.exists() and lock.exists() and pidfile.exists(), '在途文件或座位进程缺失')
                child = int(pidfile.read_text())
                with lock.open('r') as fd:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        raise AssertionError('运行中未持有 inflight 排他锁')
                if killed:
                    p.kill()
                    p.wait(timeout=5)
                    os.kill(child, 0)  # 座位仍活着，锁必须已随 worker 释放。
                    check(path.exists() and lock.exists(), 'SIGKILL 未留下状态文件')
                    with lock.open('r') as fd:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    release.touch()
                    out, err = p.communicate(timeout=10)
                    check(p.returncode == 0, out + err)
                    check(not path.exists() and not lock.exists(), '正常结束残留 inflight 文件')
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=5)
                if pidfile.exists() and pidfile.read_text():
                    try:
                        os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                p.communicate(timeout=5)
                path.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)

        inflight = Path(env['AI_HARNESS_STATE']) / 'inflight'
        inflight.chmod(0o500)
        try:
            release.touch()
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check('警告：获取 inflight 锁失败' in r.stderr and '警告：写 inflight 失败' in r.stderr,
                  '目录不可写未产生预期警告')
            check('Traceback' not in r.stderr, r.stderr)
        finally:
            inflight.chmod(0o700)



def test_inflight_lifecycle():
    import datetime as dt
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cmd, env, wd = worker_call(t, FAKE_AU_QODER='brief', CLAUDE_CODE_SESSION_ID='board-session')
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        path = Path(env['AI_HARNESS_STATE']) / 'inflight' / f'{p.pid}.json'
        try:
            deadline = time.monotonic() + 8
            while not path.exists() and p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            check(path.exists(), "worker 未及时创建 inflight")
            row = json.loads(path.read_text())
            fields = {'pid', 'host', 'session', 'claude_pid', 'seat', 'actual', 'model', 'workdir',
                      'prompt', 'ro', 'timeout', 'started', 'panel', 'nested'}
            check(fields <= row.keys(), f'字段缺失: {row}')
            check(row['seat'] == row['actual'] == 'qoder' and row['session'] == 'board-session', '座位/会话错误')
            check(row['timeout'] == 10 and dt.datetime.fromisoformat(row['started']).tzinfo is not None, '时间格式错误')
            out, err = p.communicate(timeout=10)
            check(p.returncode == 0, out + err)
            check(not path.exists(), '完成后仍有 inflight')
            rows = [json.loads(x) for x in (path.parent.parent / 'seat-ledger.jsonl').read_text().splitlines()]
            check(rows[-1]['session'] == 'board-session', '账本缺 session')
        finally:
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=10)



def test_inflight_removed_on_failure():
    with tempfile.TemporaryDirectory() as td:
        cmd, env, wd = worker_call(Path(td), FAKE_AU_QODER='auth')
        r = sh(cmd + ['--no-fallback'], env=env)
        check(r.returncode == 1, r.stderr)
        check(not list((Path(env['AI_HARNESS_STATE']) / 'inflight').glob('*.json')), '失败后 inflight 残留')
        inflight = Path(env['AI_HARNESS_STATE']) / 'inflight'
        inflight.rmdir(); inflight.write_text('not a directory')
        cmd[2] = 'workbuddy'
        r = sh(cmd + ['--no-fallback'], env=env)
        check(r.returncode == 0 and '警告：写 inflight 失败' in r.stderr, 'inflight 写失败影响派单')



def write_fallback_text_seat():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        repo = home / '.claude/ai-harness'
        config = repo / 'seats.json'
        data = json.loads(config.read_text())
        data['fallback']['codex'] = ['qoder']
        config.write_text(json.dumps(data))
        work = t / 'work'; work.mkdir()
        prompt = t / 'prompt'; prompt.write_text('修改文件')
        log = t / 'agent-calls'
        env.update(FAKE_CODEX='quota', FAKE_AU_LOG=str(log))
        r = inprocess_sh([sys.executable, str(repo / 'ai_worker.py'), 'codex', str(work),
                '10', str(prompt)], env=env)
        check(r.returncode == 1, f'写任务不能由文本座位伪成功: {r.stdout}{r.stderr}')
        check(not log.exists(), '写任务调用了文本后端')
        check(not (work / '.provider').exists(), '写任务不应记录文本座位成功')
        rows = [json.loads(x) for x in (t / 'state/seat-ledger.jsonl').read_text().splitlines()]
        check(len(rows) == 1 and rows[0]['actual'] == 'codex' and rows[0]['kind'] == 'quota',
              f'写任务不应尝试文本顶替: {rows}')



def generic_adapter_reasons():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); env = free_guard_fixture(t)
        local = t / 'generic.json'
        local.write_text(json.dumps({
            'seats': {'custom': {'kind': 'agent_use', 'provider': 'generic', 'model': 'test', 'free_guard': True}},
            'fallback': {'custom': ['claude']}}))
        env['AIW_CONFIG'] = str(local)
        prompt = t / 'prompt'; prompt.write_text('test')
        for reason, hint in (('outside_free_window', '不在免费时段'), ('promotion_expired', '免费活动已到期')):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'custom', str(t), '10', str(prompt), '--ro'],
                   env=dict(env, FAKE_AU_GENERIC=reason))
            check(r.returncode == 0 and 'custom ' + hint in r.stderr and '已顶替' in r.stderr, repr(r))
            r = inprocess_sh([sys.executable, str(REPO / 'freecheck.py')], env=dict(env, FAKE_AU_GENERIC=reason))
            check(r.returncode == 0 and 'custom:' in r.stdout and hint in r.stdout, repr(r))



def repository_identity_contract():
    from unittest.mock import patch
    import findings
    from gitenv import repository_git_env

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        harness = home / '.claude/ai-harness'
        settings = json.loads((harness / 'seats.json').read_text())
        settings['findings'] = {'collect': True}
        (harness / 'seats.json').write_text(json.dumps(settings))
        prompt, output = t / 'prompt', t / 'output'
        prompt.write_text('test')
        output.write_text('正文\n' + FINDINGS_BLOCK + '\n')
        env.update(FAKE_CODEX='findings', FAKE_FINDINGS_OUTPUT=str(output))
        for key in ('AIW_PANEL_DIR', 'AIW_MAP_DIR'):
            env.pop(key, None)
        clean_env = repository_git_env()
        repo, linked, other, plain = (t / name for name in ('repo', 'linked', 'other', 'plain'))
        plain.mkdir()

        def git(workdir, *args):
            result = sh(['git', '-C', str(workdir), *args], env=clean_env)
            check(result.returncode == 0, result.stderr)
            return result.stdout.strip()

        for path, remote in ((repo, 'git@github.com:Owner/Name.git'),
                             (other, 'git@github.com:Wrong/Repo.git')):
            path.mkdir()
            git_template(path)
            git(path, 'remote', 'add', 'origin', remote)
        git(repo, 'worktree', 'add', '-qb', 'linked', str(linked))
        sub, linked_sub = repo / 'sub', linked / 'sub'
        sub.mkdir()
        linked_sub.mkdir()
        identity = 'github.com/owner/name'
        polluted = dict(GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other))

        def verify(workdir, root, expected, locators=None):
            with patch.dict(os.environ, locators or {}):
                actual = findings.repository_identity(str(workdir))
            check(actual == (str(root.resolve()) if root else None, expected), repr(actual))
            result = inprocess_sh([sys.executable, str(harness / 'ai_worker.py'), 'codex', str(workdir),
                         '10', str(prompt), '--ro', '--no-fallback'], env=dict(env, **(locators or {})))
            check(result.returncode == 0, result.stderr)
            ledger = json.loads((t / 'state/seat-ledger.jsonl').read_text().splitlines()[-1])
            records = [json.loads(line) for line in (t / 'state/findings/records.jsonl').read_text().splitlines()]
            observations = [row for row in records if row['run_dir'] == ledger['run_dir']
                            and row['type'] == 'observation']
            check(observations and ledger['repo'] == expected
                  and all(row['repo'] == ledger['repo'] for row in observations),
                  repr((ledger, observations)))
            fallback = str(workdir.resolve()) if expected is None else None
            check(ledger['workdir'] == fallback and all(row['workdir'] == fallback for row in observations),
                  'workdir fallback changed')
            check(all(row['repo_rel'] == (os.path.relpath(workdir.resolve(), root.resolve()) if root else None)
                      for row in observations), 'repo_rel changed')

        verify(sub, repo, identity)
        verify(linked_sub, linked, identity)
        verify(sub, repo, identity, polluted)
        verify(linked_sub, linked, identity, polluted)
        verify(plain, None, None, polluted)
        for remote in ('https://github.com/Owner/Name.git', 'ssh://git@github.com/Owner/Name'):
            git(repo, 'remote', 'set-url', 'origin', remote)
            verify(sub, repo, identity)
        git(repo, 'remote', 'remove', 'origin')
        verify(sub, repo, None)
        for remote in ('not-a-remote', 'https://[invalid/Owner/Name.git'):
            git(repo, 'remote', 'add', 'origin', remote)
            verify(sub, repo, None)
            git(repo, 'remote', 'remove', 'origin')
        verify(plain, None, None)
        # Root failure suppresses the origin query; origin command failure retains the root.
        with patch.object(findings.subprocess, 'run', side_effect=OSError('git unavailable')) as run:
            check(findings.repository_identity(str(sub)) == (None, None), 'root failure fallback')
            check(run.call_count == 1, 'origin queried without a root')
        root_result = subprocess.CompletedProcess([], 0, str(repo.resolve()) + '\n', '')
        with patch.object(findings.subprocess, 'run', side_effect=[root_result, subprocess.TimeoutExpired('git', 10)]):
            check(findings.repository_identity(str(sub)) == (str(repo.resolve()), None), 'origin failure fallback')



CASES = {case.__name__: case for case in (
    inflight_liveness_lock,
    test_inflight_lifecycle,
    test_inflight_removed_on_failure,
    write_fallback_text_seat,
    generic_adapter_reasons,
    repository_identity_contract,
)}
