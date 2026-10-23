"""Independent Windows JobObject wall watchdog; PREPARATION ONLY until root authorizes execution.

Only freshly created process/job handles are terminated. No historical PID or process-name cleanup.
The supervised child starts suspended, is assigned to our kill-on-close job, then resumes.
"""

import ctypes as C
from ctypes import wintypes as W
import datetime as dt
import json

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
STARTF_USESTDHANDLES = 0x00000100


class STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
                ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.c_void_p), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


class BASIC_LIMIT(C.Structure):
    _fields_ = [('PerProcessUserTimeLimit', C.c_longlong), ('PerJobUserTimeLimit', C.c_longlong),
                ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', C.c_size_t), ('MaximumWorkingSetSize', C.c_size_t),
                ('ActiveProcessLimit', W.DWORD), ('Affinity', C.c_size_t), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class EXTENDED_LIMIT(C.Structure):
    _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', C.c_size_t), ('JobMemoryLimit', C.c_size_t),
                ('PeakProcessMemoryUsed', C.c_size_t), ('PeakJobMemoryUsed', C.c_size_t)]


class ACCOUNTING(C.Structure):
    _fields_ = [('TotalUserTime', C.c_longlong), ('TotalKernelTime', C.c_longlong),
                ('ThisPeriodTotalUserTime', C.c_longlong), ('ThisPeriodTotalKernelTime', C.c_longlong),
                ('TotalPageFaultCount', W.DWORD), ('TotalProcesses', W.DWORD),
                ('ActiveProcesses', W.DWORD), ('TotalTerminatedProcesses', W.DWORD)]


def structure_sizes():
    return {name: C.sizeof(kind) for name, kind in (('STARTUPINFO', STARTUPINFO), ('PROCESS_INFORMATION', PROCESS_INFORMATION),
                                                  ('EXTENDED_LIMIT', EXTENDED_LIMIT), ('ACCOUNTING', ACCOUNTING))}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_receipt(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def kernel_api():
    k = C.WinDLL('kernel32', use_last_error=True)
    definitions = {
        'CreateJobObjectW': ([C.c_void_p, W.LPCWSTR], W.HANDLE),
        'SetInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        'AssignProcessToJobObject': ([W.HANDLE, W.HANDLE], W.BOOL),
        'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
        'QueryInformationJobObject': ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
        'CreateProcessW': ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                            C.c_void_p, W.LPCWSTR, C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION)], W.BOOL),
        'ResumeThread': ([W.HANDLE], W.DWORD), 'TerminateProcess': ([W.HANDLE, W.UINT], W.BOOL),
        'GetExitCodeProcess': ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
        'GetProcessTimes': ([W.HANDLE, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)], W.BOOL),
        'WaitForSingleObject': ([W.HANDLE, W.DWORD], W.DWORD), 'CloseHandle': ([W.HANDLE], W.BOOL),
    }
    for name, (arguments, returns) in definitions.items():
        function = getattr(k, name)
        function.argtypes, function.restype = arguments, returns
    return k


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


def active_processes(k, job):
    info = ACCOUNTING()
    checked(k.QueryInformationJobObject(job, 1, C.byref(info), C.sizeof(info), None))
    return info.ActiveProcesses


def creation_filetime(k, handle):
    values = [W.FILETIME() for _ in range(4)]
    checked(k.GetProcessTimes(handle, *(C.byref(value) for value in values)))
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


