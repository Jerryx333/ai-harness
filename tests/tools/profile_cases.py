#!/usr/bin/env python3
"""Serial, low-priority case timings and Python-initiated subprocess counts."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

SPAWN_HOOK = r'''import os, sys, subprocess, time
_log = os.environ.get("PROF_LOG")
if _log:
    _case = os.environ.get("PROF_CASE", "?")
    def _w(kind, what):
        try:
            with open(_log, "a") as f: f.write(f"{_case}\t{kind}\t{what}\n")
        except Exception: pass
    _w("py", " ".join(sys.argv[:2])[:120])
    _orig = subprocess.Popen.__init__
    def _init(self, args, *a, **k):
        if isinstance(args, (list, tuple)): head = " ".join(os.path.basename(str(x)) if i == 0 else str(x) for i, x in enumerate(args[:2]))
        else: head = str(args)[:60]
        _w("spawn", head[:80])
        return _orig(self, args, *a, **k)
    subprocess.Popen.__init__ = _init
'''

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_harness import CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp-profile')
    parser.add_argument('--baseline', type=Path, help='directory with baseline-times.tsv and baseline-spawn.log')
    parser.add_argument('--no-nice', action='store_true', help='unprioritized serial self-check when nice is unavailable')
    parser.add_argument('cases', nargs='*')
    args = parser.parse_args()
    names = args.cases or list(CASES)
    unknown = set(names) - CASES.keys()
    if unknown:
        parser.error('unknown cases: ' + ', '.join(sorted(unknown)))
    if not args.no_nice:
        try:
            os.nice(10)
        except OSError as error:
            parser.exit(1, f'Cannot lower scheduling priority: {error}\n')
    args.output.mkdir(parents=True, exist_ok=True)
    log = args.output.resolve() / 'spawn.log'
    log.write_text('')
    rows = []
    with tempfile.TemporaryDirectory() as td:
        Path(td, 'sitecustomize.py').write_text(SPAWN_HOOK)
        for name in names:
            env = dict(os.environ, PROF_LOG=str(log), PROF_CASE=name,
                       PYTHONPATH=td + os.pathsep + os.environ.get('PYTHONPATH', ''))
            start = time.monotonic()
            result = subprocess.run([sys.executable, str(ROOT / 'tests/test_harness.py'), name],
                                    env=env, capture_output=True, text=True)
            elapsed = time.monotonic() - start
            count = sum(line.startswith(name + '\tspawn\t') for line in log.read_text().splitlines())
            rows.append((name, result.returncode, elapsed, count))
            print(f'{name}\t{result.returncode}\t{elapsed:.3f}\t{count}', flush=True)
            if result.returncode:
                print(result.stdout + result.stderr, flush=True)
    (args.output / 'times.tsv').write_text(''.join(f'{n}\t{rc}\t{t:.3f}\t{c}\n' for n, rc, t, c in rows))
    print(f'TOTAL\t{sum(rc != 0 for _, rc, _, _ in rows)} failures\t{sum(t for _, _, t, _ in rows):.3f}s\t{sum(c for _, _, _, c in rows)} spawns')
    if args.baseline:
        from collections import Counter
        baseline = {n: (float(t), int(rc)) for n, rc, t in
                    (line.split('\t') for line in (args.baseline / 'baseline-times.tsv').read_text().splitlines())}
        spawns = Counter(line.split('\t')[0] for line in
                         (args.baseline / 'baseline-spawn.log').read_text().splitlines()
                         if '\tspawn\t' in line)
        after = {n: (t, c) for n, rc, t, c in rows if rc == 0}
        common = {n for n in baseline.keys() & after.keys() if baseline[n][1] == 0}
        ordered = sorted(common, key=lambda n: baseline[n][0] * spawns[n], reverse=True)
        failures = [(n, rc) for n, rc, _, _ in rows if rc]
        report = []
        if failures:
            report += ['**FAILED CASES — acceptance failed; excluded from speedup:**',
                       *[f'- **{n}: rc={rc}**' for n, rc in failures], '']
        missing = baseline.keys() - {n for n, _, _, _ in rows}
        if missing:
            report += [f'Incomplete run: {len(missing)} baseline cases missing.', '']
        report += ['| Case | Before seconds | After seconds | Before spawns | After spawns |',
                  '|---|---:|---:|---:|---:|']
        for name in ordered[:15]:
            report.append(f'| {name} | {baseline[name][0]:.3f} | {after[name][0]:.3f} | {spawns[name]} | {after[name][1]} |')
        bt = sum(baseline[n][0] for n in common)
        at = sum(after[n][0] for n in common)
        bc = sum(spawns[n] for n in common)
        ac = sum(after[n][1] for n in common)
        report.append(f'| Total ({len(common)} matched cases) | {bt:.3f} | {at:.3f} | {bc} | {ac} |')
        if bt and bc:
            report.append(f'\nSuccessful cases only: time ratio {at / bt:.1%}; spawn ratio {ac / bc:.1%}')
        if args.no_nice:
            report.append('\nUnprioritized serial self-check (--no-nice).')
        (args.output / 'comparison.md').write_text('\n'.join(report) + '\n')
        print('\n'.join(report))
    return int(any(rc for _, rc, _, _ in rows))


if __name__ == '__main__':
    sys.exit(main())
