"""Codex main-session identity and tmux status integration."""
from support import (Path, REPO, check, json, os, sh, subprocess, sys,
                     tempfile, wait_until, worker_call)
import shlex
import shutil
import time


def tui_sessions():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        for extra, expected, mapped in (
            ({'CODEX_THREAD_ID': 'thread-one', 'AIW_TUI_ID': 'valid-id'}, 'thread-one', True),
            ({'CODEX_THREAD_ID': 'thread-two', 'CLAUDE_CODE_SESSION_ID': 'claude'}, 'claude', False),
            ({'CODEX_THREAD_ID': 'thread-three', 'AIW_TUI_ID': '../escape'}, 'thread-three', False),
            
        ):
            state = t / expected
            cmd, env, wd = worker_call(t, FAKE_AU_QODER='brief', AIW_STATE=str(state), **extra)
            p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            path = state / 'inflight' / f'{p.pid}.json'
            try:
                wait_until(lambda: path.exists() or p.poll() is not None, 'inflight not ready')
                check(path.exists() and json.loads(path.read_text())['session'] == expected, 'inflight session')
                out, err = p.communicate(timeout=30)
                check(p.returncode == 0, out + err)
                rows = [json.loads(line) for line in (state / 'seat-ledger.jsonl').read_text().splitlines()]
                check(all(row['session'] == expected for row in rows), str(rows))
                if mapped:
                    check((state / 'tui/valid-id').read_text() == expected + '\n', 'mapping')
                else:
                    check(not (state / 'tui').exists() and not (t / 'escape').exists(), 'unexpected mapping')
            finally:
                if p.poll() is None:
                    p.terminate()
                p.communicate(timeout=30)
        from env_compat import write_tui_session
        from unittest.mock import patch
        with patch.dict(os.environ, CODEX_THREAD_ID='nested-thread', AIW_TUI_ID='nested', AIW_NESTED='1'):
            write_tui_session(t / 'nested')
        check(not (t / 'nested/tui').exists(), 'nested mapping')
        state = t / 'blocked'; state.mkdir(); (state / 'tui').write_text('blocked')
        cmd, env, _ = worker_call(t, AIW_STATE=str(state), CODEX_THREAD_ID='thread', AIW_TUI_ID='valid')
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '警告：写 tui 会话失败' in r.stderr, r.stderr)


def tui_status():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); b = t / 'bin'; b.mkdir()
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state'))
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        # bin/aiw needs dirname; keep only that utility, excluding installed board/tmux.
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        cmd = ['/bin/bash', str(REPO / 'bin/aiw'), 'tui-status']
        for ident in ('missing', '../escape', ''):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == '○ 派单空闲\n', repr(r))
        folder = t / 'state/tui'; folder.mkdir(parents=True); (folder / 'ok').write_text('thread\n')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and r.stdout == '派单 ?（未安装 aiw-board）\n', repr(r))
        board = b / 'aiw-board'
        board.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\nPath({str(t / "stdin")!r}).write_text(sys.stdin.read())\nprint("summary  ")\nprint("ignored")\n')
        board.chmod(0o755)
        (folder / 'empty').write_text(' \n')
        for ident in ('missing', 'empty', 'ok'):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == 'summary  \n', repr(r))
            session = 'thread' if ident == 'ok' else 'aiw-tui-' + ident
            check(json.loads((t / 'stdin').read_text()) == {'session_id': session}, 'board stdin')
        (t / 'stdin').unlink()
        for ident in ('../escape', ''):
            r = sh(cmd + [ident], env=env)
            check(r.returncode == 0 and r.stdout == '○ 派单空闲\n', repr(r))
            check(not (t / 'stdin').exists(), 'invalid id called board')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and r.stdout == 'summary  \n', repr(r))
        check(json.loads((t / 'stdin').read_text()) == {'session_id': 'thread'}, 'board stdin')
        board.write_text('#!/bin/sh\nexit 1\n')
        r = sh(cmd + ['ok'], env=env)
        check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and not r.stderr, repr(r))


def tui_status_extra():
    with tempfile.TemporaryDirectory(prefix='aiw extra ') as td:
        t = Path(td); b = t / 'bin'; b.mkdir()
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state'))
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        cmd = ['/bin/bash', str(REPO / 'bin/aiw'), 'tui-status', 'ok']
        baseline = sh(cmd, env=env).stdout
        script = t / 'extra command'

        def write_extra(body):
            script.write_text(f'#!{sys.executable}\n' + body)
            script.chmod(0o755)
            return dict(env, AIW_TUI_STATUS_EXTRA=shlex.join([str(script), 'two words']))

        extra_env = write_extra('import sys\nassert sys.stdin.read() == ""\nassert sys.argv[1:] == ["two words"]\nprint("extra #1\\nsecond")\n')
        r = sh(cmd, env=extra_env)
        check(r.returncode == 0 and r.stdout == baseline.rstrip('\n') + ' │ extra ##1\n'
              and not r.stderr, repr(r))
        extra_env = write_extra('print("  \\n  extra #1  \\nsecond")\n')
        check(sh(cmd, env=extra_env).stdout.endswith(' │ extra ##1\n'), 'first nonempty line')
        for body in ('import sys\nprint("ignored")\nprint("error", file=sys.stderr)\nsys.exit(1)\n',
                     'pass\n', 'print("  ")\n'):
            r = sh(cmd, env=write_extra(body))
            check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        for command in (str(t / 'missing'), "'unclosed", '   ', ''):
            r = sh(cmd, env=dict(env, AIW_TUI_STATUS_EXTRA=command))
            check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        for output, expected in (('x' * 200, 'x' * 119 + '…'),
                                 ('x' * 118 + '#tail', 'x' * 118 + '…'),
                                 ('#' * 100, '##' * 59 + '…'),
                                 ('x' * 120, 'x' * 120)):
            r = sh(cmd, env=write_extra(f'print({output!r})\n'))
            extra = r.stdout.rstrip('\n').split(' │ ')[-1]
            check(extra == expected and len(extra) <= 120 and not r.stderr, repr(r))
        # Block until killed by the shortened timeout, without sleeping.
        extra_env = write_extra('import signal\nsignal.pause()\n')
        timeout_cmd = [sys.executable, '-c',
                       'import tui; tui.STATUS_EXTRA_TIMEOUT = 0.1; print(tui.status("ok"))']
        r = sh(timeout_cmd, env=extra_env, cwd=REPO)
        check(r.returncode == 0 and r.stdout == baseline and not r.stderr, repr(r))
        extra_env = write_extra('print("extra #1")\n')
        folder = t / 'state/tui'; folder.mkdir(parents=True); (folder / 'ok').write_text('thread\n')
        for board_body in (None, 'print("summary #raw  ")\n',
                           'pass\n', 'raise RuntimeError("board failed")\n'):
            if board_body is not None:
                board = b / 'aiw-board'
                board.write_text(f'#!{sys.executable}\n' + board_body)
                board.chmod(0o755)
            for ident in ('ok', '../escape'):
                base = sh(cmd[:-1] + [ident], env=env).stdout.rstrip('\n')
                r = sh(cmd[:-1] + [ident], env=extra_env)
                check(r.returncode == 0 and r.stdout == base + ' │ extra ##1\n'
                      and not r.stderr, repr(r))


def tui_launch():
    with tempfile.TemporaryDirectory(prefix='aiw space ') as td:
        t = Path(td); b = t / 'bin'; b.mkdir(); log = t / 'tmux.jsonl'; result = t / 'codex.json'
        (b / 'dirname').symlink_to(shutil.which('dirname'))
        tmux = b / 'tmux'
        tmux.write_text(f'#!{sys.executable}\nimport json, sys\nwith open({str(log)!r}, "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        tmux.chmod(0o755)
        codex = b / 'codex'
        codex.write_text(f'#!{sys.executable}\nimport os, json, sys\nfrom pathlib import Path\nPath({str(result)!r}).write_text(json.dumps([sys.argv[1:], os.environ.get("AIW_TUI_ID"), os.environ.get("AIW_STATE")]))\n')
        codex.chmod(0o755)
        # Exercise a repository entry path containing spaces as well.
        repo = t / 'repo space'; (repo / 'bin').mkdir(parents=True)
        for name in ('tui.py', 'env_compat.py', 'bin/aiw'):
            shutil.copy(REPO / name, repo / name)
        env = dict(os.environ, PATH=str(b), AIW_PYTHON=sys.executable, AIW_STATE=str(t / 'state space'))
        env.pop('TMUX', None)
        env.pop('AIW_TUI_STATUS_EXTRA', None)
        args = ['--model', 'two words', "quote'", '$(touch bad)', 'semi;colon', '', 'line\nbreak']
        cmd = ['/bin/bash', str(repo / 'bin/aiw'), 'tui', *args]
        r = sh(cmd, env=env); check(r.returncode == 0, r.stderr)
        argv = json.loads(log.read_text().splitlines()[0])
        check(argv[:2] == ['new-session', '-s'], str(argv))
        launch = argv[3]
        parsed = shlex.split(launch)
        check(parsed[-len(args):] == args and parsed[-len(args)-1] == 'codex', launch)
        callback = argv[argv.index('status-left') + 1]
        expected = [('status', 'on'), ('status-interval', '15'), ('status-justify', 'left'),
                    ('status-style', 'bg=default,fg=colour250'), ('status-left', callback),
                    ('status-left-length', '200'), ('status-right', ''),
                    ('window-status-format', ''), ('window-status-current-format', '')]
        expected_argv = []
        for key, value in expected:
            expected_argv += [';', 'set-option', '-t', argv[2], key, value]
        check(argv[4:] == expected_argv and '-g' not in argv, str(argv))
        check(callback.startswith('#(') and callback.endswith(')'), callback)
        callback_args = shlex.split(callback[2:-1])
        check(not any(a.startswith('AIW_TUI_STATUS_EXTRA=') for a in callback_args), callback)
        check(callback_args[-3:-1] == [str(repo.resolve() / 'bin/aiw'), 'tui-status'], callback)
        tui_id = callback_args[-1]
        check(all(c.isalnum() or c in '_-' for c in tui_id), tui_id)
        check(argv[2] == 'aiw-' + tui_id, str(argv))
        check('AIW_TUI_ID=' + tui_id in parsed, launch)
        check(sh(['/bin/sh', '-c', launch], env=env).returncode == 0, launch)
        check(json.loads(result.read_text()) == [args, tui_id, env['AIW_STATE']], result.read_text())
        log.unlink()
        r = sh(cmd, env=dict(env, TMUX='fake', AIW_STATE='state rel'), cwd=t)
        check(r.returncode == 0, r.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        check(len(calls) == 5 and all(a[0] == 'set-option' and '-g' not in a for a in calls), str(calls))
        recorded = json.loads(result.read_text())
        check(recorded[0] == args, result.read_text())
        check(Path(recorded[2]).is_absolute() and recorded[2] == str(t.resolve() / 'state rel'), result.read_text())
        callback = next(a[2] for a in calls if a[1] == 'status-right')
        callback_args = shlex.split(callback[2:-1])
        check('AIW_STATE=' + recorded[2] in callback_args, callback)
        check(not any(a.startswith('AIW_TUI_STATUS_EXTRA=') for a in callback_args), callback)
        extra = shlex.join([str(t / 'extra command'), "quote'", '$(touch bad)', 'line\nbreak'])
        for inside in (False, True):
            log.unlink()
            launch_env = dict(env, AIW_TUI_STATUS_EXTRA=extra)
            if inside:
                launch_env['TMUX'] = 'fake'
            r = sh(cmd, env=launch_env)
            check(r.returncode == 0 and not r.stderr, repr(r))
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            callback = (next(a[2] for a in calls if a[1] == 'status-right') if inside
                        else calls[0][calls[0].index('status-left') + 1])
            callback_args = shlex.split(callback[2:-1])
            check(callback_args[0] == '/usr/bin/env'
                  and 'AIW_TUI_STATUS_EXTRA=' + extra in callback_args[:-3], callback)
        check(not (t / 'bad').exists(), 'extra command was interpreted by shell')
        tmux.unlink()
        r = sh(cmd, env=env)
        check(r.returncode == 127 and '需要 tmux：brew install tmux' in r.stderr, repr(r))


def tui_gc():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); folder = t / 'state/tui'; folder.mkdir(parents=True)
        old = folder / 'old'; old.write_text('thread'); new = folder / 'new'; new.write_text('thread')
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        (folder / 'link').symlink_to(old)
        directory = folder / 'directory'; directory.mkdir(); os.utime(directory, (0, 0))
        env = dict(os.environ, AIW_STATE=str(t / 'state'))
        cmd = [sys.executable, str(REPO / 'gc.py'), '7']
        r = sh(cmd + ['--dry-run'], env=env)
        check(r.returncode == 0 and old.exists() and '预计删除 1' in r.stdout, repr(r))
        r = sh(cmd, env=env)
        check(r.returncode == 0 and not old.exists() and new.exists() and directory.exists()
              and (folder / 'link').is_symlink(), repr(r))


CASES = {f.__name__: f for f in (tui_sessions, tui_status, tui_status_extra, tui_launch, tui_gc)}
