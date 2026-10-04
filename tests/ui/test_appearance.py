"""System appearance updates propagate to QSS and charts without changing the preference."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from openledger.application.dto.runtime import RuntimeInfo
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.settings import DesktopSettings, SettingsStore
from openledger.presentation.appearance import ThemeController
from openledger.presentation.views.main_window import MainWindow

pytestmark = pytest.mark.ui


def test_manual_theme_ignores_os_changes_but_system_selection_uses_latest_scheme(
    qtbot: QtBot,
) -> None:
    controller = ThemeController("light")
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert controller.effective == "light"
    with qtbot.waitSignal(controller.themeChanged) as received:
        controller.set_selection("system")
    assert received.args == ["dark"]
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    assert controller.effective == "light" and controller.selection == "system"
    controller.set_selection("dark")
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Unknown)
    assert controller.effective == "dark"
    controller.set_selection("system")
    assert controller.effective == "light"


def test_system_theme_updates_main_and_quick_style_and_preserves_persisted_selection(
    qtbot: QtBot, ledger: LedgerService, tmp_path: Path
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save(DesktopSettings(theme="system"))
    runtime = RuntimeInfo(
        "0.3.0.dev0", "3.12.5", "6.11.2", "6.11.2", "3.45.3", str(tmp_path), False
    )
    window = MainWindow(runtime, ledger, store)
    qtbot.addWidget(window)
    before = ledger.total_assets()
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert "#1b1e25" in window.styleSheet()
    assert window.quick.styleSheet() == window.styleSheet()
    assert store.load().theme == "system" and window.settings.theme == "system"
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    assert "#1b1e25" not in window.styleSheet()
    assert ledger.total_assets() == before
    window.request_quit()
