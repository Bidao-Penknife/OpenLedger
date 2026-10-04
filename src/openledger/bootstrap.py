"""Assemble runtime services and run the native Qt application lifecycle."""

import argparse
import json
import logging
import platform
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openledger import __version__
from openledger.application.dto.runtime import RuntimeInfo
from openledger.domain.errors import LedgerError
from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.database.database import CURRENT_SCHEMA_VERSION, Database
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.logging_setup import configure_logging
from openledger.infrastructure.platform.installation import InstallationGuard
from openledger.infrastructure.platform.paths import resolve_app_paths
from openledger.infrastructure.runtime import sqlite_wal_supported, verify_sqlite_capabilities
from openledger.infrastructure.settings import SettingsStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenLedger 本地个人财务桌面应用")
    parser.add_argument("--version", action="version", version=f"OpenLedger {__version__}")
    parser.add_argument("--data-dir", type=Path, help="数据目录的绝对路径")
    parser.add_argument("--quick", action="store_true", help="打开或激活快速记账窗口")
    parser.add_argument("--smoke-test", action="store_true", help="隐藏窗口验证后自动关闭")
    parser.add_argument("--smoke-report", type=Path, help="启动验证 JSON 输出路径")
    parser.add_argument("--smoke-features", action="store_true", help="只读验证文件与报告引擎")
    parser.add_argument("--screenshot", type=Path, help="启动验证窗口截图路径")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument("--backup", type=Path, help="将现有账本备份到新的绝对文件路径")
    maintenance.add_argument("--restore", type=Path, help="校验备份并恢复到新数据目录")
    parser.add_argument("--restore-to", type=Path, help="恢复到新目录或空目录的绝对路径")
    return parser


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    """Publish a complete UTF-8 report using a same-directory temporary file."""
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    """Start Qt normally, or record an isolated automatic native startup check."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.smoke_test and arguments.smoke_report is None:
        parser.error("--smoke-test 需要 --smoke-report")
    if not arguments.smoke_test and (
        arguments.smoke_report or arguments.screenshot or arguments.smoke_features
    ):
        parser.error("--smoke-report、--screenshot 和 --smoke-features 仅用于 --smoke-test")
    if bool(arguments.restore) != bool(arguments.restore_to):
        parser.error("--restore 和 --restore-to 必须一起指定")
    if arguments.smoke_test and (arguments.backup or arguments.restore):
        parser.error("启动验证不能同时执行备份或恢复")
    if arguments.quick and (arguments.smoke_test or arguments.backup or arguments.restore):
        parser.error("--quick 仅用于正常桌面启动")

    guard = InstallationGuard()
    try:
        guard.acquire()
        return _run(arguments, installation_guard_held=guard.active)
    except OSError:
        if sys.stderr is not None:
            print("OpenLedger: INSTALLATION_GUARD_UNAVAILABLE", file=sys.stderr)
        return 1
    finally:
        guard.close()


def _run(arguments: argparse.Namespace, *, installation_guard_held: bool) -> int:
    """Run a validated command while the installer guard remains held."""

    # Maintenance tasks do not initialize Qt or mutate the source during restoration.
    if arguments.backup or arguments.restore:
        try:
            paths = resolve_app_paths(arguments.data_dir)
            database = Database(paths.database / "openledger.sqlite3")
            if arguments.backup:
                if not database.path.is_file():
                    raise LedgerError("ENTITY_NOT_FOUND", "请先启动应用创建账本。")
                database.initialize()
                archive = BackupService(database).backup(
                    arguments.backup, attachments_directory=paths.attachments
                )
                result = {"status": "passed", "backup": str(archive)}
            else:
                restored = BackupService(database).restore(arguments.restore, arguments.restore_to)
                result = {"status": "passed", "restored_database": str(restored.path)}
            if sys.stdout is not None:
                print(json.dumps(result, ensure_ascii=True))
            return 0
        except (LedgerError, OSError, ValueError) as error:
            if sys.stderr is not None:
                print(f"OpenLedger: {getattr(error, 'code', 'STORAGE_IO_ERROR')}", file=sys.stderr)
            return 1

    logger = logging.getLogger("openledger")
    started = time.monotonic()
    payload: dict[str, Any] = {
        "status": "failed",
        "frozen": bool(getattr(sys, "frozen", False)),
        "installation_guard_held": installation_guard_held,
    }
    instance_coordinator: Any | None = None
    try:
        # Import Qt after argparse so version/help works without GUI initialization.
        import PySide6
        from PySide6.QtCore import QCoreApplication, Qt, QTimer, qVersion
        from PySide6.QtWidgets import QApplication, QMessageBox

        from openledger.infrastructure.platform.single_instance import InstanceCoordinator
        from openledger.presentation.languages import install_language
        from openledger.presentation.views.main_window import MainWindow

        verify_sqlite_capabilities()
        paths = resolve_app_paths(arguments.data_dir)
        QCoreApplication.setOrganizationName("OpenLedger")
        QCoreApplication.setApplicationName("OpenLedger")
        QCoreApplication.setApplicationVersion(__version__)
        app = QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(True)
        paths.root.mkdir(parents=True, exist_ok=True)
        instance_coordinator = InstanceCoordinator(paths.root, app)
        if not instance_coordinator.acquire():
            if arguments.smoke_test:
                raise LedgerError("INSTANCE_ALREADY_RUNNING")
            return 0 if instance_coordinator.activate("quick" if arguments.quick else "show") else 1
        settings_store = SettingsStore(paths.root / "settings.json")
        _translator = install_language(app, settings_store.load().language)
        paths.ensure_directories()
        logger = configure_logging(paths)
        database = Database(paths.database / "openledger.sqlite3")
        database.initialize()
        with database.read() as connection:
            validate_financial_integrity(connection)
        ledger = LedgerService(database)
        ledger.ensure_defaults()
        with database.read() as connection:
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        runtime = RuntimeInfo(
            app_version=__version__,
            python_version=platform.python_version(),
            pyside_version=PySide6.__version__,
            qt_version=qVersion(),
            sqlite_version=sqlite3.sqlite_version,
            data_directory=str(paths.root),
            sqlite_wal_supported=sqlite_wal_supported(sqlite3.sqlite_version),
            database_schema_version=CURRENT_SCHEMA_VERSION,
            database_journal_mode=journal_mode,
        )
        logger.info(
            "Starting OpenLedger %s; Qt=%s; SQLite=%s",
            __version__,
            qVersion(),
            sqlite3.sqlite_version,
        )
        window = MainWindow(runtime, ledger, settings_store)

        def activate_window(command: str) -> None:
            if command == "quick":
                window.quick.open()
            elif window.desktop:
                window.desktop.show_main()
            else:
                window.showNormal()
                window.raise_()
                window.activateWindow()

        instance_coordinator.activationRequested.connect(activate_window)
        if arguments.smoke_test:
            # Exercise the native platform plugin without opening a visible helper window.
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.show()
        if not arguments.smoke_test:
            window.enable_desktop_features()
            if arguments.quick:
                window.quick.open()
        payload.update(runtime=asdict(runtime), qt_platform=app.platformName())

        def finish_smoke() -> None:
            try:
                payload["window_visible_before_close"] = window.isVisible()
                payload["native_handle_created"] = bool(window.winId())
                payload["style_loaded"] = bool(window.styleSheet())
                if arguments.smoke_features:
                    from openledger.presentation.feature_smoke import verify_features

                    payload["features"] = verify_features(
                        ledger, arguments.smoke_report.resolve().parent / "feature-check"
                    )
                    from openledger.presentation.system_smoke import verify_system_features

                    payload["system_features"] = verify_system_features(window)
                if arguments.screenshot is not None:
                    screenshot = arguments.screenshot.resolve()
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    if not window.grab().save(str(screenshot), "PNG"):
                        raise OSError("无法保存验证截图。")
                    payload["screenshot"] = str(screenshot)
                payload["close_accepted"] = window.close()
                payload["window_hidden_after_close"] = not window.isVisible()
                payload["status"] = (
                    "passed"
                    if all(
                        payload.get(field)
                        for field in (
                            "window_visible_before_close",
                            "native_handle_created",
                            "style_loaded",
                            "close_accepted",
                            "window_hidden_after_close",
                        )
                    )
                    else "failed"
                )
                app.exit(0 if payload["status"] == "passed" else 1)
            except Exception:
                logger.exception("Startup smoke check failed")
                payload["status"] = "failed"
                payload["error"] = "启动验证失败，请检查启动日志。"
                app.exit(1)

        if arguments.smoke_test:
            QTimer.singleShot(350, finish_smoke)
        exit_code = app.exec()
        payload.update(exit_code=exit_code, elapsed_seconds=round(time.monotonic() - started, 3))
        if arguments.smoke_report is not None:
            _write_report(arguments.smoke_report, payload)
        logger.info("Application closed; exit_code=%s", exit_code)
        return exit_code
    except Exception as error:
        logger.exception("Application startup failed")
        payload.update(error=str(error), exit_code=1)
        if arguments.smoke_report is not None:
            _write_report(arguments.smoke_report, payload)
        elif sys.stderr is not None:
            print("OpenLedger 启动失败，请检查启动日志或数据目录。", file=sys.stderr)
        if not arguments.smoke_test:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox

                error_app = QApplication.instance() or QApplication([sys.argv[0]])
                QMessageBox.critical(None, "OpenLedger", "启动失败。请检查数据目录权限和运行环境。")
                del error_app
            except Exception:
                pass
        return 1
    finally:
        if instance_coordinator is not None:
            instance_coordinator.close()
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
