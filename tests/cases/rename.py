"""Rename compatibility regressions using isolated installations."""
from support import Path, REPO, check, fake_home, json, os, sh, sys, tempfile


def rename_env():
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        for key in ('AIW_STATE', 'AI_HARNESS_STATE'):
            env.pop(key, None)
        script = 'from env_compat import getenv; print(getenv("AIW_STATE", "default")); print(getenv("AIW_STATE"))'
        for values, expected, warnings in (({'AI_HARNESS_STATE': 'old'}, 'old', 0),
                ({'AIW_STATE': 'new'}, 'new', 0),
                ({'AIW_STATE': 'new', 'AI_HARNESS_STATE': 'old'}, 'new', 1),
                ({'AIW_STATE': '', 'AI_HARNESS_STATE': 'old'}, '', 1)):
            r = sh([sys.executable, '-c', script], env=dict(env, **values), cwd=REPO)
            check(r.returncode == 0 and r.stdout.splitlines()[0] == expected, r.stdout + r.stderr)
            check(r.stderr.count('值不同') == warnings, r.stderr)
        # Shell children see the selected value, without another conflict warning.
        for new, old in (('AIW_STATE', 'AI_HARNESS_STATE'), ('AIW_PYTHON', 'AI_HARNESS_PYTHON'), ('AIW_NO_CODEX', 'AI_HARNESS_NO_CODEX')):
            script = 'source "$1/env_compat.sh"; printenv "$2"'
            r = sh(['bash', '-c', script, 'bash', str(REPO), new], env=dict(env, **{new: 'new', old: 'old'}))
            check(r.stdout.strip() == 'new' and r.stderr.count('值不同') == 1, r.stdout + r.stderr)


def rename_install():
    for mode in ('settings', 'plugin'):
        with tempfile.TemporaryDirectory() as td:
            home, env = fake_home(Path(td))
            env.pop('AIW_NO_CODEX', None)
            repo = home / '.claude/aiw'
            legacy = home / '.claude/ai-harness'
            repo.rename(legacy)
            codex = home / '.codex'; codex.mkdir(exist_ok=True)
            (home / '.claude/CLAUDE.md').write_text('before\n@~/.claude/ai-harness/CLAUDE.md\nafter\n')
            hook = {'hooks': {'UserPromptSubmit': [{'hooks': [
                {'type': 'command', 'command': 'python3 "$HOME/.claude/ai-harness/route.py" --hook'},
                {'type': 'command', 'command': 'echo mine'}]}]}, 'other': 42}
            for p in (home / '.claude/settings.json', codex / 'hooks.json'):
                p.write_text(json.dumps(hook))
            (codex / 'AGENTS.md').write_text('before\n<!-- ai-harness:begin（install.sh 生成，勿手改） -->\nOLD\n<!-- ai-harness:end -->\nafter\n')
            (home / '.zshrc').write_text('before\n# ai-harness: aiw on PATH\nexport PATH="$HOME/.local/bin:$PATH"\nafter\n')
            agents = home / 'Library/LaunchAgents'; agents.mkdir(parents=True)
            (agents / 'com.ai-harness.freecheck.plist').write_text('legacy')
            prior_env = dict(env, AI_HARNESS_STATE=env['AIW_STATE'])
            prior_env.pop('AIW_STATE')
            prior = sh([sys.executable, str(legacy / 'ai_worker.py'), 'doctor'], env=prior_env)
            check(prior.returncode == 0, prior.stdout + prior.stderr)
            for message in ('旧引入行', '旧钩子命令', '旧 ai-harness 标记块', '旧 label', '旧变量', '旧安装路径'):
                check(message in prior.stdout, prior.stdout)
            files = [home / '.claude/CLAUDE.md', home / '.claude/settings.json', codex / 'AGENTS.md', codex / 'hooks.json', home / '.zshrc']
            inst = ['bash', str(legacy / 'install.sh'), '--claude-hook=' + mode]
            r = sh(inst, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(repo.is_symlink() and repo.resolve() == legacy.resolve(), 'missing new install link')
            first = [p.read_bytes() for p in files]
            r = sh(inst, env=env)
            check(r.returncode == 0 and first == [p.read_bytes() for p in files], 'migration not idempotent: ' + r.stdout + r.stderr)
            md = files[0].read_text()
            check(md == 'before\n@~/.claude/aiw/CLAUDE.md\nafter\n', md)
            for p in (files[1], files[3]):
                data = json.loads(p.read_text()); commands = [h['command'] for g in data['hooks']['UserPromptSubmit'] for h in g['hooks']]
                check(data['other'] == 42 and 'echo mine' in commands, str(data))
                check(not any('ai-harness/route.py' in c for c in commands), str(commands))
                check(sum('aiw/route.py' in c for c in commands) == (0 if mode == 'plugin' and p == files[1] else 1), str(commands))
            check(files[2].read_text().count('aiw:begin') == 1 and 'OLD' not in files[2].read_text(), 'old Codex block remains')
            check(files[4].read_text() == 'before\n# aiw: on PATH\nexport PATH="$HOME/.local/bin:$PATH"\nafter\n', 'zsh migration')
            repo.unlink()
            env['AI_HARNESS_STATE'] = env.pop('AIW_STATE')
            r = sh([sys.executable, str(legacy / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0 and '旧安装路径，重跑 install.sh 迁移' in r.stdout and '旧变量 AI_HARNESS_STATE' in r.stdout, r.stdout + r.stderr)


def rename_alias():
    # Real reader normalizes records without modifying stored bytes.
    sys.path.insert(0, str(REPO))
    import findings as f
    import triage as t
    from repo_aliases import canonical_repo
    old, new = 'github.com/jerryx333/ai-harness', 'github.com/jerryx333/aiw'
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'records.jsonl'
        rows = [{'id': 'old', 'repo': old, 'type': 'observation'}, {'id': 'new', 'repo': new, 'type': 'observation'}]
        raw = ''.join(json.dumps(r) + '\n' for r in rows); p.write_text(raw)
        loaded = list(f._rows(p))
        check(len(t.overview(rows)['repo']) == len(t.grouping(loaded)['repo']) == 1, str(loaded))
        check(p.read_text() == raw and f._repo('git@github.com:Jerryx333/ai-harness.git') == new, 'identity or storage changed')
        mapping = {'jerryx333/ai-harness#project:jerryx333/ai-harness:bug': 7}
        original = f._command
        def no_command(argv):
            raise AssertionError('existing mapping must not create/query issue: ' + str(argv))
        f._command = no_command
        try:
            number = t.persist_issue(Path(td) / 'issues.json', mapping,
                'jerryx333/aiw#project:jerryx333/aiw:bug', 'Jerryx333/aiw', 'title', 'body', [], 'aiw-key: project:jerryx333/aiw:bug')
            check(number == 7, str(mapping))
        finally:
            f._command = original
        calls = []
        def legacy_issue(argv):
            calls.append(argv)
            check(argv[1:3] == ['issue', 'list'], 'duplicate issue creation: ' + str(argv))
            return json.dumps([{'number': 7, 'body': 'aiw-key: project:jerryx333/ai-harness:bug'}]) if 'ai-harness' in argv[argv.index('--search') + 1] else '[]'
        f._command = legacy_issue
        try:
            number = t.persist_issue(Path(td) / 'issues.json', {},
                'jerryx333/aiw#project:jerryx333/aiw:bug', 'Jerryx333/aiw', 'title', 'body', [], 'aiw-key: project:jerryx333/aiw:bug')
            check(number == 7 and len(calls) == 2, str(calls))
        finally:
            f._command = original


CASES = {case.__name__: case for case in (rename_env, rename_install, rename_alias)}
