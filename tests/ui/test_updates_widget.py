"""Release checks require a click and cannot apply results from changed settings."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from openledger.infrastructure.updates import (
    ReleaseResponse,
    UpdateService,
    UpdateSettingsStore,
)
from openledger.presentation.views.updates import UpdatesWidget

pytestmark = pytest.mark.ui


class Transport:
    def __init__(self, block: bool = False) -> None:
        self.calls: list[str] = []
        self.threads: list[QThread] = []
        self.entered = Event()
        self.released = Event()
        if not block:
            self.released.set()

    def __call__(
        self, url: str, headers: dict[str, str], cancelled: Callable[[], bool]
    ) -> ReleaseResponse:
        self.calls.append(url)
        self.threads.append(QThread.currentThread())
        self.entered.set()
        if not self.released.wait(3):
            raise AssertionError("Test request was not released")
        return ReleaseResponse(
            200,
            json.dumps(
                {
                    "draft": False,
                    "prerelease": False,
                    "tag_name": "v0.3.0",
                    "html_url": "https://github.com/example/OpenLedger/releases/tag/v0.3.0",
                }
            ).encode(),
        )


@pytest.fixture
def widget(qtbot: QtBot, tmp_path: Path) -> Iterator[tuple[UpdatesWidget, Transport, list[str]]]:
    transport = Transport()
    opened: list[str] = []

    def open_url(url: str) -> bool:
        opened.append(url)
        return True

    page = UpdatesWidget(
        UpdateSettingsStore(tmp_path / "updates.json"),
        "0.3.0.dev0",
        service=UpdateService("0.3.0.dev0", transport),
        open_release=open_url,
    )
    qtbot.addWidget(page)
    page.show()
    yield page, transport, opened
    transport.released.set()
    page.close_workers()


def configure(page: UpdatesWidget) -> None:
    page.owner.setText("example")
    page.repo.setText("OpenLedger")


def test_startup_and_configuration_do_not_contact_github(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, opened = widget
    configure(page)
    qtbot.wait(25)
    assert transport.calls == [] and opened == []
    assert not page.release_button.isEnabled()
    assert not page.store.path.exists()


def test_manual_check_runs_off_gui_thread_and_only_explicit_click_opens_release(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, opened = widget
    configure(page)
    QTest.mouseClick(page.check_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.last_result is not None, timeout=3000)
    assert page.last_result is not None and page.last_result.status == "available"
    assert page.store.load().owner == "example"
    assert transport.threads[0] != page.thread()
    assert opened == [] and page.release_button.isEnabled()
    QTest.mouseClick(page.release_button, Qt.MouseButton.LeftButton)
    assert opened == ["https://github.com/example/OpenLedger/releases/tag/v0.3.0"]


def test_changed_settings_discard_success_from_old_request(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, opened = widget
    transport.released.clear()
    configure(page)
    page.check()
    qtbot.waitUntil(transport.entered.is_set, timeout=3000)
    page.repo.setText("Other")
    transport.released.set()
    qtbot.waitUntil(lambda: not page.tasks.busy, timeout=3000)
    assert page.last_result is None and not page.release_button.isEnabled()
    assert "更改" in page.status.text() and opened == []


def test_cancel_immediately_invalidates_pending_request(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, opened = widget
    transport.released.clear()
    configure(page)
    page.check()
    qtbot.waitUntil(transport.entered.is_set, timeout=3000)
    QTest.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)
    assert "取消" in page.status.text()
    transport.released.set()
    qtbot.waitUntil(lambda: not page.tasks.busy, timeout=3000)
    assert page.last_result is None and opened == []
    assert page.check_button.isEnabled() and not page.cancel_button.isEnabled()


def test_unconfigured_and_invalid_settings_make_no_network_request(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, opened = widget
    page.check()
    qtbot.waitUntil(lambda: not page.tasks.busy, timeout=3000)
    assert page.last_result is not None and page.last_result.status == "unconfigured"
    page.owner.setText("https://evil.example")
    page.repo.setText("repo")
    page.check()
    assert "有效" in page.status.text()
    assert transport.calls == [] and opened == []


def test_editing_configuration_invalidates_previously_valid_browser_link(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, _, opened = widget
    configure(page)
    page.check()
    qtbot.waitUntil(lambda: not page.tasks.busy, timeout=3000)
    page.owner.setText("Other")
    page.open_release()
    assert opened == [] and not page.release_button.isEnabled()


def test_check_button_prevents_duplicate_visible_intent(
    widget: tuple[UpdatesWidget, Transport, list[str]], qtbot: QtBot
) -> None:
    page, transport, _ = widget
    transport.released.clear()
    configure(page)
    page.check()
    page.check()
    qtbot.waitUntil(transport.entered.is_set, timeout=3000)
    assert len(transport.calls) == 1 and not page.check_button.isEnabled()
    transport.released.set()
    qtbot.waitUntil(lambda: not page.tasks.busy, timeout=3000)
