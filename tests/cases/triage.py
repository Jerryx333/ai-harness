"""Local triage regression cases."""
import copy
import datetime as dt
import importlib.util
from unittest.mock import patch
from support import Path, REPO, check, findings_fixture, findings_append, json, os, tempfile


def triage_fixture(root):
    f = findings_fixture(root)
    spec = importlib.util.spec_from_file_location('triage_test', REPO / 'triage.py')
    t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
    t.f = f
    folder = f.STATE / 'findings'; folder.mkdir(parents=True)
    return t, f, folder, None


def triage_decisions():
    return dict(self_cause_considered='考虑了规则', hypotheses=[dict(
        id='H-new', title='标题', mechanism='机制', explains=['new'], predicts=['预测'],
        refuted_by=['反例'], status='验证中', layer='机制', probe='true',
        expect_down=['超时'], window_days=7, evidence=dict(kind='已见后果', refs=['tests:1']),
        baseline=dict(metric='每 100 单 timeout 观察数', direction='down',
                      window='2026-10-01..2026-10-07', value=10, evidence=['before.json']))],
        records=[dict(id='new', status='explained', hypothesis='H-new'),
                 dict(id='local', status='unexplained')])


def comparison(value=5, task_mix='可比'):
    return dict(window='2026-10-08..2026-10-14', value=value,
                task_mix=task_mix, evidence=['after.json'])


def triage_baseline_verified():
    import copy
    with tempfile.TemporaryDirectory() as td:
        t, _, _, _ = triage_fixture(Path(td))
        rows = [dict(id='new'), dict(id='local')]
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
        template['records'] = []
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



def triage_local_flow():
    with tempfile.TemporaryDirectory() as td:
        t, f, folder, _ = triage_fixture(Path(td))
        stamp = t.now().isoformat()
        rows = [dict(id=rid, type='observation', ts=stamp, observation='超时')
                for rid in ('new', 'local', 'old', 'done', 'unverifiable')]
        findings_append(folder, *rows)
        t.append(folder / 'status.jsonl', [dict(id='old', status='unexplained'),
                                         dict(id='done', status='explained'),
                                         dict(id='unverifiable', status='unverifiable')])
        # Freeze extraction inputs to avoid invoking Git during lease/state checks.
        with patch.object(f, 'extract', return_value={}):
            check(t.main(['start']) == 0, 'start failed')
            current, lock = t.latest()
            batch = t.read(current.parent / 'batch.json')
            check({r['id'] for r in batch['pending']} == {'new', 'local'}, 'pending semantics')
            check({r['id'] for r in batch['pool']} == {'old', 'unverifiable'}, 'pool semantics')
            check(lock['pid'] == os.getpid() and t.read(folder / 'triage.lock') == lock, 'lease fields')
            with patch.object(t, 'host', return_value='competitor'):
                check(t.main(['start']) == 75, 'active lease acquired')
            invalid = triage_decisions(); invalid['self_cause_considered'] = ''
            check(t.apply(invalid) == 2 and (folder / 'triage.lock').exists(), 'invalid apply released lease')
            check(t.apply(triage_decisions()) == 0, 'apply failed')
            check(not current.exists() and not (folder / 'triage.lock').exists(), 'apply left lease')
            h = t.read(folder / 'hypotheses.json')['H-new']
            check(h['created'] == h['updated'] and h['evidence']['kind'] == '已见后果', 'hypothesis persistence')
            log = list(f._rows(folder / 'hypotheses-log.jsonl'))
            check(log[0]['before'] is None and log[0]['after'] == h, 'hypothesis log')
            cache = t.read(folder / 'count-cache.json')
            check(cache['open'] == 2 and cache['last_triage'], 'unexplained count')
            check(set(list(f._rows(folder / 'triages.jsonl'))[0]) ==
                  {'ts', 'triage_id', 'host', 'records', 'hypotheses'}, 'triage log fields')
            t.f._atomic_json(folder / 'triage.lock', dict(lock, expires='2000-01-01T00:00:00+00:00'))
            with patch.object(t, 'host', return_value='takeover'):
                check(t.main(['start']) == 0, 'expired lease takeover failed')
                path, lease = t.latest()
                f._atomic_json(folder / 'triage.lock', dict(lease, triage_id='replacement'))
                check(t.main(['abort']) == 75 and path.exists(), 'foreign lease removed')
                f._atomic_json(folder / 'triage.lock', lease)
                check(t.main(['abort']) == 0 and not path.exists(), 'abort failed')
        check(t.main(['apply', str(folder / 'missing')]) == 2, 'missing decisions')
        with patch.dict(os.environ, AIW_NESTED='1'):
            check(t.main(['start']) == 64, 'nested triage accepted')


def triage_validate_core():
    with tempfile.TemporaryDirectory() as td:
        t, f, _, _ = triage_fixture(Path(td))
        rows = [dict(id='new'), dict(id='local')]
        batch = dict(records=rows, pending=rows, pool=[])
        base = triage_decisions()
        check(t.validate(base, batch) == [], 'valid decisions rejected')
        for value in (None, [], 'bad'):
            check(t.validate(value, batch), 'nonobject accepted')
        for key in ('records', 'hypotheses'):
            d = copy.deepcopy(base); d[key] = [1]
            check(t.validate(d, batch), 'invalid group accepted')
        for key in ('id', 'title', 'mechanism', 'explains', 'predicts', 'refuted_by', 'status', 'layer',
                    'probe', 'expect_down', 'window_days'):
            d = copy.deepcopy(base); d['hypotheses'][0][key] = None
            check(t.validate(d, batch), 'invalid hypothesis ' + key)
        for status in ('支持', '试修', '验证中', '已验证'):
            d = copy.deepcopy(base); h = d['hypotheses'][0]
            h.update(status=status, comparison=comparison())
            check(t.validate(d, batch) == [], 'evidence rejected')
            for evidence in (None, dict(kind='线索', refs=['tests:1']),
                             dict(kind='确定缺陷', refs=[]), dict(kind='错误', refs=['tests:1'])):
                h['evidence'] = evidence
                check(any('evidence' in e for e in t.validate(d, batch)), 'evidence gate bypass')
        for status in ('待补证', '反驳', '已放弃'):
            d = copy.deepcopy(base); h = d['hypotheses'][0]
            h.update(status=status, evidence=dict(kind='线索', refs=[]))
            check(t.validate(d, batch) == [], 'clue rejected')
            del h['evidence']
            check(t.validate(d, batch) == [], 'optional evidence required')
        for key, value in (('id', 'outside'), ('status', 'wrong'), ('hypothesis', 'missing')):
            d = copy.deepcopy(base); d['records'][0][key] = value
            check(t.validate(d, batch), 'invalid record ' + key)
        d = copy.deepcopy(base); d['hypotheses'][0]['explains'] = ['outside']
        check(any('explains' in e for e in t.validate(d, batch)), 'unknown explains accepted')
        for target, key in (('hypotheses', 'title'), ('records', 'note')):
            d = copy.deepcopy(base); d[target][0][key] = 'sk-' + 'a' * 24
            check(any('密钥特征' in e for e in t.validate(d, batch)), 'secret accepted')
        d = dict(self_cause_considered='考虑', records=[dict(id='new', status='explained', hypothesis='existing')])
        check(t.validate(d, batch, existing={'existing': {}}) == [], 'existing hypothesis rejected')


CASES = {case.__name__: case for case in (triage_baseline_verified, triage_local_flow, triage_validate_core)}
