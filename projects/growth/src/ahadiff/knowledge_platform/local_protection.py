"""CurrentUser DPAPI primitive shared by separate explicit identity policies."""

from __future__ import annotations

import ctypes
import sys


class LocalProtectionError(ValueError):
    """Only controlled diagnostic codes, never native messages or secret values."""


def protect_current_user(data: bytes, *, entropy_bytes: bytes, decrypt: bool = False) -> bytes:
    """Current-user DPAPI, UI forbidden, no machine-wide protection."""
    if sys.platform != "win32":
        raise LocalProtectionError("WINDOWS_REQUIRED")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    # LOAD_LIBRARY_SEARCH_SYSTEM32: do not load a same-named DLL from the package.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True, winmode=0x800)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not operation(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy),
            None,
            None,
            0x1,
            ctypes.byref(output),  # CRYPTPROTECT_UI_FORBIDDEN, not LOCAL_MACHINE.
        ):
            raise LocalProtectionError("LOCAL_PROTECTION_FAILED")
        if not output.data or not 0 < output.size <= 32768:
            raise LocalProtectionError("LOCAL_PROTECTION_INVALID")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, len(data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))
