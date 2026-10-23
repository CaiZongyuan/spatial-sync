"""Own the entire D2 launch, including preflight/hash/readback, under one deadline.

No GO/identity/input/source hash is read in this parent. All such work happens
in its suspended-created child Job. The PS entrypoint supplies the launch origin
before starting this interpreter and independently bounds this guardian handle.
"""

import argparse
import ctypes as C
from ctypes import wintypes as W
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import d2_jobapi as api

WORK_STOP_SECONDS = 870.0
CLEANUP_SECONDS = 15.0
TOTAL_SECONDS = 900.0
ROOT = Path("D:/Projects/Robots/spatial-sync")


def execute(preparation, root_go, origin_text):
    origin = dt.datetime.fromisoformat(origin_text.replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - origin).total_seconds()
    started = time.monotonic() - age
    if os.name != "nt" or age < 0 or age >= WORK_STOP_SECONDS:
        raise RuntimeError("Fresh Windows launch origin required; do not start preflight")
    k, process = None, api.PROCESS_INFORMATION()
    job, bound, resumed, timer = None, False, False, None
    expired = threading.Event()
    receipt = {"owner": "issue29_round2", "scope": "D2_entire_launch", "launch_origin_utc": origin.isoformat(),
               "work_stop_seconds": WORK_STOP_SECONDS, "cleanup_seconds": CLEANUP_SECONDS,
               "total_deadline_seconds": TOTAL_SECONDS, "status": "initializing", "job_zero": False,
               "preparation_argument": preparation, "root_go_argument": root_go,
               "acceptance_claim": False, "live_ledger_modified": False}
    evidence = ROOT / ".scratch/rsi-platform/issue-29-round2/d2-launches" / (
        origin.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
    )

    def hard_stop():
        expired.set()
        if job:
            k.TerminateJobObject(job, 124)  # This current unnamed job, never PID lookup.
        if process.hProcess and not bound:
            k.TerminateProcess(process.hProcess, 124)  # Fresh suspended child not yet assigned.

    def save(name, value):
        # Best-effort receipts never delay termination: timer and outer handle guard remain armed.
        api.write_receipt(evidence / name, value)

    try:
        # This parent does not perform user-controlled reads/hashes at any point.
        # Even native API startup precedes no user preflight and stays under the PS handle bound.
        timer = threading.Timer(max(0.0, WORK_STOP_SECONDS - (time.monotonic() - started)), hard_stop)
        timer.daemon = True
        timer.start()
        print(json.dumps({"state": "guardian_start", "launch_origin_utc": origin.isoformat(),
            "evidence": str(evidence), "guardian_source": __file__,
            "preparation_argument": preparation, "root_go_argument": root_go}), flush=True)
        k = api.kernel_api()
        job = api.checked(k.CreateJobObjectW(None, None))
        limits = api.EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = api.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        api.checked(k.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
        if expired.is_set() or time.monotonic() - started >= WORK_STOP_SECONDS:
            raise TimeoutError("Startup consumed the global window; no preflight child")
        evidence.mkdir(parents=True, exist_ok=False)
        worker = Path(__file__).resolve().with_name("Invoke-D2-Pair.ps1")
        command = ["powershell.exe", "-NoProfile", "-File", str(worker), "-Preparation", preparation,
                   "-RootGo", root_go, "-LaunchEvidence", str(evidence), "-LaunchOrigin", origin.isoformat()]
        receipt.update({"command": command, "cwd": str(evidence), "status": "suspended_child_pending"})
        save("launch-start.json", receipt)
        import msvcrt

        with (evidence / "coordinator.stdout.log").open("xb", buffering=0) as output, open(os.devnull, "rb", buffering=0) as null:
            for stream in (output, null):
                os.set_inheritable(stream.fileno(), True)
            startup = api.STARTUPINFO()
            startup.cb, startup.dwFlags = C.sizeof(startup), api.STARTF_USESTDHANDLES
            startup.hStdInput = msvcrt.get_osfhandle(null.fileno())
            startup.hStdOutput = startup.hStdError = msvcrt.get_osfhandle(output.fileno())
            environment = dict(os.environ)
            environment.update({"FR3_D2_PAIR_ORIGIN_UTC": origin.isoformat(),
                "FR3_D2_PAIR_WORK_STOP_UTC": (origin + dt.timedelta(seconds=WORK_STOP_SECONDS)).isoformat(),
                "FR3_D2_PAIR_TOTAL_DEADLINE_UTC": (origin + dt.timedelta(seconds=TOTAL_SECONDS)).isoformat(),
                "FR3_D2_GUARDIAN_PID": str(os.getpid())})
            block = C.create_unicode_buffer("\0".join(key + "=" + value for key, value in
                sorted(environment.items(), key=lambda item: item[0].upper())) + "\0\0")
            # Resolve the system executable, not an untrusted working-directory binary.
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            command[0] = str(powershell)
            line = C.create_unicode_buffer(subprocess.list2cmdline(command))
            api.checked(k.CreateProcessW(command[0], line, None, None, True,
                api.CREATE_SUSPENDED | api.CREATE_NO_WINDOW | api.CREATE_UNICODE_ENVIRONMENT,
                C.cast(block, C.c_void_p), str(evidence), C.byref(startup), C.byref(process)))
            receipt.update({"command": command, "coordinator_pid": process.dwProcessId,
                "coordinator_creation_filetime_100ns": api.creation_filetime(k, process.hProcess),
                "assigned_before_resume": False, "status": "fresh_suspended_child"})
            api.checked(k.AssignProcessToJobObject(job, process.hProcess))
            bound = True
            receipt.update({"assigned_before_resume": True, "status": "owned_child_bound"})
            save("launch-bound.json", receipt)
            if expired.is_set() or time.monotonic() - started >= WORK_STOP_SECONDS:
                hard_stop()
            else:
                if k.ResumeThread(process.hThread) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                resumed = True
            while api.active_processes(k, job) and not expired.is_set():
                wait = k.WaitForSingleObject(process.hProcess, 0)
                if wait == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error())
                if wait == 0:
                    # A coordinator must not leave any descendants behind on exit.
                    k.TerminateJobObject(job, 125)
                    receipt["coordinator_left_descendants"] = True
                    break
                time.sleep(0.02)
        cleanup_origin = time.monotonic()
        if expired.is_set() or receipt.get("coordinator_left_descendants"):
            k.TerminateJobObject(job, 124 if expired.is_set() else 125)
        while api.active_processes(k, job) and time.monotonic() - cleanup_origin < CLEANUP_SECONDS:
            if time.monotonic() - started >= TOTAL_SECONDS:
                break
            time.sleep(0.02)
        remaining = api.active_processes(k, job)
        code = W.DWORD()
        api.checked(k.GetExitCodeProcess(process.hProcess, C.byref(code)))
        elapsed = time.monotonic() - started
        receipt.update({"status": "deadline_fatal" if expired.is_set() else "cleanup_unknown" if remaining
            else "coordinator_error" if receipt.get("coordinator_left_descendants") or code.value else "process_exit_only",
            "resumed": resumed, "job_zero": remaining == 0, "remaining_processes": remaining,
            "elapsed_origin_to_job_zero_seconds": elapsed, "proposed_charge_seconds": int(elapsed) + (elapsed % 1 > 0),
            "coordinator_exit_code": code.value, "finished_utc": api.utc(),
            "terminal_readback_scope": "All detailed qualification/terminal reads ran inside the owned coordinator Job"})
        save("launch-result.json", receipt)
        print(json.dumps({"state": receipt["status"], "evidence": str(evidence),
            "job_zero": receipt["job_zero"], "proposed_charge_seconds": receipt["proposed_charge_seconds"]}), flush=True)
        return 124 if expired.is_set() else 126 if remaining else 125 if receipt.get("coordinator_left_descendants") else int(code.value)
    except BaseException as error:
        unbound_exited = True
        cleanup_end = min(started + TOTAL_SECONDS, time.monotonic() + CLEANUP_SECONDS)
        if process.hProcess:
            if bound:
                k.TerminateJobObject(job, 125)
            else:
                k.TerminateProcess(process.hProcess, 125)  # Fresh suspended handle only.
            remaining_time = max(0.0, cleanup_end - time.monotonic())
            unbound_exited = k.WaitForSingleObject(process.hProcess, int(remaining_time * 1000)) == 0
        while job and api.active_processes(k, job) and time.monotonic() < cleanup_end:
            time.sleep(0.02)
        remaining = api.active_processes(k, job) if job else None
        elapsed = time.monotonic() - started
        receipt.update({"status": "guardian_error", "error": repr(error), "resumed": resumed,
            "job_zero": remaining == 0 and unbound_exited if job else False, "remaining_processes": remaining,
            "elapsed_origin_to_job_zero_seconds": elapsed, "proposed_charge_seconds": int(elapsed) + (elapsed % 1 > 0),
            "finished_utc": api.utc()})
        if evidence.exists():
            save("launch-error.partial.json", receipt)
        raise
    finally:
        # Keep the timer armed through receipt persistence; outer PS fallback owns this handle.
        if timer is not None:
            timer.cancel()
            timer.join(timeout=0.1)
        for handle in (process.hThread, process.hProcess, job):
            if handle:
                k.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", required=True)
    parser.add_argument("--root-go", required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    raise SystemExit(execute(args.preparation, args.root_go, args.origin))


if __name__ == "__main__":
    main()
