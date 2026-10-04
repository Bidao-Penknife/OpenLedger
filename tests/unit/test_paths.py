"""Data paths must not depend on cwd or silently select an unintended location."""

from pathlib import Path

import pytest

from openledger.infrastructure.platform.paths import DataDirectoryError, resolve_app_paths


def test_default_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "Local AppData"
    other = tmp_path / "different working directory"
    other.mkdir()
    monkeypatch.chdir(other)
    paths = resolve_app_paths(environ={"LOCALAPPDATA": str(local)})
    assert paths.root == local / "OpenLedger"
    paths.ensure_directories()
    assert paths.logs.is_dir()
    assert not tuple(paths.database.iterdir())
    assert not (other / "OpenLedger").exists()


def test_override_supports_unicode_and_spaces(tmp_path: Path) -> None:
    paths = resolve_app_paths(tmp_path / "用户数据 with spaces", environ={})
    paths.ensure_directories()
    paths.ensure_directories()
    assert paths.root.is_dir()
    assert paths.attachments.is_dir()
    assert paths.backups.is_dir()


def test_missing_local_appdata_requires_explicit_override() -> None:
    with pytest.raises(DataDirectoryError, match="LOCALAPPDATA"):
        resolve_app_paths(environ={})


def test_relative_override_is_rejected() -> None:
    with pytest.raises(DataDirectoryError, match="绝对路径"):
        resolve_app_paths(Path("relative"))


def test_conflicting_file_reports_error_without_overwriting(tmp_path: Path) -> None:
    target = tmp_path / "existing file"
    target.write_text("keep this", encoding="utf-8")
    with pytest.raises(DataDirectoryError):
        resolve_app_paths(target).ensure_directories()
    assert target.read_text(encoding="utf-8") == "keep this"
