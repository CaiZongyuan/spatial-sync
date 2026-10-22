"""One diagnostic run, verified creating handles, and reserved cleanup/upload time."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import psutil

output = Path('.scratch/observer95-host')
output.mkdir(parents=True, exist_ok=True)
handles, identities, born, groups = [], {}, {}, set()
receipt = {'startedUtc': time.time(), 'processes': [], 'cleanup': [], 'ownershipIssues': []}
exit_code = 2
job_deadline = float(os.environ.get('OBSERVER95_JOB_DEADLINE', '0'))
work_deadline = job_deadline - 50  # 15s cleanup plus 35s upload/escalation guard.

class BudgetExhausted(Exception):
    pass

def identify(process):
    return {'created': process.create_time(), 'command': process.cmdline()}

def issue(stage, error, pid=None):
    receipt['ownershipIssues'].append({'stage': stage, 'pid': pid, 'error': str(error)})

def register_root(child):
    # Only a current creating handle, same ctime and two stable samples within 1s.
    until = born[child.pid] + 1
    created = identities.get(child.pid, {}).get('created')
    previous = None
    while time.monotonic() < until and child.poll() is None:
        current = identify(psutil.Process(child.pid))
        created = current['created'] if created is None else created
        if current['created'] != created:
            raise RuntimeError('Creating handle ctime changed; not owned')
        if current == previous:
            identities[child.pid] = current
            return current
        previous = current
        time.sleep(.03)
    raise RuntimeError('Creating handle not stable twice within 1s')

def current_root(child):
    current = identify(psutil.Process(child.pid))
    if current != identities.get(child.pid) and time.monotonic() - born[child.pid] <= 1:
        current = register_root(child)
        receipt['processes'].append({'pid': child.pid, 'registration': 'same_ctime_exec_refresh', **current})
    if current != identities.get(child.pid):
        raise RuntimeError('Root identity changed; no group signal/discovery')
    return current

def remember():
    for child in handles:
        try:
            if child.poll() is not None:
                continue
            before = current_root(child)
            discovered = {}
            for process in psutil.Process(child.pid).children(recursive=True):
                try:
                    discovered[process.pid] = identify(process)
                except psutil.NoSuchProcess:
                    pass
            if identify(psutil.Process(child.pid)) != before:
                raise RuntimeError('Root changed during discovery; discard new identities')
            identities.update(discovered)
        except psutil.NoSuchProcess:
            pass  # Natural root exit gives no new descendant ownership claim.
        except Exception as error:
            issue('remember', error, child.pid)

def spawn(command, **options):
    child = subprocess.Popen(command, start_new_session=True, **options)
    handles.append(child)
    born[child.pid] = time.monotonic()
    groups.add(child.pid)
    identity = register_root(child)
    receipt['processes'].append({'pid': child.pid, **identity})
    return child

def interrupted(signum, frame):
    raise SystemExit(128 + signum)

def require_test_budget():
    remaining = work_deadline - time.time()
    if remaining < 194:
        raise BudgetExhausted(f'Only {remaining:.1f}s; full original shard not admitted')
    return min(210, remaining)

for event in (signal.SIGTERM, signal.SIGINT):
    signal.signal(event, interrupted)

try:
    require_test_budget()
    api_deadline = min(time.time() + 30, work_deadline - 194)
    if api_deadline <= time.time():
        raise BudgetExhausted('No API readiness time before full-shard admission boundary')
    with open(output / 'api.log', 'wb') as log:
        api = spawn([sys.executable, '-m', 'uvicorn', 'approval_gateway:app', '--app-dir',
                     'tests/fixtures', '--host', '127.0.0.1', '--port', '8000'], stdout=log,
                    stderr=subprocess.STDOUT)
    while True:
        if api.poll() is not None:
            raise RuntimeError('Owned API exited before readiness')
        if time.time() >= api_deadline:
            raise BudgetExhausted('API readiness consumed remaining full-shard admission time')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(.5)
    test_budget = require_test_budget()
    receipt['budget'] = {'jobDeadlineUtc': job_deadline, 'workDeadlineUtc': work_deadline,
                         'testBudgetSeconds': test_budget, 'cleanupReserve': 15,
                         'uploadGuard': 35, 'outerKillEscalationReserve': 5}
    child = spawn(['pnpm', 'test:e2e', '--fully-parallel', '--workers=1', *sys.argv[1:]],
                  env=os.environ | {'SPATIAL_E2E_VIDEO': '0'})
    execution_deadline = min(time.time() + test_budget, work_deadline)
    while child.poll() is None:
        if time.time() >= execution_deadline:
            raise BudgetExhausted('Execution budget exhausted; no business verdict')
        remember()
        time.sleep(.5)
    receipt['testExitCode'] = child.returncode
    receipt['verdict'] = 'original_tests_completed'
    exit_code = child.returncode
    try:
        host = json.loads((output / 'host-state.json').read_text(encoding='utf-8'))
        receipt['host'] = {key: host.get(key) for key in ('business', 'installed', 'restored', 'identityComplete', 'controlMatchObserved', 'overflow', 'pendingOverflow', 'recorderError', 'packetComplete', 'errors')}
        if not host.get('packetComplete') and exit_code == 0:
            exit_code = 4  # Original business success is retained separately.
    except Exception as error:
        receipt['hostStateError'] = str(error)
        if exit_code == 0:
            exit_code = 4
except BudgetExhausted as error:
    receipt.update(verdict='budget-exhausted-no-business-verdict', error=str(error))
    exit_code = 124
except BaseException as error:
    receipt.update(verdict='interrupted-or-readiness-failed', error=str(error))
    exit_code = 2
finally:
    try:
        remember()
        cleanup_deadline = time.monotonic() + min(15, max(0, job_deadline - 35 - time.time()))
        for child in reversed(handles):
            try:
                if child.poll() is None:
                    current_root(child)
                    if os.getpgid(child.pid) != child.pid:
                        raise RuntimeError('Owned root no longer leads creating group')
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=max(.01, min(2, cleanup_deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        current_root(child)
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=max(.01, min(1, cleanup_deadline - time.monotonic())))
            except psutil.NoSuchProcess:
                pass
            except Exception as error:
                issue('handle_cleanup', error, child.pid)
        for pid, identity in identities.items():
            try:
                process = psutil.Process(pid)
                if identify(process) != identity:
                    raise RuntimeError('Identity changed; not individually signalled')
                process.terminate()
                try:
                    process.wait(timeout=max(.01, min(.3, cleanup_deadline - time.monotonic())))
                except psutil.TimeoutExpired:
                    if identify(process) != identity:
                        raise RuntimeError('Identity changed before kill; not signalled')
                    process.kill()
                receipt['cleanup'].append({'pid': pid, 'status': 'owned_signal_sent'})
            except psutil.NoSuchProcess:
                receipt['cleanup'].append({'pid': pid, 'status': 'gone'})
            except Exception as error:
                issue('identity_cleanup', error, pid)
    except Exception as error:
        issue('cleanup_outer', error)
    finally:
        receipt['remainingGroups'], receipt['remainingIdentities'], receipt['listeners'] = [], [], []
        for pid, identity in identities.items():
            try:
                actual = identify(psutil.Process(pid))
                if actual != identity:
                    issue('fresh_identity_readback', 'Identity changed; not signalled', pid)
                else:
                    receipt['remainingIdentities'].append({'pid': pid, **actual})
            except psutil.NoSuchProcess:
                pass
            except Exception as error:
                issue('fresh_identity_readback', error, pid)
        try:
            for process in psutil.process_iter():
                try:
                    group = os.getpgid(process.pid)
                    if group in groups:
                        receipt['remainingGroups'].append({'pid': process.pid, 'pgid': group, **identify(process)})
                except (psutil.NoSuchProcess, ProcessLookupError):
                    pass
        except Exception as error:
            issue('fresh_group_readback', error)
        try:
            receipt['listeners'] = [{'pid': row.pid, 'port': row.laddr.port}
                                    for row in psutil.net_connections(kind='tcp')
                                    if row.status == psutil.CONN_LISTEN and row.laddr.port in (8000, 5184)]
        except Exception as error:
            issue('fresh_listener_readback', error)
        handles_exited = True
        for child in handles:
            try:
                handles_exited = child.poll() is not None and handles_exited
            except Exception as error:
                handles_exited = False
                issue('handle_readback', error, child.pid)
        receipt['handlesExited'] = handles_exited
        receipt['cleanupVerified'] = (handles_exited and not receipt['remainingGroups'] and not receipt['remainingIdentities']
                                      and not receipt['listeners'] and not receipt['ownershipIssues'])
        if not receipt['cleanupVerified']:
            exit_code = 3
        receipt.update(finalExitCode=exit_code, finishedUtc=time.time())
        (output / 'diagnostic-owned.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')

raise SystemExit(exit_code)
