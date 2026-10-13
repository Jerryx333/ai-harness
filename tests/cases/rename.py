"""Repository history identities and isolated installation regressions."""
from support import Path, REPO, check, fake_home, json, os, sh, sys, tempfile


def rename_alias():
    # Real reader normalizes records without modifying stored bytes.
    sys.path.insert(0, str(REPO))
    import findings as f
    import triage as t
    from repo_aliases import canonical_repo, canonical_key
    for key in ('jerryx333/ai-harness-tools#bug', 'aiw-key: project:jerryx333/ai-harness-tools:bug'):
        check(canonical_key(key) == key, 'unrelated repository rewritten: ' + key)
    check(canonical_key('aiw-key: project:JERRYX333/AI-HARNESS:bug') == 'aiw-key: project:jerryx333/aiw:bug', 'case insensitive identity')
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
                'jerryx333/aiw#project:jerryx333/aiw:bug', 'Jerryx333/aiw', 'title', 'body', [], 'aiw-key: project:JERRYX333/AI-HARNESS:bug')
            check(number == 7 and len(calls) == 2, str(calls))
        finally:
            f._command = original

        # Replayed decisions may still supply the old key and marker.
        calls.clear()
        f._command = legacy_issue
        try:
            number = t.persist_issue(Path(td) / 'replay.json', {},
                'jerryx333/ai-harness#project:jerryx333/ai-harness:bug', 'Jerryx333/ai-harness',
                'title', 'body\naiw-key: project:jerryx333/ai-harness:bug', [],
                'aiw-key: project:jerryx333/ai-harness:bug')
            check(number == 7 and len(calls) == 2, str(calls))
        finally:
            f._command = original
        created = []
        def create_issue(argv):
            if argv[1:3] == ['issue', 'list']:
                return '[]'
            check(argv[1:3] == ['issue', 'create'], str(argv))
            created.append(argv)
            return 'https://github.com/Jerryx333/aiw/issues/8'
        f._command = create_issue
        try:
            marker = 'aiw-key: project:JERRYX333/AI-HARNESS:bug'
            t.persist_issue(Path(td) / 'created.json', {}, 'jerryx333/ai-harness#project:jerryx333/ai-harness:bug',
                'Jerryx333/aiw', 'title', 'keep jerryx333/ai-harness-tools\n' + marker, [], marker)
            check(created[0][created[0].index('--body') + 1] == 'keep jerryx333/ai-harness-tools\naiw-key: project:jerryx333/aiw:bug', str(created))
            mapping = {'jerryx333/ai-harness-tools#project:jerryx333/ai-harness-tools:bug': 9}
            f._command = no_command
            check(t.persist_issue(Path(td) / 'other.json', mapping, next(iter(mapping)), 'jerryx333/ai-harness-tools',
                'title', 'body', [], 'aiw-key: project:jerryx333/ai-harness-tools:bug') == 9, str(mapping))
        finally:
            f._command = original


def rename_install_target():
    for kind in ('directory', 'symlink', 'dangling'):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home, env = fake_home(root)
            repo = home / '.claude/aiw'
            source = home / '.claude/source'
            repo.rename(source)
            other = root / 'other'
            if kind != 'dangling':
                other.mkdir()
            if kind == 'directory':
                repo.mkdir()
                (repo / 'sentinel').write_text('untouched')
            else:
                repo.symlink_to(other)
            before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            result = sh(['bash', str(source / 'install.sh')], env=env)
            check(result.returncode != 0 and '~/.claude/aiw 指向另一份 checkout：' in result.stderr, result.stdout + result.stderr)
            check(before == sorted(str(p.relative_to(root)) for p in root.rglob('*')), 'failed preflight modified HOME')
            if kind == 'directory':
                check((repo / 'sentinel').read_text() == 'untouched', 'directory modified')
            probe = 'from pathlib import Path; import doctor_config; doctor_config.check_config(Path("' + str(source) + '"), include_local=False)'
            result = sh([sys.executable, '-c', probe], env=env, cwd=source)
            check('⚠ 安装链接 aiw' in result.stdout and '指向另一份 checkout' in result.stdout, result.stdout + result.stderr)


def rename_hook_duplicates():
    for commands in (('aiw', 'aiw'),):
        with tempfile.TemporaryDirectory() as td:
            home, env = fake_home(Path(td))
            env.pop('AIW_NO_CODEX', None)
            (home / '.codex').mkdir(exist_ok=True)
            files = [home / '.claude/settings.json', home / '.codex/hooks.json']
            data = {'other': 42, 'hooks': {'UserPromptSubmit': [
                {'matcher': '*', 'extra': 1, 'hooks': [{'type': 'command', 'command': 'python3 ' + commands[0] + '/route.py'}, {'type': 'command', 'command': 'echo other'}]},
                {'matcher': 'second', 'extra': 2, 'hooks': [{'type': 'command', 'command': 'python3 ' + commands[1] + '/route.py'}]}]}}
            for path in files:
                path.write_text(json.dumps(data))
            command = ['bash', str(home / '.claude/aiw/install.sh')]
            result = sh(command, env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            for path in files:
                value = json.loads(path.read_text())
                groups = value['hooks']['UserPromptSubmit']
                check([(g['matcher'], g['extra']) for g in groups] == [('*', 1), ('second', 2)], str(value))
                hooks = [h for g in groups for h in g['hooks']]
                check(sum('aiw/route.py' in h['command'] for h in hooks) == 1 and any(h['command'] == 'echo other' for h in hooks), str(value))
                check(value['other'] == 42, str(value))
            before = [p.read_bytes() for p in files]
            result = sh(command, env=env)
            check(result.returncode == 0 and before == [p.read_bytes() for p in files], 'duplicate removal not idempotent')
            result = sh(command + ['--claude-hook=plugin'], env=env)
            groups = json.loads(files[0].read_text())['hooks']['UserPromptSubmit']
            check(result.returncode == 0 and len(groups) == 2 and groups[1]['hooks'] == [], str(groups))


def rename_empty_python_state():
    for python in ('', None):
        with tempfile.TemporaryDirectory() as td:
            home, env = fake_home(Path(td))
            env.pop('AIW_PYTHON', None)
            if python is not None:
                env['AIW_PYTHON'] = python
            env['AIW_STATE'] = '~/expanded-state'
            repo = home / '.claude/aiw'
            result = sh(['bash', str(repo / 'install.sh')], env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            result = sh(['bash', str(repo / 'bin/aiw'), 'route', '--check'], env=env)
            check(result.returncode == 0, result.stdout + result.stderr)
            probe = 'from env_compat import getenv; import ai_worker, route, findings, panel, map, handoff, tokens, weekly; print(getenv("AIW_PYTHON")); print(getenv("AIW_STATE")); print("\\n".join(str(m.STATE) for m in (ai_worker, route, findings, panel, map, handoff, tokens, weekly)))'
            result = sh([sys.executable, '-c', probe], env=env, cwd=repo)
            lines = result.stdout.splitlines()
            check(result.returncode == 0 and lines[0] == 'python3' and len(lines) == 10 and all(line == str(home / 'expanded-state') for line in lines[1:]), result.stdout + result.stderr)


def rename_launch_failure():
    import plistlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        bins = root / 'launchbin'; bins.mkdir()
        launch = bins / 'launchctl'
        launch.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LAUNCH_LOG"\n[ "$1" = bootstrap ] && exit 1\nexit 0\n')
        launch.chmod(0o755)
        directory = root / 'agents'; directory.mkdir()
        env.update(PATH=str(bins) + ':' + env['PATH'], AIW_LAUNCH_AGENTS_DIR=str(directory), LAUNCH_LOG=str(root / 'launch.log'))
        result = sh(['bash', str(home / '.claude/aiw/bin/aiw'), 'freecheck', '--install'], env=env)
        check(result.returncode != 0 and '失败' in result.stderr, result.stdout + result.stderr)
        calls = (root / 'launch.log').read_text()
        check('bootstrap' in calls, 'bootstrap not attempted: ' + calls)
        check(plistlib.loads((directory / 'com.aiw.freecheck.plist').read_bytes())['Label'] == 'com.aiw.freecheck', 'new plist invalid')


CASES = {case.__name__: case for case in (rename_alias, rename_install_target, rename_hook_duplicates, rename_empty_python_state, rename_launch_failure)}
