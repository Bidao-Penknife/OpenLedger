"""Atomic presentation preferences, bounded reads and failure preservation."""

import json
import os
from dataclasses import asdict
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from openledger.infrastructure.settings import DesktopSettings, SettingsStore


def test_missing_settings_do_not_create_files_or_directories(tmp_path: Path) -> None:
    folder = tmp_path / "尚未创建的数据目录"
    store = SettingsStore(folder / "settings.json")
    assert store.load() == DesktopSettings()
    assert not folder.exists()


def test_existing_preferences_gain_desktop_defaults_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = b'{"theme":"dark","time_zone":"UTC"}'
    path.write_bytes(original)
    assert SettingsStore(path).load() == DesktopSettings(theme="dark", time_zone="UTC")
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tray_enabled", 1),
        ("close_to_tray", "yes"),
        ("hotkey_enabled", None),
        ("language", "unknown"),
        ("shortcut", "L"),
        ("shortcut", "Shift+L"),
        ("shortcut", "Ctrl+Ctrl+L"),
        ("shortcut", []),
    ],
)
def test_invalid_native_preferences_fall_back_without_side_effects(
    tmp_path: Path, field: str, value: object
) -> None:
    path = tmp_path / "settings.json"
    values = asdict(DesktopSettings())
    values[field] = value
    path.write_text(json.dumps(values), encoding="utf-8")
    before = path.read_bytes()
    assert SettingsStore(path).load() == DesktopSettings()
    assert path.read_bytes() == before


def test_settings_round_trip_atomic_replace_and_financial_files_untouched(tmp_path: Path) -> None:
    database = tmp_path / "database" / "ledger.sqlite3"
    database.parent.mkdir()
    database.write_bytes(b"synthetic financial file; never parsed by preferences")
    attachment = tmp_path / "attachments" / "sample.png"
    attachment.parent.mkdir()
    attachment.write_bytes(b"synthetic attachment")
    before = {database: database.read_bytes(), attachment: attachment.read_bytes()}
    path = tmp_path / "偏好 设置" / "settings.json"
    store = SettingsStore(path)
    first = DesktopSettings(theme="dark", time_zone="Asia/Shanghai")
    store.save(first)
    assert SettingsStore(path).load() == first
    assert json.loads(path.read_text(encoding="utf-8")) == asdict(first)
    second = DesktopSettings(theme="light", time_zone="Europe/Paris")
    store.save(second)
    assert store.load() == second
    assert list(path.parent.iterdir()) == [path]
    assert {file: file.read_bytes() for file in before} == before


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON",
        b"\xff\xfe",
        b"[]",
        b"null",
        b'{"theme":"dark"}',
        b'{"theme":"unknown","time_zone":"UTC"}',
        b'{"theme":[],"time_zone":"UTC"}',
        b'{"theme":"dark","time_zone":42}',
        b'{"theme":"dark","time_zone":"Missing/Zone"}',
        b'{"theme":"dark","time_zone":"../outside"}',
    ],
)
def test_malformed_settings_fall_back_without_rewriting_file(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(raw)
    assert SettingsStore(path).load() == DesktopSettings()
    assert path.read_bytes() == raw


def test_oversized_settings_are_rejected_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b" " * 4097)

    def forbidden_read(path: Path, *args: object, **kwargs: object) -> str:
        raise AssertionError("An oversized preferences file must not be read")

    monkeypatch.setattr(Path, "read_text", forbidden_read)
    assert SettingsStore(path).load() == DesktopSettings()


@pytest.mark.parametrize(
    "settings",
    [
        DesktopSettings(theme="invalid", time_zone="UTC"),
        DesktopSettings(theme="dark", time_zone="Missing/Zone"),
        DesktopSettings(theme="dark", time_zone="../outside"),
    ],
)
def test_invalid_save_keeps_previous_settings_and_creates_no_temporary_file(
    tmp_path: Path, settings: DesktopSettings
) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(DesktopSettings())
    before = path.read_bytes()
    with pytest.raises((ValueError, ZoneInfoNotFoundError)):
        store.save(settings)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_failed_publish_preserves_previous_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(DesktopSettings())
    before = path.read_bytes()

    def denied_replace(source: Path, target: str | Path) -> Path:
        assert source.parent == path.parent and Path(target) == path
        assert source.read_bytes() != before
        assert path.read_bytes() == before
        raise PermissionError("Injected publish failure")

    monkeypatch.setattr(Path, "replace", denied_replace)
    with pytest.raises(PermissionError, match="Injected publish failure"):
        store.save(DesktopSettings(theme="dark", time_zone="UTC"))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_failed_flush_preserves_previous_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(DesktopSettings())
    before = path.read_bytes()

    def denied_flush(descriptor: int) -> None:
        raise OSError("Injected flush failure")

    monkeypatch.setattr(os, "fsync", denied_flush)
    with pytest.raises(OSError, match="Injected flush failure"):
        store.save(DesktopSettings(theme="dark", time_zone="UTC"))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
