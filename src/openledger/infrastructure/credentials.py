"""Windows Credential Manager adapter; no credential enumeration or plain-text fallback."""

import ctypes
import hashlib
import os
from ctypes import wintypes
from pathlib import Path

from openledger.application.dto.ai import AIConfig
from openledger.domain.errors import LedgerError

_PREFIX = "OpenLedger/AI/"
_GENERIC = 1
_LOCAL_MACHINE = 2  # Persists for this user on this computer, without roaming.
_NOT_FOUND = 1168
_MAX_SECRET_BYTES = 2560


def credential_target(data_dir: Path, config: AIConfig) -> str:
    """Scope a credential to an absolute data directory and explicit API endpoint."""
    identity = str(data_dir.resolve()).casefold() + "\n" + config.base_url.rstrip("/")
    return _PREFIX + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _target(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith(_PREFIX)
        or len(value) != len(_PREFIX) + 64
    ):
        raise LedgerError("CREDENTIAL_UNAVAILABLE")
    if any(character not in "0123456789abcdef" for character in value[len(_PREFIX) :]):
        raise LedgerError("CREDENTIAL_UNAVAILABLE")


def _secret(value: str) -> bytes:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LedgerError("CREDENTIAL_INVALID_KEY")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise LedgerError("CREDENTIAL_INVALID_KEY")
    encoded = value.encode("utf-16-le")
    if len(encoded) > _MAX_SECRET_BYTES:
        raise LedgerError("CREDENTIAL_INVALID_KEY")
    return encoded


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    """Lazily call Win32 only after an explicit credential action by the user."""

    @staticmethod
    def _library() -> ctypes.WinDLL:
        if os.name != "nt":
            raise LedgerError("CREDENTIAL_UNAVAILABLE")
        try:
            library = ctypes.WinDLL("advapi32", use_last_error=True)
            library.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_Credential)),
            ]
            library.CredReadW.restype = wintypes.BOOL
            library.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
            library.CredWriteW.restype = wintypes.BOOL
            library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            library.CredDeleteW.restype = wintypes.BOOL
            library.CredFree.argtypes = [ctypes.c_void_p]
            library.CredFree.restype = None
            return library
        except OSError as error:
            raise LedgerError("CREDENTIAL_UNAVAILABLE") from error

    def get(self, target: str) -> str | None:
        """Read the named generic credential and release the system-owned buffer."""
        _target(target)
        library = self._library()
        pointer = ctypes.POINTER(_Credential)()
        if not library.CredReadW(target, _GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _NOT_FOUND:
                return None
            raise LedgerError("CREDENTIAL_UNAVAILABLE")
        try:
            value = pointer.contents
            if value.CredentialBlobSize > _MAX_SECRET_BYTES or value.CredentialBlobSize % 2:
                raise LedgerError("CREDENTIAL_UNAVAILABLE")
            key = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode(
                "utf-16-le"
            )
            _secret(key)
            return key
        except UnicodeError as error:
            raise LedgerError("CREDENTIAL_UNAVAILABLE") from error
        finally:
            library.CredFree(pointer)

    def set(self, target: str, key: str) -> None:
        """Write a per-user local credential without ever opening a settings file."""
        _target(target)
        encoded = _secret(key)
        library = self._library()
        buffer = (wintypes.BYTE * len(encoded)).from_buffer_copy(encoded)
        credential = _Credential()
        credential.Type = _GENERIC
        credential.TargetName = target
        credential.Comment = "OpenLedger optional AI parser"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = _LOCAL_MACHINE
        credential.UserName = "OpenLedger"
        try:
            if not library.CredWriteW(ctypes.byref(credential), 0):
                raise LedgerError("CREDENTIAL_UNAVAILABLE")
        finally:
            ctypes.memset(buffer, 0, len(buffer))

    def delete(self, target: str) -> None:
        """Delete only the named OpenLedger credential, never enumerate user secrets."""
        _target(target)
        library = self._library()
        if not library.CredDeleteW(target, _GENERIC, 0) and ctypes.get_last_error() != _NOT_FOUND:
            raise LedgerError("CREDENTIAL_UNAVAILABLE")


class MemoryCredentialStore:
    """An injectable, non-persistent credential store for isolated automated tests."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, target: str) -> str | None:
        """Read a synthetic credential."""
        _target(target)
        return self._values.get(target)

    def set(self, target: str, key: str) -> None:
        """Store a synthetic credential only in memory."""
        _target(target)
        _secret(key)
        self._values[target] = key

    def delete(self, target: str) -> None:
        """Discard a synthetic credential."""
        _target(target)
        self._values.pop(target, None)
