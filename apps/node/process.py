"""A node owns the launcher handle and its child process group until cleanup."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil


class CleanupIncomplete(RuntimeError):
    pass


def cleanup_confirmed(root, receipt):
    marker = root / receipt["cleanup"]
    if marker.is_file():
        return True
    if os.name == "nt" and receipt.get("jobName"):
        import pywintypes
        import win32job

        try:
            handle = win32job.OpenJobObject(win32job.JOB_OBJECT_QUERY, False, receipt["jobName"])
        except pywintypes.error as error:
            if error.winerror != 2:
                return False
        else:
            try:
                if win32job.QueryInformationJobObject(handle, win32job.JobObjectBasicAccountingInformation)[
                    "ActiveProcesses"
                ]:
                    return False
            finally:
                handle.Close()
        # A named Job disappears only after handles close and its associated processes exit.
        marker.write_text("clean", encoding="ascii")
        return True
    return False


class OwnedProcess:
    def __init__(self, command, *, cwd, env, stdout, stderr):
        self.job = None
        identity = uuid.uuid4().hex
        self.job_name = "Local\\lw-node-" + identity
        self.cleanup = Path(cwd) / f"clean-{identity}"
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("job_launcher.py"))],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            if os.name == "nt":
                import win32job

                self.job = win32job.CreateJobObject(None, self.job_name)
                limits = win32job.QueryInformationJobObject(
                    self.job, win32job.JobObjectExtendedLimitInformation
                )
                limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
                win32job.AssignProcessToJobObject(self.job, int(self.process._handle))
            current = psutil.Process(self.process.pid)
            (Path(cwd) / f"process-{identity}.json").write_text(
                json.dumps(
                    {
                        "pid": self.process.pid,
                        "createdAt": current.create_time(),
                        "command": current.cmdline(),
                        "cleanup": self.cleanup.name,
                        "jobName": self.job_name if os.name == "nt" else None,
                    }
                ),
                encoding="utf-8",
            )
            self.process.stdin.write(
                (
                    json.dumps({"command": command, "cleanup": str(self.cleanup), "parentPid": os.getpid()})
                    + "\n"
                ).encode()
            )
            self.process.stdin.close()
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.job:
            import win32job

            win32job.TerminateJobObject(self.job, 1)
            deadline = time.monotonic() + 5
            while win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicAccountingInformation)[
                "ActiveProcesses"
            ]:
                if time.monotonic() >= deadline:
                    raise CleanupIncomplete("Owned Windows job still has active descendants")
                time.sleep(0.02)
            self.cleanup.write_text("clean", encoding="ascii")
            self.job.Close()
            self.job = None
        elif self.process.poll() is None:
            if os.name == "nt":
                self.process.terminate()
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
            self.process.wait(timeout=5)

    def close(self):
        self.stop()
        if not self.cleanup.is_file():
            raise CleanupIncomplete("Process cleanup was not confirmed; retain the dispatch lease")
