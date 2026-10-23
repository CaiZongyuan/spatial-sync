"""Single outer deadline and ownership wrapper around the frozen C0 adapter."""
import time
ENTRY = time.monotonic()
ENTRY_UTC = time.time()
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
OUT = Path(__file__).resolve().parent
SOURCE = OUT.parent / 'c0-paired-source-003'
AUTHORITY = OUT.parent / 'ROOT-C0-LAUNCHER2-GO-20261023.json'
RUNTIME = SOURCE / 'static-registration-002'
# Set the actual Windows environment block in Python, before any SDK import.
# PowerShell's empty-string assignment deletes the variable on this host.
assert not any(name.split('.')[0] in {'newton', 'warp', 'pxr', 'numpy', 'scipy', 'mujoco'}
               for name in sys.modules)
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['WARP_CACHE_PATH'] = str(RUNTIME / 'kernel-cache')
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
spec = importlib.util.spec_from_file_location('c0_outer_unchanged_r3', OUT.parent / 'r18-n1b-r3/adapter.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import psutil

budget = m.Budget(start=ENTRY)
owner = m.identity(psutil.Process())
state = {'status': 'starting', 'owner': owner, 'startedUtc': datetime.datetime.fromtimestamp(ENTRY_UTC, datetime.UTC).isoformat(),
         'startMonotonic': ENTRY, 'endMonotonic': budget.end, 'workEndMonotonic': budget.work_end,
         'deadlineUtc': datetime.datetime.fromtimestamp(ENTRY_UTC + 180, datetime.UTC).isoformat(),
         'runtimeOutput': str(RUNTIME), 'attempts': 0, 'mechanicalBudget': '2/2 permanently consumed; unchanged',
         'cudaVisibleDevices': os.environ.get('CUDA_VISIBLE_DEVICES'),
         'warpCachePath': os.environ.get('WARP_CACHE_PATH'), 'errors': []}
m.save(OUT / 'OWNER.json', state)
exit_code = 1
try:
    assert os.environ.get('CUDA_VISIBLE_DEVICES') == ''
    assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'
    assert Path(os.environ['WARP_CACHE_PATH']).resolve() == RUNTIME / 'kernel-cache'
    assert not RUNTIME.exists()
    authority = m.load(AUTHORITY)
    assert authority['modelBudgetAllocated'] is True and authority['totalSeconds'] == 180
    auth = authority | {'deadlineUtc': state['deadlineUtc'], 'outerEntryUtc': state['startedUtc'],
                        'outerEntryMonotonic': ENTRY, 'authorityPath': str(AUTHORITY),
                        'authoritySha256': m.digest(AUTHORITY, budget)}
    authorization = OUT / 'AUTHORIZATION.json'
    assert not authorization.exists()
    m.save(authorization, auth)
    state['authorizationSha256'] = m.digest(authorization, budget)
    for name, field in [('adapter.py', 'adapterSha256'), ('PLAN.json', 'planSha256'),
                        ('SOURCE_MANIFEST.json', 'sourceManifestSha256')]:
        assert m.digest(SOURCE / name, budget) == authority[field]
    state['attempts'] = 1
    state['status'] = 'running'
    m.save(OUT / 'OWNER.json', state)
    argv = [str(Path(sys.executable).resolve()), '-I', '-B', str(SOURCE / 'adapter.py'),
            '--authorization', str(authorization), '--pm-go', authority['pmGo'], '--output', str(RUNTIME),
            '--total-end', str(budget.end), '--work-end', str(budget.work_end)]
    m.owned_call(argv, OUT, os.environ.copy(), OUT / 'owned-adapter', budget, phase='total')
    state['status'] = 'adapter-returned-zero; readback-pending'
except BaseException as error:
    state['status'] = 'failed'
    state['errors'].append({'type': type(error).__name__, 'message': str(error)})
    (OUT / 'outer-traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
finally:
    try:
        budget.check('total')
        receipts = []
        for path in sorted([*RUNTIME.rglob('receipt.json'), OUT / 'owned-adapter/receipt.json']):
            receipt = m.load(path)
            observations = [m.observe(row, psutil) for row in receipt.get('processes', [])]
            receipts.append({'path': str(path), 'status': receipt['status'], 'exitCode': receipt.get('exitCode'),
                             'uncertain': receipt.get('uncertain'), 'cleanupErrors': receipt.get('cleanupErrors'),
                             'persistenceErrors': receipt.get('persistenceErrors'), 'postrun': observations})
        state['freshProcessReadback'] = receipts
        assert receipts and all(row['status'] == 'complete' and row['exitCode'] == 0
                                and not row['uncertain'] and not row['cleanupErrors'] and not row['persistenceErrors']
                                and all(p['state'] == 'gone' for p in row['postrun']) for row in receipts)
        finals = list((RUNTIME / 'terminal').glob('*.json'))
        assert len(finals) == 1
        terminal = m.load(finals[0])
        state['terminalPath'] = str(finals[0])
        state['terminalSha256'] = m.digest(finals[0], budget, 'total')
        state['adapterWallSeconds'] = terminal['wallSeconds']
        state['preVerified'] = terminal.get('pre', {}).get('status') == 'verified'
        state['postVerified'] = terminal.get('post', {}).get('status') == 'verified'
        state['diagnosticComparison'] = terminal.get('diagnosticComparison')
        state['samples'] = terminal.get('samples', [])
        assert terminal['status'].startswith('diagnostic-complete') and not terminal['errors']
        assert state['preVerified'] and state['postVerified']
        comparison = state['diagnosticComparison']
        assert all(comparison[key] for key in ('structureWithinDiagnosticTolerance',
                   'sameObservedBasePosesWithinTolerance', 'declaredBasePosesWithinTolerance'))
        model, teacher = terminal['model'], terminal['teacher']
        state['modelCounters'] = model['counters']
        state['teacherFkCalls'] = teacher['fkCalls']
        assert len(model['samples']) == len(teacher['samples']) == 2
        assert all(a['actualReadbackQ'] == b['sameInputQ'] for a, b in zip(model['samples'], teacher['samples'], strict=True))
        assert len(model['runtimeAssetsPost']) == 14
        state['sameReadbackQ'] = True
        state['runtimeAssetPostHashCount'] = len(model['runtimeAssetsPost'])
        assert not state['errors']
        state['status'] = 'static-registration-observed; not global or physical feasibility'
        exit_code = 0
    except BaseException as error:
        state['status'] = 'failed'
        state['errors'].append({'type': type(error).__name__, 'message': str(error), 'phase': 'readback'})
    state['elapsedThroughReadbackSeconds'] = time.monotonic() - ENTRY
    state['releasedUtc'] = datetime.datetime.now(datetime.UTC).isoformat()
    state['exitCode'] = exit_code
    if time.monotonic() >= budget.end:
        state['status'] = 'failed'
        state['exitCode'] = exit_code = 1
        state['errors'].append({'type': 'StageDeadline', 'phase': 'outer-readback'})
    m.save(OUT / 'RESULT.json', state)
    print(json.dumps(state, sort_keys=True))
sys.exit(exit_code)
