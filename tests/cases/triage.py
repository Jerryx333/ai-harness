"""triage regression cases."""
from __future__ import annotations

from support import (
    Path,
    check,
    complete_config_json,
    findings_append,
    findings_sync_row,
    json,
    os,
    sh,
    tempfile,
    triage_decisions as legacy_triage_decisions,
    triage_fixture,
    triage_project_decisions as legacy_triage_project_decisions,
    triage_project_route as legacy_triage_project_route,
    triage_seed,
)


def triage_decisions():
    data = legacy_triage_decisions()
    data['hypotheses'][0].update(
        evidence={'kind': '已见后果', 'refs': ['tests:1']},
        baseline={'metric': '每 100 单 timeout 观察数', 'direction': 'down',
                  'window': '2026-10-01..2026-10-07', 'value': 10, 'evidence': ['before.json']})
    return data


def comparison(value=5, task_mix='可比'):
    return {'window': '2026-10-08..2026-10-14', 'value': value,
            'task_mix': task_mix, 'evidence': ['after.json']}


def triage_project_decisions():
    data = legacy_triage_project_decisions()
    data['project_issues'][0]['evidence'] = {'kind': '确定缺陷', 'refs': ['tests:1']}
    return data


def triage_project_route(mode):
    from unittest.mock import patch
    with patch('support.triage_project_decisions', triage_project_decisions):
        legacy_triage_project_route(mode)


def triage_start_lock():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        check(t.main(['start']) == 0, 'start failed')
        batch = next((folder / 'triage').iterdir())
        check((batch / 'batch.md').exists() and (batch / 'batch.json').exists(), 'batch missing')
        lock = json.loads((folder / 'hub/state/triage.lock').read_text())
        check(lock['triage_id'] == batch.name and lock['pid'] == os.getpid(), 'lock fields')
        real_host = t.host
        t.host = lambda: 'other-host'
        before = list((folder / 'triage').iterdir())
        check(t.main(['start']) == 75 and list((folder / 'triage').iterdir()) == before, 'other host acquired lock')
        t.host = real_host
        check(t.main(['abort']) == 0, 'abort failed')
        check(not (folder / 'hub/state/triage.lock').exists() and not (batch / 'lock.json').exists(), 'abort left lock')
        def expired(hub):
            f._atomic_json(hub / 'state/triage.lock', dict(lock, expires='2000-01-01T00:00:00+00:00'))
        check(f.hub_transaction(expired, 'expired')[0], 'expired seed')
        t.host = lambda: 'takeover-host'
        check(t.main(['start']) == 0, 'expired takeover failed')
        check(t.main(['abort']) == 0, 'takeover abort failed')
        # A competing lock committed between fetch and push must be rechecked on replay.
        other = Path(td) / 'competitor'
        check(sh(['git', 'clone', '-b', 'main', str(remote), str(other)]).returncode == 0, 'clone competitor')
        command = f._command
        raced = []
        def race(argv, cwd=None):
            if 'push' in argv and not raced:
                raced.append(True)
                f._atomic_json(other / 'state/triage.lock', dict(lock, host='competitor', triage_id='competitor-id'))
                for args in (['add', '-A'], ['-c', 'user.name=test', '-c', 'user.email=t@t', 'commit', '-m', 'lock'], ['push']):
                    result = sh(['git', *args], cwd=other)
                    check(result.returncode == 0, result.stderr)
            return command(argv, cwd)
        f._command = race
        t.host = lambda: 'race-host'
        check(t.main(['start']) == 75 and raced, 'push replay did not recheck lock')
        check(not list((folder / 'triage').glob('race-host-*')), 'race loser generated batch')
        f._command = command
        check(f.hub_transaction(lambda hub: (hub / 'state/triage.lock').unlink(), 'cleanup test lock')[0], 'cleanup')
        # Failed hub push must not create a local batch.
        (remote / 'hooks/pre-receive').write_text('#!/bin/sh\nexit 1\n')
        (remote / 'hooks/pre-receive').chmod(0o755)
        t.host = lambda: 'failed-host'
        check(t.main(['start']) == 1 and not list((folder / 'triage').glob('failed-host-*')), 'failed push created batch')



def triage_batch():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        check(t.main(['start']) == 0, 'start failed')
        batch_dir = next((folder / 'triage').iterdir())
        batch = json.loads((batch_dir / 'batch.json').read_text())
        md = (batch_dir / 'batch.md').read_text()
        for heading in ('1. 概况', '2. 假设台账', '3. 机械信号', '4. 机械分组', '5. 记录'):
            check('## ' + heading in md, heading)
        check('answers=2, observation=4, 每 100 单观察数=200.00' in md, 'denominator/rate wrong')
        check('guess 无或空：1/2 (50.00%)' in md and 'deviation 无或空：2/2 (100.00%)' in md, 'answer ratios')
        check('⚠ 两台规则版本不一致' in md and '⏰ 到期：跑探针并核对两台 rules_hash' in md, 'warnings missing')
        check({r['id'] for r in batch['pending']} == {'new', 'local'}, 'pending wrong')
        check([r['id'] for r in batch['pool']] == ['old'], 'pool wrong')
        record_section = md.split('## 5. 记录')[1].split('## decisions.json')[0]
        check('done |' not in record_section and 'old |' in record_section and 'local |' in record_section, 'record display')
        check(batch['groups']['keywords']['超时']['count'] == 3, 'keyword count')
        check(len(batch['records']) == 7 and len(batch['signals']) >= 3, 'raw records/signals missing')
        check(t.main(['abort']) == 0, 'abort')
        cfg = json.loads((f.HERE / 'seats.json').read_text()); cfg['findings']['sync'] = False
        (f.HERE / 'seats.json').write_text(json.dumps(cfg))
        check(t.main(['start']) == 1, 'disabled sync start accepted')



def triage_apply():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        check(t.main(['start']) == 0, 'start')
        before = json.loads((folder / 'count-cache.json').read_text())['open']
        decisions = triage_decisions()
        path = Path(td) / 'decisions.json'; path.write_text(json.dumps(decisions))
        check(t.main(['apply', str(path)]) == 0, 'apply')
        state = folder / 'hub/state'
        h = json.loads((state / 'hypotheses.json').read_text())['H-new']
        check(h['created'] == h['updated'] and h['mechanism'] == '机制', 'hypothesis fields')
        log = list(f._rows(state / 'hypotheses-log.jsonl'))
        check(len(log) == 1 and log[0]['before'] is None and log[0]['after'] == h, 'hypothesis log')
        check([r['id'] for r in f._rows(state / 'status.jsonl')] == ['old', 'done', 'new'], 'hub status/local leak')
        check([r['id'] for r in f._rows(folder / 'local-status.jsonl')] == ['local'], 'local status')
        triages = list(f._rows(state / 'triages.jsonl'))
        check(len(triages) == 1 and triages[0]['records'] == 2 and triages[0]['hypotheses'] == 1 and triages[0]['issues'] == 1, 'triage audit')
        check(not (state / 'triage.lock').exists() and not list((folder / 'triage').glob('*/lock.json')), 'lock remains')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == before - 2, 'cache not refreshed')
        check(t.main(['apply', str(path)]) == 64, 'apply replay allowed')
        # Next batch upsert preserves created and logs the old value.
        t.host = lambda: 'next-host'
        check(t.main(['start']) == 0, 'second start')
        batch = json.loads(t.latest()[0].with_name('batch.json').read_text())
        check('new' in {r['id'] for r in batch['records']}, 'explained record missing')
        check('new' not in {r['id'] for r in batch['pending'] + batch['pool']}, 'explained record still pending')
        h2 = dict(decisions['hypotheses'][0], status='已验证', title='更新', comparison=comparison())
        check(t.apply(dict(self_cause_considered='检查', hypotheses=[dict(h2, explains=['missing'])])) == 2,
              'unknown explains accepted')
        check(t.apply(dict(self_cause_considered='检查', records=[dict(id='new', status='unexplained')])) == 2,
              'processed record update accepted')
        check(t.apply(dict(self_cause_considered='检查', hypotheses=[h2])) == 0, 'upsert')
        changed = json.loads((state / 'hypotheses.json').read_text())['H-new']
        check(changed['status'] == '已验证' and changed['explains'] == h['explains'], 'status/explains update')
        check(changed['created'] == h['created'] and changed['updated'] > h['updated'], 'timestamps')
        check(list(f._rows(state / 'hypotheses-log.jsonl'))[-1]['before'] == h, 'upsert before')



def triage_apply_validation():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        check(t.main(['start']) == 0, 'start')
        def snapshot():
            return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        original = snapshot()
        head = sh(['git', '--git-dir', str(remote), 'rev-parse', 'main']).stdout
        variants = []
        for field, value in [('predicts', []), ('probe', ''), ('window_days', True), ('layer', []), ('explains', ['missing'])]:
            d = triage_decisions(); d['hypotheses'][0][field] = value; variants.append(d)
        d = triage_decisions(); del d['self_cause_considered']; variants.append(d)
        d = triage_decisions(); d['records'][0]['id'] = 'missing'; variants.append(d)
        d = triage_decisions(); del d['records'][0]['hypothesis']; variants.append(d)
        d = triage_decisions(); d['issues'][0]['labels'] = [1]; variants.append(d)
        variants.extend([[], {'self_cause_considered': 'x', 'records': 'bad'}])
        for d in variants:
            p = Path(td) / 'decisions.json'; p.write_text(json.dumps(d))
            check(t.main(['apply', str(p)]) == 2, f'invalid accepted: {d}')
            check(snapshot() == original, 'validation wrote local files')
            check(sh(['git', '--git-dir', str(remote), 'rev-parse', 'main']).stdout == head, 'validation committed')
        def steal(hub):
            lock = json.loads((hub / 'state/triage.lock').read_text()); lock['host'] = 'thief'
            f._atomic_json(hub / 'state/triage.lock', lock)
        check(f.hub_transaction(steal, 'stolen')[0], 'steal')
        p.write_text(json.dumps(triage_decisions()))
        check(t.main(['apply', str(p)]) == 75, 'stolen lock applied')
        check(t.main(['abort']) == 75, 'stolen lock aborted')
        check(not (folder / 'local-status.jsonl').exists(), 'conflict wrote local status')



def triage_issue_persistence_behavior():
    import copy, contextlib, io
    from unittest.mock import patch
    for kind in ('root', 'project'):
        for scenario in ('exact', 'substring', 'mapped', 'retry'):
            with tempfile.TemporaryDirectory() as td:
                t, f, folder, _ = triage_fixture(Path(td))
                triage_seed(t, f, folder)
                if kind == 'root':
                    # This test exercises persistence, so upgrade the legacy hub evidence first.
                    path = folder / 'hub/state/hypotheses.json'
                    hypotheses = t.read(path)
                    hypotheses['H-old']['evidence'] = {'kind': '已见后果', 'refs': ['tests:1']}
                    f._atomic_json(path, hypotheses)
                    issue = dict(triage_decisions()['issues'][0], hypothesis='H-old')
                    group, target, key = 'issues', 'test/private', issue['key']
                else:
                    issue = triage_project_decisions()['project_issues'][0]
                    group, target = 'project_issues', issue['repo']
                    key = 'project:' + target.lower() + ':' + issue['key']
                mapping_key = target.lower() + '#' + key
                marker = 'aiw-key: ' + key
                data = {group: [issue]}
                mapping_path = folder / 'issues.json'
                if scenario == 'mapped':
                    f._atomic_json(mapping_path, {mapping_key: 71})
                if scenario == 'retry':
                    other = copy.deepcopy(issue)
                    other.update(key='other', title='成功项')
                    data[group].append(other)
                command, atomic = f._command, f._atomic_json
                calls, pending = [], []
                fail = [scenario == 'retry']

                def run(argv, cwd=None):
                    check(not pending, f'{kind}: mapping not persisted before next command')
                    if argv[:3] == ['gh', 'issue', 'list']:
                        calls.append('list')
                        check(argv[argv.index('--repo') + 1] == target and
                              argv[argv.index('--state') + 1] == 'all', 'search target/state')
                        if scenario in ('exact', 'substring'):
                            bodies = [marker + '-suffix', 'prefix ' + marker, marker + ' suffix']
                            if scenario == 'exact':
                                bodies.append('正文\n' + marker + '\n出处')
                            return json.dumps([dict(number=71+i, body=body) for i, body in enumerate(bodies)])
                    if argv[:3] == ['gh', 'issue', 'create']:
                        calls.append('create')
                        if fail[0] and argv[argv.index('--title') + 1] == issue['title']:
                            raise RuntimeError('fake create failure')
                        result = command(argv, cwd)
                        pending.append(True)
                        return result
                    return command(argv, cwd)

                def persist(path, value):
                    if pending:
                        check(path == mapping_path, f'{kind}: next write must be issues.json')
                        check(any(number == len(t.read(Path(os.environ['FAKE_GH_ISSUES']))['issues'])
                                  for number in value.values()), 'created number missing')
                    atomic(path, value)
                    check(t.read(path) == value, 'atomic write not on disk')
                    pending.clear()

                with patch.object(f, '_command', side_effect=run), \
                     patch.object(f, '_atomic_json', side_effect=persist), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as warning:
                    check(t.issues(data) == (1 if scenario == 'retry' else 0), f'{kind}/{scenario}: rc')
                    check(not pending, 'create returned without persisting mapping')
                    if scenario == 'mapped':
                        check(calls == [], 'mapping hit called list/create')
                    elif scenario == 'exact':
                        check(calls == ['list'] and t.read(mapping_path)[mapping_key] == 74,
                              'full marker line not reused')
                    elif scenario == 'substring':
                        check(calls == ['list', 'create'], 'substring counted as marker line')
                    else:
                        check(mapping_key not in t.read(mapping_path), 'failed create mapped')
                        check(len(t.read(mapping_path)) == 1, 'failure skipped remaining issue')
                        check('aiw triage issues <decisions.json>' in warning.getvalue(), 'retry hint')
                        fail[0] = False
                        check(t.issues(data) == 0, 'retry failed')
                        check(calls.count('create') == 3 and calls.count('list') == 3,
                              'retry recreated successful issue')
                    before = list(calls)
                    check(t.issues(data) == 0 and calls == before, 'rerun duplicated issue')
                if kind == 'project':
                    check(t.read(folder / 'project-issues.json')[mapping_key] ==
                          dict(target=target, number=t.read(mapping_path)[mapping_key], records=issue['records']),
                          'independent project transfer record')



def triage_issue_idempotent():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        check(t.main(['start']) == 0, 'start')
        data = triage_decisions()
        p = Path(td) / 'decisions.json'; p.write_text(json.dumps(data))
        os.environ['FAKE_GH_ISSUE_FAIL'] = '1'
        check(t.main(['apply', str(p)]) == 1, 'issue failure not surfaced')
        check(not (folder / 'hub/state/triage.lock').exists(), 'issue failure retained lock')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == 0, 'issue failure skipped cache')
        os.environ.pop('FAKE_GH_ISSUE_FAIL')
        check(t.main(['issues', str(p)]) == 0, 'retry failed')
        check(t.main(['issues', str(p)]) == 0, 'idempotence failed')
        db = json.loads(Path(os.environ['FAKE_GH_ISSUES']).read_text())
        check(len(db['issues']) == 1 and db['issues'][0]['body'].endswith('\n\naiw-key: root-timeout'), 'duplicate/key missing')
        check('layer:机制' in db['labels'], 'label create missing')



def triage_project_private():
    triage_project_route('PRIVATE')



def triage_project_public():
    triage_project_route('PUBLIC')
    triage_project_route('INTERNAL')



def triage_project_disabled():
    triage_project_route('disabled')



def triage_project_view_failure():
    triage_project_route('failed')



def triage_project_idempotent():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        d = triage_project_decisions()
        os.environ['FAKE_GH_CREATE_LOG'] = str(Path(td) / 'create.jsonl')
        check(t.issues(d) == 0, 'create')
        (folder / 'project-issues.json').unlink()
        check(t.issues(d) == 0, 'mapping hit')
        check(t.read(folder / 'project-issues.json'), 'project map not repaired')
        (folder / 'issues.json').unlink()
        check(t.issues(d) == 0, 'search hit')
        calls = Path(os.environ['FAKE_GH_CREATE_LOG']).read_text().splitlines()
        check(len(calls) == 1, 'duplicate create')
        # A longer marker is not an exact match; closed issues must also be searched.
        d['project_issues'][0]['key'] = 'defec'
        check(t.issues(d) == 0, 'exact marker create')
        check(len(Path(os.environ['FAKE_GH_CREATE_LOG']).read_text().splitlines()) == 2, 'substring matched')



def triage_project_validation():
    import copy, contextlib, io
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        batch = t.read(t.latest()[0].with_name('batch.json'))
        base = dict(triage_project_decisions(), self_cause_considered='已考虑',
                    records=[dict(id='new', status='unexplained')])
        check(not t.validate(base, batch), 'renamed owner/case rejected')
        mismatch = copy.deepcopy(base)
        mismatch['project_issues'][0]['repo'] = 'owner/image-use'
        check(not t.validate(mismatch, batch), 'cross-project issue rejected')
        check(not t.validate(triage_project_decisions()), 'batchless retry rejected')
        variants = [('records', ['local'], 'owned=true'), ('records', ['missing'], '不在本批'),
                    ('records', ['done'], '不在本批'),
                    ('repo', 'github.com/test/project', '格式'), ('body', 'ghp_' + 'a'*36, '密钥'),
                    ('key', 'line\nbreak', '换行'), ('key', '', '非空'), ('title', '', '非空'),
                    ('body', '', '非空'), ('labels', [1], '字符串列表'), ('records', [], '非空字符串列表'),
                    ('records', [1], '非空字符串列表')]
        for field in ('key', 'title', 'labels'):
            variants.append((field, ['ghp_'+'a'*36] if field == 'labels' else 'ghp_'+'a'*36, '密钥'))
        snapshot = {str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        for field, value, expected in variants:
            d = copy.deepcopy(base); d['project_issues'][0][field] = value
            errors = t.validate(d, batch)
            check(any(expected in e for e in errors), f'missing rejection: {field}')
            warning = io.StringIO()
            with contextlib.redirect_stderr(warning):
                check(t.apply(d) == 2, 'invalid apply accepted')
            check('ghp_' not in warning.getvalue(), 'secret echoed')
            check({str(p): p.read_bytes() for p in folder.rglob('*') if p.is_file()} == snapshot, 'validation mutated state')
        for status in ('explained', 'wontfix', 'stale', 'unverifiable'):
            d = copy.deepcopy(base); d['records'][0]['status'] = status
            check(any('status 必须' in e for e in t.validate(d, batch)), 'status accepted')
        d = copy.deepcopy(base); d['records'] = []
        check(not t.validate(d, batch), 'pool/pending without decision rejected')
        check(t.abort() == 0, 'abort')



def triage_project_hub_visibility():
    import copy, contextlib, io
    from unittest.mock import patch
    for direct in (False, True):
        for mode in ('PUBLIC', 'failed', 'PRIVATE'):
            with tempfile.TemporaryDirectory() as td:
                t, f, folder, _ = triage_fixture(Path(td))
                triage_seed(t, f, folder)
                # Seed a current PRIVATE cache before changing remote visibility.
                f.require_private_sync()
                d = triage_project_decisions()
                if direct:
                    d['project_issues'][0]['repo'] = 'test/private'
                other = copy.deepcopy(d['project_issues'][0]); other['key'] = 'second'
                d['project_issues'].append(other)
                env = dict(FAKE_GH_PROJECT_VISIBILITY='PRIVATE' if direct else 'PUBLIC',
                           FAKE_GH_VISIBILITY='PUBLIC' if mode == 'PUBLIC' else 'PRIVATE',
                           FAKE_GH_HUB_VIEW_FAIL='1' if mode == 'failed' else '',
                           FAKE_GH_VIEW_LOG=str(Path(td) / 'views.jsonl'),
                           FAKE_GH_CREATE_LOG=str(Path(td) / 'create.jsonl'))
                warning = io.StringIO()
                with patch.dict(os.environ, env), contextlib.redirect_stderr(warning):
                    check(t.issues(d) == (0 if mode == 'PRIVATE' else 1), 'hub visibility rc')
                views = [json.loads(line) for line in Path(env['FAKE_GH_VIEW_LOG']).read_text().splitlines()]
                check(sum(v[2] == 'test/private' and v[-1] == 'visibility' for v in views) == 1,
                      'hub current visibility must be checked once')
                created = Path(env['FAKE_GH_CREATE_LOG'])
                if mode == 'PRIVATE':
                    check(len(created.read_text().splitlines()) == 2, 'private hub create missing')
                else:
                    check(not created.exists(), 'unsafe hub create')
                    check('集中仓库 test/private 当前可见性检查失败' in warning.getvalue(), 'missing visibility error')



def triage_project_standalone_owned():
    import contextlib, io
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        d = triage_project_decisions()
        for rid, expected in (('local', 'owned=true'), ('missing', '不存在')):
            d['project_issues'][0]['records'] = [rid]
            check(any(expected in e for e in t.validate(d)), 'standalone validation missing')
            warning = io.StringIO()
            with contextlib.redirect_stderr(warning):
                check(t.issues(d) == 2, 'standalone unsafe record accepted')
            check(expected in warning.getvalue(), 'missing validation error')
            check(not Path(os.environ['FAKE_GH_ISSUES']).exists(), 'validation issued gh create')
        # Read synced local records too, even if absent from the current hub raw set.
        findings_append(folder, findings_sync_row('local-owned'))
        state = t.read(folder / 'sync.json'); state['offset'] = (folder / 'records.jsonl').stat().st_size
        f._atomic_json(folder / 'sync.json', state)
        d['project_issues'][0]['records'] = ['new', 'local-owned']
        check(t.issues(d) == 0, 'standalone owned records rejected')



def triage_project_canonical_identity():
    from unittest.mock import patch
    for renamed in (False, True):
        with tempfile.TemporaryDirectory() as td:
            t, f, folder, _ = triage_fixture(Path(td))
            triage_seed(t, f, folder)
            d = triage_project_decisions()
            d['project_issues'][0]['repo'] = 'Owner/Project'
            env = dict(FAKE_GH_CANONICAL='NewOwner/Renamed' if renamed else 'Owner/Project',
                       FAKE_GH_CREATE_LOG=str(Path(td) / 'create.jsonl'))
            with patch.dict(os.environ, env):
                check(t.issues(d) == 0, 'canonical create')
                d['project_issues'][0]['repo'] = 'NewOwner/Renamed' if renamed else 'OWNER/PROJECT'
                command = f._command
                def no_search(argv, **kwargs):
                    check(argv[:3] != ['gh', 'issue', 'list'], 'canonical mapping bypassed')
                    return command(argv, **kwargs)
                with patch.object(f, '_command', no_search):
                    check(t.issues(d) == 0, 'canonical mapping retry')
                (folder / 'issues.json').unlink()
                check(t.issues(d) == 0, 'canonical marker search retry')
            canonical = env['FAKE_GH_CANONICAL']
            key = f'{canonical.lower()}#project:{canonical.lower()}:defect'
            check(t.read(folder / 'issues.json') == {key: 1}, 'canonical mapping key')
            check(t.read(folder / 'project-issues.json')[key]['target'] == canonical, 'canonical target')
            check(len(Path(env['FAKE_GH_CREATE_LOG']).read_text().splitlines()) == 1, 'canonical duplicate')



def triage_project_repo_mismatch():
    import contextlib, io
    for visibility in ('PRIVATE', 'PUBLIC', 'INTERNAL'):
        with tempfile.TemporaryDirectory() as td:
            t, f, folder, _ = triage_fixture(Path(td))
            triage_seed(t, f, folder); check(t.start() == 0, 'start')
            os.environ['FAKE_GH_PROJECT_VISIBILITY'] = visibility
            d = dict(triage_project_decisions(), self_cause_considered='已考虑',
                     records=[dict(id='new', status='unexplained')])
            d['project_issues'][0]['repo'] = 'owner/image-use'
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                check(t.apply(d) == 0, 'cross-project apply failed')
            hints = [line for line in output.getvalue().splitlines() if '仓库名不一致' in line]
            check(len(hints) == 2, 'missing pending/pool mismatch hints')
            for rid in ('new', 'old'):
                check(any(f'记录 {rid} ' in line and '记录仓库 github.com/test/project' in line
                          and '目标仓库 owner/image-use' in line for line in hints), 'missing hint fields')
            issue = t.read(Path(os.environ['FAKE_GH_ISSUES']))['issues'][0]
            target = 'owner/image-use' if visibility == 'PRIVATE' else 'test/private'
            check(issue['repo'] == target, 'cross-project issue routed incorrectly')
            if visibility != 'PRIVATE':
                check('改开在集中仓库' in output.getvalue(), 'missing fallback hint')
                check(issue['title'] == '[image-use] 项目缺陷' and 'project:image-use' in issue['labels'],
                      'missing fallback metadata')



def triage_project_failure_retry():
    import copy, contextlib, io
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        d = triage_project_decisions()
        other = copy.deepcopy(d['project_issues'][0]); other.update(key='other', title='成功项')
        d['project_issues'].append(other)
        os.environ['FAKE_GH_FAIL_TITLE'] = '项目缺陷'
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning):
            check(t.issues(d) == 1, 'failure rc')
        check('aiw triage issues <decisions.json>' in warning.getvalue(), 'retry hint')
        check(len(t.read(Path(os.environ['FAKE_GH_ISSUES']))['issues']) == 1, 'remaining item skipped')
        os.environ.pop('FAKE_GH_FAIL_TITLE')
        check(t.issues(d) == 0, 'retry')
        check(len(t.read(Path(os.environ['FAKE_GH_ISSUES']))['issues']) == 2, 'retry duplicate')



def triage_project_apply_render():
    import contextlib, io
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        d = dict(triage_project_decisions(), self_cause_considered='已考虑', records=[
            dict(id='new', status='unexplained'), dict(id='old', status='unexplained'),
            dict(id='local', status='unexplained')])
        d['project_issues'][0]['records'] = ['old']
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            check(t.apply(d) == 0, 'apply')
        hints = [line for line in output.getvalue().splitlines() if line.startswith('提示：')]
        check(len(hints) == 1 and 'new（github.com/test/project）' in hints[0], 'untransferred hint')
        t.host = lambda: 'next-host'
        check(t.start() == 0, 'next start')
        batch = t.read(t.latest()[0].with_name('batch.json'))
        section = t.render(batch).split('### 未解释池')[1].split('### 本批待处理')[0]
        old = next(line for line in section.splitlines() if line.startswith('old |'))
        check(old.endswith('→ NewOwner/Project#1'), 'pool transfer missing')
        check(not any('→' in line for line in section.splitlines() if line.startswith('new |')), 'unrelated transfer')
        check(t.abort() == 0, 'abort')



def triage_privacy_and_nested():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        p = Path(td) / 'decisions.json'; p.write_text('{}')
        for disabled in (True, False):
            cfg = {'sync': not disabled, 'hub': 'test/private'}
            (f.HERE / 'seats.json').write_text(complete_config_json(json.dumps({'findings': cfg})))
            os.environ['FAKE_GH_VISIBILITY'] = 'PUBLIC'
            for args in (['start'], ['apply', str(p)], ['issues', str(p)], ['abort']):
                check(t.main(args) == 1, 'upload gate bypass: ' + str(args))
            check(not (folder / 'hub').exists() and not Path(os.environ['FAKE_GH_ISSUES']).exists(), 'remote side effects')
        os.environ['AIW_NESTED'] = '1'
        for args in (['start'], ['apply', str(p)], ['issues', str(p)], ['abort']):
            check(t.main(args) == 64, 'nested triage accepted')



def triage_local_hypotheses_retry():
    import time
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder)
        check(t.main(['start']) == 0, 'start')
        d = triage_decisions(); d['hypotheses'][0]['explains'].append('local')
        p = Path(td) / 'decisions.json'; p.write_text(json.dumps(d))
        check(t.main(['apply', str(p)]) == 2, 'issue references local hypothesis')
        d['issues'] = []
        d['hypotheses'][0]['title'] = '本机私有内容'
        p.write_text(json.dumps(d))
        hook = remote / 'hooks/pre-receive'; hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        check(t.main(['apply', str(p)]) == 1, 'push should fail')
        check(t.latest() is not None and (folder / 'hub/state/triage.lock').exists(), 'failed apply lost lock')
        local = folder / 'local-hypotheses.json'
        check(json.loads(local.read_text())['H-new']['title'] == '本机私有内容', 'local hypothesis not written first')
        check(len(list(f._rows(folder / 'local-status.jsonl'))) == 1, 'local status not written first')
        hook.unlink()
        d['records'][1]['status'] = 'wontfix'; p.write_text(json.dumps(d))
        check(t.main(['apply', str(p)]) == 0, 'retry apply failed')
        statuses = list(f._rows(folder / 'local-status.jsonl'))
        check(len(statuses) == 2 and statuses[-1]['status'] == 'wontfix', 'append/last status')
        with f.hub_read() as hub:
            check(t.collect(hub)[1]['local']['status'] == 'wontfix', 'last row not authoritative')
        check(len(list(f._rows(folder / 'local-hypotheses-log.jsonl'))) == 2, 'local history missing')
        check('H-new' not in json.loads((folder / 'hub/state/hypotheses.json').read_text()), 'local hypothesis uploaded')
        remote_data = sh(['git', '--git-dir', str(remote), 'grep', '本机私有内容', 'main'])
        check(remote_data.returncode == 1, 'private content leaked')
        check(json.loads((folder / 'count-cache.json').read_text())['open'] == 0, 'retry cache wrong')
        h = json.loads(local.read_text()); h['H-new']['updated'] = '2000-01-01T00:00:00+00:00'; local.write_text(json.dumps(h))
        t.append(folder / 'local-hypotheses-log.jsonl', [dict(id='H-new', after=h['H-new'])])
        time.sleep(1.05)
        check(t.main(['start']) == 0, 'next start')
        batch = json.loads(t.latest()[0].with_name('batch.json').read_text())
        check(any(h['id'] == 'H-new' and h['local'] and h['due'] for h in batch['hypotheses']), 'local due ledger missing')
        check('(本机) H-new' in t.latest()[0].with_name('batch.md').read_text(), 'local label missing')



def triage_explains_record_id():
    import contextlib
    import io
    import copy
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        rid = 'Example-MacBook-Pro-20261011144503-53420-1'
        findings_append(folder, findings_sync_row(rid))
        check(f.sync() == 0, 'seed sync')
        check(t.main(['start']) == 0, 'start')
        batch = json.loads(t.latest()[0].with_name('batch.json').read_text())
        check(any(r['id'] == rid and r['owned'] for r in batch['records']), 'hub record missing')
        original_has_secret = f.has_secret
        try:
            context = f.build_scan_context()
            f.has_secret = lambda value, context: value == rid or original_has_secret(value, context)
            check(f.has_secret(rid, context), 'record id must trigger the patched secret rule')
            base = triage_decisions()
            base['hypotheses'][0]['explains'] = [rid]
            base['records'] = [dict(id=rid, status='explained', hypothesis='H-new', note='证据')]
            check(t.validate(base, batch) == [], 'record id rejected by validation')
            secret = 'ghp_' + 'abcdefghijklmnopqrstuvwxyz0123456789'
            check(f.has_secret(secret, context), 'title must trigger the credential rule')
            bad = copy.deepcopy(base)
            bad['hypotheses'][0]['title'] = secret
            check(t.validate(bad, batch) == ['密钥特征：hypotheses[0].title'], 'title secret not isolated')
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                check(t.apply(bad) == 2, 'title secret accepted')
            check('密钥特征：hypotheses[0].title' in warning.getvalue() and secret not in warning.getvalue(),
                  'unsafe title diagnostic')
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                check(t.apply(base) == 0, 'record id rejected by apply')
            check('密钥特征' not in warning.getvalue(), 'record id reported as secret')
            saved = json.loads((folder / 'hub/state/hypotheses.json').read_text())
            check(saved['H-new']['explains'] == [rid], 'record reference not saved')
        finally:
            f.has_secret = original_has_secret



def triage_secret_paths():
    import contextlib
    import io
    import copy
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.main(['start']) == 0, 'start')
        secret = 'ghp_' + 'a' * 25
        base = triage_decisions()
        variants = []
        d = copy.deepcopy(base); d['hypotheses'][0]['fix'] = {'nested': [secret]}; variants.append((d, 'hypotheses[0].fix.nested[0]'))
        d = copy.deepcopy(base); d['records'][0]['note'] = secret; variants.append((d, 'records[0].note'))
        for key in ('title', 'body', 'labels'):
            d = copy.deepcopy(base); d['issues'][0][key] = [secret] if key == 'labels' else secret
            variants.append((d, 'issues[0].' + key))
        for d, field in variants:
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                check(t.apply(d) == 2, 'secret accepted')
            check(field in warning.getvalue() and secret not in warning.getvalue(), 'unsafe diagnostic')
        check(t.apply(base) == 0, 'valid apply')
        for d, field in variants[2:]:
            with contextlib.redirect_stderr(io.StringIO()) as warning:
                check(t.issues(d) == 2, 'retry secret accepted')
            check(field in warning.getvalue() and secret not in warning.getvalue(), 'retry diagnostic')
        d = copy.deepcopy(base); del d['issues'][0]['hypothesis']
        check(t.issues(d) == 2, 'missing hypothesis accepted')



def triage_issue_exact_key():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.main(['start']) == 0, 'start')
        dbpath = Path(os.environ['FAKE_GH_ISSUES'])
        dbpath.write_text(json.dumps({'issues': [dict(number=1, title='different', body='aiw-key: root-timeout-more')], 'labels': []}))
        d = triage_decisions()
        check(t.apply(d) == 0, 'apply')
        check(len(json.loads(dbpath.read_text())['issues']) == 2, 'substring issue treated as exact')
        check(json.loads((folder / 'issues.json').read_text()) == {'test/private#root-timeout': 2}, 'mapping not persisted')
        dbpath.unlink()
        check(t.issues(d) == 0 and not dbpath.exists(), 'local mapping ignored')
        (folder / 'issues.json').unlink()
        dbpath.write_text(json.dumps({'issues': [dict(number=9, title='existing', body='text\naiw-key: root-timeout\nend')], 'labels': []}))
        check(t.issues(d) == 0 and json.loads((folder / 'issues.json').read_text())['test/private#root-timeout'] == 9, 'exact search not cached')



def triage_signal_summary():
    with tempfile.TemporaryDirectory() as td:
        t, _, _, _ = triage_fixture(Path(td))
        import socket
        host = socket.gethostname()
        batch = dict(triage_id='test', overview=t.overview([]), hypotheses=[], pending=[], pool=[],
            groups={'repo': [('repo', 2)], 'keywords': {'重复': {'count': 2}}},
            signals={'overlap': [dict(repo='repo', file='a.py', records=[{'host': host}] * 2)],
                     'rule_churn': [dict(file='rules', section='section', commits=[{'title': 'commit'+str(i)} for i in range(4)])],
                     'repeated_instruction': [dict(count=5, line='重复指令')]})
        md = t.render(batch).split('## 3. 机械信号')[1].split('## 5. 记录')[0]
        for text in ('repo a.py — 2 次（主机 '+host+'）', 'rules / section — 4 次提交', 'commit2', '出现 5 次：重复指令', 'repo / repo — 2 条'):
            check(text in md, 'missing summary: '+text)
        check('```' not in md and 'commit3' not in md, 'raw dump or too many titles')



def triage_issue_repo_scope():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        d = triage_decisions(); check(t.apply(d) == 0, 'apply')
        cfg = f.HERE / 'seats.json'
        cfg.write_text(complete_config_json(json.dumps({'findings': {'sync': True, 'hub': 'test/second'}})))
        db = Path(os.environ['FAKE_GH_ISSUES']); db.unlink()
        check(t.issues(d) == 0 and db.exists(), 'old mapping prevented new repository issue')
        mapping = json.loads((folder / 'issues.json').read_text())
        check(mapping == {'test/private#root-timeout': 1, 'test/second#root-timeout': 1}, str(mapping))
        # Bare keys are accepted only with matching legacy repository provenance.
        (folder / 'issues.json').write_text('{"root-timeout": 7}')
        (folder / 'sync.json').write_text('{"visibility_hub":"test/private"}')
        db.unlink()
        check(t.issues(d) == 0 and db.exists(), 'foreign legacy key reused')
        (folder / 'issues.json').write_text('{"root-timeout": 7}')
        (folder / 'sync.json').write_text('{"visibility_hub":"test/second"}')
        db.unlink()
        check(t.issues(d) == 0 and not db.exists(), 'matching legacy key ignored')



def triage_local_promotes_to_hub():
    import time
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        d = triage_decisions(); d['issues'] = []
        d['hypotheses'][0]['explains'].append('local')
        d['records'][0]['status'] = 'unexplained'
        check(t.apply(d) == 0, 'local apply')
        time.sleep(1.05); check(t.start() == 0, 'second start')
        d['hypotheses'][0].update(explains=['new'], title='hub version')
        d['records'] = [dict(id='new', status='explained', hypothesis='H-new')]
        check(t.apply(d) == 0, 'promote')
        check('H-new' not in t.read_local_hypotheses(), 'local replay shadows hub')
        check('H-new' not in json.loads((folder / 'local-hypotheses.json').read_text()), 'snapshot retained')
        check(list(f._rows(folder / 'local-hypotheses-log.jsonl'))[-1]['deleted'], 'no tombstone')
        check(list(f._rows(folder / 'hub/state/status.jsonl'))[-1]['hypothesis'] == 'H-new', 'reference stripped')
        time.sleep(1.05); check(t.start() == 0, 'third start')
        batch = json.loads(t.latest()[0].with_name('batch.json').read_text())
        h = next(h for h in batch['hypotheses'] if h['id'] == 'H-new')
        check(h['title'] == 'hub version' and not h.get('local'), str(h))



def triage_local_log_half_line():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, remote = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        d = triage_decisions(); d['issues'] = []; d['hypotheses'][0]['explains'].append('local')
        hook = remote / 'hooks/pre-receive'; hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        check(t.apply(d) == 1, 'expected failed push')
        log = folder / 'local-hypotheses-log.jsonl'
        with log.open('a') as stream:
            stream.write('{"id":')
        hook.unlink(); d['hypotheses'][0]['title'] = 'retry version'
        check(t.apply(d) == 0, 'retry')
        check(json.loads(log.read_text().splitlines()[-1])['after']['title'] == 'retry version', 'merged half line')
        check(t.read_local_hypotheses()['H-new']['title'] == 'retry version', 'replay lost update')



def triage_status_hypothesis_secret():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        secret = 'sk-' + 'a' * 40
        d = triage_decisions(); d['issues'] = []
        d['hypotheses'][0].update(id=secret, explains=['local'])
        d['records'][0].update(status='unexplained', hypothesis=secret)
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            check(t.apply(d) == 2, 'secret hypothesis uploaded')
        check('records[0].hypothesis' in warning.getvalue() and secret not in warning.getvalue(), 'diagnostic')



def triage_status_hypothesis_missing():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        triage_seed(t, f, folder); check(t.start() == 0, 'start')
        d = triage_decisions(); d['records'][0].update(status='wontfix', hypothesis='missing')
        check(t.apply(d) == 2, 'unknown hypothesis accepted')



def triage_evidence_gate():
    import copy
    with tempfile.TemporaryDirectory() as td:
        t, f, _, _ = triage_fixture(Path(td))
        rows = [dict(id='new', owned=True), dict(id='old', owned=True), dict(id='local', owned=False)]
        batch = dict(records=rows, pending=rows, pool=[])
        base = triage_decisions()
        base['hypotheses'][0]['status'] = '支持'
        check(t.validate(base, batch) == [], 'valid evidence rejected')
        for evidence in (None, {'kind': '线索', 'refs': ['tests:1']},
                         {'kind': '确定缺陷', 'refs': []}, {'kind': '未知', 'refs': ['tests:1']},
                         {'kind': [], 'refs': ['tests:1']}, {'kind': '明文要求', 'refs': [' ']},
                         {'kind': '已见后果', 'refs': [1]}):
            d = copy.deepcopy(base)
            if evidence is None:
                del d['hypotheses'][0]['evidence']
            else:
                d['hypotheses'][0]['evidence'] = evidence
            check(any('假设 evidence' in e for e in t.validate(d, batch)), 'invalid evidence accepted')
        for status in ('待补证', '反驳', '已放弃'):
            d = copy.deepcopy(base); d['issues'] = []; h = d['hypotheses'][0]; h['status'] = status
            del h['evidence']
            check(t.validate(d, batch) == [], 'optional evidence required')
            h['evidence'] = {'kind': '线索', 'refs': []}
            check(t.validate(d, batch) == [], 'empty clue refs rejected')
            h['evidence'] = {'kind': '确定缺陷', 'refs': []}
            check(any('假设 evidence' in e for e in t.validate(d, batch)), 'optional invalid evidence accepted')
        old = {'H-new': dict(status='支持')}
        d = copy.deepcopy(base); d['hypotheses'] = []
        check(any('issue 引用的假设缺少非线索证据' in e for e in t.validate(d, batch, existing=old)),
              'legacy hub evidence bypassed')
        check(t.validate(base, batch, existing=old) == [], 'submitted evidence did not override legacy hub')
        check(any('issue 引用的假设缺少非线索证据' in e for e in t.validate(d, existing=old)),
              'standalone legacy evidence bypassed')
        good_hub = {'H-new': base['hypotheses'][0]}
        check(t.validate(d, existing=good_hub) == [], 'valid hub evidence rejected')
        bad = copy.deepcopy(base); bad['hypotheses'][0]['evidence'] = {'kind': '线索', 'refs': []}
        check(any('issue 引用的假设缺少非线索证据' in e for e in t.validate(bad, batch, existing=good_hub)),
              'submitted clue fell back to old evidence')
        project = dict(triage_project_decisions(), self_cause_considered='已考虑')
        for current in (batch, None):
            check(t.validate(project, current, available_records=rows) == [], 'valid project evidence rejected')
            for evidence in (None, {'kind': '线索', 'refs': ['tests:1']}):
                d = copy.deepcopy(project)
                if evidence is None:
                    del d['project_issues'][0]['evidence']
                else:
                    d['project_issues'][0]['evidence'] = evidence
                check(any('project_issue evidence' in e for e in t.validate(d, current, available_records=rows)),
                      'project evidence gate bypassed')
        secret = 'ghp_' + 'a' * 36
        for group, original in (('hypotheses', base), ('project_issues', project)):
            d = copy.deepcopy(original); d[group][0]['evidence']['refs'] = [secret]
            for current in ((batch,) if group == 'hypotheses' else (batch, None)):
                errors = t.validate(d, current, available_records=rows)
                check(any('密钥特征：' + group + '[0].evidence.refs[0]' == e for e in errors),
                      'evidence secret not scanned')
                check(all(secret not in e for e in errors), 'secret leaked in diagnostic')


def triage_baseline_verified():
    import copy
    with tempfile.TemporaryDirectory() as td:
        t, _, _, _ = triage_fixture(Path(td))
        rows = [dict(id='new', owned=True), dict(id='local', owned=False)]
        batch = dict(records=rows, pending=rows, pool=[])
        base = triage_decisions(); base['hypotheses'][0].update(status='已验证', comparison=comparison())
        check(t.validate(base, batch) == [], 'valid verified decision rejected')
        d = copy.deepcopy(base); h = d['hypotheses'][0]; h['status'] = '试修'; del h['baseline']
        check(any('baseline' in e for e in t.validate(d, batch)), 'trial without baseline accepted')
        d = copy.deepcopy(base); del d['hypotheses'][0]['comparison']
        check(any('comparison' in e for e in t.validate(d, batch)), 'verified without comparison accepted')
        for mix in ('未核实', '不可比'):
            d = copy.deepcopy(base); d['hypotheses'][0]['comparison']['task_mix'] = mix
            check(any('应保持「验证中」' in e for e in t.validate(d, batch)), 'uncomparable verification accepted')
        for value in (10, 11):
            d = copy.deepcopy(base); d['hypotheses'][0]['comparison']['value'] = value
            check(any('按 direction 有改进' in e for e in t.validate(d, batch)), 'no improvement accepted')
        for field in ('baseline', 'comparison'):
            for value in (True, float('nan'), float('inf'), -float('inf'), '5'):
                d = copy.deepcopy(base); d['hypotheses'][0][field]['value'] = value
                check(any(field in e for e in t.validate(d, batch)), 'invalid measurement value accepted')
            for key, value in (('window', ' '), ('evidence', []), ('evidence', [1])):
                d = copy.deepcopy(base); d['hypotheses'][0][field][key] = value
                check(any(field in e for e in t.validate(d, batch)), 'invalid measurement metadata accepted')
        d = copy.deepcopy(base); d['hypotheses'][0]['baseline'].update(direction='up', value=0)
        check(t.validate(d, batch) == [], 'up improvement rejected')
        d['hypotheses'][0]['comparison']['value'] = 0
        check(any('按 direction 有改进' in e for e in t.validate(d, batch)), 'up equality accepted')
        for status in ('支持', '待补证', '反驳', '已放弃', '试修', '验证中'):
            d = copy.deepcopy(base); h = d['hypotheses'][0]; h['status'] = status; del h['comparison']
            if status not in ('试修', '验证中'):
                del h['baseline']
            check(t.validate(d, batch) == [], 'optional measurements required')
            h['comparison'] = comparison(); h['comparison']['task_mix'] = '错误'
            check(any('comparison' in e for e in t.validate(d, batch)), 'optional invalid comparison accepted')
            h['comparison'] = comparison(); h['baseline'] = dict(base['hypotheses'][0]['baseline'], metric='')
            check(any('baseline' in e for e in t.validate(d, batch)), 'optional invalid baseline accepted')
        template = copy.deepcopy(t.TEMPLATE)
        template['hypotheses'][0].update(title='示例', mechanism='机制', explains=['new'])
        template['records'] = []; template['issues'] = []; template['project_issues'] = []
        check(t.validate(template, batch) == [], 'template example fields invalid')
        render_batch = dict(triage_id='test', overview=t.overview([]), pending=[], pool=[],
                            signals={}, groups={}, hypotheses=[dict(base['hypotheses'][0], due=True)])
        md = t.render(render_batch)
        for text in ('metric=每 100 单 timeout 观察数', 'direction=down',
                     'window=2026-10-01..2026-10-07', 'value=10', '待填写的骨架'):
            check(text in md, 'baseline display missing: ' + text)
        render_batch['hypotheses'][0]['baseline']['value'] = 0
        check('value=0' in t.render(render_batch), 'zero baseline hidden')
        del render_batch['hypotheses'][0]['baseline']
        check('无基线' in t.render(render_batch), 'legacy missing baseline hidden')


CASES = {case.__name__: case for case in (
    triage_evidence_gate,
    triage_baseline_verified,
    triage_start_lock,
    triage_batch,
    triage_apply,
    triage_apply_validation,
    triage_issue_persistence_behavior,
    triage_issue_idempotent,
    triage_project_private,
    triage_project_public,
    triage_project_disabled,
    triage_project_view_failure,
    triage_project_idempotent,
    triage_project_validation,
    triage_project_hub_visibility,
    triage_project_standalone_owned,
    triage_project_canonical_identity,
    triage_project_repo_mismatch,
    triage_project_failure_retry,
    triage_project_apply_render,
    triage_privacy_and_nested,
    triage_local_hypotheses_retry,
    triage_explains_record_id,
    triage_secret_paths,
    triage_issue_exact_key,
    triage_signal_summary,
    triage_issue_repo_scope,
    triage_local_promotes_to_hub,
    triage_local_log_half_line,
    triage_status_hypothesis_secret,
    triage_status_hypothesis_missing,
)}
