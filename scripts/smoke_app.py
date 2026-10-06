"""Run source or frozen Qt startup from a different cwd and validate its report."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    """Create isolated paths, launch the app, and require complete startup evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/smoke"))
    parser.add_argument("--timeout", type=float, default=30)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Unique root prevents stale reports or data from making a new run appear successful.
    run_directory = Path(tempfile.mkdtemp(prefix="启动验证 space-", dir=output))
    report = run_directory / "startup-report.json"
    screenshot = run_directory / "window.png"
    command = (
        [str(arguments.executable.resolve())]
        if arguments.executable is not None
        else [sys.executable, str(root / "main.py")]
    )
    command.extend(
        [
            "--data-dir",
            str(run_directory / "独立数据目录"),
            "--smoke-test",
            "--smoke-features",
            "--smoke-report",
            str(report),
            "--screenshot",
            str(screenshot),
        ]
    )
    environment = os.environ.copy()
    if sys.platform == "win32":
        environment.pop("QT_QPA_PLATFORM", None)
        environment.pop("QT_PLUGIN_PATH", None)
        environment.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    if arguments.executable is not None:
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        if sys.platform == "win32":
            windows_directory = Path(os.environ["SYSTEMROOT"])
            environment["PATH"] = os.pathsep.join(
                (str(windows_directory / "System32"), str(windows_directory))
            )
    try:
        process = subprocess.run(
            command,
            cwd=run_directory,
            env=environment,
            capture_output=True,
            timeout=arguments.timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if process.returncode != 0:
            raise RuntimeError(f"Startup exit code {process.returncode}: {process.stderr!r}")
        payload = json.loads(report.read_text(encoding="utf-8"))
        required = (
            "window_visible_before_close",
            "native_handle_created",
            "style_loaded",
            "close_accepted",
            "window_hidden_after_close",
        )
        if payload.get("status") != "passed" or not all(payload.get(field) for field in required):
            raise RuntimeError(f"Incomplete smoke evidence: {payload!r}")
        if payload.get("frozen") != (arguments.executable is not None):
            raise RuntimeError("Source/frozen runtime does not match the requested test.")
        features = payload.get("features", {})
        if (
            not all(
                features.get(key)
                for key in (
                    "financial_counts_unchanged",
                    "defusedxml_active",
                    "csv_xlsx_readers_verified",
                )
            )
            or features.get("status") != "passed"
        ):
            raise RuntimeError("File and report engines were not verified.")
        system = payload.get("system_features", {})
        if (
            not system.get("translations_loaded", {}).get("en_US")
            or not all(
                system.get(key)
                for key in (
                    "ai_offline_suggestion",
                    "release_url_validated",
                    "quick_confirm_only",
                    "plugins_disabled_by_default",
                    "native_integrations_disabled",
                )
            )
            or system.get("network_requests") != 0
            or system.get("financial_writes") != 0
            or system.get("real_credentials_accessed") is not False
        ):
            raise RuntimeError("Offline desktop and optional service engines were not verified.")
        if sys.platform == "win32" and payload.get("qt_platform") != "windows":
            raise RuntimeError("The Windows platform plugin was not exercised.")
        if not screenshot.is_file() or screenshot.stat().st_size == 0:
            raise RuntimeError("The window screenshot is missing.")
        import sqlite3
        from contextlib import closing

        database = run_directory / "独立数据目录" / "database" / "openledger.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise RuntimeError("The initialized schema version is incorrect.")
            if connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] != 0:
                raise RuntimeError("Startup must not seed financial transactions.")
            if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] != 0:
                raise RuntimeError("Startup must not invent account balances.")
            if connection.execute("SELECT COUNT(*) FROM books").fetchone()[0] != 1:
                raise RuntimeError("The default book was not initialized.")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("The initialized database failed integrity checking.")
        summary = {"status": "passed", "report": str(report), "screenshot": str(screenshot)}
        (output / "latest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Smoke check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
