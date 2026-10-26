"""Own one spawned worker tree, including runtime descendants after a crash."""

import os
import signal


class WorkerTree:
    def __init__(self, process):
        self.process = process
        self.job = None
        self.kernel = None
        if os.name == "nt":
            self._windows_job()

    def _windows_job(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("ProcessTime", ctypes.c_int64),
                ("JobTime", ctypes.c_int64),
                ("Flags", wintypes.DWORD),
                ("MinWorking", ctypes.c_size_t),
                ("MaxWorking", ctypes.c_size_t),
                ("ActiveProcesses", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("Priority", wintypes.DWORD),
                ("Scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("Basic", BasicLimits),
                ("Io", IoCounters),
                ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t),
                ("PeakProcessMemory", ctypes.c_size_t),
                ("PeakJobMemory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            # asyncio retains the creating Popen handle in its transport. No
            # process enumeration or PID-based reopening is needed.
            popen = self.process._transport.get_extra_info("subprocess")
            if not kernel.AssignProcessToJobObject(job, int(popen._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel.CloseHandle(job)
            raise
        self.job, self.kernel = job, kernel

    def close(self):
        if self.job is not None:
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != "nt":
            # The creating subprocess starts a new session whose group is the
            # owned tree. Closing it catches descendants after leader failure.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
