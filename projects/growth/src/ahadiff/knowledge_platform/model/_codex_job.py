"""Standard-library Windows kill-on-close job for an owned, stdin-gated child."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class CodexOwnedJob:
    """Close only this invocation's process tree, including exited-parent descendants."""

    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            primary = ctypes.WinError(ctypes.get_last_error())
            try:
                self.close()
            except OSError as cleanup_error:
                # Keep the SetInformation failure primary. The runner records
                # this separate exception through a fixed, non-text vocabulary.
                primary.__dict__["codex_cleanup_error"] = cleanup_error
            raise primary

    def assign(self, pid: int) -> None:
        """Assign before releasing the helper's stdin gate, preventing the spawn race."""
        process = self.kernel.OpenProcess(0x0101, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        primary: OSError | None = None
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError as exc:
            primary = exc
            raise
        finally:
            if not self.kernel.CloseHandle(process):
                error = ctypes.get_last_error()
                cleanup_error = ctypes.WinError(error)
                if primary is not None:
                    primary.__dict__["codex_cleanup_error"] = cleanup_error
                else:
                    # Assign succeeded but the temporary process handle did not
                    # close. This is still a failed setup, never clean success.
                    primary = ctypes.WinError(error)
                    primary.__dict__["codex_cleanup_error"] = cleanup_error
                    raise primary

    def terminate(self) -> None:
        """Best-effort fallback for this owned Job only if handle closure fails."""
        if self.handle:
            terminate = self.kernel.TerminateJobObject
            terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate.restype = wintypes.BOOL
            if not terminate(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        """Release job ownership and terminate all remaining job members."""
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None
