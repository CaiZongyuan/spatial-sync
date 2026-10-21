"""Own this diagnostic's creating handles and verify descendants before cleanup."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

import psutil

output = Path('.scratch/viewer-e2e')
output.mkdir(parents=True, exist_ok=True)
handles, identities = [], {}
receipt = {'startedUtc': time.time(), 'processes': [], 'cleanup': []}

def identify(process):
    return {'created': process.create_time(), 'command': process.cmdline()}

def remember():
    for child in handles:
        if child.poll() is None:
            try:
                children = psutil.Process(child.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                continue
            for process in children:
                try:
                    identities[process.pid] = identify(process)
                except psutil.NoSuchProcess:
                    pass

def spawn(command, **options):
    child = subprocess.Popen(command, start_new_session=True, **options)
    identity = identify(psutil.Process(child.pid))
    identities[child.pid] = identity
    handles.append(child)
    receipt['processes'].append({'pid': child.pid, **identity})
    return child

def interrupted(signum, frame):
    raise SystemExit(128 + signum)

for event in (signal.SIGTERM, signal.SIGINT):
    signal.signal(event, interrupted)

try:
    with open('/tmp/spatial-api.log', 'wb') as log:
        api = spawn([sys.executable, '-m', 'uvicorn', 'approval_gateway:app', '--app-dir',
                     'tests/fixtures', '--host', '127.0.0.1', '--port', '8000'], stdout=log,
                    stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 30
    while True:
        if api.poll() is not None:
            raise RuntimeError('Owned API exited before readiness')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('READINESS FAILURE: API')
        time.sleep(.5)
    child = spawn(['pnpm', 'test:e2e', '--fully-parallel', '--workers=1', *sys.argv[1:]],
                  env=os.environ | {'SPATIAL_E2E_VIDEO': '0'})
    while child.poll() is None:
        remember()
        time.sleep(.5)
    receipt['testExitCode'] = child.returncode
    raise SystemExit(child.returncode)
finally:
    remember()
    for child in reversed(handles):
        if child.poll() is None:
            try:
                process = psutil.Process(child.pid)
            except psutil.NoSuchProcess:
                continue
            if identify(process) == identities[child.pid] and os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # The same unreaped creating handle still owns this process group.
                    if identify(psutil.Process(child.pid)) == identities[child.pid]:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=2)
    for pid, identity in identities.items():
        try:
            process = psutil.Process(pid)
            if identify(process) != identity:
                receipt['cleanup'].append({'pid': pid, 'status': 'identity_changed_not_signalled'})
                continue
            process.terminate()
            try:
                process.wait(timeout=2)
            except psutil.TimeoutExpired:
                if identify(process) == identity:
                    process.kill()
                    process.wait(timeout=1)
            receipt['cleanup'].append({'pid': pid, 'status': 'owned_exited'})
        except psutil.NoSuchProcess:
            receipt['cleanup'].append({'pid': pid, 'status': 'gone'})
    receipt['handlesExited'] = all(child.poll() is not None for child in handles)
    (output / 'diagnostic-owned.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
