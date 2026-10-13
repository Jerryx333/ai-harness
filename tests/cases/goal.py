"""goal regression cases."""
from __future__ import annotations

from support import (
    Path,
    REPO,
    check,
    goal_file,
    json,
    os,
    inprocess_sh as sh,
    sys,
    task,
    tempfile,
)


def goal_init_mkdir():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        r = sh([str(REPO / "bin/aiw"), "goal", "init", "docs/goals/x.json", "mkdir test"], cwd=t)
        check(r.returncode == 0, r.stderr)
        path = t / "docs/goals/x.json"
        check(path.is_file() and json.loads(path.read_text())["goal"] == "mkdir test", "goal 未生成合法 JSON")



def goal_help_no_write():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, HOME=str(t / 'home'), AIW_STATE=str(t / 'state'))
        cmd = [str(REPO / 'bin/aiw'), 'goal']
        for flag in ('-h', '--help'):
            args = [[flag]]
            for subcommand in ('init', 'next', 'verify', 'status'):
                args.extend(([subcommand, flag], [subcommand, 'new.json', flag]))
            for argv in args:
                r = sh(cmd + argv, env=env, cwd=t)
                check(r.returncode == 0, f'{argv}: {r.returncode} {r.stderr}')
                check('goal.py init' in r.stdout and 'goal.py verify' in r.stdout, f'{argv}: 未打印用法')
                check(not list(t.iterdir()), f'{argv}: 帮助不应写文件或创建 state')



def goal_init_unknown_option():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, HOME=str(t / 'home'), AIW_STATE=str(t / 'state'))
        for path in ('--unknown', '-x', '-goal.json', '-'):
            r = sh([str(REPO / 'bin/aiw'), 'goal', 'init', path, 'demo'], env=env, cwd=t)
            check(r.returncode == 64, f'{path}: {r.returncode} {r.stderr}')
            check(not list(t.iterdir()), f'{path}: 未知选项不应写文件')



def goal_verify_failure_tail():
    import shlex
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env = dict(os.environ, HOME=str(t / 'home'), AIW_STATE=str(t / 'state'))
        stdout = '开头不应保留\n' + '中' * 1700 + '\nstdout end\n'
        stderr = 'stderr end\n'
        script = (
            "from pathlib import Path; import sys; "
            "p = Path('retry.marker'); retry = p.exists(); p.touch(); "
            f"sys.stdout.write('recovered\\n' if retry else {stdout!r}); "
            f"sys.stderr.write('' if retry else {stderr!r}); sys.exit(0 if retry else 1)"
        )
        verify = f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}'
        g = goal_file(t, [task('T6', verify=verify), task('T7', verify='echo other failure >&2; false'),
                          task('T8', verify='echo success')])
        cmd = [str(REPO / 'bin/aiw'), 'goal', 'verify', str(g)]
        r = sh(cmd, env=env, cwd=t)
        check(r.returncode == 1, r.stdout + r.stderr)
        failures = {'T6': (stdout + stderr)[-1500:], 'T7': 'other failure\n'}
        d = json.loads(g.read_text())
        check(d['rounds'][-1].get('failures') == failures, '失败输出尾部未按任务完整保存或截取错误')
        terminal_tail = '   ' + failures['T6'].strip().replace('\n', '\n   ')[-600:]
        check('[T6] FAIL' in r.stdout and terminal_tail in r.stdout, '终端失败尾部行为改变')
        r = sh(cmd + ['T6'], env=env, cwd=t)
        check(r.returncode == 0, r.stdout + r.stderr)
        d = json.loads(g.read_text())
        check(d['rounds'][0].get('failures') == failures, '重跑通过丢失历史失败输出')
        check(not d['rounds'][-1].get('failures'), '通过轮次不应记录失败')



def goal_regression():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / ".git").mkdir()
        flag = t / "flag"; flag.write_text("ok")
        g = t / "g.json"
        g.write_text(json.dumps({"goal": "x", "fuse": {"max_rounds": 25, "stall_rounds": 3}, "rounds": [], "human_blockers": [],
                                 "tasks": [{"id": "T1", "title": "flag 存在", "owner": "main", "verify": f"test -f {flag}", "passes": False}]}))
        G = [sys.executable, str(REPO / "goal.py")]
        p = sh(G + ["verify", str(g)]); check(p.returncode == 0 and json.loads(g.read_text())["tasks"][0]["passes"] is True, "首轮应通过")
        flag.unlink()
        p = sh(G + ["verify", str(g)]); d = json.loads(g.read_text())
        check(d["tasks"][0]["passes"] is False and "regressed_at" in d["tasks"][0], f"复验失败应撤销通过: {d['tasks'][0]}")
        check(p.returncode == 1 and d["rounds"][-1]["regressed"] == ["T1"] and "本轮回归 1 项：T1" in p.stdout, "全量回归记录缺失")
        check("全部通过" not in p.stdout, "回归后不应宣称全部通过")



def goal_next_human():
    with tempfile.TemporaryDirectory() as td:
        g = goal_file(Path(td), [task('H', 'human'), task('A')])
        cmd = [sys.executable, str(REPO / 'goal.py'), 'next', str(g)]
        r = sh(cmd); check(r.returncode == 0 and json.loads(r.stdout)['id'] == 'A', '未跳过人工任务')
        d = json.loads(g.read_text()); d['tasks'][1]['passes'] = True; g.write_text(json.dumps(d))
        r = sh(cmd); check(r.returncode == 0 and '只剩人工任务：H' in r.stdout, '人工剩余提示缺失')



def verify_root():
    with tempfile.TemporaryDirectory(prefix='harness space ') as td:
        t = Path(td); (t / 'flag').touch()
        g = goal_file(t, [task('quoted', verify='cd {root} && test -f flag'), task('relative', verify='test -f flag')])
        r = sh([sys.executable, str(REPO / 'goal.py'), 'verify', str(g)], cwd='/')
        check(r.returncode == 0 and all(x['passes'] for x in json.loads(g.read_text())['tasks']), f'root 错误: {r.stdout}{r.stderr}')



def goal_fuse_lock():
    import fcntl, hashlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); g = goal_file(t, [task('A')]); env = dict(os.environ, AIW_STATE=str(t / 'state'))
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g)]
        for expected in (1, 1, 3):
            r = sh(cmd, env=env); check(r.returncode == expected, f'保险丝错误: {r.stdout}')
        lock = t / 'state' / ('goal-' + hashlib.sha1(str(g.resolve()).encode()).hexdigest()[:12] + '.lock')
        before = g.read_text()
        with lock.open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            r = sh(cmd, env=env)
            check(r.returncode == 75 and g.read_text() == before, '并发锁失效')



def goal_fuse_preflight():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); g = goal_file(t, [task('A', verify='touch marker; false')])
        d = json.loads(g.read_text()); d['rounds'] = [{'flipped': []}] * 3; g.write_text(json.dumps(d))
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g)]
        r = sh(cmd)
        check(r.returncode == 3 and not (t / 'marker').exists(), '保险丝未阻止执行')
        r = sh(cmd + ['--force'])
        check((t / 'marker').exists() and r.returncode == 3, 'force 未执行')
        g = goal_file(t, [task('A', verify='true')]); d = json.loads(g.read_text())
        d['fuse']['max_rounds'] = 1; g.write_text(json.dumps(d))
        r = sh(cmd)
        check(r.returncode == 0 and '自动任务全部通过' in r.stdout, '全部通过未优先于保险丝')
        r = sh(cmd)
        check(r.returncode == 3, '达到轮次上限仍执行')



def goal_verify_integrity():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); g = goal_file(t, [task('A', verify='false'), task('B', verify='touch marker')])
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g)]
        check(sh(cmd).returncode == 1, '首轮失败码错误')
        (t / 'marker').unlink()
        d = json.loads(g.read_text()); d['tasks'][0]['verify'] = 'true'; g.write_text(json.dumps(d))
        r = sh(cmd + ['B'])
        check(r.returncode == 65 and 'A' in r.stderr and not (t / 'marker').exists(), '变更应阻止所有执行')
        r = sh([sys.executable, str(REPO / 'goal.py'), 'status', str(g)])
        check('⚠ verify 已变更' in r.stdout, '状态未提示变更')
        r = sh(cmd + ['--accept-verify-change', 'A', '--force'])
        d = json.loads(g.read_text())
        check(r.returncode == 0 and d['tasks'][0]['verify_sha'] == hashlib.sha256(b'true').hexdigest()[:16], '哈希未接受')
        check(d['rounds'][-1]['verify_changed'] == ['A'], '变更记录缺失')
        check(sh(cmd + ['B', '--force', '--accept-verify-change']).returncode == 0, 'id 前后参数未支持')
        before = g.read_text()
        check(sh(cmd + ['--forse']).returncode == 64 and g.read_text() == before, '未知开关未拒绝')
        check(sh(cmd + ['A', 'B']).returncode == 64, '多个 id 未拒绝')



def goal_paths_skip():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'a' / 'nested').mkdir(parents=True)
        (t / 'a' / 'nested' / 'input.txt').write_text('a')
        (t / 'b.txt').write_text('b')
        g = goal_file(t, [dict(task('A', verify='echo run >> A.marker'), paths=['a/**']),
                          dict(task('B', verify='echo run >> B.marker'), paths=['b.txt'])])
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g)]
        env = dict(os.environ, AIW_STATE=str(t / 'state'))

        def run(*args):
            r = sh(cmd + list(args), env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            return r

        def counts():
            return [(t / (id + '.marker')).read_text().count('run') for id in ('A', 'B')]

        r = run()
        check(counts() == [1, 1], '首轮未全部执行')
        check('宣告完成前先跑一次 aiw goal verify <文件> --full' in r.stdout, '收尾提示缺失')
        check(all(len(a['paths_sha']) == 16 for a in json.loads(g.read_text())['tasks']), '未保存 paths 哈希')
        (t / 'b.txt').write_text('changed')
        r = run()
        check('[A] SKIP 相关文件未变' in r.stdout and counts() == [1, 2], '未仅执行 B')
        r = run()
        d = json.loads(g.read_text())
        check(counts() == [1, 2] and d['rounds'][-1]['skipped'] == ['A', 'B'], '未全部跳过')
        check(d['rounds'][-1]['flipped'] == [] and d['rounds'][-1]['regressed'] == [], '跳过误计进展或回归')
        run('--full')
        check(counts() == [2, 3] and json.loads(g.read_text())['rounds'][-1]['skipped'] == [], 'full 未全部执行')
        check(sh(cmd, env=env).returncode == 3, '跳过轮次未计入无进展保险丝')
        run('--force', 'A')
        check(counts() == [3, 3], '单任务不应跳过')
        (t / 'a' / 'nested' / 'input.txt').write_text('new a')
        run('--force')
        check(counts() == [4, 3], '递归文件变更未使缓存失效')
        d = json.loads(g.read_text()); d['tasks'][0]['passes'] = False; g.write_text(json.dumps(d))
        run('--force')
        check(counts() == [5, 3], '未通过任务被跳过')
        d = json.loads(g.read_text()); d['tasks'][0]['verify'] += '; true'; g.write_text(json.dumps(d))
        before = g.read_text()
        r = sh(cmd + ['--force'], env=env)
        check(r.returncode == 65 and g.read_text() == before and counts() == [5, 3], 'A3 未优先于跳过')
        run('--accept-verify-change', '--force')
        check(counts() == [6, 3], '接受新命令后仍跳过')
        # 单任务接受变更时，另一个任务的旧缓存也必须失效。
        d = json.loads(g.read_text()); d['tasks'][1]['verify'] += '; true'; g.write_text(json.dumps(d))
        run('--full', '--accept-verify-change', 'A', '--force')
        check(counts() == [7, 3], '组合开关或单任务范围错误')
        run('--force')
        check(counts() == [7, 4], '未执行的新命令复用了旧 paths 缓存')



def goal_paths_empty_and_legacy():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        g = goal_file(t, [dict(task('empty', verify='echo run >> empty.marker'), paths=['missing/**']),
                          task('legacy', verify='echo run >> legacy.marker')])
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g)]
        env = dict(os.environ, AIW_STATE=str(t / 'state'))
        for args in ([], [], ['--full']):
            r = sh(cmd + args, env=env)
            check(r.returncode == 0 and '[empty] paths 没有匹配到文件' in r.stdout, '空匹配未警告')
            check('SKIP' not in r.stdout, '空匹配或无 paths 被跳过')
        check(all((t / (id + '.marker')).read_text().count('run') == 3 for id in ('empty', 'legacy')), '未每轮执行')
        check(all('paths_sha' not in a for a in json.loads(g.read_text())['tasks']), '无匹配仍保存缓存')



def goal_paths_fingerprint():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'src' / 'nested').mkdir(parents=True)
        f = t / 'src' / 'nested' / 'f'; f.write_bytes(b'first')
        g = goal_file(t, [dict(task('A', verify='echo run >> marker'), paths=['src'])])
        cmd = [sys.executable, str(REPO / 'goal.py'), 'verify', str(g), '--force']
        env = dict(os.environ, AIW_STATE=str(t / 'state'))

        def run():
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            return json.loads(g.read_text())['tasks'][0].get('paths_sha')

        original = run()
        d = json.loads(g.read_text()); d['tasks'][0]['paths'] = ['src/**', 'src', 'src/nested/*']; g.write_text(json.dumps(d))
        check(run() == original and (t / 'marker').read_text().count('run') == 1, '目录匹配、去重或排序不稳定')
        f.touch()
        check(run() == original, '仅时间戳改变不应改变哈希')
        f.write_bytes(b'changed')
        modified = run(); check(modified != original, '内容未参与哈希')
        f.rename(f.with_name('renamed'))
        renamed = run(); check(renamed != modified, '路径未参与哈希')
        f.write_bytes(b'added')
        added = run(); check(added != renamed, '新增文件未参与哈希')
        f.unlink()
        check(run() == renamed, '删除文件未参与哈希')
        f.with_name('renamed').unlink()
        check(run() is None, '空匹配未清理旧缓存')
        init = t / 'template.json'
        r = sh([sys.executable, str(REPO / 'goal.py'), 'init', str(init), 'demo'], env=env)
        check(r.returncode == 0 and json.loads(init.read_text())['tasks'][0]['paths'], '模板缺少 paths 示例')



CASES = {case.__name__: case for case in (
    goal_init_mkdir,
    goal_help_no_write,
    goal_init_unknown_option,
    goal_verify_failure_tail,
    goal_regression,
    goal_next_human,
    verify_root,
    goal_fuse_lock,
    goal_fuse_preflight,
    goal_verify_integrity,
    goal_paths_skip,
    goal_paths_empty_and_legacy,
    goal_paths_fingerprint,
)}
