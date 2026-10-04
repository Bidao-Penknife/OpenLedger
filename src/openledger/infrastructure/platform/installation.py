"""Keep installers from replacing files while an application process is alive."""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

INSTALLATION_MUTEX = r"Local\OpenLedger.InstallationGate"
INSTALLATION_IN_PROGRESS = r"Local\OpenLedger.InstallationInProgress"


class InstallationGuard:
    """Hold a shared named object; this does not restrict multiple data directories."""

    def __init__(self) -> None:
        self._handle: int | None = None
        self._kernel: Any = None

    @property
    def active(self) -> bool:
        """Report whether the native installer gate is held by this process."""
        return self._handle is not None

    def acquire(self) -> None:
        """Create or open the Windows object without acquiring exclusive ownership."""
        if sys.platform != "win32" or self._handle is not None:
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenMutexW.restype = wintypes.HANDLE
        handle = kernel.CreateMutexW(None, False, INSTALLATION_MUTEX)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel = kernel
        self._handle = int(handle)
        progress = kernel.OpenMutexW(0x100000, False, INSTALLATION_IN_PROGRESS)
        error = ctypes.get_last_error()
        if progress:
            kernel.CloseHandle(progress)
            self.close()
            raise OSError("INSTALLATION_IN_PROGRESS")
        if error != 2:  # ERROR_FILE_NOT_FOUND means no installer object exists.
            self.close()
            raise ctypes.WinError(error)

    def close(self) -> None:
        """Release this process's handle, preserving handles held by other processes."""
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
