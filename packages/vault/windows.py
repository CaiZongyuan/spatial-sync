"""Owner-only Windows DACLs, verified through the native security APIs."""

import ctypes
from ctypes import wintypes
from functools import lru_cache


@lru_cache(maxsize=1)
def _api():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    signatures = {
        "OpenProcessToken": (
            [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)],
            wintypes.BOOL,
        ),
        "GetTokenInformation": (
            [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "ConvertSidToStringSidW": ([pointer, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer],
            wintypes.BOOL,
        ),
        "SetFileSecurityW": ([wintypes.LPCWSTR, wintypes.DWORD, pointer], wintypes.BOOL),
        "GetNamedSecurityInfoW": (
            [
                wintypes.LPWSTR,
                ctypes.c_int,
                wintypes.DWORD,
                ctypes.POINTER(pointer),
                pointer,
                ctypes.POINTER(pointer),
                pointer,
                ctypes.POINTER(pointer),
            ],
            wintypes.DWORD,
        ),
        "GetSecurityDescriptorControl": (
            [pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "GetAclInformation": ([pointer, pointer, wintypes.DWORD, ctypes.c_int], wintypes.BOOL),
        "GetAce": ([pointer, wintypes.DWORD, ctypes.POINTER(pointer)], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    return api, kernel


def _sid_text(sid):
    api, kernel = _api()
    text = wintypes.LPWSTR()
    if not api.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise OSError("Cannot inspect Vault owner")
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


@lru_cache(maxsize=1)
def _current_sid():
    api, kernel = _api()
    token = wintypes.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("Cannot inspect Vault owner")
    try:
        size = wintypes.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buffer, size.value, ctypes.byref(size)):
            raise OSError("Cannot inspect Vault owner")
        return _sid_text(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
    finally:
        kernel.CloseHandle(token)


def protect(path):
    api, kernel = _api()
    descriptor = ctypes.c_void_p()
    # A protected DACL has one full-control ACE for the current user; no inherited grants.
    sddl = f"O:{_current_sid()}D:P(A;;FA;;;{_current_sid()})"
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError("Cannot protect Vault")
    try:
        if not api.SetFileSecurityW(str(path), 0x80000005, descriptor):
            raise OSError("Cannot protect Vault")
    finally:
        kernel.LocalFree(descriptor)
    verify(path)


def verify(path):
    api, kernel = _api()
    owner, dacl, descriptor = (ctypes.c_void_p() for _ in range(3))
    error = api.GetNamedSecurityInfoW(
        str(path),
        1,
        5,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if error:
        raise OSError("Cannot inspect Vault permissions")
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not api.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise OSError("Cannot inspect Vault permissions")
        information = (wintypes.DWORD * 3)()
        if not dacl or not api.GetAclInformation(dacl, information, ctypes.sizeof(information), 2):
            raise OSError("Vault requires an owner-only DACL")
        ace = ctypes.c_void_p()
        if information[0] != 1 or not api.GetAce(dacl, 0, ctypes.byref(ace)):
            raise OSError("Vault requires an owner-only DACL")
        header = ctypes.string_at(ace, 8)
        mask = int.from_bytes(header[4:8], "little")
        if (
            not control.value & 0x1000
            or header[:2] != b"\x00\x00"
            or mask != 0x1F01FF
            or _sid_text(owner) != _current_sid()
            or _sid_text(ace.value + 8) != _current_sid()
        ):
            raise OSError("Vault requires an owner-only DACL")
    finally:
        kernel.LocalFree(descriptor)
