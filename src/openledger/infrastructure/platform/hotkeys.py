"""Explicit Windows hotkey registration with transactional binding changes."""

import ctypes
import itertools
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, QCoreApplication, QObject, Signal

from openledger.domain.errors import LedgerError

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
_IDENTIFIERS = itertools.count(0x4C00)


@dataclass(frozen=True)
class Shortcut:
    """Canonical shortcut and its Win32 virtual key representation."""

    text: str
    modifiers: int
    virtual_key: int


def parse_shortcut(value: str) -> Shortcut:
    """Accept Ctrl/Alt/Shift plus one letter, digit or F1–F24; require Ctrl or Alt."""
    if not isinstance(value, str) or len(value) > 48:
        raise LedgerError("INVALID_SHORTCUT")
    parts = [part.strip().upper() for part in value.split("+")]
    modifiers = {"CTRL": 0x0002, "ALT": 0x0001, "SHIFT": 0x0004}
    if len(parts) < 2 or any(part not in modifiers for part in parts[:-1]):
        raise LedgerError("INVALID_SHORTCUT")
    if len(set(parts[:-1])) != len(parts[:-1]) or not {"CTRL", "ALT"}.intersection(parts[:-1]):
        raise LedgerError("INVALID_SHORTCUT")
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        virtual_key = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
        key = "F" + str(int(key[1:]))
    else:
        raise LedgerError("INVALID_SHORTCUT")
    names = [name.title() for name in ("CTRL", "ALT", "SHIFT") if name in parts[:-1]]
    return Shortcut("+".join([*names, key]), sum(modifiers[p] for p in parts[:-1]), virtual_key)


def normalize_shortcut(value: str) -> str:
    """Validate a setting without requesting any operating system registration."""
    return parse_shortcut(value).text


class HotkeyBackend(Protocol):
    """Small native boundary allowing conflict and cleanup tests without user bindings."""

    def register(self, identifier: int, modifiers: int, virtual_key: int) -> bool: ...

    def unregister(self, identifier: int) -> bool: ...


class WindowsHotkeyBackend:
    """Bind hotkeys to the current GUI thread, never install a keyboard hook."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise LedgerError("HOTKEY_UNSUPPORTED")
        self._library = ctypes.WinDLL("user32", use_last_error=True)
        self._library.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._library.RegisterHotKey.restype = wintypes.BOOL
        self._library.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._library.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, identifier: int, modifiers: int, virtual_key: int) -> bool:
        return bool(self._library.RegisterHotKey(None, identifier, modifiers, virtual_key))

    def unregister(self, identifier: int) -> bool:
        return bool(self._library.UnregisterHotKey(None, identifier))


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkey") -> None:
        super().__init__()
        self.owner = owner

    def nativeEventFilter(
        self, eventType: QByteArray | bytes | bytearray | memoryview, message: int, /
    ) -> object:
        event_name = eventType.data() if isinstance(eventType, QByteArray) else bytes(eventType)
        if sys.platform == "win32" and event_name in {
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        }:
            native = wintypes.MSG.from_address(int(message))
            if native.message == WM_HOTKEY and native.wParam == self.owner.identifier:
                self.owner.triggered.emit()
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """Keep the previous shortcut registered if a replacement is unavailable."""

    triggered = Signal()

    def __init__(
        self, parent: QObject | None = None, *, backend: HotkeyBackend | None = None
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self.identifier: int | None = None
        self.shortcut: str | None = None
        self.error = ""
        self._filter = _NativeFilter(self)
        self._installed = False

    def set_shortcut(self, shortcut: str) -> bool:
        """Register first, then retire the previous binding; failures retain it."""
        try:
            parsed = parse_shortcut(shortcut)
            if parsed.text == self.shortcut:
                self.error = ""
                return True
            if self._backend is None:
                self._backend = WindowsHotkeyBackend()
            app = QCoreApplication.instance()
            if app is None:
                raise LedgerError("HOTKEY_NO_APPLICATION")
            identifier = next(_IDENTIFIERS)
            if identifier > 0xBFFF:
                raise LedgerError("HOTKEY_REGISTRATION_FAILED")
            if not self._backend.register(
                identifier, parsed.modifiers | MOD_NOREPEAT, parsed.virtual_key
            ):
                raise LedgerError("HOTKEY_CONFLICT")
            if self.identifier is not None and not self._backend.unregister(self.identifier):
                self._backend.unregister(identifier)
                raise LedgerError("HOTKEY_UNREGISTER_FAILED")
            self.identifier, self.shortcut = identifier, parsed.text
            if not self._installed:
                app.installNativeEventFilter(self._filter)
                self._installed = True
            self.error = ""
            return True
        except (LedgerError, OSError) as error:
            self.error = (
                error.code if isinstance(error, LedgerError) else "HOTKEY_REGISTRATION_FAILED"
            )
            return False

    def disable(self) -> bool:
        """Remove the registration; report a failed unregister instead of hiding it."""
        if self.identifier is not None:
            if self._backend is None or not self._backend.unregister(self.identifier):
                self.error = "HOTKEY_UNREGISTER_FAILED"
                return False
            self.identifier, self.shortcut = None, None
        self.error = ""
        return True

    def close(self) -> None:
        """Release the native registration and event filter before QApplication exits."""
        self.disable()
        app = QCoreApplication.instance()
        if app is not None and self._installed:
            app.removeNativeEventFilter(self._filter)
        self._installed = False
