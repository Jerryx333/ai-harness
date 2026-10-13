"""findings regression cases."""
from __future__ import annotations

from support import (
    inprocess_sh,
    git_history,
    git_template,
    FINDINGS_BLOCK,
    Path,
    REPO,
    check,
    complete_config_json,
    fake_home,
    findings_append,
    findings_fixture,
    findings_remote_rows,
    findings_sync_fixture,
    findings_sync_row,
    findings_worker_fixture,
    json,
    os,
    sh,
    shutil,
    subprocess,
    sys,
    tempfile,
    wait_until,
    write_legacy_local,
)
from .triage import triage_explains_record_id, triage_secret_paths


def findings_hook_hint():
    import datetime as dt
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, env = fake_home(root)
        harness = home / '.claude/aiw'
        settings_path = harness / 'seats.json'
        settings = json.loads(settings_path.read_text())
        settings['findings'] = {'hint': True}
        settings_path.write_text(json.dumps(settings))
        folder = Path(env['AIW_STATE']) / 'findings'
        folder.mkdir(parents=True)
        cache = folder / 'count-cache.json'
        throttle = folder / 'hint.json'
        now = dt.datetime.now(dt.timezone.utc)
        def setup(count=120, days=None):
            throttle.unlink(missing_ok=True)
            cache.write_text(json.dumps({'open': count, 'last_triage':
                (now - dt.timedelta(days=days)).isoformat() if days is not None else None,
                'computed': now.isoformat()}))
        def run(message='你好', nested=False, hook=True):
            result = inprocess_sh([sys.executable, str(harness / 'route.py'),
                         *(['--hook'] if hook else [message])],
                        env=dict(env, AIW_NESTED='1' if nested else ''), cwd=root,
                        input=json.dumps({'prompt': message, 'cwd': str(root)}))
            check(result.returncode == 0 and not result.stderr, repr(result))
            if not hook:
                return json.loads(result.stdout)
            return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout else ''

        setup()
        throttle.write_text('{broken')
        check(run(), '损坏 hint 永久阻止提示')
        setup()
        instruction = run('帮我写个 commit message', hook=False)['instruction']
        check(not throttle.exists(), '非 hook 消耗节流')
        expected = '[缓冲区] 待处理 120 条，还没评审过，建议运行 aiw triage start'
        check(run('帮我写个 commit message') == '[派单] ' + instruction + '\n\n' + expected,
              '派单拼接错误')
        check(run() == '', '四小时内重复提示')
        check(run('帮我写个 commit message') == '[派单] ' + instruction, '节流吞掉派单')
        throttle.write_text(json.dumps({'ts': (now - dt.timedelta(hours=5)).isoformat()}))
        check(run() == expected, '五小时后未恢复纯提示')
        setup(5, 4)
        check('上次评审 4 天前' in run(), '天数阈值未触发')
        for count, days in ((5, 1), (0, 4)):
            setup(count, days)
            check(run() == '' and not throttle.exists(), '不应提示')
        setup()
        settings['findings']['hint'] = False
        settings_path.write_text(json.dumps(settings))
        check(run() == '' and not throttle.exists(), '关闭提示无效')
        settings['findings']['hint'] = True
        settings_path.write_text(json.dumps(settings))
        for content in ('{bad', '[]', '{"open": "120"}', '{"open": 120, "last_triage": "bad"}'):
            cache.write_text(content)
            check(run() == '', '损坏缓存未静默')
        cache.unlink()
        check(run() == '', '缺失缓存未静默')
        setup()
        check(run('帮我写个 commit message', nested=True) == '' and not throttle.exists(), '嵌套提示')
        setup(5)
        check(run() == '', '无记录不应按天数触发')
        records = folder / 'records.jsonl'
        records.write_text('\n'.join(json.dumps({'ts': (now - dt.timedelta(days=d)).isoformat()})
                                    for d in (1, 4)) + '\n')
        check('还没评审过' in run(), '未采用最早记录时间')

        # Deterministic boundary and failure checks without any child processes.
        f = findings_fixture(root)
        f.config = lambda: {'hint': True, 'trigger_count': 100, 'trigger_days': 3}
        setup(5, 3)
        with patch.object(f.subprocess, 'run', side_effect=AssertionError('subprocess forbidden')):
            check(f.hint_text(now) is not None, '三天边界未触发')
            check(f.hint_text(now + dt.timedelta(hours=4) - dt.timedelta(seconds=1)) is None,
                  '四小时内边界未节流')
            check(f.hint_text(now + dt.timedelta(hours=4)) is not None, '四小时边界未恢复')
        setup()
        with patch.object(f, '_atomic_json', side_effect=OSError('unwritable')):
            check(f.hint_text(now) is None, '写入失败未静默')
        check(not throttle.exists(), '写入失败留有节流文件')



def findings_parse_strip():
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        block = ('<<<AIW-FINDINGS\nguess: 无\ndeviation: 改了步骤\nabandoned: 放弃\nfixed: 修复\n'
                 '- observation: 无\n- observation: None。\n'
                 '- observation: 现象 | expected: 预期 | evidence: f.py:12 | location: f.py:main | other: 忽略\n'
                 '- observation:  | expected: 丢弃\nAIW-FINDINGS>>>')
        body, result, error = f.parse('正文\n' + block + '\n \t')
        check(body == '正文' and error is None, '正常块未剥离')
        check(result == {'guess': '无', 'deviation': '改了步骤', 'abandoned': '放弃', 'fixed': '修复',
                         'observations': [{'observation': '现象', 'expected': '预期', 'evidence': 'f.py:12', 'location': 'f.py:main'}]}, str(result))
        answers = {key: '无' for key in ('guess', 'deviation', 'abandoned', 'fixed')}
        for value in ('', ' \t ', '无', '没有', 'none', 'n/a', 'None。', ' NONE. ', 'N/A。', '没有。', '无.'):
            empty_block = ('<<<AIW-FINDINGS\n' + ''.join(f'{key}: {val}\n' for key, val in answers.items()) +
                           f'- observation: {value} | expected: 不收录\nAIW-FINDINGS>>>')
            body, result, error = f.parse('正文\n' + empty_block)
            check(body == '正文' and error is None and result == {**answers, 'observations': []},
                  f'空观察未过滤或回答被修改: {value!r}: {result}')
        check(f.parse('<<<AIW-FINDINGS\n- observation: 无\nAIW-FINDINGS>>>') ==
              ('', {**dict.fromkeys(answers, ''), 'observations': []}, None), '只有无观察的块未被识别')
        for bad in (block + '\n正文\n' * 4, '<<<AIW-FINDINGS\nguess: 无',
                    'inline ' + block, block.replace('\nAIW-FINDINGS>>>', ' AIW-FINDINGS>>>')):
            stripped, parsed, error = f.parse(bad)
            check(stripped == bad and parsed is None and error, '坏块被吞掉')
        tolerant = ('<<<AIW-FINDINGS\n忽略前言\nguess：第一行\n第二行\n'
                    '- observation：现象 | expected：预期 | 补充 | evidence：f.py:12\n'
                    '证据续行\nAIW-FINDINGS>>>')
        _, result, error = f.parse(tolerant)
        check(error is None and result['guess'] == '第一行 第二行', str(result))
        check(result['observations'][0]['expected'] == '预期 补充' and
              result['observations'][0]['evidence'] == 'f.py:12 证据续行', str(result))
        bad = '<<<AIW-FINDINGS\n无归属\n- observation 缺冒号\nAIW-FINDINGS>>>'
        check(f.parse(bad)[0] == bad and f.parse(bad)[1] is None and f.parse(bad)[2], '无协议块被剥离')
        check(f.parse(block + '\n中间\n' + block)[0] == block + '\n中间', '未只剥离最后一个')
        many = '<<<AIW-FINDINGS\n' + '- observation: 多条\n' * 5 + 'AIW-FINDINGS>>>'
        check(len(f.parse(many)[1]['observations']) == 2, '未限制 max_items')
        check(f.parse(many)[1]['guess'] == '', '缺省字段不为空')
        check(f.parse('原文 \n') == ('原文 \n', None, None), '无块被修改')
        doc = (REPO / 'docs/findings.md').read_text()
        protocol = doc.split('## 记录块协议', 1)[1].split('```\n', 1)[1].split('\n```', 1)[0]
        check(f.SUFFIX == protocol, 'SUFFIX 与协议不一致')
        (f.HERE / 'seats.json').write_text(complete_config_json('{}'))
        check(all(f.config()[key] is False for key in ('collect', 'sync', 'hint')), '缺省开关未关闭')



def findings_parse_trailing():
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        _, expected, _ = f.parse(FINDINGS_BLOCK)
        for trailing in ('派单记录', '一\n\n二\n \t\n三', '字' * 300, 'a' * 98 + '\n' + 'b' * 99 + '\n' + 'c' * 101):
            text = '正文\n' + FINDINGS_BLOCK + '\n' + trailing + '\n'
            answer, parsed, notice = f.parse(text)
            check(answer == '正文\n' + trailing and parsed == expected and notice,
                  f'短尾文丢失或记录被拒: {answer!r}, {notice!r}')
        answer, parsed, notice = f.parse(FINDINGS_BLOCK + '\n派单记录')
        check(answer == '派单记录' and parsed == expected and notice, '只有尾文时丢失正文')
        for trailing in ('正文', ' 正文\n尾文', '\t正文\r\n尾文', '字' * 301):
            text = '正文\n' + FINDINGS_BLOCK + trailing
            check(f.parse(text) == (text, None, '记录块标记格式错误'),
                  '结束标记同一行的尾文未拒绝或原输出被修改')
        for trailing in ('一\n二\n三\n四', '字' * 301):
            text = '正文\n' + FINDINGS_BLOCK + '\n' + trailing
            check(f.parse(text) == (text, None, '记录块后还有正文'), '超限尾文未拒绝')
        for trailing in ('<<<AIW-FINDINGS', '<<<AIW-FINDINGS\nguess: 无\nAIW-FINDINGS>>>\n' + '字' * 301):
            text = FINDINGS_BLOCK + '\n' + trailing
            answer, parsed, error = f.parse(text)
            check(answer == text and parsed is None and error, '尾文中的协议标记被忽略')



def findings_secret_segments():
    import re
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        (f.STATE / 'findings/hub/raw/Example-MacBook-Pro').mkdir(parents=True)
        original_load_seats = f.load_seats
        try:
            f.load_seats = lambda *args, **kwargs: {'seats': dict.fromkeys(
                ('codex', 'codex-sol', 'workbuddy', 'workbuddy-cn'), {})}
            context = f.build_scan_context()
            random = 'aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL5oR7uX9n'
            check(len(random) == 40, '随机样本长度错误')
            segmented = 'Xk9fQ2mZ_p7Lr4tW-a8Nc3vB6yH1-eJ5gU0sD2qR'
            check(len(segmented) == 40 and all(len(part) < 20 for part in re.split(r'[-_]', segmented)),
                  '分段随机样本长度错误')
            letters = 'QwErTyUiOpAsDfGhJkLzXcVbNmQaZwSxEdCrFvTg'
            # 夹在凭据中间的标识符被剥离后，凭据仍按整串判定。
            check(f.has_secret(random[:20] + '/20261011-174531-81790/' + random[20:], context), '标识符切断凭据')
            for value in (random, segmented, letters,
                          'Q7m2Z9v4R8x1K6p3-N5t0W2y8C4s7H9d1-20261011144503-53420-1',
                          '20261010-162916-abcdefghijklmnop-qrstuvwx-yz012345-73190-6b28e1ea',
                          'Z8cF1iL5oR7uX9nB-20261011144503-53420-1',
                          'aZ7q-B2mX-c9Wp-D4rN-e8Ks-F3vH-g6Jt-L5uY',
                          letters[:20] + '-' + letters[20:], 'AIza' + 'A' * 34 + '-',
                          'AIza' + 'SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q',
                          'task-run-prefix-' + random[:20], 'task_run_prefix_' + random[:20],
                          'task-run-prefix-' + random[:19], 'task_run_prefix_' + random[:19]):
                check(f.has_secret(value, context), '随机段未命中: ' + value)
            for value in ('20261010-162916-codex-73190-6b28e1ea',
                          '20261010-162916-codex-sol-73190-6b28e1ea',
                          'runs/20261010-171024-workbuddy-22968-6351683f',
                          '20261011-174531-workbuddy-cn-81790-ee6ddbb2',
                          '~/.claude/aiw/state/runs/20261011-174531-workbuddy-cn-81790-ee6ddbb2',
                          'Example-MacBook-Pro-20261011144503-53420-1',
                          'state/panels/20261010-231558-50917/workbuddy',
                          'short-words_joined_together_into_a_long_string'):
                check(not f.has_secret(value, context), '误报: ' + value)
            # 紧邻标识符的高熵串不能随标识符一起被剥离。
            check(f.has_secret(letters + '-Example-MacBook-Pro-20261011144503-53420-1', context), '主机名吞入凭据')
            check(f.has_secret('20261010-162916-' + random.lower() + '-73190-6b28e1ea', context), '座位名吞入凭据')
            check(not f.has_secret('20261011-174531-workbuddy-cn-81790-ee6ddbb2', context), '多段座位名误报')
            check(f.has_secret(random[:20] + 'Example-MacBook-Pro-20261011144503-53420-1' + random[20:], context),
                  '嵌在随机串中的记录 id 不应剥离')

        finally:
            f.load_seats = original_load_seats



def findings_sanitize_quarantine():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        check(f.sanitize('```python\n  中文   hello\n```\n world', 7) == '中文 hell', '清洗或截断错误')
        secrets = [prefix + 'a' * 20 for prefix in ('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'sk-')] + ['AKIA' + 'A' * 16,
                   '-----BEGIN PRIVATE KEY-----', 'xoxb-' + 'a' * 10, 'xoxa-' + 'a' * 10, 'xoxp-' + 'a' * 10,
                   'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature',
                   'aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL']
        context = f.build_scan_context()
        for value in secrets:
            check(f.has_secret(value, context), '未检测到密钥特征: ' + value[:8])
        for value in ('task-runner', 'risk-free', 'disk-usage', 'AKIAxyz 普通词',
                      '0123456789abcdef0123456789abcdef01234567ab',
                      'src/findings.py', '/Users/developer/projects/harness/tests/test_findings.py',
                      'src/components/Observations/FindingsBuffer.py',
                      '普通中文句子，不是密钥', 'configuration documentation implementation'):
            check(not f.has_secret(value, context), '误报: ' + value)
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            result = f.record([{'type': 'answers', 'evidence': secrets[0]}])
        folder = f.STATE / 'findings'
        check(result == {'written': 0, 'quarantined': 1, 'capped': 0}, str(result))
        check(not (folder / 'records.jsonl').exists(), '密钥写入普通日志')
        check(json.loads((folder / 'quarantine.jsonl').read_text())['evidence'] == secrets[0], '隔离丢数据')
        check('警告' in warning.getvalue(), '隔离未警告')
        import uuid
        repo = Path(td) / 'real-repo'; repo.mkdir()
        check(sh(['git', 'init', '-q', str(repo)]).returncode == 0, 'git init')
        row = f.common_fields(str(repo), session=str(uuid.uuid4()), seat='codex', actual='codex',
                              via='direct', run_dir=str(Path(td) / 'state/runs/20261010-124501-codex-12345'))
        row.update(guess='无', deviation='无', abandoned='无', fixed='正常记录')
        check(f.record([row])['written'] == 1, '真实元数据被隔离')
        check(json.loads((folder / 'records.jsonl').read_text())['id'] == row['id'], '正常记录丢失')
        row['guess'] = secrets[0]
        check(f.record([row])['quarantined'] == 1, '自由文本令牌未隔离')
        f.STATE = Path(td) / 'file'
        f.STATE.write_text('block')
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            check(f.record([{}]) == {'written': 0, 'quarantined': 0, 'capped': 0}, '写失败计数不对')
        check('警告' in warning.getvalue(), '写失败未警告')



def findings_record_fields():
    import hashlib
    import re
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); f = findings_fixture(root)
        repo = root / 'repo'; repo.mkdir()
        def git(*args):
            r = sh(['git', *args], cwd=repo)
            check(r.returncode == 0, r.stderr)
            return r.stdout.strip()
        git('init', '-q'); git('checkout', '-qb', 'findings-test')
        git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'initial')
        sub = repo / 'sub'; sub.mkdir()
        git('remote', 'add', 'origin', 'git@github.com:Owner/Name.git')
        previous = None
        for remote in ('git@github.com:Owner/Name.git', 'https://github.com/Owner/Name.git',
                       'https://github.com/Owner/Name', 'ssh://git@github.com/Owner/Name'):
            git('remote', 'set-url', 'origin', remote)
            fields = f.common_fields(str(sub), seat='text', actual='provider', via='direct', ro=True, prompt_id='p', run_dir='run')
            check(fields['repo'] == 'github.com/owner/name' and fields['owned'], str(fields))
            check(fields['repo_rel'] == 'sub' and fields['commit'] == git('rev-parse', 'HEAD') and fields['branch'] == 'findings-test', str(fields))
            check(fields['partial_context'] and fields['actual'] == 'provider' and fields['ro'] is True, '派单字段丢失')
            check(re.fullmatch(r'.+-\d{14}-\d+-\d+', fields['id']), 'id 格式错误')
            check(previous is None or int(fields['id'].rsplit('-', 1)[1]) > previous, 'id 序号未递增')
            previous = int(fields['id'].rsplit('-', 1)[1])
            check(fields['rules_hash'] == hashlib.sha1((f.HERE / 'seats.json').read_bytes()).hexdigest()[:12], '规则哈希错误')
        git('remote', 'set-url', 'origin', 'https://github.com/Other/Name')
        check(not f.common_fields(str(repo))['owned'], '外部仓库误认 owned')
        git('remote', 'remove', 'origin')
        check(f.common_fields(str(repo))['repo'] is None, '无 remote 未返回 None')
        git('checkout', '--detach', '-q')
        check(f.common_fields(str(repo))['branch'] is None, '游离 HEAD branch 非空')
        for workdir in (str(root), str(root / 'missing')):
            fields = f.common_fields(workdir)
            check(all(fields[key] is None for key in ('repo', 'repo_rel', 'commit', 'branch')), str(fields))
            check(not fields['owned'] and len(fields['rules_hash']) == 12, str(fields))



def findings_daily_cap():
    import concurrent.futures
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        # 并发调用与跨次调用都不能重置限额；answers 始终不占观察名额。
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: f.record([{'type': 'observation', 'observation': str(i)}]), range(8)))
        check(sum(r['written'] for r in results) == 2 and sum(r['capped'] for r in results) == 6, str(results))
        check(f.record([{'type': 'answers'}, {'type': 'changes'}]) == {'written': 2, 'quarantined': 0, 'capped': 0}, '非观察受限')
        folder = f.STATE / 'findings'
        check(next(folder.glob('daily-*.count')).read_text() == '8', '超限未累计')
        rows = [json.loads(line) for line in (folder / 'records.jsonl').read_text().splitlines()]
        check(len(rows) == 4 and sum(row['type'] == 'observation' for row in rows) == 2, '落盘条数不符')



def findings_worker_collect():
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, work, prompt, output, harness = findings_worker_fixture(root)
        r, row, records = run()
        check(r.returncode == 0 and r.stdout == '正文', r.stderr + repr(r.stdout))
        check([x['type'] for x in records] == ['answers', 'observation', 'observation'], str(records))
        check(len({x['id'] for x in records}) == 3, '重复 id')
        for x in records:
            check(x['seat'] == x['actual'] == 'codex' and x['via'] == 'direct' and
                  x['prompt_id'] == hashlib.sha1(prompt.read_bytes()).hexdigest()[:10] == row['prompt'] and
                  x['run_dir'] == row['run_dir'] and not x['partial_context'], str(x))
        check(row['findings_n'] == 2 and '观察记录：2 条' in r.stderr and '范围内修改：修复了测试' in r.stderr, r.stderr)
        check((Path(row['run_dir']) / 'output.md').read_text() == output.read_text(), '原文丢失')
        import findings
        task = (Path(row['run_dir']) / 'task.txt').read_text()
        check(task.startswith('今天是 ') and task.split('\n\n', 1)[1].startswith(prompt.read_text() + '\n\n')
              and '转述以原文为准' in task and task.endswith('\n\n' + findings.SUFFIX)
              and task.index('转述以原文为准') < task.index(findings.SUFFIX), 'prompt 前言或后缀错误')
        # 写入失败只警告，仍交付正文。
        shutil.rmtree(root / 'state/findings'); (root / 'state/findings').write_text('blocked')
        r, _, records = run()
        check(r.returncode == 0 and r.stdout == output.read_text() and '收集失败' in r.stderr, r.stderr)
        (root / 'state/findings').unlink()
        settings = json.loads((harness / 'seats.json').read_text())
        settings['findings']['owners'] = [None]
        (harness / 'seats.json').write_text(json.dumps(settings))
        check(sh(['git', 'init', '-q', str(work)]).returncode == 0, 'init')
        check(sh(['git', '-C', str(work), 'remote', 'add', 'origin', 'https://github.com/test/repo']).returncode == 0, 'remote')
        r, row, records = run()
        check(r.returncode == 0 and r.stdout == output.read_text() and row['kind'] == 'ok' and
              '收集失败' in r.stderr and 'AttributeError' in r.stderr, 'unexpected collection exception interrupted delivery')



def findings_worker_skip_rules():
    with tempfile.TemporaryDirectory() as td:
        run, work, _, _, _ = findings_worker_fixture(Path(td))
        for seat, flags, overrides in (
            ('codex', ('--ro',), {'AIW_PANEL_DIR': 'panel'}),
            ('codex', ('--ro',), {'AIW_MAP_DIR': 'map'}),
            ('codex', ('--ro', '--no-findings'), {}),
            ('workbuddy', ('--ro',), {})):
            r, row, records = run(seat, flags, **overrides)
            check(r.returncode == 0 and not records and 'findings_n' not in row, r.stderr)
            check('【aiw 观察记录】' not in (Path(row['run_dir']) / 'task.txt').read_text(), '不应追加')
        (work / 'code.py').write_text('print(1)')
        r, row, records = run('workbuddy', ('--ro', '--attach', 'code.py'))
        check(r.returncode == 0 and len(records) == 3 and all(x['partial_context'] for x in records), r.stderr)
        check('【aiw 观察记录】' in (Path(row['run_dir']) / 'task.txt').read_text(), '附件单未追加')
        # direct CLI 的收集决定沿顶替链保留，实际 agent_use 仍追加。
        # fixture 默认 no-fallback；显式开启顶替。
        r, row, records = run(flags=('--ro',), FAKE_CODEX='quota')
        check(r.returncode == 0 and row['actual'] != 'codex' and records[-1]['partial_context'], r.stderr)
        check('【aiw 观察记录】' in (Path(row['run_dir']) / 'task.txt').read_text(), '顶替丢失后缀')



def findings_worker_empty_body_rc2():
    with tempfile.TemporaryDirectory() as td:
        run, _, _, output, _ = findings_worker_fixture(Path(td))
        output.write_text(FINDINGS_BLOCK)
        r, row, records = run()
        check(r.returncode == 2 and row['kind'] == 'empty' and row['rc'] == 2 and
              row['findings_n'] == 2 and not records and '只有记录块，没有正文' in r.stderr, r.stderr)



def findings_worker_malformed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, _, _, output, _ = findings_worker_fixture(root)
        for text in ('正文\n' + FINDINGS_BLOCK + '\n后文\n' * 4, '正文\n<<<AIW-FINDINGS\nguess: 无'):
            output.write_text(text)
            r, row, records = run()
            check(r.returncode == 0 and r.stdout == text and not records and '格式不对' in r.stderr and
                  'findings_n' not in row, r.stderr)
            breaker = root / 'state/breaker.json'
            check(not breaker.exists() or 'codex' not in json.loads(breaker.read_text()), '坏块触发熔断')



def findings_worker_trailing():
    with tempfile.TemporaryDirectory() as td:
        run, _, _, output, _ = findings_worker_fixture(Path(td))
        output.write_text('正文\n' + FINDINGS_BLOCK + '\n派单记录')
        r, row, records = run()
        check(r.returncode == 0 and r.stdout == '正文\n派单记录' and row['kind'] == 'ok' and
              row['findings_n'] == 2 and len(records) == 3 and '格式不对' not in r.stderr,
              r.stderr + repr(r.stdout))
        check((Path(row['run_dir']) / 'output.md').read_text() == output.read_text(), '原文未保留')



def findings_worker_disabled():
    with tempfile.TemporaryDirectory() as td:
        run, _, prompt, output, harness = findings_worker_fixture(Path(td), False)
        keysets = []
        for missing in (False, True):
            if missing:
                config = json.loads((harness / 'seats.json').read_text()); config.pop('findings')
                (harness / 'seats.json').write_text(json.dumps(config))
            r, row, records = run()
            keysets.append(set(row))
            check(r.returncode == 0 and r.stdout == output.read_text() and not records and
                  'findings_n' not in row and '观察记录' not in r.stderr, r.stderr)
            task = (Path(row['run_dir']) / 'task.txt').read_text().split('\n\n', 1)[1]
            check(task.startswith(prompt.read_text() + '\n\n') and '转述以原文为准' in task
                  and '<<<AIW-FINDINGS' not in task and '沙箱说明' not in task, '关闭 findings 后任务内容被修改')
        check(keysets[0] == keysets[1], '关闭时账本键变化')



def findings_changes_record():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, work, _, _, _ = findings_worker_fixture(root)
        check(sh(['git', 'init', '-q', str(root)]).returncode == 0, 'git init')
        (work / 'tracked').write_text('before')
        check(sh(['git', 'add', 'work/tracked'], cwd=root).returncode == 0, 'git add')
        check(sh(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], cwd=root).returncode == 0, 'git commit')
        r, row, records = run(flags=('--no-fallback',), FAKE_CODEX='write')
        check(r.returncode == 0 and len(records) == 1 and records[0]['type'] == 'changes' and
              records[0]['files'] == ['work/created', 'work/tracked'], r.stderr + str(records))
    with tempfile.TemporaryDirectory() as td:
        run, _, _, _, _ = findings_worker_fixture(Path(td))
        r, _, records = run(flags=('--no-fallback',), FAKE_CODEX='write')
        check(r.returncode == 0 and not records and '无法统计' in r.stderr, r.stderr)



def findings_note():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); home, env = fake_home(root)
        harness = home / '.claude/aiw'
        work = root / 'work'; work.mkdir()
        check(sh(['git', 'init', '-q', str(work)]).returncode == 0, 'git init')
        check(sh(['git', '-C', str(work), 'remote', 'add', 'origin', 'git@github.com:Owner/Name.git']).returncode == 0, 'remote')
        def run(*args, **overrides):
            return inprocess_sh([sys.executable, str(harness / 'findings.py'), 'note', str(work), *args], env=dict(env, **overrides))
        r = run('  现象   描述 ', '--expected', '```\n应当成功\n```', '--location', 'test.py:1')
        folder = root / 'state/findings'
        row = json.loads((folder / 'records.jsonl').read_text())
        check(r.returncode == 0 and r.stdout.strip() == row['id'], r.stderr)
        check(row['source'] == 'main' and row['type'] == 'observation' and
              row['seat'] == row['actual'] == 'main' and row['via'] == 'note' and
              row['repo'] == 'github.com/owner/name' and row['repo_rel'] == '.' and
              row['observation'] == '现象 描述' and row['expected'] == '应当成功', str(row))
        check(all(row[key] is None for key in ('ro', 'run_dir', 'prompt_id')), str(row))
        check(run('   ').returncode == 64, '空现象')
        check(run('正常', AIW_NESTED='1').returncode == 64, 'nested')
        missing = inprocess_sh([sys.executable, str(harness / 'findings.py'), 'note', str(root / 'missing'), '现象'], env=env)
        check(missing.returncode == 64, missing.stderr)
        r = run('ghp_' + 'a' * 25)
        check(r.returncode == 0 and len((folder / 'records.jsonl').read_text().splitlines()) == 1 and
              json.loads((folder / 'quarantine.jsonl').read_text())['id'] == r.stdout.strip(), r.stderr)



def findings_extract():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); f = findings_fixture(root)
        now = dt.datetime.now().astimezone()
        folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        def row(id, hours, host, file):
            return {'id': id, 'ts': (now - dt.timedelta(hours=hours)).isoformat(),
                    'host': host, 'repo': 'github.com/owner/repo', 'type': 'changes',
                    'files': [file], 'run_dir': '/runs/' + id}
        local = [row('a', 1, 'host-a', 'same.py'), row('c', 1, 'host-a', 'far.py')]
        remote = [row('b', 11, 'host-b', 'same.py'), row('d', 31, 'host-b', 'far.py'), local[0]]
        (folder / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in local))
        hub = folder / 'hub/raw/host-b'; hub.mkdir(parents=True)
        (hub / 'month.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in remote))
        harness = root / 'history'; harness.mkdir()
        def git(*args, **kwargs):
            r = sh(['git', '-C', str(harness), *args], **kwargs)
            check(r.returncode == 0, r.stderr)
        old = (now - dt.timedelta(days=30)).isoformat()
        initial = '\n## frequent\ninitial\n## rare\ninitial\n'
        history = [('base', {'CLAUDE.md': initial}, old)]
        history.extend((f'frequent {i}', {'CLAUDE.md': f'\n## frequent\nversion {i}\n## rare\ninitial\n'}, now.isoformat()) for i in range(3))
        history.append(('rare', {'CLAUDE.md': '\n## frequent\nversion 2\n## rare\nchanged\n'}, now.isoformat()))
        git_template(harness, empty=True)
        git_history(harness, history)
        repeated = '每次修改结束以后必须运行相关的离线回归测试'
        noise = '这一行只在附件或观察记录说明里出现不应该计入'
        for i in range(6):
            run = f.STATE / 'runs' / ((now - dt.timedelta(minutes=1)).strftime('%Y%m%d-%H%M%S') + f'-seat-{i}')
            run.mkdir(parents=True)
            unique = min(i, 4)
            marker = '\n\n---\n以下是调用方附上的文件' if i % 2 else '\n\n---\n【aiw 观察记录】'
            (run / 'task.txt').write_text(f'任务 {unique}\n{repeated}\n{repeated}{marker}\n{noise}')
        # A separate old run must not increase the count.
        oldrun = f.STATE / 'runs' / ((now - dt.timedelta(days=30)).strftime('%Y%m%d-%H%M%S') + '-old')
        oldrun.mkdir(); (oldrun / 'task.txt').write_text('old\n' + repeated)
        data = f.extract(harness=harness)
        check(len(data['overlap']) == 1, str(data))
        check({r['host'] for r in data['overlap'][0]['records']} == {'host-a', 'host-b'} and
              len(data['overlap'][0]['records']) == 2, str(data['overlap']))
        check(len(data['rule_churn']) == 1 and data['rule_churn'][0]['section'] == 'frequent' and
              len(data['rule_churn'][0]['commits']) == 3, str(data['rule_churn']))
        check(len(data['repeated_instruction']) == 1 and data['repeated_instruction'][0]['line'] == repeated and
              data['repeated_instruction'][0]['count'] == 5 and len(data['repeated_instruction'][0]['examples']) == 3,
              str(data['repeated_instruction']))
        check(json.loads((folder / 'signals.json').read_text()) == data, 'signals snapshot')
        # No remote: identical repo_rel values in distinct workdirs must not collide.
        isolated_rows = []
        for id, wd in (('e', '/workspace/one'), ('f', '/workspace/two')):
            item = row(id, 2, 'host-a', 'local.py')
            item.update(repo=None, repo_rel='.', workdir=wd)
            isolated_rows.append(item)
        with (folder / 'records.jsonl').open('a') as stream:
            for item in isolated_rows:
                stream.write(json.dumps(item) + '\n')
        check(len(f._overlap(now - dt.timedelta(days=14), now)) == 1, '无 remote 仓库误合并')
        # Re-extraction replaces the whole snapshot and supports a non-git harness.
        data = f.extract(days=1, harness=f.HERE)
        check(data['rule_churn'] == [] and json.loads((folder / 'signals.json').read_text())['days'] == 1, 'replace')
        for days in ('0', '-1', 'bad'):
            check(sh([sys.executable, str(REPO / 'findings.py'), 'extract', '--days', days]).returncode == 64, days)



def findings_status():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); home, env = fake_home(root)
        cmd = [sys.executable, str(home / '.claude/aiw/findings.py'), 'status']
        r = inprocess_sh(cmd, env=env)
        check(r.returncode == 0 and all(s in r.stdout for s in ('answers: 0', 'quarantine: 0', 'daily: 0', '未同步过', '无')), r.stdout)
        folder = root / 'state/findings'; folder.mkdir(parents=True)
        (folder / 'records.jsonl').write_text('{"type":"answers"}\n{"type":"observation"}\n')
        (folder / 'quarantine.jsonl').write_text('{"type":"answers"}\n')
        (folder / f'daily-{dt.date.today().isoformat()}.count').write_text('7')
        (folder / 'sync.json').write_text(json.dumps({'offset': 123, 'last_sync': '2026-10-09', 'last_error': 'offline'}))
        (folder / 'count-cache.json').write_text(json.dumps({'pending': 42, 'last_triage': '2026-10-08'}))
        r = inprocess_sh(cmd, env=env)
        check(r.returncode == 0 and all(s in r.stdout for s in ('answers: 1', 'observation: 1', 'quarantine: 1', 'daily: 7',
              '123', '2026-10-09', 'offline', '42', '2026-10-08')), r.stdout)



def findings_sync():
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        findings_append(folder, findings_sync_row('a'), findings_sync_row('b', month='02', host='host-b'),
                        findings_sync_row('local', False), findings_sync_row('answer', type='answers'))
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        rows, paths = findings_remote_rows(remote)
        check({r['id'] for r in rows} == {'a', 'b', 'answer'}, 'owned filter')
        check('raw/host-a/2026-01.jsonl' in paths and 'raw/host-b/2026-02.jsonl' in paths, paths)
        check(json.loads((folder / 'sync.json').read_text())['offset'] == (folder / 'records.jsonl').stat().st_size, 'offset')
        def mark(hub):
            (hub / 'state').mkdir(exist_ok=True)
            (hub / 'state/status.jsonl').write_text('{"id":"a"}\n')
            (hub / 'state/triages.jsonl').write_text('{"ts":"2026-10-01"}\n{"ts":"2026-10-02"}\n')
        check(f.hub_transaction(mark, 'triage')[0], 'status push')
        findings_append(folder, findings_sync_row('c'))
        check(f.sync(True) == 0, 'second sync')
        rows, _ = findings_remote_rows(remote)
        check(len(rows) == 4 and len({r['id'] for r in rows}) == 4, 'duplicates')
        cache = json.loads((folder / 'count-cache.json').read_text())
        check(cache['open'] == 3 and cache['last_triage'] == '2026-10-02', str(cache))
        # Simulate a crash after push but before the offset write.
        state = json.loads((folder / 'sync.json').read_text()); state['offset'] = 0
        (folder / 'sync.json').write_text(json.dumps(state))
        check(f.sync(True) == 0 and len(findings_remote_rows(remote)[0]) == 4, 'replay duplicated rows')
        (folder / 'local-status.jsonl').write_text('{"id":"local"}\n')
        findings_append(folder, findings_sync_row('pending'))
        (f.HERE / 'seats.json').write_text(complete_config_json('{"findings":{"sync":false}}'))
        check(f.sync(True) == 0, 'disabled sync')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == 3, 'pending/local status count')
        import fcntl
        before = (folder / 'sync.json').read_bytes()
        with (folder / 'sync.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check(f.sync(True) == 0, 'concurrent sync did not skip')
        check((folder / 'sync.json').read_bytes() == before, 'skipped sync wrote state')
        # Discover a non-main default branch from origin's symbolic HEAD.
        check(sh(['git', '--git-dir', str(remote), 'branch', '-m', 'main', 'trunk']).returncode == 0, 'rename branch')
        check(sh(['git', '--git-dir', str(remote), 'symbolic-ref', 'HEAD', 'refs/heads/trunk']).returncode == 0, 'default branch')
        (f.HERE / 'seats.json').write_text(complete_config_json('{"findings":{"sync":true,"hub":"test/private"}}'))
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        result = sh(['git', '--git-dir', str(remote), 'show', 'trunk:raw/host-a/2026-01.jsonl'])
        check(result.returncode == 0 and 'pending' in result.stdout, 'default branch push')



def findings_sync_failure_keeps_offset():
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        hook = remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        findings_append(folder, findings_sync_row('a'))
        complete_end = (folder / 'records.jsonl').stat().st_size
        partial = json.dumps(findings_sync_row('b')).encode()
        with (folder / 'records.jsonl').open('ab') as stream:
            stream.write(partial)
        check(f.sync(True) == 1, 'push failure ignored')
        state = json.loads((folder / 'sync.json').read_text())
        check(state['offset'] == 0 and state['last_error'], str(state))
        hook.unlink()
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        state = json.loads((folder / 'sync.json').read_text())
        check(state['offset'] == 0 and (folder / 'records.jsonl').read_bytes() == partial, 'partial consumed')
        check([r['id'] for r in findings_remote_rows(remote)[0]] == ['a'], 'failed root commit duplicated')
        with (folder / 'records.jsonl').open('ab') as stream:
            stream.write(b'\n')
        check(f.sync(True) == 0, 'completed partial failed')
        check([r['id'] for r in findings_remote_rows(remote)[0]] == ['a', 'b'], 'partial not resumed')
        # Also exercise failure after a remote history already exists.
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        before = json.loads((folder / 'sync.json').read_text())['offset']
        findings_append(folder, findings_sync_row('c'))
        check(f.sync(True) == 1, 'existing history failure')
        check(json.loads((folder / 'sync.json').read_text())['offset'] == before, 'offset on failure')
        hook.unlink()
        check(f.sync(True) == 0 and len(findings_remote_rows(remote)[0]) == 3, 'existing history replay')
        # Advance the remote between fetch and push, forcing a real rejected push.
        other = Path(td) / 'other'
        check(sh(['git', 'clone', '-b', 'main', str(remote), str(other)]).returncode == 0, 'other clone')
        original_command = f._command
        raced = []
        def racing_command(argv, cwd=None):
            if 'push' in argv and not raced:
                raced.append(True)
                (other / 'concurrent').write_text('other host')
                for args in (['add', '-A'], ['-c', 'user.name=test', '-c', 'user.email=test@localhost',
                                            'commit', '-m', 'concurrent'], ['push']):
                    result = sh(['git', *args], cwd=other)
                    check(result.returncode == 0, result.stderr)
            return original_command(argv, cwd)
        f._command = racing_command
        findings_append(folder, findings_sync_row('d'))
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        rows, paths = findings_remote_rows(remote)
        check(raced and len(rows) == 4 and 'concurrent' in paths, 'non-fast-forward replay lost data')



def findings_compact_after_sync():
    import datetime as dt
    import gzip
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        current = dt.date.today().replace(day=1)
        previous = current - dt.timedelta(days=1)
        def row(id, owned=False, kind='observation', date=current):
            return dict(findings_sync_row(id, owned, type=kind), ts=date.isoformat() + 'T00:00:00+00:00')
        initial = [row('uploaded', True), row('closed', date=previous), row('open')]
        initial += [row(kind + '-old', kind=kind, date=previous) for kind in ('answers', 'changes')]
        initial += [row(kind + '-new', kind=kind) for kind in ('answers', 'changes')]
        findings_append(folder, *initial)
        (folder / 'local-status.jsonl').write_text('{"id":"closed","status":"unexplained"}\n')
        original = f.hub_transaction
        half = b'{"unfinished":'
        def concurrent(mutate, message):
            result = original(mutate, message)
            findings_append(folder, row('pending', True))
            with (folder / 'records.jsonl').open('ab') as stream:
                stream.write(half)
            return result
        f.hub_transaction = concurrent
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        f.hub_transaction = original
        data = (folder / 'records.jsonl').read_bytes()
        kept = [json.loads(line)['id'] for line in data[:-len(half)].splitlines()]
        check(kept == ['open', 'answers-new', 'changes-new', 'pending'], str(kept))
        offset = json.loads((folder / 'sync.json').read_text())['offset']
        check(json.loads(data[offset:].splitlines()[0])['id'] == 'pending' and data.endswith(half), 'offset/half')
        archives = list((folder / 'archive').glob('*.gz'))
        check([p.name for p in archives] == [previous.strftime('%Y-%m') + '.jsonl.gz'], str(archives))
        with gzip.open(archives[0], 'rt') as stream:
            check([json.loads(line)['id'] for line in stream] == ['closed', 'answers-old', 'changes-old'], 'archive content')
        # Complete the interrupted append through the actual writer and drain pending.
        f.record([row('next', True)], strict=True)
        check(f.sync(True) == 0, 'drain')
        check({r['id'] for r in findings_remote_rows(remote)[0]} == {'uploaded', 'pending', 'next'}, 'pending lost')
        findings_append(folder, row('one-more', True))
        check(f.sync(True) == 0, 'next sync')
        ids = [r['id'] for r in findings_remote_rows(remote)[0]]
        check(len(ids) == 4 and len(set(ids)) == 4, str(ids))
        # A second member in the same month is readable, with no duplicate first member.
        findings_append(folder, row('closed-again', date=previous))
        (folder / 'local-status.jsonl').write_text('{"id":"closed"}\n{"id":"closed-again"}\n')
        check(f.sync(True) == 0, 'second member')
        with gzip.open(archives[0], 'rt') as stream:
            check(len(stream.readlines()) == 4, 'gzip multi-member append')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == 5, 'open cache')
        check(json.loads((folder / 'count-cache.json').read_text())['oldest_open'], 'hint lost hub age')
        findings_append(folder, row('closed-current'))
        with (folder / 'local-status.jsonl').open('a') as stream:
            stream.write('{"id":"closed-current"}\n')
        check(f.sync(True) == 0, 'current observation archive')
        current_archive = folder / 'archive' / (current.strftime('%Y-%m') + '.jsonl.gz')
        with gzip.open(current_archive, 'rt') as stream:
            check([json.loads(line)['id'] for line in stream] == ['closed-current'], 'month partition')
        import contextlib, io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            f.status()
        check(f'archive: 2 files; {archives[0].stat().st_size + current_archive.stat().st_size} bytes' in output.getvalue(), 'archive status')



def findings_compact_safe():
    import time
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        f.RECORDS_LOCK_SECONDS = 1
        findings_append(folder, findings_sync_row('owned'), findings_sync_row('closed', False))
        (folder / 'local-status.jsonl').write_text('{"id":"closed"}\n')
        original = (folder / 'records.jsonl').read_bytes()
        holder = subprocess.Popen([sys.executable, '-c',
            'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); time.sleep(60)',
            str(folder / 'records.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(holder.stdout.readline().strip() == 'locked', 'child lock')
            start = time.monotonic()
            check(f.sync(True) == 0, 'locked sync')
            check(0.9 <= time.monotonic() - start < 5, 'bounded real wait')
            check((folder / 'records.jsonl').read_bytes() == original, 'locked data changed')
            check(len(findings_remote_rows(remote)[0]) == 1, 'upload skipped with lock')
        finally:
            holder.terminate(); holder.wait(); holder.stdout.close()
        (f.HERE / 'seats.json').write_text(complete_config_json('{"findings":{"sync":false}}'))
        check(f.sync(True) == 0 and (folder / 'records.jsonl').read_bytes() == original, 'disabled compaction')
        (f.HERE / 'seats.json').write_text(complete_config_json('{"findings":{"sync":true,"hub":"test/private"}}'))
        # Fail an actual archive open via a filesystem obstruction, not a mocked sync.
        (folder / 'archive').write_text('not a directory')
        check(f.sync(True) == 0, 'archive error failed upload')
        check((folder / 'records.jsonl').read_bytes() == original, 'archive failure lost data')
        check('compaction:' in json.loads((folder / 'sync.json').read_text())['last_error'], 'error missing')
        (folder / 'archive').unlink()
        # Also fail after writing bytes and verify rollback of the incomplete gzip member.
        def broken_write(self, lines):
            self.write(lines[0]); raise OSError('archive write failed')
        with patch.object(f.gzip.GzipFile, 'writelines', broken_write):
            check(f.sync(True) == 0, 'partial archive failure')
        check((folder / 'records.jsonl').read_bytes() == original, 'partial write lost records')
        check(not list((folder / 'archive').glob('*.gz')), 'partial gzip left behind')
        check(f.sync(True) == 0 and (folder / 'records.jsonl').read_bytes() == b'', 'retry failed')
        # Recover the offset when interrupted after records replacement but before state save.
        findings_append(folder, findings_sync_row('after-crash'))
        state = json.loads((folder / 'sync.json').read_text())
        state.update(offset=99999, compaction={'inode': (folder / 'records.jsonl').stat().st_ino, 'offset': 0})
        (folder / 'sync.json').write_text(json.dumps(state))
        check(f.sync(True) == 0, 'compaction recovery')
        check({r['id'] for r in findings_remote_rows(remote)[0]} == {'owned', 'after-crash'}, 'recovery skipped pending')



def findings_hub_yearly_archive():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        today = dt.date.today().replace(day=1)
        boundary = today.replace(year=today.year - 1)
        old = (boundary - dt.timedelta(days=1)).replace(day=1)
        seed = Path(td) / 'seed'
        check(sh(['git', 'clone', str(remote), str(seed)]).returncode == 0, 'seed clone')
        for id, date in [('old', old), ('boundary', boundary), ('current', today)]:
            path = seed / 'raw/host-a' / (date.strftime('%Y-%m') + '.jsonl')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(findings_sync_row(id), ts=date.isoformat() + 'T00:00:00+00:00')) + '\n')
        for args in (['checkout', '-B', 'main'], ['add', '-A'],
                     ['-c', 'user.name=test', '-c', 'user.email=t@t', 'commit', '-m', 'seed'], ['push', 'origin', 'main']):
            result = sh(['git', *args], cwd=seed); check(result.returncode == 0, result.stderr)
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        records, paths = findings_remote_rows(remote)
        check({r['id'] for r in records} == {'boundary', 'current'}, str(records))
        check('archive/raw/host-a/' + old.strftime('%Y-%m') + '.jsonl' in paths, paths)
        check(not (folder / 'hub/archive').exists(), 'archive materialized')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == 2, 'archive counted')
        check(sh(['git', 'config', '--get', 'core.sparseCheckout'], cwd=folder / 'hub').stdout.strip() == 'true', 'not sparse')
        # A newly uploaded old month must be staged before git mv can archive it.
        older = (old - dt.timedelta(days=1)).replace(day=1)
        findings_append(folder, dict(findings_sync_row('older-upload'), ts=older.isoformat() + 'T00:00:00+00:00'))
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        check('archive/raw/host-a/' + older.strftime('%Y-%m') + '.jsonl' in findings_remote_rows(remote)[1], 'old upload missing')
        # A replay and a late record in an archived month preserve the old file.
        findings_append(folder, dict(findings_sync_row('old'), ts=old.isoformat() + 'T00:00:00+00:00'),
                        dict(findings_sync_row('late'), ts=old.isoformat() + 'T00:00:00+00:00'))
        check(f.sync(True) == 0, (folder / 'sync.json').read_text())
        content = sh(['git', '--git-dir', str(remote), 'show',
                      'main:archive/raw/host-a/' + old.strftime('%Y-%m') + '.jsonl'])
        check([json.loads(line)['id'] for line in content.stdout.splitlines()] == ['old', 'late'], content.stderr)
        check(not (folder / 'hub/archive').exists(), 'late archive materialized')
        # Existing full clones are migrated on the next transaction.
        check(sh(['git', 'sparse-checkout', 'disable'], cwd=folder / 'hub').returncode == 0, 'disable')
        check((folder / 'hub/archive').exists(), 'full checkout fixture')
        check(f.sync(True) == 0 and not (folder / 'hub/archive').exists(), 'existing clone not migrated')



def findings_sync_rejects_public():
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        findings_append(folder, findings_sync_row('a'))
        os.environ['FAKE_GH_VISIBILITY'] = 'PUBLIC'
        check(f.sync(True) == 1, 'public accepted')
        check(not (folder / 'hub').exists(), 'public cloned')
        check(not sh(['git', '--git-dir', str(remote), 'show-ref']).stdout, 'public pushed')
        state = json.loads((folder / 'sync.json').read_text())
        check('visibility' in state['last_error'] and state['offset'] == 0 and 'visibility_checked' not in state, str(state))



def findings_sync_background():
    import datetime as dt
    import time
    import shlex
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run, work, prompt, output, harness = findings_worker_fixture(root)
        marker = root / 'started'
        script = root / 'background.py'
        script.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("started")\n')
        command = shlex.join([sys.executable, str(script)])
        path = harness / 'seats.json'
        cfg = json.loads(path.read_text())
        cfg['findings'].update(sync=True, sync_interval=0)
        local = root / 'local.json'
        write_legacy_local(local, {'findings': {'hub': 'test/private'}})
        os.environ['AIW_CONFIG'] = str(local)
        original_run = run
        run = lambda **kwargs: original_run(AIW_CONFIG=str(local), **kwargs)
        path.write_text(json.dumps(cfg))
        for args in (['doctor'], ['codex'], ['codex', str(work), 'bad', str(prompt)]):
            result = sh([sys.executable, str(harness / 'ai_worker.py'), *args],
                        env=dict(os.environ, AIW_FINDINGS_SYNC_CMD=command))
            time.sleep(.2)
            check(not marker.exists(), 'non-dispatch started background sync: ' + str(args))
        cfg['findings']['collect'] = False
        path.write_text(json.dumps(cfg))
        result, _, _ = run(AIW_FINDINGS_SYNC_CMD=command)
        check(result.returncode == 0, result.stderr)
        wait_until(marker.exists, 'background not started', timeout=120)
        check(marker.exists(), 'background not started')
        marker.unlink()
        cfg['findings']['sync_interval'] = 3600
        path.write_text(json.dumps(cfg))
        (root / 'state/findings').mkdir(parents=True, exist_ok=True)
        (root / 'state/findings/sync.json').write_text(json.dumps({'last_attempt': dt.datetime.now(dt.timezone.utc).isoformat()}))
        check(run(AIW_FINDINGS_SYNC_CMD=command)[0].returncode == 0, 'interval worker failed')
        time.sleep(.2)
        check(not marker.exists(), 'interval ignored')
        cfg['findings'].update(sync=False, sync_interval=0)
        path.write_text(json.dumps(cfg))
        check(run(AIW_FINDINGS_SYNC_CMD=command)[0].returncode == 0, 'disabled worker failed')
        time.sleep(.2)
        check(not marker.exists(), 'sync=false ignored')
        cfg['findings']['sync'] = True
        path.write_text(json.dumps(cfg))
        result, _, _ = run(AIW_FINDINGS_SYNC_CMD=command, FAKE_CODEX='quota')
        check(result.returncode != 0, 'expected failed dispatch')
        wait_until(marker.exists, 'failed dispatch did not trigger', timeout=120)
        check(marker.exists(), 'failed dispatch did not trigger')



def findings_docs():
    readme = (REPO / 'docs/reference.zh-CN.md').read_text()
    check('## 改进缓冲区' in readme, '参考手册缺少缓冲区小节')
    for command in ('aiw note', 'aiw findings extract', 'aiw findings status',
                    'aiw findings sync', 'aiw triage start', 'aiw triage apply',
                    'aiw triage issues', 'aiw triage abort', '--no-findings'):
        check(command in readme, f'参考手册缺少 {command}')
    contract = (REPO / 'docs/state-contract.md').read_text()
    check('findings/' in contract and '| findings_n |' in contract, '状态约定缺少 findings')
    for name in ('CLAUDE.md', 'AGENTS.harness.md'):
        check('## 改进缓冲区' in (REPO / name).read_text(), f'{name} 缺少缓冲区小节')
    result = sh(['bash', str(REPO / 'bin/aiw'), 'help'])
    check(result.returncode == 0, result.stderr)
    for command in ('note', 'findings', 'triage'):
        check(f'aiw {command} ' in result.stdout, f'帮助缺少 {command}')



def findings_weekly():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        env = dict(os.environ, AIW_STATE=str(state))
        cmd = [sys.executable, str(REPO / 'weekly.py'), '3']
        result = inprocess_sh(cmd, env=env)
        check(result.returncode == 0 and '## 改进缓冲区' in result.stdout and
              '无数据' in result.stdout.split('## 改进缓冲区')[1], result.stdout + result.stderr)
        folder = state / 'findings'
        (folder / 'hub/state').mkdir(parents=True)
        now = dt.datetime.now(dt.timezone.utc)
        base = dict(ts=(now - dt.timedelta(days=1)).isoformat(), seat='qoder', source='worker')
        records = [dict(base, type='answers', guess='无', deviation='', abandoned='尝试过', fixed='修改'),
                   dict(base, type='answers', guess='推测', deviation=' 无 ', abandoned=None, fixed='无'),
                   dict(base, type='answers', seat='codex', guess='无', deviation='偏离', abandoned='', fixed='修复')]
        records += [dict(base, type='observation') for _ in range(3)]
        records += [dict(base, type='observation', source='main', seat='main'),
                    dict(base, type='changes'),
                    dict(base, type='answers', seat='expired', ts=(now - dt.timedelta(days=4)).isoformat()),
                    dict(base, type='observation', seat='expired', ts=(now - dt.timedelta(days=4)).isoformat())]
        (folder / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        (folder / 'quarantine.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in [base, base, records[-1]]))
        # Snapshot timestamps are intentionally outside the window: these are current state.
        stamp = (now - dt.timedelta(days=10)).isoformat()
        (folder / 'sync.json').write_text(json.dumps(dict(last_ok=stamp, last_error='push rejected')))
        (folder / 'count-cache.json').write_text(json.dumps(dict(open=17, last_triage=stamp)))
        (folder / 'hub/state/hypotheses.json').write_text(json.dumps({
            'H1': {'status': '支持'}, 'H2': {'status': '支持'}, 'H3': {'status': '已验证'}}))
        result = inprocess_sh(cmd, env=env)
        check(result.returncode == 0, result.stderr)
        section = result.stdout.split('## 改进缓冲区')[1]
        for expected in ('- answers: 3', '- observation: 4', '- changes: 1', '- worker: 3', '- main: 1',
                         'qoder: answers 2 · observation 3 · 每 100 单观察数 150.0',
                         'codex: answers 1 · observation 0 · 每 100 单观察数 0.0',
                         'main: answers 0 · observation 1 · 每 100 单观察数 无数据',
                         'guess: 2/3（66.7%）', 'deviation: 2/3（66.7%）',
                         'abandoned: 2/3（66.7%）', 'fixed: 1/3（33.3%）',
                         'quarantine（窗口内）: 2', f'sync 上次成功: {stamp}',
                         'sync 最近错误: push rejected', 'count-cache 待处理数: 17',
                         f'count-cache 上次评审: {stamp}', '- 支持: 2', '- 已验证: 1'):
            check(expected in section, f'缺少 {expected}: {section}')
        check('expired' not in section, '记录未按指定天数过滤')
        # Uploaded records have left the buffer; weekly reads raw, excluding archive.
        hub_raw = folder / 'hub/raw/host'
        hub_raw.mkdir(parents=True)
        hub_record = dict(base, id='hub-owned', owned=True, type='answers', seat='hub-seat')
        (hub_raw / 'current.jsonl').write_text(json.dumps(hub_record) + '\n')
        archive = folder / 'hub/archive/raw/host'
        archive.mkdir(parents=True)
        (archive / 'old.jsonl').write_text(json.dumps(dict(hub_record, id='archived', seat='archive-seat')) + '\n')
        result = inprocess_sh(cmd, env=env)
        check('hub-seat: answers 1' in result.stdout and 'archive-seat' not in result.stdout, 'weekly hub/archive scope')
        # A still-local uploaded row is not counted twice when compaction was skipped.
        with (folder / 'records.jsonl').open('a') as stream:
            stream.write(json.dumps(hub_record) + '\n')
        result = inprocess_sh(cmd, env=env)
        check('hub-seat: answers 1' in result.stdout, 'weekly duplicated uploaded row')
        (hub_raw / 'current.jsonl').unlink()
        (folder / 'records.jsonl').write_text('')
        (folder / 'count-cache.json').write_text('{"open": 0}')
        (folder / 'sync.json').write_text('{"last_error": null}')
        result = inprocess_sh(cmd, env=env)
        check(result.returncode == 0 and 'guess: 无数据' in result.stdout and
              'count-cache 待处理数: 0' in result.stdout and 'sync 最近错误: 无' in result.stdout,
              result.stdout + result.stderr)



def findings_config_validation():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        invalid = dict(max_items='2', daily_cap=-1, field_max=True, trigger_count=0,
                       trigger_days=[], sync_interval=None, collect=1, sync='true', hint={})
        (f.HERE / 'seats.json').write_text(complete_config_json(json.dumps({'findings': invalid})))
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            for _ in range(3):
                cfg = f.config()
                check([cfg[k] for k in invalid] == [5, 300, 300, 100, 3, 3600, False, False, False], str(cfg))
        check(len(warning.getvalue().splitlines()) == 6, 'warning not once per numeric option')
        for value in (1.2, False, -2, 10**100):
            (f.HERE / 'seats.json').write_text(complete_config_json(json.dumps({'findings': {'max_items': value}})))
            check(f.config()['max_items'] == 5, 'invalid numeric accepted')



def findings_lock_defaults():
    import contextlib
    import io
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        check(f.HUB_LOCK_SECONDS == 60, 'hub lock default changed')
        check(f.RECORDS_LOCK_SECONDS == 10, 'records lock default changed')
        with patch.object(f, '_acquire_lock', return_value=False) as acquire:
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                with f.hub_read() as hub:
                    check(hub is None, 'timeout did not skip hub')
            check(warning.getvalue() == 'findings: 警告：hub.lock timeout (60s)，跳过 hub 读取\n',
                  'default hub warning changed')
            check(acquire.call_args.args[1] == 60, 'hub read timeout changed')
            with patch.object(f, 'config', return_value={'hub': 'test/private'}):
                check(f.hub_transaction(lambda hub: None, 'unused') ==
                      (False, 'hub.lock timeout (60s)'), 'default hub error changed')
            check(acquire.call_args.args[1] == 60, 'hub transaction timeout changed')
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                try:
                    f.record([{'type': 'answers'}], strict=True)
                except TimeoutError as exc:
                    check(str(exc) == 'records.lock timeout (10s)', 'default records error changed')
                else:
                    raise AssertionError('strict timeout did not raise')
            check(warning.getvalue() == '[ai-worker] 警告：records.lock timeout (10s)，放弃本次记录\n',
                  'default records warning changed')
            check(acquire.call_args.args[1] == 10, 'record timeout changed')
            f.compact_records(f.STATE / 'findings', {})
            check(acquire.call_args.args[1] == 10, 'compaction timeout changed')



def findings_record_lock_timeout():
    import time
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        f.RECORDS_LOCK_SECONDS = 1
        folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(30)",
            str(folder / 'records.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(child.stdout.readline().strip() == 'ready', 'child not locked')
            start = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                result = f.record([{'type': 'answers'}])
            elapsed = time.monotonic() - start
            check(0.9 <= elapsed < 5, f'unbounded wait: {elapsed}')
            check(result['written'] == 0 and not (folder / 'records.jsonl').exists(), 'locked record written')
            check('records.lock' in warning.getvalue(), 'timeout warning missing')
        finally:
            child.terminate(); child.wait(); child.stdout.close()



def findings_half_line_recovery():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        (folder / 'records.jsonl').write_text('{"half":')
        row = findings_sync_row('complete')
        check(f.record([row])['written'] == 1, 'append failed')
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            check(f.sync(True) == 0, 'sync failed')
        check('跳过 1 行' in warning.getvalue(), 'bad line not reported')
        check([r['id'] for r in findings_remote_rows(remote)[0]] == ['complete'], 'complete row lost')
        check(json.loads((folder / 'sync.json').read_text())['offset'] == (folder / 'records.jsonl').stat().st_size, 'offset stuck')



def findings_hub_read_lock():
    import time
    with tempfile.TemporaryDirectory() as td:
        f, folder, remote = findings_sync_fixture(Path(td))
        findings_append(folder, findings_sync_row('locked')); check(f.sync(True) == 0, 'seed')
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(2)",
            str(folder / 'hub.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(child.stdout.readline().strip() == 'ready', 'lock ready')
            start = time.monotonic(); cache = f.recompute_count_cache(0)
            check(time.monotonic() - start >= 1.7 and cache['open'] == 1, 'hub read did not lock')
        finally:
            child.wait(); child.stdout.close()



def findings_hub_read_timeout():
    import contextlib
    import io
    import time
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td)); folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        f.HUB_LOCK_SECONDS = 1
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(90)",
            str(folder / 'hub.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(child.stdout.readline().strip() == 'ready', 'child not locked')
            start = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                with f.hub_read() as hub:
                    check(hub is None, 'timeout did not skip hub')
            check(0.9 <= time.monotonic() - start < 5 and 'hub.lock timeout' in warning.getvalue(), 'read wait unbounded')
        finally:
            child.terminate(); child.wait(); child.stdout.close()



def findings_visibility_offset_race():
    with tempfile.TemporaryDirectory() as td:
        f, folder, _ = findings_sync_fixture(Path(td))
        path = folder / 'sync.json'
        path.write_text(json.dumps(dict(offset=12, visibility_checked='old')))
        original = f._command
        def advance(argv, cwd=None):
            result = original(argv, cwd)
            path.write_text(json.dumps(dict(offset=99)))
            return result
        f._command = advance
        f.require_private_sync()
        check(json.loads(path.read_text()) == {'offset': 99}, 'gate rolled back offset')
        cache = json.loads((folder / 'visibility.json').read_text())
        check(cache['repo'] == 'test/private' and cache['visibility'] == 'PRIVATE' and cache['date'], 'cache')



def findings_worker_lock_preserves_output():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); run, _, _, output, _ = findings_worker_fixture(root)
        folder = root / 'state/findings'; folder.mkdir(parents=True)
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(45)",
            str(folder / 'records.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(child.stdout.readline().strip() == 'ready', 'lock')
            result, _, records = run()
            check(result.returncode == 0 and FINDINGS_BLOCK in result.stdout, result.stdout + result.stderr)
            check(not records and 'TimeoutError' in result.stderr, 'record timeout not preserved')
        finally:
            child.terminate(); child.wait(); child.stdout.close()



def findings_extract_hub_timeout():
    import contextlib
    import io
    import time
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td)); folder = f.STATE / 'findings'; folder.mkdir(parents=True)
        f.HUB_LOCK_SECONDS = 1
        child = subprocess.Popen([sys.executable, '-u', '-c',
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(90)",
            str(folder / 'hub.lock')], stdout=subprocess.PIPE, text=True)
        try:
            check(child.stdout.readline().strip() == 'ready', 'lock')
            start = time.monotonic()
            with contextlib.redirect_stdout(io.StringIO()) as summary, contextlib.redirect_stderr(io.StringIO()) as warning:
                check(f.main(['extract']) == 0, 'extract failed')
            check(0.9 <= time.monotonic() - start < 5, 'not bounded by real lock timeout')
            check('跳过 hub' in summary.getvalue() and 'hub.lock timeout' in warning.getvalue(), 'missing skip notice')
            check(json.loads((folder / 'signals.json').read_text())['hub_skipped'], 'skip not saved')
        finally:
            child.terminate(); child.wait(); child.stdout.close()



def scanner_context_stable():
    from unittest.mock import patch
    from dataclasses import FrozenInstanceError
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        raw = f.STATE / 'findings/hub/raw'
        (raw / 'Example-MacBook-Pro').mkdir(parents=True)
        with patch.object(f.socket, 'gethostname', return_value='old-host.local'):
            c = f.build_scan_context()
        samples = ['20261011-174531-text-81790-ee6ddbb2',
                   'Example-MacBook-Pro-20261011144503-53420-1',
                   '20261011-174531-workbuddy-cn-81790-ee6ddbb2',
                   'ghp_' + 'a' * 20, 'ordinary text']
        before = [f.has_secret(s, c) for s in samples]
        (raw / 'Example-MacBook-Pro').rmdir()
        (raw / 'new-host').mkdir()
        (f.HERE / 'seats.json').write_text(complete_config_json(json.dumps({
            'seats': {'workbuddy-cn': {'kind': 'agent_use', 'provider': 'test'}}})))
        with patch.object(f.socket, 'gethostname', return_value='new-host.local'):
            c2 = f.build_scan_context()
        check([f.has_secret(s, c) for s in samples] == before, 'snapshot drift')
        check(c.hosts != c2.hosts and c.seats != c2.seats, 'environment not refreshed')
        check([f.has_secret(s, c2) for s in samples] != before, 'new snapshot ineffective')
        try:
            c.hosts = frozenset({'injected'})
        except (AttributeError, FrozenInstanceError):
            pass
        else:
            raise AssertionError('context mutable')



def scanner_context_no_io():
    import contextlib
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        c = f.build_scan_context()
        with contextlib.ExitStack() as stack:
            spies = [stack.enter_context(patch.object(obj, name, side_effect=AssertionError('scan I/O')))
                     for obj, name in ((f, 'load_seats'), (f.socket, 'gethostname'),
                                       (Path, 'iterdir'), (Path, 'is_dir'), (Path, 'read_text'),
                                       (Path, 'open'), (os, 'scandir'))]
            spies.append(stack.enter_context(patch('builtins.open', side_effect=AssertionError('scan open'))))
            for s, expected in [('ghp_' + 'a' * 20, True), ('ordinary text', False),
                                ('20261011-174531-text-81790-ee6ddbb2', False),
                                ('aB3dE5gH7jK9mN0pQ2sT4vW6yZ8cF1iL5oR7uX9n', True)]:
                check(f.has_secret(s, c) == expected, 'scan result')
            check(all(s.call_count == 0 for s in spies), 'scan touched environment')
        try:
            f.has_secret('ordinary text')
        except TypeError:
            pass
        else:
            raise AssertionError('implicit context allowed')



def scanner_context_once():
    import importlib.util
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        spec = importlib.util.spec_from_file_location('triage_gate', REPO / 'triage.py')
        t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t); t.f = f
        c = f.build_scan_context()
        with patch.object(f, 'build_scan_context', return_value=c) as build, \
                patch.object(f, 'has_secret', wraps=f.has_secret) as scan:
            f.record([{'guess': 'safe', 'evidence': 'safe'}, {'fixed': 'safe', 'location': 'safe'}], strict=True)
            check(build.call_count == 1 and len(scan.call_args_list) == 4, 'record snapshot count')
            check(all(call.args[1] is c for call in scan.call_args_list), 'record context identity')
            row = {'id': 'R', 'owned': True}
            batch = {'pending': [row], 'pool': [], 'records': [row]}
            for data, kwargs in [({'self_cause_considered': 'yes',
                    'hypotheses': [{'id': 'H', 'explains': ['R'], 'mechanism': {'nested': ['safe']}}],
                    'records': [{'id': 'R', 'status': 'explained', 'hypothesis': 'H', 'note': ['safe']}],
                    'issues': [
                    {'hypothesis': 'H', 'key': 'k', 'title': 'safe', 'body': {'nested': ['safe']}, 'labels': []}]},
                    {'batch': batch, 'existing': {'H': {}}}),
                    ({'issues': [{'hypothesis': 'H', 'key': 'k', 'title': 'safe',
                                  'body': {'nested': ['safe']}, 'labels': []}]}, {'existing': {'H': {}}})]:
                build.reset_mock(); scan.reset_mock()
                t.validate(data, **kwargs)
                check(build.call_count == 1, 'validate snapshot count')
                check(any(call.args[0] == 'nested' for call in scan.call_args_list), 'dictionary key not scanned')
                check(all(call.args[1] is c for call in scan.call_args_list), 'validate context identity')
                build.reset_mock()
                t.validate(data, context=c, **kwargs)
                check(build.call_count == 0, 'injected context rebuilt')



def scanner_context_bypass():
    from unittest.mock import patch
    import findings
    findings.build_scan_context()
    findings_secret_segments()
    scanner_context_no_io()
    with tempfile.TemporaryDirectory() as td:
        f = findings_fixture(Path(td))
        credential_name = 'sk-' + 'a' * 20
        with patch.object(
                f.socket, 'gethostname', return_value=credential_name):
            c = f.build_scan_context()
        check(f.has_secret(credential_name + '-20261012112233-1-1', c),
              'credential prefix stripped as trusted ID')



def scanner_context_downstream():
    import findings
    findings.build_scan_context()
    findings_sanitize_quarantine()
    triage_explains_record_id()
    triage_secret_paths()
    scanner_context_once()



def scanner_id_rules():
    import datetime as dt
    import harness_ids as ids
    stamp = dt.datetime(2026, 10, 12, 11, 22, 33)
    check(ids.format_run_id(stamp, 'workbuddy-cn', 12345, 'abcdef01') ==
          '20261012-112233-workbuddy-cn-12345-abcdef01', 'run format')
    check(ids.format_record_id('host', stamp, 12345, 9999) == 'host-20261012112233-12345-9999', 'record format')
    check(ids.format_panel_id(stamp, 12345) == '20261012-112233-12345', 'panel/map format')
    pattern = ids.compile_id_pattern({'host', 'host-long', 'h.st'}, {'seat', 'seat-long', 's.eat'})
    check(pattern.pattern.index('host\\-long') < pattern.pattern.index('|host'), 'host longest first')
    check(pattern.pattern.index('seat\\-long') < pattern.pattern.index('|seat'), 'seat longest first')
    for value in ('host-long-20261012112233-1234567-9999', 'h.st-20261012112233-1-9999',
                  '20261012-112233-seat-long-1234567-abcdef01', '20261012-112233-s.eat-1-abcdef01',
                  '20261012-112233-1234567'):
        check(pattern.fullmatch(value) is not None, 'known ID rejected: ' + value)
        for edge in ('A', '0', '_', '-'):
            check(pattern.search(edge + value) is None and pattern.search(value + edge) is None, 'ID boundary')
    for value in ('unknown-20261012112233-1-1', 'hxst-20261012112233-1-1',
                  'host-20261012112233-12345678-1', 'host-20261012112233-1-10000',
                  '20261012-112233-seat-12345678-abcdef01', '20261012-112233-seat-1-ABCDEF01',
                  '20261012-112233-12345678'):
        check(pattern.search(value) is None, 'ID exemption expanded: ' + value)



CASES = {case.__name__: case for case in (
    findings_hook_hint,
    findings_parse_strip,
    findings_parse_trailing,
    findings_secret_segments,
    findings_sanitize_quarantine,
    findings_record_fields,
    findings_daily_cap,
    findings_worker_collect,
    findings_worker_skip_rules,
    findings_worker_empty_body_rc2,
    findings_worker_malformed,
    findings_worker_trailing,
    findings_worker_disabled,
    findings_changes_record,
    findings_note,
    findings_extract,
    findings_status,
    findings_sync,
    findings_sync_failure_keeps_offset,
    findings_compact_after_sync,
    findings_compact_safe,
    findings_hub_yearly_archive,
    findings_sync_rejects_public,
    findings_sync_background,
    findings_docs,
    findings_weekly,
    findings_config_validation,
    findings_lock_defaults,
    findings_record_lock_timeout,
    findings_half_line_recovery,
    findings_hub_read_lock,
    findings_hub_read_timeout,
    findings_visibility_offset_race,
    findings_worker_lock_preserves_output,
    findings_extract_hub_timeout,
    scanner_context_stable,
    scanner_context_no_io,
    scanner_context_once,
    scanner_context_bypass,
    scanner_context_downstream,
    scanner_id_rules,
)}
