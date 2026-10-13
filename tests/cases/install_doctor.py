"""install doctor regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    git_template,
    Path,
    REPO,
    check,
    codex_home_fixture,
    fake_agent_use,
    fake_bins,
    fake_home,
    free_guard_fixture,
    json,
    os,
    sh,
    shutil,
    sys,
    tempfile,
    write_legacy_local,
)


def doctor():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        env = dict(os.environ, PATH=f"{fake_bins(t)}:{os.environ['PATH']}", AI_HARNESS_STATE=str(t / "state"),
                   AGENT_USE_HOME=str(fake_agent_use(t)))
        versions = t / 'versions'
        codex = t / 'fakebin/codex'
        codex.write_text(codex.read_text().replace('echo "codex-cli fake"',
            'echo version >> "$FAKE_VERSION_LOG"; echo "codex-cli fake"'))
        env['FAKE_VERSION_LOG'] = str(versions)
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=env)
        check(p.returncode == 0, f"doctor 应成功: {p.stdout}{p.stderr}")
        for s in ("qoder", "workbuddy", "antigravity", "codex", "host:", "agent-use:qoder", "agent-use:doubao", "codex-cli fake"):
            check(s in p.stdout, f"doctor 输出缺 {s}: {p.stdout}")
        check("仅免费模型" in p.stdout and "硬禁工具" in p.stdout, "doctor 应标出免费模型与工具策略")
        check(versions.read_text().splitlines() == ['version'], '同次 doctor 重复探测同一 CLI')
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=dict(env, AGENT_USE_HOME=str(t / "nope")))
        check(p.returncode == 1 and "AGENT_USE_HOME" in p.stdout, f"agent-use 缺失时应 rc=1 并提示: {p.stdout}")
        check(versions.read_text().splitlines() == ['version', 'version'], 'CLI 版本缓存跨 doctor 运行泄漏')
        p = sh(["bash", str(REPO / "bin" / "aiw"), "doctor"], env=dict(env, CODEX_BIN="/nonexistent/codex"))
        check(p.returncode == 1 and "未找到" in p.stdout, f"缺 CLI 时应报未找到并 rc=1: {p.stdout}")



def doctor_config():
    import importlib.util
    spec = importlib.util.spec_from_file_location('doctor_config_checks', REPO / 'doctor_config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        home, env = fake_home(t)
        env.pop('AI_HARNESS_NO_CODEX', None)
        env.pop('CODEX_BIN', None)
        repo = home / '.claude/ai-harness'
        # 隔离 PATH，避免本机已安装的看板影响测试。
        bins = t / 'fakebin'
        for name in ('bash', 'dirname', 'readlink', 'python3', 'git', 'mkdir', 'chmod', 'ln', 'grep', 'cp', 'date'):
            path = shutil.which(name)
            if path and not (bins / name).exists():
                (bins / name).symlink_to(path)
        env['PATH'] = str(bins)
        p = sh(['bash', str(repo / 'install.sh')], env=env)
        check(p.returncode == 0, p.stdout + p.stderr)
        settings = home / '.claude/settings.json'
        hp = home / '.codex/hooks.json'
        config = home / '.codex/config.toml'
        md = home / '.claude/CLAUDE.md'
        agents = home / '.codex/AGENTS.md'
        original = {f: f.read_text() for f in (settings, hp, md, agents)}

        def run(*expected, **overrides):
            # Configuration variants exercise the same production checks directly;
            # CLI installation/version/exit behavior is covered by doctor().
            probe = "from pathlib import Path; import doctor_config as d, sys; d.check_config(Path(sys.argv[1]), include_local=False)"
            p = inprocess_sh([sys.executable, '-c', probe, str(repo)], cwd=repo, env=dict(env, **overrides))
            check(p.returncode == 0, f'配置改变返回码：{p.stdout}{p.stderr}')
            for text in expected:
                check(text in p.stdout, f'缺少 {text}: {p.stdout}')
            check('PRIVATE_SENTINEL' not in p.stdout + p.stderr, '泄露配置内容')
            return p.stdout

        def trusted(path=hp, g=0, i=0, enabled=True):
            key = f'{path}:user_prompt_submit:{g}:{i}'
            config.write_text(f'[hooks.state.{json.dumps(key)}]\ntrusted_hash = "PRIVATE_SENTINEL"\nenabled = {str(enabled).lower()}\n')

        for path in (hp, hp.resolve()):
            trusted(path)
            run('✓ Claude 派单钩子', '✓ CLAUDE.md 引入', '✓ 安装链接 harness',
                '✓ 安装链接 aiw', '✓ Codex 信任 [0:0]', '✓ Codex AGENTS 标记块',
                '无法确认是否过期', '· 仓库状态：不是 git 仓库', '⚠ aiw-board 转发')
        config.write_text('')
        run('⚠ Codex 信任', 'Codex 会跳过这个钩子')
        trusted(enabled=False)
        run('⚠ Codex 信任', '被禁用')
        trusted()
        config.write_text(config.read_text() + '\n[features]\nhooks = false\n')
        run('Codex 关闭了钩子功能')
        trusted()
        data = json.loads(original[hp])
        data['hooks']['UserPromptSubmit'].insert(0, {'hooks': [{'command': 'echo other'}]})
        hp.write_text(json.dumps(data))
        run('⚠ Codex 信任 [1:0]')
        trusted(g=1)
        run('✓ Codex 信任 [1:0]')
        hp.write_text(original[hp])
        data = json.loads(original[hp])
        data['hooks']['UserPromptSubmit'][0]['hooks'].insert(0, {'command': 'echo other'})
        hp.write_text(json.dumps(data))
        trusted()
        run('⚠ Codex 信任 [0:1]')
        trusted(i=1)
        run('✓ Codex 信任 [0:1]')
        data['hooks']['UserPromptSubmit'][0]['hooks'][1]['timeout'] = 99
        hp.write_text(json.dumps(data))
        run('⚠ Codex 派单钩子', '命令或 timeout')
        hp.write_text(original[hp])
        trusted()
        data = json.loads(original[settings])
        data['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] += ' --changed'
        settings.write_text(json.dumps(data))
        md.write_text('# missing import\n')
        agents.write_text(original[agents].replace(module.BEGIN + '\n', module.BEGIN + '\nchanged\n'))
        run('⚠ Claude 派单钩子', '⚠ CLAUDE.md 引入', '⚠ Codex AGENTS 标记块')
        out = run('· 跳过 Codex', AI_HARNESS_NO_CODEX='1')
        check('⚠ Codex' not in out and '✓ Codex' not in out, out)
        for f, text in original.items():
            f.write_text(text)
        data = json.loads(original[settings])
        script = home / 'aiw-board/bin/aiw-statusline'
        data['statusLine'] = {'command': f'"{script}"'}
        settings.write_text(json.dumps(data))
        run('⚠ 状态栏命令', '状态栏会静默失效')
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\nexit 0\n')
        script.chmod(0o755)
        (bins / 'aiw-board').symlink_to(script)
        run('✓ 状态栏命令', '✓ aiw-board 转发')
        script.chmod(0o644)
        run('⚠ 状态栏命令', '⚠ aiw-board 转发')
        settings.write_text(original[settings])
        # 缺文件、坏 JSON、重复和缺失钩子均只提示。
        for text, expected in (('{bad', 'JSONDecodeError'), ('{}', '没有 harness 钩子'),
                               ('{"hooks": {"UserPromptSubmit": null}}', 'ValueError')):
            settings.write_text(text)
            run('⚠ Claude 派单钩子', expected)
        data = json.loads(original[settings])
        data['hooks']['UserPromptSubmit'] *= 2
        settings.write_text(json.dumps(data))
        run('有多个 harness 钩子')
        settings.unlink()
        run('⚠ Claude 派单钩子：文件不存在')
        settings.write_text(original[settings])
        # Plugin-managed Claude hooks need no settings hook; duplicate sources warn.
        plugin_name = 'ai-harness@test-marketplace'
        plugin_dir = home / '.claude/plugins/cache/test-marketplace/ai-harness/0.1.0'
        plugin_hooks = plugin_dir / 'hooks/hooks.json'
        plugin_hooks.parent.mkdir(parents=True)
        plugin_data = {'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command',
            'command': '"${CLAUDE_PLUGIN_ROOT}/scripts/route-hook.sh"', 'timeout': 10}]}]}}
        plugin_hooks.write_text(json.dumps(plugin_data))
        installed = home / '.claude/plugins/installed_plugins.json'
        records = {plugin_name: [{'scope': 'user', 'installPath': str(plugin_dir)}]}
        data = json.loads(original[settings])
        data['enabledPlugins'] = {plugin_name: True}
        settings.write_text(json.dumps(data))
        installed.write_text(json.dumps({'plugins': records}))
        out = run(f'⚠ Claude 派单钩子：settings.json 与插件 {plugin_name} 都有派单钩子，会重复执行',
                  '重跑 install.sh --claude-hook=plugin')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks']['UserPromptSubmit'] = []
        settings.write_text(json.dumps(data))
        for metadata in ({'plugins': records}, records):
            installed.write_text(json.dumps(metadata))
            out = run(f'✓ Claude 派单钩子（由插件 {plugin_name} 提供）')
            check('⚠ Claude 派单钩子' not in out, out)
        plugin_hooks.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'type': 'command', 'command': 'echo other'}]}]}}))
        run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        plugin_hooks.write_text(json.dumps({'hooks': {'UserPromptSubmit': [
            {'hooks': [{'type': 'command', 'command': module.HOOK_COMMAND}]}]}}))
        run(f'✓ Claude 派单钩子（由插件 {plugin_name} 提供）')
        plugin_hooks.write_text(json.dumps(plugin_data))
        multiple_names = ['ai-harness@a', 'ai-harness@b']
        multiple_records = {name: records[plugin_name] * 2 for name in multiple_names}
        data['enabledPlugins'] = {name: True for name in reversed(multiple_names)}
        settings.write_text(json.dumps(data))
        installed.write_text(json.dumps({'plugins': multiple_records}))
        out = run('⚠ Claude 派单钩子：多个插件（ai-harness@a、ai-harness@b）都有派单钩子，会重复执行。修：在 Claude Code 中只保留一个 ai-harness 插件启用')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks'] = json.loads(original[settings])['hooks']
        settings.write_text(json.dumps(data))
        out = run('⚠ Claude 派单钩子：settings.json 与插件 ai-harness@a、ai-harness@b 都有派单钩子，会重复执行',
                  '重跑 install.sh --claude-hook=plugin')
        check('✓ Claude 派单钩子' not in out, out)
        data['hooks']['UserPromptSubmit'] = []
        data['enabledPlugins'] = {plugin_name: True}
        installed.write_text(json.dumps({'plugins': records}))
        data['enabledPlugins'][plugin_name] = False
        settings.write_text(json.dumps(data))
        run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        data['enabledPlugins'][plugin_name] = True
        settings.write_text(json.dumps(data))
        for broken in ('{PRIVATE_SENTINEL', '[]', '{"hooks": {"UserPromptSubmit": {}}}',
                       '{"hooks": {"UserPromptSubmit": [{"hooks": null}]}}',
                       '{"hooks": {"UserPromptSubmit": [{"hooks": [null]}]}}'):
            plugin_hooks.write_text(broken)
            run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        plugin_hooks.unlink()
        run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        plugin_hooks.write_text(json.dumps(plugin_data))
        for broken in ('{PRIVATE_SENTINEL', '[]', '{"plugins": []}',
                       json.dumps({plugin_name: {'installPath': str(plugin_dir)}})):
            installed.write_text(broken)
            run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        installed.unlink()
        run('⚠ Claude 派单钩子：没有 harness 钩子。修：重跑 install.sh')
        settings.write_text(original[settings])
        config.unlink()
        run('⚠ Codex 配置：文件不存在')
        config.write_text('secret = "PRIVATE_SENTINEL"\nbroken = [')
        run('⚠ Codex 配置', 'TOMLDecodeError')
        hp.unlink()
        run('· 未接入 Codex')
        hp.write_text(original[hp])
        trusted()
        link = home / '.local/bin/aiw'
        link.unlink()
        link.symlink_to(t / 'wrong')
        run('⚠ 安装链接 aiw', '实际指向')
        link.unlink()
        link.symlink_to(repo / 'bin/aiw')
        # 本地提交和上游比较无需联网。
        def git(*args):
            p = sh(['git', '-C', str(repo), *args], env=env)
            check(p.returncode == 0, p.stderr)
        git('init')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'Test')
        git('add', '.')
        git('commit', '-m', 'initial')
        run('✓ 仓库状态（无未提交改动）', '· 仓库上游')
        git('branch', 'upstream-test')
        git('branch', '--set-upstream-to=upstream-test')
        run('领先 0 / 落后 0（按上次 fetch）')
        (repo / 'README.md').write_text('changed\n')
        run('⚠ 仓库状态：未提交改动 1 项')
        git('add', 'README.md')
        git('commit', '-m', 'ahead')
        run('领先 1 / 落后 0', '还没 push')
        branch = sh(['git', '-C', str(repo), 'branch', '--show-current'], env=env).stdout.strip()
        git('checkout', 'upstream-test')
        git('branch', '--set-upstream-to=' + branch)
        run('领先 0 / 落后 1', 'aiw sync')
        p = sh(['bash', str(repo / 'bin/aiw'), 'doctor'], env=dict(env, CODEX_BIN='/nonexistent/codex'))
        check(p.returncode == 1, '座位失效应仍返回 1')



def install_fresh():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        p = sh(["bash", str(home / ".claude/ai-harness/install.sh")], env=env)
        check(p.returncode == 0, f"安装失败: {p.stdout}{p.stderr}")
        md = (home / ".claude/CLAUDE.md").read_text()
        check("@~/.claude/ai-harness/CLAUDE.md" in md, "CLAUDE.md 未引入")
        st = json.loads((home / ".claude/settings.json").read_text())
        cmds = [h["command"] for g in st["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check(any("$HOME/.claude/ai-harness/route.py" in c for c in cmds), f"钩子未写入或路径写死: {cmds}")
        aiw = home / ".local/bin/aiw"
        check(aiw.is_symlink() and os.access(aiw, os.X_OK), "aiw 软链不存在或不可执行")
        # 通过软链调用（真实使用方式）
        p = sh([str(aiw), "route", "帮我批量重命名所有文件"], env=env, cwd=home)
        check(p.returncode == 0 and '"bulk"' in p.stdout, f"通过软链调用 aiw route 失败: {p.stdout}{p.stderr}")
        check("ai-harness: aiw on PATH" in (home / ".zshrc").read_text(), "PATH 未写入 .zshrc")



def install_existing():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        (home / ".claude/CLAUDE.md").write_text("# 我原来的全局约定\n- 用中文回答\n")
        (home / ".claude/settings.json").write_text(json.dumps({
            "model": "opus", "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}))
        (home / ".codex").mkdir()
        (home / ".codex/AGENTS.md").write_text("# 我的 Codex 约定\n- 中文\n")
        (home / ".codex/hooks.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo codex-mine"}]}],
                                                                    "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}))
        inst = str(home / ".claude/ai-harness/install.sh")
        for _ in range(2):  # 跑两次验证幂等
            p = sh(["bash", inst], env=env)
            check(p.returncode == 0, f"安装失败: {p.stdout}{p.stderr}")
        md = (home / ".claude/CLAUDE.md").read_text()
        check(md.startswith("# 我原来的全局约定") and md.count("@~/.claude/ai-harness/CLAUDE.md") == 1, f"CLAUDE.md 原内容被改或重复追加:\n{md}")
        st = json.loads((home / ".claude/settings.json").read_text())
        check(st["model"] == "opus" and st["permissions"]["allow"] == ["Bash(git status)"], "原 settings 被改")
        cmds = [h["command"] for g in st["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check("echo mine" in cmds and sum("ai-harness/route.py" in c for c in cmds) == 1, f"钩子合并不对: {cmds}")
        check(st["hooks"]["Stop"][0]["hooks"][0]["command"] == "say done", "其它钩子被改")
        baks = list((home / ".claude").glob("*.bak-*"))
        check(len(baks) >= 2, f"应生成 CLAUDE.md 与 settings.json 备份: {baks}")
        check((home / ".zshrc").read_text().count("ai-harness: aiw on PATH") == 1, "PATH 重复追加")
        # Codex 接入：原 AGENTS.md 保留、标记块只有一份；hooks.json 原有钩子保留、派单钩子只加一次
        ag = (home / ".codex/AGENTS.md").read_text()
        check(ag.startswith("# 我的 Codex 约定") and ag.count("ai-harness:begin") == 1 and "AIW_MAIN=codex" in ag, f"AGENTS.md 合并不对:\n{ag[:400]}")
        hk = json.loads((home / ".codex/hooks.json").read_text())
        cmds = [h["command"] for g in hk["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
        check("echo codex-mine" in cmds and sum("ai-harness/route.py" in c for c in cmds) == 1 and hk["hooks"]["Stop"], f"Codex 钩子合并不对: {hk}")
        # 改了 AGENTS.harness.md 再装：标记块被刷新，块外内容不动
        src = home / ".claude/ai-harness/AGENTS.harness.md"
        src.write_text(src.read_text() + "\nNEW_LINE_X\n")
        sh(["bash", inst], env=env)
        ag = (home / ".codex/AGENTS.md").read_text()
        check(ag.count("ai-harness:begin") == 1 and "NEW_LINE_X" in ag and ag.startswith("# 我的 Codex 约定"), "标记块应原地刷新")
        # 坏 settings.json：拒绝改动，不覆盖
        (home / ".claude/settings.json").write_text("{oops")
        p = sh(["bash", inst], env=env)
        check(p.returncode != 0 and (home / ".claude/settings.json").read_text() == "{oops", "坏 JSON 时应拒绝并保持原样")



def install_personal_rules():
    import contextlib
    import io
    import doctor_config as d
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td))
        env.pop('AI_HARNESS_NO_CODEX', None)
        (home / '.codex').mkdir(exist_ok=True)
        repo = home / '.claude/ai-harness'
        md = home / '.claude/CLAUDE.md'
        ag = home / '.codex/AGENTS.md'
        folder = home / '.config/aiw'
        folder.mkdir(parents=True)
        cp, ap = folder / 'CLAUDE.personal.md', folder / 'AGENTS.personal.md'
        public = (repo / 'AGENTS.harness.md').read_text().strip()
        md.write_text('before\n' + d.IMPORT_LINE + '\nafter\n')
        ag.write_text('outside\n')

        def install():
            r = sh(['bash', str(repo / 'install.sh')], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            return r.stdout

        def doctor(*expected):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                d.personal_import(home)
                d.agents(home, repo)
            for text in expected:
                check(text in out.getvalue(), out.getvalue())

        def stable():
            before = (md.read_bytes(), ag.read_bytes())
            backups = sorted(home.rglob('*.bak-*'))
            install()
            check(before == (md.read_bytes(), ag.read_bytes()), '个人规则安装不幂等')
            check(backups == sorted(home.rglob('*.bak-*')), '幂等安装产生额外备份')

        install()
        check(md.read_text() == 'before\n' + d.IMPORT_LINE + '\nafter\n', '无个人文件时改了 Claude 内容')
        check(f'{d.BEGIN}\n{public}\n{d.END}\n' in ag.read_text(), '无个人文件时 Codex 块错误')
        doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
        stable()
        for symlink in (False, True):
            for path, body in ((cp, '  Claude private rule\n'), (ap, '\n  Codex private rule  \n')):
                path.unlink(missing_ok=True)
                if symlink:
                    target = folder / (path.name + '.source')
                    target.write_text(body)
                    path.symlink_to(target)
                else:
                    path.write_text(body)
            doctor('⚠ Claude 个人规则引入', '重跑 install.sh', '⚠ Codex AGENTS 标记块')
            # Deduplicate imports without touching surrounding content.
            md.write_text(d.PERSONAL_LINE + '\nbefore\n' + d.IMPORT_LINE + '\nafter\n' + d.PERSONAL_LINE + '\n')
            old = md.read_text()
            install()
            check(md.read_text() == 'before\n' + d.IMPORT_LINE + '\n' + d.PERSONAL_LINE + '\nafter\n', '个人引入顺序、去重或块外内容错误')
            check(any(p.read_text() == old for p in md.parent.glob('CLAUDE.md.bak-*')), '个人引入修改未备份')
            check(ag.read_text().startswith('outside\n'), 'Codex 块外内容被改')
            check(f'{d.BEGIN}\n{public}\n\nCodex private rule\n{d.END}\n' in ag.read_text(), '个人内容未 strip 或加载顺序错误')
            doctor('✓ Claude 个人规则引入', '✓ Codex AGENTS 标记块')
            stable()
            # Missing files preserve an existing Claude import but refresh Codex.
            cp.unlink()
            ap.unlink()
            install()
            check(d.PERSONAL_LINE in md.read_text().splitlines(), '个人文件移除后不应删除 Claude 引入')
            doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
            md.write_text('before\n' + d.IMPORT_LINE + '\nafter\n')
        for path in (cp, ap):
            path.symlink_to(folder / 'missing')
        output = install()
        check(output.count('是断链，视为无个人规则') == 2, output)
        check(d.PERSONAL_LINE not in md.read_text(), '断链不应引入')
        doctor('无个人规则（可选）', '✓ Codex AGENTS 标记块')
        stable()



def install_claude_hook_mode():
    with tempfile.TemporaryDirectory() as t:
        home, env = fake_home(Path(t))
        inst = ["bash", str(home / ".claude/ai-harness/install.sh")]
        settings = home / ".claude/settings.json"
        (home / ".codex").mkdir()
        route = {"type": "command", "command": 'python3 "$HOME/.claude/ai-harness/route.py" --hook'}
        other = {"type": "command", "command": "echo other", "timeout": 23}
        expected = {"model": "opus", "permissions": {"allow": ["Read"]}, "hooks": {
            "UserPromptSubmit": [{"matcher": "*", "hooks": [other]}, {"hooks": [other]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}}
        data = json.loads(json.dumps(expected))
        data["hooks"]["UserPromptSubmit"][0]["hooks"].extend([route, route])
        data["hooks"]["UserPromptSubmit"].insert(1, {"hooks": [route]})
        original = json.dumps(data, ensure_ascii=False).encode()
        settings.write_bytes(original)

        def run(*args):
            r = sh(inst + list(args), env=env)
            check(r.returncode == 0, f"安装失败: {r.stdout}{r.stderr}")
            return r

        run("--claude-hook=plugin")
        check(json.loads(settings.read_text()) == expected, "plugin 删除范围不正确")
        backups = list(settings.parent.glob("settings.json.bak-*"))
        check(len(backups) == 1 and backups[0].read_bytes() == original, "原文件备份不正确")
        # 固定旧备份名，避免同秒覆盖掩盖重复备份错误。
        backups[0].rename(settings.with_name("settings.json.bak-sentinel"))
        before = settings.read_bytes()
        r = run("--claude-hook=plugin")
        check("✓ 派单钩子由插件管理，settings.json 无需改动" in r.stdout, "缺少无改动说明")
        check(settings.read_bytes() == before and len(list(settings.parent.glob("settings.json.bak-*"))) == 1,
              "重复 plugin 改写了文件或产生新备份")
        codex_hooks = (home / ".codex/hooks.json").read_bytes()
        codex_agents = (home / ".codex/AGENTS.md").read_bytes()
        check("ai-harness/route.py" in codex_hooks.decode() and "ai-harness:begin" in codex_agents.decode(),
              "plugin 影响了 Codex 安装")
        for args in ((), ("--claude-hook=settings",)):
            run(*args)
            hooks = json.loads(settings.read_text())["hooks"]["UserPromptSubmit"]
            check(sum("ai-harness/route.py" in h["command"] for g in hooks for h in g["hooks"]) == 1,
                  "默认恢复或显式 settings 失败")
        check((home / ".codex/hooks.json").read_bytes() == codex_hooks
              and (home / ".codex/AGENTS.md").read_bytes() == codex_agents, "模式切换影响 Codex")
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [route]}]}}))
        run("--claude-hook=plugin")
        check(json.loads(settings.read_text()) == {"hooks": {}}, "空组或空事件键未删除")
        for raw in (b'{ "model": "opus" }\n', b'{broken'):
            settings.write_bytes(raw)
            for args in (("--claude-hook=invalid",), ("--unknown",), ("--claude-hook", "plugin"),
                         ("--claude-hook=plugin", "--unknown")):
                before_files = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
                                if p.is_file() and not p.is_symlink()}
                r = sh(inst + list(args), env=dict(env, AI_HARNESS_PYTHON="/missing/python"))
                check(r.returncode == 64 and "用法：" in r.stderr, "非法参数没有在预检前退出 64")
                after_files = {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
                               if p.is_file() and not p.is_symlink()}
                check(before_files == after_files, "非法参数修改了文件")
        settings.unlink()
        run("--claude-hook=plugin")
        check(not settings.exists(), "plugin 不应新建 settings.json")



def install_preflight():
    with tempfile.TemporaryDirectory() as td:
        home, env = fake_home(Path(td)); inst = ['bash', str(home / '.claude/ai-harness/install.sh')]
        settings = home / '.claude/settings.json'; md = home / '.claude/CLAUDE.md'
        for value in ([], {'hooks': []}, {'hooks': {'UserPromptSubmit': {}}},
                      {'hooks': {'UserPromptSubmit': [1]}}, {'hooks': {'UserPromptSubmit': [{'hooks': {}}]}}):
            settings.write_text(json.dumps(value))
            r = sh(inst, env=env)
            check(r.returncode == 1 and str(settings) in r.stderr and not md.exists(), f'预检改了文件: {r.stderr}')
        md.write_text('保留'); settings.write_text('{"hooks": []}')
        r = sh(inst, env=env); check(r.returncode == 1 and md.read_text() == '保留', '预检覆盖原文件')
        settings.write_text('{}'); (home / '.codex').mkdir(); bad = home / '.codex/hooks.json'; bad.write_text('{bad')
        r = sh(inst, env=dict(env, AI_HARNESS_NO_CODEX='1'))
        check(r.returncode == 0 and bad.read_text() == '{bad', f'跳过 Codex 失败: {r.stderr}')



def doctor_fingerprint():
    import hashlib
    import contextlib
    import io
    from unittest.mock import patch
    import doctor_config as d
    import ai_worker as w
    import seats_config as sc
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); repo = t / 'repo'; repo.mkdir()
        def git(*args):
            r = sh(['git', '-C', str(repo), *args])
            check(r.returncode == 0, r.stderr)
            return r.stdout.strip()
        git_template(repo, {'tracked': 'one'})
        head = git('rev-parse', '--short', 'HEAD')
        with d.memoized_checks(), patch.object(d.subprocess, 'run', wraps=d.subprocess.run) as queries:
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head), '初始指纹错误')
            count = queries.call_count
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head) and queries.call_count == count,
                  '同次 doctor 重复 Git 查询未复用')
        (repo / 'tracked').write_text('changed')
        with d.memoized_checks():
            check(d.git_fingerprint(repo) == (str(repo.resolve()), head + '*'), 'Git 查询缓存跨 doctor 运行泄漏')
        (repo / 'tracked').write_text('one')
        config = sc.load_seats(REPO)
        with patch.dict(os.environ, HOME=str(t), AIW_CONFIG=str(t / 'absent'), AIW_ROUTER_RULES=str(t / 'absent-route'), AGENT_USE_HOME=str(repo), AGENT_USE_CONFIG=str(t / 'missing-registry')):
            data = d.fingerprint(repo, config)
            check(data['harness'] == data['agent_use'] == head, str(data))
            default_home = t / 'code/ai/agent-use'
            default_home.parent.mkdir(parents=True)
            default_home.symlink_to(repo, target_is_directory=True)
            os.environ.pop('AGENT_USE_HOME')
            check(d.fingerprint(repo, config)['agent_use'] == head, 'default agent-use HEAD missing')
            os.environ['AGENT_USE_HOME'] = str(repo)
            check(data['registry'] == {'source': 'env', 'sha256': '-', 'git': '-'}, str(data))
            check(set(data['local'].values()) == {'-'} and data['local_git'] == ['-'], str(data))
            registry = repo / 'registry.json'; registry.write_text('{"providers":{"generic":{}}}')
            (repo / 'local.json').write_text('{}')
            (t / 'local-link').symlink_to(repo / 'local.json')
            os.environ.update(AGENT_USE_CONFIG=str(registry), AIW_CONFIG=str(t / 'local-link'), AIW_ROUTER_RULES=str(repo / 'local.json'))
            personal = t / '.config/aiw'
            personal.mkdir(parents=True)
            for name in ('CLAUDE.personal.md', 'AGENTS.personal.md'):
                (personal / name).symlink_to(repo / 'local.json')
            data = d.fingerprint(repo, config)
            check(len(set(data['local'].values())) == 1 and data['local']['claude'] != '-', str(data))
            check(data['effective_hash'] == sc.effective_hash(config), str(data))
            check(data['harness'] == data['agent_use'] == data['registry']['git'] == head + '*', str(data))
            check(data['local_git'] == [head + '*'] and len(data['local']['seats']) == 12, str(data))
            line = d.fingerprint_line(data)
            check(str(t) not in line and '\n' not in line and len(data['registry']['sha256']) == 12, line)
            os.environ.pop('AGENT_USE_CONFIG')
            check(d.fingerprint(repo, config)['registry']['source'] == 'repo', 'repo source')
            user = t / '.config/agent-use/dispatcher.json'; user.parent.mkdir(parents=True); user.write_text('{}')
            check(d.fingerprint(repo, config)['registry']['source'] == 'user', 'user source')
            xdg = t / 'xdg/agent-use/dispatcher.json'
            xdg.parent.mkdir(parents=True); xdg.write_text('{"xdg":true}')
            os.environ['XDG_CONFIG_HOME'] = str(xdg.parent.parent)
            data = d.fingerprint(repo, config)['registry']
            check(data['source'] == 'user' and data['sha256'] == hashlib.sha256(xdg.read_bytes()).hexdigest()[:12], str(data))
            xdg.unlink(); xdg.symlink_to(t / 'absent')
            check(d.fingerprint(repo, config)['registry'] == {'source': 'user', 'sha256': '-', 'git': '-'}, 'broken XDG source')
        seats = {'seats': {'generic': {'kind': 'agent_use', 'provider': 'generic'},
                           'disabled': {'kind': 'agent_use', 'provider': 'absent', 'disabled_by_user': True}}}
        for content, expected in (('{"providers":{"generic":{}}}', ''), ('{"providers":{}}', '不在生效注册表'), ('{"providers":{"generic":{"enabled":false}}}', 'enabled=false'), ('{', '不可读')):
            registry.write_text(content)
            with contextlib.redirect_stdout(io.StringIO()) as capture:
                d.registry_check(seats, lambda: registry)
            output = capture.getvalue()
            check((expected in output if expected else output == '') and 'disabled' not in output, output)
        local = t / 'doctor-local.json'
        write_legacy_local(local, {})
        env = dict(os.environ, HOME=str(t), AIW_CONFIG=str(local),
                   AGENT_USE_CONFIG=str(registry), AGENT_USE_HOME=str(fake_agent_use(t)))
        for content in ('{"providers":{}}', '{"providers":{"doubao":{"enabled":false}}}', '{'):
            registry.write_text(content)
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor'], env=env)
            check(r.returncode == 0 and '⚠ agent-use' in r.stdout, r.stdout + r.stderr)
        env = dict(os.environ, HOME=str(t), AIW_CONFIG=str(t / 'broken'), AGENT_USE_CONFIG=str(t / 'absent'))
        r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), 'doctor', '--fingerprint'], env=env)
        check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and r.stdout.startswith('指纹：') and not r.stderr, r.stdout + r.stderr)
        check(str(t) not in r.stdout and str(REPO) not in r.stdout, r.stdout)



def doctor_readonly():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env = free_guard_fixture(root)
        source = codex_home_fixture(root)
        env.update(HOME=str(root), CODEX_HOME=str(source))
        state = root / 'state'
        state.mkdir()
        entry = {'id': 'pending-disable', 'since': 'now', 'host': 'test',
                 'reason': 'paid', 'evidence': 'HTTP 402', 'notified': False}
        (state / 'disabled.json').write_text(json.dumps({'qoder:free-model-q': entry}))
        (state / 'notify-pending.jsonl').write_text(json.dumps({'id': 'pending-notice', 'message': 'test'}) + '\n')
        (state / 'breaker.json').write_text(json.dumps({'codex': {'until': 9999999999, 'reason': 'quota'}}))
        (state / '.state.lock').write_text('keep lock contents')
        (state / 'codex-home').mkdir()
        (state / 'codex-home/config.toml').write_text('keep existing config')
        (state / 'inflight').mkdir()
        (state / 'inflight/keep.json').write_text('{}')

        def snapshot():
            return {str(p.relative_to(state)): (p.is_dir(), p.stat().st_mtime_ns,
                    None if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in [state, *state.rglob('*')]}

        before = snapshot()
        for args in (['doctor'], ['doctor', '--fingerprint'], ['doctor', '--require-no-tools'], ['doctor', '--ro']):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), *args], env=env)
            check(r.returncode == (64 if '--ro' in args else 0), r.stdout + r.stderr)
            check(snapshot() == before, 'doctor 修改了 STATE 路径、内容或 mtime')
            check(not (root / 'discord').exists(), 'doctor 调用了 discord-use')
            if args == ['doctor']:
                check('待发通知：2' in r.stdout and '由 aiw freecheck 或下一次通知补发' in r.stdout, r.stdout)
                check('可生成' in r.stdout and '熔断: quota' in r.stdout and '⛔ qoder' in r.stdout, r.stdout)
        shutil.rmtree(state)
        for args in (['doctor'], ['doctor', '--fingerprint'], ['doctor', '--require-no-tools'], ['doctor', '--ro']):
            r = inprocess_sh([sys.executable, str(REPO / 'ai_worker.py'), *args], env=env)
            check(r.returncode == (64 if '--ro' in args else 0) and not state.exists(), 'doctor 创建了不存在的 STATE')
            check(not (root / 'discord').exists(), 'doctor 调用了 discord-use')



CASES = {case.__name__: case for case in (
    doctor,
    doctor_config,
    install_fresh,
    install_existing,
    install_personal_rules,
    install_claude_hook_mode,
    install_preflight,
    doctor_fingerprint,
    doctor_readonly,
)}
