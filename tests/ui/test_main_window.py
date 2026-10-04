"""Verify native shell navigation, visible scope, and normal window shutdown."""

from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QStackedWidget
from pytestqt.qtbot import QtBot

from openledger.application.dto.runtime import RuntimeInfo
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.views.main_window import MainWindow

pytestmark = pytest.mark.ui


@pytest.fixture
def runtime() -> RuntimeInfo:
    """Provide a stable runtime snapshot without reading real user data."""
    return RuntimeInfo(
        app_version="0.1.0.dev0",
        python_version="3.12.5",
        pyside_version="6.8.3",
        qt_version="6.8.3",
        sqlite_version="3.45.3",
        data_directory="C:/测试用户/OpenLedger",
        sqlite_wal_supported=False,
    )


def test_home_explains_scope_and_window_closes(
    qtbot: QtBot, runtime: RuntimeInfo, ledger: LedgerService
) -> None:
    """Startup must show the incomplete stage clearly and close without persistence."""
    before = ledger.balances()
    window = MainWindow(runtime, ledger)
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
    assert window.objectName() == "mainWindow"
    notice = window.findChild(QLabel, "homeVersion")
    assert notice is not None
    assert runtime.app_version in notice.text()
    labels = window.findChildren(QLabel)
    assert any("确认记账草稿" in label.text() for label in labels)
    assert ledger.balances() == before
    exit_button = window.findChild(QPushButton, "exitButton")
    assert exit_button is not None
    QTest.mouseClick(exit_button, Qt.MouseButton.LeftButton)
    assert not window.isVisible()


def test_environment_navigation_uses_provided_runtime(
    qtbot: QtBot, runtime: RuntimeInfo, ledger: LedgerService
) -> None:
    """Navigation exposes the same immutable values the bootstrap supplied."""
    window = MainWindow(runtime, ledger)
    qtbot.addWidget(window)
    window.show()
    navigation = window.findChild(QPushButton, "environmentNavigation")
    pages = window.findChild(QStackedWidget, "pages")
    assert navigation is not None
    assert pages is not None
    QTest.mouseClick(navigation, Qt.MouseButton.LeftButton)
    assert pages.currentIndex() == 3
    assert navigation.isChecked()
    directory = window.findChild(QLabel, "dataDirectory")
    sqlite_status = window.findChild(QLabel, "sqliteStatus")
    assert directory is not None
    assert sqlite_status is not None
    assert directory.text() == runtime.data_directory
    assert "未达到" in sqlite_status.text()
    home = window.findChild(QPushButton, "homeNavigation")
    assert home is not None
    QTest.mouseClick(home, Qt.MouseButton.LeftButton)
    assert pages.currentIndex() == 0
    assert home.isChecked()
    assert not navigation.isChecked()


def test_supported_runtime_has_positive_status(
    qtbot: QtBot, runtime: RuntimeInfo, ledger: LedgerService
) -> None:
    """A compatible SQLite runtime must not show the upgrade warning."""
    window = MainWindow(
        replace(runtime, sqlite_version="3.51.3", sqlite_wal_supported=True), ledger
    )
    qtbot.addWidget(window)
    status = window.findChild(QLabel, "sqliteStatus")
    assert status is not None
    assert "满足" in status.text()
    assert status.property("warning") is False


def test_minimum_window_size_keeps_navigation_accessible(
    qtbot: QtBot, runtime: RuntimeInfo, ledger: LedgerService
) -> None:
    """Both navigation buttons remain visible after resizing to the minimum size."""
    window = MainWindow(runtime, ledger)
    qtbot.addWidget(window)
    window.resize(window.minimumSize())
    window.show()
    for name in (
        "homeNavigation",
        "transactionsNavigation",
        "managementNavigation",
        "environmentNavigation",
        "exitButton",
    ):
        button = window.findChild(QPushButton, name)
        assert button is not None
        assert button.isVisible()
        assert window.rect().contains(button.mapTo(window, button.rect().center()))
