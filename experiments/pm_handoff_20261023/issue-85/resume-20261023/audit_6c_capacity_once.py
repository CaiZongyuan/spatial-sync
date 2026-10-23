"""Audit one explicit final-sequence attempt, without mutating source or CI."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1]).resolve()
WORK = HERE.parents[3] / '.worktrees/issue-85-refresh'
HEAD = '6c17ed514c80c84eda80265edef7b136ab321497'
TREE = 'ae0fd300ea70dccb84691fa0d9c31a4c279a016a'
BASE = 'f9f58650b471f7330714325ec6bcf55d1dcfc24e'
RUN = 39947577121

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def dump(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def git(*args):
    return subprocess.check_output(['git', '-C', str(WORK), *args]).decode().strip()

run = load(ROOT / 'run-attempt.json')
attempt = run['run_attempt']
assert run['id'] == RUN and 1 <= attempt <= 5 and run['head_sha'] == HEAD
assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
assert not git('status', '--porcelain')
receipt = load(ROOT / 'receipt.json')
assert receipt['status'] == 'downloaded-and-digests-verified'
expected_names = {f'browser-acceptance-evidence-{n}-attempt-{attempt}' for n in range(1, 9)} | {f'python-checks-core-1-of-1-attempt-{attempt}', f'python-checks-gateway-1-of-2-attempt-{attempt}', f'python-checks-gateway-2-of-2-attempt-{attempt}'}
actual_artifacts = [a for a in load(ROOT / 'artifacts-run.json')['artifacts'] if a['name'].endswith(f'-attempt-{attempt}')]
assert len(actual_artifacts) == 11 and {a['name'] for a in actual_artifacts} == expected_names
for artifact in actual_artifacts:
    archive = ROOT / (artifact['name'] + '.zip')
    assert 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest() == artifact['digest']
dump('artifact-set-audit.json', {'run': RUN, 'attempt': attempt, 'expectedExactNames': sorted(expected_names), 'all11DigestsVerified': True, 'actual': actual_artifacts})

references = [path for path in ROOT.rglob('collection-controller.json') if load(path).get('lane') == 'reference']
assert len(references) == 1
reference = references[0]
local_reference = HERE / 'spatial-sync-issue85-capacity-20261023T103815Z/evidence/reference/collection-controller.json'
assert set(load(reference)['collected']) == set(load(local_reference)['collected'])
assert len(load(reference)['collected']) == len(load(local_reference)['collected'])

previous = HERE / 'audit-39919667060-attempt1-20261023T081754Z'
skip_ref = previous / 'python-baseline-skips-audited.json'
skip_proof = load(previous / 'python-skip-audit.json')
scope = ['tests/unit/test_info_log.py', 'tests/unit/test_info_benchmark_phases.py', 'tests/unit/test_teach_approach.py', 'tests/unit/test_teach_grasp_height.py', 'pyproject.toml', 'uv.lock', 'apps', 'packages', 'tests/gateway']
facts = [{'path': path, 'previous': git('rev-parse', f'860d426ad662a3b8a3ac193da50f89a5623383c8:{path}'), 'current': git('rev-parse', f'{HEAD}:{path}')} for path in scope]
assert all(row['previous'] == row['current'] for row in facts)
junits = []
skips = []
for lane, index, count in [('core', 1, 1), ('gateway', 1, 2), ('gateway', 2, 2)]:
    paths = list((ROOT / f'python-checks-{lane}-{index}-of-{count}-attempt-{attempt}').rglob(f'{lane}-{index}-of-{count}.xml'))
    assert len(paths) == 1
    junits.append(paths[0])
    for case in ET.parse(paths[0]).iter('testcase'):
        skipped = case.find('skipped')
        if skipped is not None:
            identity = [case.get('classname'), case.get('name')]
            guard = next(g for g in skip_proof['guards'] if g['identity'] == identity)
            assert skipped.get('message') == guard['reason']
            assert f"{guard['path']}:{guard['line']}:" in (skipped.text or '')
            skips.append({'identity': identity, 'message': skipped.get('message'), 'text': skipped.text, 'junit': str(paths[0])})
assert sorted(row['identity'] for row in skips) == sorted(load(skip_ref))
dump('independent-reference-audit.json', {'sourceGuardAndDependencyFacts': facts, 'auditedSkipReference': str(skip_ref), 'actualRuntimeSkips': skips, 'allFourRuntimeSkipsMatch': True, 'independentCompleteCollection': str(local_reference), 'actualCompleteCollection': str(reference), 'exactIndependentNodeSetMatches': True})

argv = [sys.executable, str(HERE / 'analyze_ci_attempt_capacity.py'), '--run-json', str(ROOT / 'run-attempt.json'), '--jobs-json', str(ROOT / 'jobs-attempt.json'), '--log', str(ROOT / 'ci-attempt.log'), '--expected-head', HEAD, '--expected-browser-json', str(HERE / 'expected-browser-collection-capacity.json'), '--shards', '8', '--python-collection', str(reference), '--python-baseline-skips', str(skip_ref), '--python-coverage-root', str(ROOT), '--output', str(ROOT / 'strict-analysis.json')]
reports = []
for shard in range(1, 9):
    paths = list((ROOT / f'browser-acceptance-evidence-{shard}-attempt-{attempt}').rglob('results.json'))
    assert len(paths) == 1
    report = load(paths[0])
    assert report['config']['version'] == '1.64.1' and report['config']['workers'] == 1 and report['config']['fullyParallel']
    assert all(p['retries'] == 0 and p['repeatEach'] == 1 and p['timeout'] == 45000 for p in report['config']['projects'])
    reports.append(report)
    argv += ['--report', str(paths[0])]
for path in junits:
    argv += ['--junit', str(path)]
assert not (ROOT / 'analyzer-exit.json').exists(), 'One analyzer invocation per explicit owned attempt'
dump('analyzer-argv.json', argv)
with (ROOT / 'analyzer-stdout.json').open('wb') as out, (ROOT / 'analyzer-stderr.log').open('wb') as err:
    result = subprocess.run(argv, stdout=out, stderr=err, timeout=30)
dump('analyzer-exit.json', {'exitCode': result.returncode, 'invocations': 1, 'parserChanged': False})
assert result.returncode in [0, 1] and (ROOT / 'strict-analysis.json').exists()
analysis = load(ROOT / 'strict-analysis.json')
dump('browser-config-audit.json', {'all8ReportsPlaywright1641': True, 'allWorkers1FullyParallel': True, 'allProjectRetries0Repeat1Default45000': True})
summary = {key: analysis[key] for key in ['runId', 'attempt', 'head', 'valid', 'checks', 'longestJobSeconds', 'python', 'pythonCoverage', 'browser']}
summary['overBudgetJobs'] = [row for row in analysis['jobs'] if row['seconds'] > 200]
summary['overMarginCases'] = [{key: row[key] for key in ['file', 'title', 'timeoutMs', 'durationMs', 'report']} for row in analysis['cases'] if row['durationMs'] * 5 > row['timeoutMs'] * 3]
summary['longestMarginCases'] = [{key: row[key] for key in ['file', 'title', 'timeoutMs', 'durationMs']} for row in analysis['cases'][:5]]
summary['auditedAt'] = datetime.now(timezone.utc).isoformat()
dump('strict-summary.json', summary)
print(json.dumps(summary, ensure_ascii=True, indent=2))
