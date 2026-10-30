"""One bounded issue86 Linux run of the five focused original contract journeys on the test/workflow candidate; starts only after root GO."""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psutil

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("--head", required=True)
parser.add_argument("--script-sha", required=True)
parser.add_argument("--tests-sha", required=True)
parser.add_argument("--config-sha", required=True)
parser.add_argument("--tree", required=True)
args = parser.parse_args()
scratch = Path('/mnt/d/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-86')
readiness = json.loads((scratch / "linux-abba/preparation-ready.json").read_text())
assert readiness["ready"]
task_root = Path(readiness["environmentRoot"])
task = Path(readiness["sourceSnapshot"])
worktree = Path("/mnt/d/Projects/Robots/spatial-sync/.worktrees/issue-86")
snapshot = json.loads(Path('/mnt/d/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-86/workbench-B-d59303b9/source-manifest.json').read_text())
assert hashlib.sha256(Path('/mnt/d/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-86/workbench-B-d59303b9/source-manifest.json').read_bytes()).hexdigest() == 'f645b5ead30efd3cc0aafa346fea180b98aa37ca80301d14c844e5b52c6bb003'
assert all((task / name).is_file() and (hashlib.sha256((task / name).read_bytes()).hexdigest() == digest or (name == "apps/web/src/routeTree.gen.ts" and hashlib.sha256((task / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == snapshot["normalizedFileHashes"][name])) for name, digest in snapshot["snapshotFileHashes"].items())
assert all((task / name).is_symlink() and os.readlink(task / name) == row["target"] for name, row in snapshot["snapshotSymlinks"].items())
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == args.script_sha.lower()
head = subprocess.check_output(["git", "-c", "core.autocrlf=true", "--git-dir=/mnt/d/Projects/Robots/spatial-sync/.git/worktrees/issue-86", "--work-tree=/mnt/d/Projects/Robots/spatial-sync/.worktrees/issue-86", "rev-parse", "HEAD"], cwd=worktree, text=True).strip()
assert head == args.head
tree = subprocess.check_output(["git", "-c", "core.autocrlf=true", "--git-dir=/mnt/d/Projects/Robots/spatial-sync/.git/worktrees/issue-86", "--work-tree=/mnt/d/Projects/Robots/spatial-sync/.worktrees/issue-86", "rev-parse", "HEAD^{tree}"], cwd=worktree, text=True).strip()
assert tree == args.tree
test_files = ['workbench.spec.ts']
tests = [task / "tests/e2e" / name for name in test_files]
test_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in zip(test_files, tests)}
assert test_hashes == {'workbench.spec.ts': '57b4570313bd174ab347512d0dce79d41dc1979fc1e8e330f1de843f24a11d43'}
assert hashlib.sha256(b"\0".join(path.read_bytes() for path in tests)).hexdigest() == args.tests_sha.lower()
config = task / "apps/web/playwright.config.ts"
assert hashlib.sha256(config.read_bytes()).hexdigest() == args.config_sha.lower()
assert args.config_sha.lower() == '2b80bb9fcb8f2eb5bd49de2fa65f50693ce0635806046e8bf01a612345111734'
assert head == 'd59303b9dc608a75a113f927a4283ea91dccf13b'
assert tree == 'dde110417b9464287104de49837189e4b5a75a31'
assert not subprocess.check_output(["git", "-c", "core.autocrlf=true", "--git-dir=/mnt/d/Projects/Robots/spatial-sync/.git/worktrees/issue-86", "--work-tree=/mnt/d/Projects/Robots/spatial-sync/.worktrees/issue-86", "status", "--porcelain"], cwd=worktree, text=True).strip()
ports = (19886, 5388)
for port in ports:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
evidence = task_root / "runs" / ("workbench-B-native-ws-linux" + "-" + head[:7] + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
evidence.mkdir(parents=True)
(evidence / "temp").mkdir()
api, web = "http://127.0.0.1:19886", "http://127.0.0.1:5388"
env = os.environ | {
    "SPATIAL_API_URL": api, "SPATIAL_WEB_URL": web, "SPATIAL_ALLOWED_ORIGINS": web,
    "SPATIAL_TEST_APPROVAL": "1",
    "LW_LIBRARY": str(evidence / "library"), "SPATIAL_STATE_ROOT": str(evidence / "state"),
    "SPATIAL_RECORDINGS_ROOT": str(evidence / "recordings"),
    "TEMP": str(evidence / "temp"), "TMP": str(evidence / "temp"),
    "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "-1",
    "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
    "SPATIAL_E2E_VIDEO": "1", "PLAYWRIGHT_JSON_OUTPUT_NAME": str(evidence / "playwright.json"),
}
env["SPATIAL_UI_CANDIDATE"] = str(task.resolve())
env["SPATIAL_PAPER_EVIDENCE"] = str(evidence.resolve())
for key in ["SPATIAL_UI_POLISH_SOURCE", "SPATIAL_UI_DIAGNOSTICS", "SPATIAL_UI_WINDOW_DEADLINE_MS", "SPATIAL_NEWTON_E2E", "SPATIAL_TEST_NEWTON", "SPATIAL_PLATFORM_NEWTON_E2E", "SPATIAL_TEST_APPROVAL_AUTH"]:
    env.pop(key, None)
env.update(readiness["runtimeEnvironment"])
env.update({"LW_LIBRARY": str(evidence / "library"), "SPATIAL_STATE_ROOT": str(evidence / "state"), "SPATIAL_RECORDINGS_ROOT": str(evidence / "recordings"), "TEMP": str(evidence / "temp"), "TMP": str(evidence / "temp"), "TMPDIR": str(evidence / "temp")})
env.pop("NODE_OPTIONS", None)
env.pop("NODE_ENV", None)
env["SPATIAL_TEST_APPROVAL_AUTH"] = "0"
node = readiness["node"]
assert node
handles, roots, identities, unknowns, cleanup_errors = [], [], {}, [], []
started = time.monotonic()
deadline = started + 120
result = {"source": str(task), "head": head, "scriptSha": args.script_sha, "case": "workbench-B-native-ws-linux", "tree": tree, "testsSha": args.tests_sha, "testFilesSha256": test_hashes, "configSha": args.config_sha,
          "api": api, "web": web, "budgetSeconds": 120, "cleanupBudgetSeconds": 25, "totalBudgetSeconds": 145,
          "normalizedLockSha256": {name: hashlib.sha256((task / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in ("uv.lock", "pnpm-lock.yaml")},
          "supervisorTemplateSha256": "6560efb248a8a6eb540652bf3220346371afcd7a00794d9ba308c0128cfa5b31", "snapshotFileHashesVerified": len(snapshot["snapshotFileHashes"]), "runtimeEnvironment": {key: env.get(key) for key in ["UV_PROJECT_ENVIRONMENT", "LW_LIBRARY", "SPATIAL_STATE_ROOT", "SPATIAL_RECORDINGS_ROOT", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH", "NODE_OPTIONS", "SPATIAL_TEST_APPROVAL", "SPATIAL_TEST_APPROVAL_AUTH", "SPATIAL_E2E_VIDEO"]}}


def capture(process, label):
    try:
        receipt = {"pid": process.pid, "creationTime": process.create_time(), "command": process.cmdline(), "label": label}
        identities[(receipt["pid"], receipt["creationTime"])] = receipt
    except psutil.NoSuchProcess:
        pass
    except (psutil.AccessDenied, OSError) as error:
        unknowns.append({"pid": process.pid, "label": label, "operation": "capture", "error": repr(error)})


def refresh():
    for label, child in roots:
        if child.poll() is not None:
            continue
        try:
            process = psutil.Process(child.pid)
            capture(process, label)
            for descendant in process.children(recursive=True):
                capture(descendant, label + " descendant")
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, OSError) as error:
            unknowns.append({"pid": child.pid, "label": label, "operation": "descendants", "error": repr(error)})
    for process in psutil.process_iter():
        try:
            if os.getpgid(process.pid) in {child.pid for _, child in roots}:
                capture(process, "owned Linux process group")
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
        except (psutil.AccessDenied, PermissionError) as error:
            unknowns.append({"pid": process.pid, "operation": "group observation", "error": repr(error)})
    (evidence / "ownership.json").write_text(json.dumps(list(identities.values()), indent=2), encoding="utf-8")


def matches(receipt):
    try:
        process = psutil.Process(receipt["pid"])
        if process.create_time() == receipt["creationTime"] and process.status() == psutil.STATUS_ZOMBIE:
            return "zombie", None
        if process.create_time() == receipt["creationTime"] and process.cmdline() == receipt["command"]:
            return "same", process
        return "reused", None
    except psutil.NoSuchProcess:
        return "gone", None
    except (psutil.AccessDenied, OSError) as error:
        unknowns.append({**receipt, "operation": "identity", "error": repr(error)})
        return "unknown", None


def launch(label, command, cwd):
    log = (evidence / (label + ".log")).open("wb")
    handles.append(log)
    child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    roots.append((label, child))
    # Retain the creating handle before any fallible identity observation.
    refresh()
    if unknowns:
        raise RuntimeError("Unknown owned process identity")
    return child


def ready(url, child):
    stop = min(deadline, time.monotonic() + 25)
    with httpx.Client(trust_env=False, timeout=.6) as client:
        while time.monotonic() < stop:
            assert child.poll() is None, f"Owned service exited before ready: {url}"
            refresh()
            if unknowns:
                raise RuntimeError("Unknown owned process identity")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.15)
    raise RuntimeError("Owned service readiness budget exceeded: " + url)


code = 1
try:
    backend = launch("api", [readiness["python"], "-B", "-m", "uvicorn",
                             "approval_gateway:app", "--app-dir", "tests/fixtures", "--host", "127.0.0.1",
                             "--port", "19886", "--log-level", "warning"], task)
    vite = launch("web", [node, str(task / "apps/web/node_modules/vite/bin/vite.js"),
                          "--host", "127.0.0.1", "--port", "5388", "--strictPort"], task / "apps/web")
    ready(api + "/api/v1/health", backend)
    ready(web, vite)
    command = [node, str(task / "node_modules/@playwright/test/cli.js"), "test", *test_files,
               "--grep", 'real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume', "--config", str(config), "--workers=1", "--retries=0", "--reporter=line,json", "--output", str(evidence / "results")]
    result["actualCommand"] = command
    executed = evidence / "executed-scripts"
    executed.mkdir()
    for script in [Path(__file__), config, *tests, task / "tests/e2e/cleanup-observer.ts", task / "tests/fixtures/approval_gateway.py"]:
        shutil.copy2(script, executed / script.name)
    runner = launch("playwright", command, task)
    print(json.dumps({"evidence": str(evidence), **result}), flush=True)
    while runner.poll() is None:
        refresh()
        if unknowns:
            raise RuntimeError("Unknown owned process identity")
        if time.monotonic() >= deadline:
            raise TimeoutError("120s original workbench B body budget exhausted")
        if backend.poll() is not None or vite.poll() is not None:
            raise RuntimeError("Owned service exited during browser window")
        time.sleep(.2)
    code = runner.returncode
except Exception as error:
    code = 1
    result["error"] = repr(error)
finally:
    cleanup_started = time.monotonic()
    cleanup_deadline = cleanup_started + 25
    remaining, listeners = [], None

    def budget():
        return max(0, cleanup_deadline - time.monotonic())

    def attempt(label, action):
        try:
            return action()
        except Exception as error:
            cleanup_errors.append({"operation": label, "error": repr(error)})
            return None

    try:
        attempt("refresh-before-cleanup", refresh)
        def close_sessions():
            if not any(label == "api" and child.poll() is None for label, child in roots):
                return
            with httpx.Client(trust_env=False, timeout=min(1, budget())) as client:
                before = client.get(api + "/api/v1/health").json()
                result["sessionCleanup"] = {"before": before, "deletions": []}
                for session in before["sessions"]:
                    if budget() <= 0:
                        raise TimeoutError("Aggregate cleanup deadline reached")
                    if session["state"] not in ("STOPPED", "FAILED"):
                        response = client.delete(api + "/api/v1/sessions/" + session["sessionId"], timeout=min(1, budget()))
                        result["sessionCleanup"]["deletions"].append({"sessionId": session["sessionId"], "status": response.status_code})
                        response.raise_for_status()
                if budget() <= 0:
                    raise TimeoutError("Aggregate cleanup deadline reached")
                after = client.get(api + "/api/v1/health", timeout=min(1, budget())).json()
                result["sessionCleanup"]["after"] = after
                result["sessionCleanup"]["activeSessionCount"] = sum(session["state"] not in ("STOPPED", "FAILED") for session in after["sessions"])
                if any(session["state"] not in ("STOPPED", "FAILED") for session in after["sessions"]):
                    raise RuntimeError("Owned session remains active")
        if budget() > 0:
            attempt("close-owned-sessions", close_sessions)
        attempt("refresh-after-session-close", refresh)
        # Only exact verified descendant identities may be terminated by PID.
        for receipt in reversed(list(identities.values())):
            if budget() <= 0:
                break
            status, process = attempt("classify-for-termination", lambda: matches(receipt)) or ("unknown", None)
            if status == "same":
                attempt("terminate-matching-descendant", process.terminate)
        # Creating Popen handles remain available when identity inspection fails.
        for _, child in reversed(roots):
            if child.poll() is None:
                attempt("terminate-creating-handle", child.terminate)
        for _, child in roots:
            if budget() <= 0:
                break
            attempt("wait-creating-handle", lambda: child.wait(timeout=min(2, budget())))
        # Wait on every recorded descendant, retaining an aggregate escalation reserve.
        while budget() > 3:
            alive = []
            for receipt in identities.values():
                status, _ = attempt("classify-during-exit-wait", lambda: matches(receipt)) or ("unknown", None)
                if status == "same":
                    alive.append(receipt)
            if not alive or unknowns:
                break
            time.sleep(min(.1, budget()))
        for receipt in identities.values():
            status, process = attempt("classify-for-final-kill", lambda: matches(receipt)) or ("unknown", None)
            if status == "same" and budget() > 0:
                attempt("kill-verified-remaining-process", process.kill)
        for _, child in roots:
            if child.poll() is None and budget() > 0:
                attempt("kill-creating-handle", child.kill)
                attempt("reap-creating-handle", lambda: child.wait(timeout=min(1, budget())))
        for receipt in identities.values():
            status, _ = attempt("classify-after-cleanup", lambda: matches(receipt)) or ("unknown", None)
            if status in ("same", "unknown", "zombie"):
                remaining.append({**receipt, "identityStatus": status})
        if budget() > 0:
            connections = attempt("inspect-owned-listeners", lambda: psutil.net_connections(kind="tcp"))
            if connections is not None:
                listeners = [{"port": row.laddr.port, "pid": row.pid} for row in connections
                             if row.status == psutil.CONN_LISTEN and row.laddr.port in ports]
    finally:
        # Receipts survive refresh, psutil, terminate and wait failures.
        for log in handles:
            attempt("close-log", log.close)
        result |= {"exitCode": code, "elapsedSeconds": time.monotonic() - started,
                   "cleanupSeconds": time.monotonic() - cleanup_started,
                   "remainingOwnedProcesses": remaining, "remainingListeners": listeners,
                   "unknownObservations": unknowns, "cleanupErrors": cleanup_errors,
                   "creatingHandles": [{"label": label, "pid": child.pid, "exitCode": child.poll()} for label, child in roots]}
        if result.get("error") or remaining or listeners is None or listeners or unknowns or cleanup_errors or result["cleanupSeconds"] >= 25:
            code = 1
        result["exitCode"] = code
        result["observedFinalIdentities"] = [{**receipt, "finalIdentityState": matches(receipt)[0]} for receipt in identities.values()]
        result["primaryEvidenceMirror"] = str(scratch / evidence.name)
        (evidence / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print((evidence / "playwright.log").read_text("utf-8", errors="replace") if (evidence / "playwright.log").exists() else "Browser did not start", flush=True)
        print(json.dumps({"receipt": str(evidence / "result.json"), **result}), flush=True)
mirror = scratch / evidence.name
assert not mirror.exists()
shutil.copytree(evidence, mirror)
assert all((mirror / path.relative_to(evidence)).read_bytes() == path.read_bytes() for path in evidence.rglob("*") if path.is_file())
print(json.dumps({"primaryEvidenceMirror": str(mirror), "copyBytesVerified": True}), flush=True)
sys.exit(code)
