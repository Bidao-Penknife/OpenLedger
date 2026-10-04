"""Bounded native entry points must never acquire a financial write capability."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, Qt
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget
from pytestqt.qtbot import QtBot

from openledger.domain.errors import LedgerError
from openledger.infrastructure.platform.hotkeys import (
    MOD_NOREPEAT,
    WM_HOTKEY,
    GlobalHotkey,
    normalize_shortcut,
    parse_shortcut,
)
from openledger.infrastructure.platform.single_instance import InstanceCoordinator
from openledger.presentation.desktop import DesktopController

pytestmark = pytest.mark.ui


class FakeBackend:
    def __init__(self) -> None:
        self.bindings: dict[int, tuple[int, int]] = {}
        self.conflict = False
        self.unregister_failure: int | None = None

    def register(self, identifier: int, modifiers: int, virtual_key: int) -> bool:
        if self.conflict:
            return False
        self.bindings[identifier] = modifiers, virtual_key
        return True

    def unregister(self, identifier: int) -> bool:
        if identifier == self.unregister_failure:
            return False
        self.bindings.pop(identifier, None)
        return True


@pytest.mark.parametrize(
    ("value", "canonical"),
    [
        ("ctrl + alt + l", "Ctrl+Alt+L"),
        ("Shift+Ctrl+F24", "Ctrl+Shift+F24"),
        ("ALT+8", "Alt+8"),
        ("Ctrl+F01", "Ctrl+F1"),
    ],
)
def test_shortcut_has_one_canonical_form(value: str, canonical: str) -> None:
    assert normalize_shortcut(value) == canonical


@pytest.mark.parametrize(
    "value",
    [
        "L",
        "Shift+L",
        "Win+L",
        "Ctrl+Ctrl+L",
        "Ctrl+F25",
        "Ctrl+F0",
        "Ctrl+咖",
        "Ctrl+Enter",
        "Ctrl++",
        "Ctrl+",
        "Ctrl+Alt+AA",
        "",
    ],
)
def test_invalid_shortcuts_cannot_request_system_bindings(value: str) -> None:
    with pytest.raises(LedgerError, match="INVALID_SHORTCUT"):
        parse_shortcut(value)


def test_hotkey_conflict_preserves_old_binding(qapp: QApplication) -> None:
    backend = FakeBackend()
    hotkey = GlobalHotkey(backend=backend)
    try:
        assert hotkey.set_shortcut("Ctrl+Alt+L")
        old_id = hotkey.identifier
        assert old_id is not None
        assert backend.bindings[old_id][0] & MOD_NOREPEAT
        backend.conflict = True
        assert not hotkey.set_shortcut("Ctrl+Alt+K")
        assert hotkey.shortcut == "Ctrl+Alt+L"
        assert hotkey.identifier == old_id
        assert set(backend.bindings) == {old_id}
        assert hotkey.error == "HOTKEY_CONFLICT"
    finally:
        hotkey.close()
    assert backend.bindings == {}


def test_failed_old_unregister_rolls_back_replacement(qapp: QApplication) -> None:
    backend = FakeBackend()
    hotkey = GlobalHotkey(backend=backend)
    assert hotkey.set_shortcut("Ctrl+Alt+L")
    old_id = hotkey.identifier
    assert old_id is not None
    backend.unregister_failure = old_id
    assert not hotkey.set_shortcut("Ctrl+Alt+K")
    assert set(backend.bindings) == {old_id}
    assert hotkey.identifier == old_id
    assert not hotkey.disable()
    backend.unregister_failure = None
    hotkey.close()
    assert backend.bindings == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows RegisterHotKey native lifecycle")
def test_native_hotkey_event_and_unregister(qtbot: QtBot) -> None:
    hotkey = GlobalHotkey()
    try:
        # A rare modifier/function-key combination; never inject keyboard input.
        assert hotkey.set_shortcut("Ctrl+Alt+Shift+F24"), hotkey.error
        calls: list[bool] = []
        hotkey.triggered.connect(lambda: calls.append(True))
        library = ctypes.WinDLL("user32", use_last_error=True)
        library.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        library.PostThreadMessageW.restype = wintypes.BOOL
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        assert hotkey.identifier is not None
        assert library.PostThreadMessageW(
            kernel.GetCurrentThreadId(), WM_HOTKEY, hotkey.identifier, 0
        )
        qtbot.waitUntil(lambda: calls == [True], timeout=3000)
        assert hotkey.disable()
        assert hotkey.identifier is None
    finally:
        hotkey.close()


def test_same_data_directory_has_one_owner(qapp: QApplication, tmp_path: Path) -> None:
    first = InstanceCoordinator(tmp_path)
    second = InstanceCoordinator(tmp_path / ".")
    try:
        assert first.acquire()
        assert first.acquire()
        assert first.server_name == second.server_name
        assert not second.acquire()
        second.close()
        assert first.is_primary
        first.close()
        assert second.acquire()
    finally:
        first.close()
        second.close()


def test_different_directories_can_run_independently(qapp: QApplication, tmp_path: Path) -> None:
    other = tmp_path / "独立账本"
    other.mkdir()
    first, second = InstanceCoordinator(tmp_path), InstanceCoordinator(other)
    try:
        assert first.server_name != second.server_name
        assert first.acquire() and second.acquire()
    finally:
        first.close()
        second.close()


def connect(qtbot: QtBot, owner: InstanceCoordinator) -> QLocalSocket:
    previous = len(owner._clients)
    socket = QLocalSocket()
    socket.connectToServer(owner.server_name)
    assert socket.waitForConnected(1000)
    qtbot.waitUntil(lambda: len(owner._clients) > previous, timeout=1000)
    return socket


def test_partial_requests_are_isolated_and_acknowledged_once(qtbot: QtBot, tmp_path: Path) -> None:
    owner = InstanceCoordinator(tmp_path)
    assert owner.acquire()
    actions: list[str] = []
    owner.activationRequested.connect(actions.append)
    first, second = connect(qtbot, owner), connect(qtbot, owner)
    try:
        first.write(b"sh")
        first.flush()
        second.write(b"quick\n")
        second.flush()
        qtbot.waitUntil(lambda: actions == ["quick"], timeout=1000)
        first.write(b"ow\n")
        first.flush()
        qtbot.waitUntil(lambda: actions == ["quick", "show"], timeout=1000)
        assert first.readAll().data() == b"ok\n"
        assert second.readAll().data() == b"ok\n"
        first.write(
            b"quick\n"
        ) if first.state() == QLocalSocket.LocalSocketState.ConnectedState else None
        assert actions == ["quick", "show"]
    finally:
        first.abort()
        second.abort()
        owner.close()


@pytest.mark.parametrize(
    "packet", [b"unknown\n", b"show\nquick\n", b"show\n{amount:25}", b"x" * 33]
)
def test_protocol_rejects_unknown_extra_or_oversized_payloads(
    qtbot: QtBot, tmp_path: Path, packet: bytes
) -> None:
    owner = InstanceCoordinator(tmp_path)
    assert owner.acquire()
    actions: list[str] = []
    owner.activationRequested.connect(actions.append)
    socket = connect(qtbot, owner)
    try:
        socket.write(packet)
        socket.flush()
        qtbot.waitUntil(
            lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState, timeout=2000
        )
        assert actions == []
    finally:
        socket.abort()
        owner.close()


def test_partial_clients_timeout_and_count_is_bounded(qtbot: QtBot, tmp_path: Path) -> None:
    owner = InstanceCoordinator(tmp_path)
    assert owner.acquire()
    sockets = [connect(qtbot, owner) for _ in range(8)]
    extra = QLocalSocket()
    try:
        extra.connectToServer(owner.server_name)
        extra.waitForConnected(1000)
        qtbot.waitUntil(
            lambda: extra.state() == QLocalSocket.LocalSocketState.UnconnectedState, timeout=1000
        )
        assert len(owner._clients) <= 8
        qtbot.waitUntil(lambda: not owner._clients, timeout=2500)
    finally:
        for socket in [*sockets, extra]:
            socket.abort()
        owner.close()


def test_second_real_process_activates_owner_and_releases_lock(
    qtbot: QtBot, tmp_path: Path
) -> None:
    code = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from openledger.infrastructure.platform.single_instance import InstanceCoordinator
app=QCoreApplication([])
owner=InstanceCoordinator(Path(sys.argv[1]))
assert owner.acquire()
def activate(action):
    print('ACTION:'+action, flush=True)
    QTimer.singleShot(50, app.quit)
owner.activationRequested.connect(activate)
QTimer.singleShot(5000, app.quit)
print('READY', flush=True)
app.exec()
owner.close()
"""
    process = QProcess()
    process.start(sys.executable, ["-c", code, str(tmp_path)])
    try:
        assert process.waitForStarted(3000)
        assert process.waitForReadyRead(3000)
        assert b"READY" in process.readAllStandardOutput().data()
        secondary = InstanceCoordinator(tmp_path)
        assert not secondary.acquire()
        assert secondary.activate("quick")
        assert process.waitForFinished(3000)
        assert process.exitCode() == 0, process.readAllStandardError().data()
        assert b"ACTION:quick" in process.readAllStandardOutput().data()
        assert secondary.acquire()
        secondary.close()
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(3000)


def test_dead_process_lock_is_recovered_without_deleting_live_owner_lock(
    qtbot: QtBot, tmp_path: Path
) -> None:
    code = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from openledger.infrastructure.platform.single_instance import InstanceCoordinator
app=QCoreApplication([])
owner=InstanceCoordinator(Path(sys.argv[1]))
assert owner.acquire()
print('READY', flush=True)
app.exec()
"""
    process = QProcess()
    process.start(sys.executable, ["-c", code, str(tmp_path)])
    secondary = InstanceCoordinator(tmp_path)
    try:
        assert process.waitForStarted(3000)
        assert process.waitForReadyRead(3000)
        assert b"READY" in process.readAllStandardOutput().data()
        assert not secondary.acquire()
        assert (tmp_path / ".instance.lock").is_file()
        process.kill()
        assert process.waitForFinished(3000)
        assert secondary.acquire()
        assert secondary.server.isListening()
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(3000)
        secondary.close()


def test_missing_directory_fails_instance_lock_instead_of_claiming_secondary(
    qapp: QApplication, tmp_path: Path
) -> None:
    coordinator = InstanceCoordinator(tmp_path / "missing")
    with pytest.raises(LedgerError, match="INSTANCE_LOCK_FAILED"):
        coordinator.acquire()
    coordinator.close()


def test_activation_refuses_financial_payload_and_invalid_timeout(
    qapp: QApplication, tmp_path: Path
) -> None:
    coordinator = InstanceCoordinator(tmp_path)
    with pytest.raises(LedgerError, match="INVALID_ACTIVATION"):
        coordinator.activate("show", 0)
    with pytest.raises(LedgerError, match="INVALID_ACTIVATION"):
        coordinator.activate("show", 5001)


def test_tray_unavailable_never_hides_main_window(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = QWidget()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(window)
    window.show()
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    controller = DesktopController(window, lambda: None, lambda: None)
    try:
        assert controller.apply(tray_enabled=True, hotkey_enabled=False, shortcut="Ctrl+Alt+L")
        assert not controller.tray_available
        assert not controller.tray_active
        assert window.isVisible()
    finally:
        controller.close()


def test_hotkey_conflict_still_applies_independent_tray_preference(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    controller = DesktopController(window, lambda: None, lambda: None)
    backend = FakeBackend()
    backend.conflict = True
    controller.hotkey._backend = backend
    calls: list[str] = []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    monkeypatch.setattr(controller.tray, "show", lambda: calls.append("show"))
    monkeypatch.setattr(controller.tray, "hide", lambda: calls.append("hide"))
    try:
        assert not controller.apply(tray_enabled=True, hotkey_enabled=True, shortcut="Ctrl+Alt+L")
        assert calls == ["show"]
        assert controller.hotkey_error == "HOTKEY_CONFLICT"
    finally:
        controller.close()
