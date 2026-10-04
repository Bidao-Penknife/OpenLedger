"""Credential isolation and Win32 buffer ownership with entirely synthetic API doubles."""

import ctypes
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from openledger.application.dto.ai import AIConfig
from openledger.domain.errors import LedgerError
from openledger.infrastructure.credentials import (
    MemoryCredentialStore,
    WindowsCredentialStore,
    _Credential,
    credential_target,
)


def test_target_is_directory_and_endpoint_scoped_without_exposing_private_paths(
    tmp_path: Path,
) -> None:
    first = credential_target(tmp_path, AIConfig())
    assert first.startswith("OpenLedger/AI/") and len(first) == len("OpenLedger/AI/") + 64
    assert str(tmp_path) not in first and "api.openai.com" not in first
    assert first == credential_target(tmp_path, AIConfig(base_url="https://api.openai.com/v1/"))
    assert first != credential_target(tmp_path / "other", AIConfig())
    assert first != credential_target(tmp_path, AIConfig(base_url="https://example.com/v1"))


def test_memory_credentials_have_no_plain_text_file_fallback(tmp_path: Path) -> None:
    store = MemoryCredentialStore()
    target = credential_target(tmp_path, AIConfig())
    assert store.get(target) is None
    store.set(target, "synthetic-test-key")
    assert store.get(target) == "synthetic-test-key"
    assert list(tmp_path.iterdir()) == []
    store.delete(target)
    store.delete(target)
    assert store.get(target) is None


@pytest.mark.parametrize("key", ["", " key", "key ", "line\nbreak", "中", "x" * 1281])
def test_invalid_secret_rejected_without_storage(tmp_path: Path, key: str) -> None:
    store = MemoryCredentialStore()
    target = credential_target(tmp_path, AIConfig())
    with pytest.raises(LedgerError, match="CREDENTIAL_INVALID_KEY"):
        store.set(target, key)
    assert store.get(target) is None


@pytest.mark.parametrize("target", ["", "OtherApp/key", "OpenLedger/AI/" + "X" * 64])
def test_adapter_cannot_read_or_delete_arbitrary_credentials(target: str) -> None:
    store = WindowsCredentialStore()
    with pytest.raises(LedgerError, match="CREDENTIAL_UNAVAILABLE"):
        store.get(target)
    with pytest.raises(LedgerError, match="CREDENTIAL_UNAVAILABLE"):
        store.delete(target)


class FakeWin32:
    """Imitate ownership of a Win32 credential; no real operating-system secrets touched."""

    def __init__(self, key: str = "synthetic-key") -> None:
        self.buffer = ctypes.create_string_buffer(key.encode("utf-16-le"))
        self.credential = _Credential()
        self.credential.CredentialBlobSize = len(key.encode("utf-16-le"))
        self.credential.CredentialBlob = ctypes.cast(self.buffer, ctypes.POINTER(ctypes.c_ubyte))
        self.freed = 0
        self.written: list[tuple[str, bytes, int]] = []
        self.fail_read = False
        self.fail_write = False
        self.fail_delete = False

    def CredReadW(self, target: str, kind: int, flags: int, output: object) -> bool:
        if self.fail_read:
            return False
        pointer = ctypes.cast(cast(Any, output), ctypes.POINTER(ctypes.POINTER(_Credential)))
        pointer[0] = ctypes.pointer(self.credential)
        return True

    def CredFree(self, pointer: object) -> None:
        self.freed += 1

    def CredWriteW(self, pointer: object, flags: int) -> bool:
        value = ctypes.cast(cast(Any, pointer), ctypes.POINTER(_Credential)).contents
        self.written.append(
            (
                value.TargetName,
                ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize),
                value.Persist,
            )
        )
        return not self.fail_write

    def CredDeleteW(self, target: str, kind: int, flags: int) -> bool:
        return not self.fail_delete


def test_win32_read_releases_system_buffer_even_for_malformed_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeWin32()
    monkeypatch.setattr(WindowsCredentialStore, "_library", staticmethod(lambda: api))
    store = WindowsCredentialStore()
    target = credential_target(tmp_path, AIConfig())
    assert store.get(target) == "synthetic-key" and api.freed == 1
    api.credential.CredentialBlobSize = 1
    with pytest.raises(LedgerError, match="CREDENTIAL_UNAVAILABLE"):
        store.get(target)
    assert api.freed == 2


def test_win32_write_uses_user_local_persistence_and_zeroes_owned_buffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeWin32()
    monkeypatch.setattr(WindowsCredentialStore, "_library", staticmethod(lambda: api))
    target = credential_target(tmp_path, AIConfig())
    cleared: list[bytes] = []
    original_memset = ctypes.memset

    def observe_clear(buffer: object, value: int, count: int) -> object:
        result = original_memset(cast(Any, buffer), value, count)
        cleared.append(ctypes.string_at(cast(Any, buffer), count))
        return result

    monkeypatch.setattr(ctypes, "memset", observe_clear)
    WindowsCredentialStore().set(target, "synthetic-key")
    assert api.written == [(target, "synthetic-key".encode("utf-16-le"), 2)]
    assert cleared == [b"\0" * len("synthetic-key".encode("utf-16-le"))]


def test_win32_failures_are_codes_without_secret_or_system_error_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeWin32()
    api.fail_read = api.fail_write = api.fail_delete = True
    monkeypatch.setattr(WindowsCredentialStore, "_library", staticmethod(lambda: api))
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    target = credential_target(tmp_path, AIConfig())
    store = WindowsCredentialStore()
    operations: list[Callable[[], object]] = [
        lambda: store.get(target),
        lambda: store.set(target, "synthetic-key"),
        lambda: store.delete(target),
    ]
    for operation in operations:
        with pytest.raises(LedgerError) as captured:
            operation()
        assert str(captured.value) == "CREDENTIAL_UNAVAILABLE"
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1168)
    assert store.get(target) is None
    store.delete(target)
