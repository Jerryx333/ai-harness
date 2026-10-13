"""codex home regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    Path,
    REPO,
    check,
    codex_home_fixture,
    codex_home_run_case,
    os,
    sh,
    sys,
    tempfile,
)


def codex_home_whitelist():
    import tomllib
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
        check(home is not None and not reason, reason)
        config = tomllib.loads((home / 'config.toml').read_text())
        original = tomllib.loads((source / 'config.toml').read_text())
        check(set(config) == {'model_provider', 'model_providers'}, '白名单外键泄漏')
        check(config['model_providers'] == {'X': original['model_providers']['X']}, 'provider 嵌套表或转义丢失')
        check(home.stat().st_mode & 0o777 == 0o700, 'home 权限错误')
        check((home / 'config.toml').stat().st_mode & 0o777 == 0o600, 'config 权限错误')



def codex_home_auth_symlink():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is not None, reason)
            link = home / 'auth.json'
            check(link.is_symlink() and os.readlink(link) == str(source.resolve() / 'auth.json'), 'auth 应软链到源文件')
            before = link.lstat()
            worker.prepare_codex_home()
            after = link.lstat()
            check((before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), '正确软链不应重建')
            (source / 'auth.json').unlink()
            worker.prepare_codex_home()
            check(not link.is_symlink(), '源 auth 消失时应清除旧链')



def codex_home_unchanged():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is not None, reason)
            target = home / 'config.toml'
            before = target.stat()
            worker.prepare_codex_home()
            after = target.stat()
            check((before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), '内容不变不应重写')



def codex_home_fallback():
    codex_home_run_case('fallback')



def codex_home_shared_switch():
    codex_home_run_case('shared')



def codex_home_exec_env():
    codex_home_run_case('normal')



def codex_home_claude_unchanged():
    codex_home_run_case('claude')



def codex_home_write_failure():
    from unittest.mock import patch
    import ai_worker as worker
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        blocked = root / 'blocked'
        blocked.write_text('not a directory')
        with patch.object(worker, 'STATE', blocked), patch.dict(os.environ, CODEX_HOME=str(source)):
            home, reason = worker.prepare_codex_home()
            check(home is None and '写入执行方 home' in reason, '写入失败应安全返回原因')
        default = root / '.codex'
        source.rename(default)
        with patch.object(worker, 'STATE', root / 'state'), patch.dict(os.environ, HOME=str(root)):
            os.environ.pop('CODEX_HOME', None)
            home, reason = worker.prepare_codex_home()
            check(home is not None and not reason, '默认源应取 HOME/.codex')



def codex_home_doctor():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = codex_home_fixture(root)
        env = dict(os.environ, CODEX_HOME=str(source), AIW_STATE=str(root / 'state'))
        for mode in ('normal', 'bad', 'shared'):
            if mode == 'bad':
                (source / 'config.toml').write_text('broken = [ "sensitive-fake-value"')
            if mode == 'shared':
                env['AIW_CODEX_SHARED_HOME'] = '1'
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0, r.stderr)
            line = next((v for v in r.stdout.splitlines() if 'Codex 执行方 home:' in v), '')
            check(str(root / 'state/codex-home') in line, 'doctor 未显示路径')
            check({'normal': '可生成', 'bad': '不可生成：', 'shared': '已关闭'}[mode] in line, 'doctor 状态错误')
            for value in ('fake-provider', 'https://fake.invalid', 'fake-test-only', 'sensitive-fake-value'):
                check(value not in r.stdout + r.stderr, 'doctor 泄漏配置值')
            check(not (root / 'state').exists(), 'doctor 不应创建 STATE')



CASES = {case.__name__: case for case in (
    codex_home_whitelist,
    codex_home_auth_symlink,
    codex_home_unchanged,
    codex_home_fallback,
    codex_home_shared_switch,
    codex_home_exec_env,
    codex_home_claude_unchanged,
    codex_home_write_failure,
    codex_home_doctor,
)}
