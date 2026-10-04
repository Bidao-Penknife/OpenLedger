"""Release identities and archives must fail before publishing incorrect assets."""

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.release_tools import (
    APP_ID,
    bundle_manifest,
    checked_version,
    collect_assets,
    prepare,
    safe_source_name,
    sha256,
    source_archive,
    windows_version,
)


@pytest.mark.parametrize("value", ["1.0.0", "1.0.0rc1", "0.3.0.dev0", "1.0.0a2", "1.0.0b2"])
def test_exact_tag_and_bounded_pe_fields(value: str) -> None:
    assert str(checked_version(value, "v" + value)) == value
    assert all(0 <= field <= 65535 for field in windows_version(value))


@pytest.mark.parametrize(
    "value",
    [
        "1.0",
        "v1.0.0",
        "01.0.0",
        "1.0.0+local",
        "1.0.0.post1",
        "65536.0.0",
        "1.0.0rc10000",
        "1.0.0.dev10000",
        "1.0.0\n",
        "1.0.0';evil",
    ],
)
def test_bad_versions_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        checked_version(value)


def test_windows_fields_order_prereleases_before_final() -> None:
    values = ["1.0.0.dev2", "1.0.0a1", "1.0.0b1", "1.0.0rc1", "1.0.0"]
    assert [windows_version(value) for value in values] == sorted(
        windows_version(value) for value in values
    )
    with pytest.raises(ValueError, match="Tag"):
        checked_version("1.0.0rc1", "v1.0.0")


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "/absolute",
        "C:/secret",
        "docs\\file",
        "a\nfile",
        ".git/config",
        "build/report.txt",
        ".venv/pyvenv.cfg",
        "data/sample.csv",
        "private.sqlite3",
        "backup.olbackup",
        "server.pem",
        "secret.key",
        ".env",
        ".env.local",
        "ai-settings.json",
        "nested/settings.json",
    ],
)
def test_source_paths_exclude_private_data_and_escapes(name: str) -> None:
    with pytest.raises(ValueError):
        safe_source_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "src/openledger/infrastructure/credentials.py",
        ".env.example",
        "tests/fixtures/合成示例.csv",
    ],
)
def test_source_paths_preserve_developer_code(name: str) -> None:
    assert safe_source_name(name) == name


def test_source_archive_contains_unstaged_additions(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n*.sqlite3\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "new.py").write_bytes(b"# new module\n")
    (tmp_path / "private.sqlite3").write_bytes(b"private synthetic data")
    target = tmp_path / "build/source.zip"
    assert source_archive(tmp_path, target) == 3
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {
            "OpenLedger/.gitignore",
            "OpenLedger/main.py",
            "OpenLedger/new.py",
        }
        assert archive.read("OpenLedger/new.py") == b"# new module\n"


def test_source_archive_rejects_unignored_private_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "accounts.db").write_bytes(b"synthetic private")
    with pytest.raises(ValueError):
        source_archive(tmp_path, tmp_path / "build/source.zip")
    assert not (tmp_path / "build/source.zip").exists()


def test_metadata_and_manifest_match_actual_bytes(tmp_path: Path) -> None:
    identity = prepare(tmp_path, "1.0.0rc1")
    assert identity["windows_version"] == "1.0.0.30001"
    assert "'1.0.0rc1'" in (tmp_path / "build/release/windows-version.txt").read_text("utf-8")
    bundle = tmp_path / "dist/windows/OpenLedger"
    bundle.mkdir(parents=True)
    executable = bundle / "OpenLedger.exe"
    executable.write_bytes(b"synthetic executable")
    manifest = bundle_manifest(tmp_path, "1.0.0rc1")
    assert manifest["app_id"] == APP_ID
    assert next(item for item in manifest["files"] if item["path"] == "OpenLedger.exe")[
        "sha256"
    ] == sha256(executable)
    assert not any(item["path"] == "bundle-manifest.json" for item in manifest["files"])
    (bundle / "private.olbackup").write_bytes(b"private")
    with pytest.raises(ValueError):
        bundle_manifest(tmp_path, "1.0.0rc1")


def test_missing_binary_prevents_release_collection(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Required asset"):
        collect_assets(tmp_path, tmp_path / "dist/release", "1.0.0rc1", "v1.0.0rc1")
    assert not (tmp_path / "dist/release/release-manifest.json").exists()


def test_asset_checksums_cover_exactly_three_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")
    for directory, name in (
        ("windows", "OpenLedger-1.0.0rc1-windows-x64.zip"),
        ("installer", "OpenLedger-1.0.0rc1-windows-x64-setup.exe"),
    ):
        path = tmp_path / "dist" / directory / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"synthetic asset")
    result = collect_assets(tmp_path, tmp_path / "dist/release", "1.0.0rc1", "v1.0.0rc1")
    assert result["prerelease"] and len(result["assets"]) == 3
    recorded = json.loads((tmp_path / "dist/release/release-manifest.json").read_text("utf-8"))
    assert recorded == result
    for item in result["assets"]:
        assert item["sha256"] == sha256(tmp_path / "dist/release" / item["file"])
