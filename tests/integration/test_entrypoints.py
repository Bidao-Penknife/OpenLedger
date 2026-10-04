"""Source entry points and invalid CLI must work independently of cwd."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openledger import __version__


@pytest.mark.integration
@pytest.mark.parametrize("module_entry", [True, False])
def test_version_does_not_initialize_gui_or_data(tmp_path: Path, module_entry: bool) -> None:
    root = Path(__file__).resolve().parents[2]
    command = (
        [sys.executable, "-m", "openledger"]
        if module_entry
        else [sys.executable, str(root / "main.py")]
    )
    result = subprocess.run(
        [*command, "--version"], cwd=tmp_path, capture_output=True, timeout=20, check=False
    )
    assert result.returncode == 0
    assert f"OpenLedger {__version__}" in result.stdout.decode("utf-8")
    assert not tuple(tmp_path.iterdir())


@pytest.mark.integration
def test_smoke_requires_report_before_starting(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "openledger", "--smoke-test"],
        cwd=tmp_path,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert b"--smoke-report" in result.stderr
    assert not tuple(tmp_path.iterdir())


@pytest.mark.integration
def test_failed_startup_keeps_existing_file_and_reports_failure(tmp_path: Path) -> None:
    target = tmp_path / "existing data location"
    target.write_text("must be preserved", encoding="utf-8")
    report = tmp_path / "failure.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openledger",
            "--smoke-test",
            "--data-dir",
            str(target),
            "--smoke-report",
            str(report),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 1
    assert target.read_text(encoding="utf-8") == "must be preserved"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
