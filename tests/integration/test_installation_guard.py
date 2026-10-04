"""Check the real shared Windows object and process handle lifetime."""

import ctypes
import sys
from ctypes import wintypes

import pytest

from openledger.infrastructure.platform.installation import (
    INSTALLATION_IN_PROGRESS,
    INSTALLATION_MUTEX,
    InstallationGuard,
)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer gate")
def test_multiple_handles_preserve_guard_until_last_process_closes() -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    def present() -> bool:
        handle = kernel.OpenMutexW(0x100000, False, INSTALLATION_MUTEX)
        if handle:
            kernel.CloseHandle(handle)
        return bool(handle)

    first, second = InstallationGuard(), InstallationGuard()
    assert not present()
    try:
        first.acquire()
        first.acquire()
        second.acquire()
        assert first.active and second.active and present()
        first.close()
        first.close()
        assert not first.active and present()
    finally:
        first.close()
        second.close()
    assert not second.active and not present()


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer gate")
def test_new_application_is_rejected_while_installer_is_active() -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, INSTALLATION_IN_PROGRESS)
    assert handle
    guard = InstallationGuard()
    try:
        with pytest.raises(OSError, match="INSTALLATION_IN_PROGRESS"):
            guard.acquire()
        assert not guard.active
    finally:
        kernel.CloseHandle(handle)
        guard.close()
    guard.acquire()
    try:
        assert guard.active
    finally:
        guard.close()
