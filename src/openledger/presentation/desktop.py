"""Tray actions and optional global hotkey with an explicit lifecycle."""

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QStyle, QSystemTrayIcon, QWidget

from openledger.infrastructure.platform.hotkeys import GlobalHotkey


class DesktopController(QObject):
    """Offer desktop entry points without owning financial writes or window-close policy."""

    def __init__(
        self,
        window: QWidget,
        show_quick: Callable[[], None],
        quit_callback: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self.window = window
        self.hotkey = GlobalHotkey(self)
        self.hotkey.triggered.connect(show_quick)
        self.tray = QSystemTrayIcon(self)
        icon = window.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self.tray.setIcon(icon if not icon.isNull() else QIcon())
        self.tray.setToolTip("OpenLedger")
        self.menu = QMenu(window)
        for label, callback in (
            (self.tr("打开主窗口"), self.show_main),
            (self.tr("快速记账"), show_quick),
            (self.tr("退出 OpenLedger"), quit_callback),
        ):
            action = QAction(label, self.menu)
            action.triggered.connect(callback)
            self.menu.addAction(action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)

    @property
    def tray_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def tray_active(self) -> bool:
        return self.tray_available and self.tray.isVisible()

    @property
    def hotkey_error(self) -> str:
        return self.hotkey.error

    def apply(self, *, tray_enabled: bool, hotkey_enabled: bool, shortcut: str) -> bool:
        """Apply preferences; a shortcut conflict leaves the previous binding intact."""
        # The tray remains an independent recovery path when a shortcut is occupied.
        if tray_enabled and self.tray_available:
            self.tray.show()
        else:
            self.tray.hide()
        if hotkey_enabled:
            if not self.hotkey.set_shortcut(shortcut):
                return False
        elif not self.hotkey.disable():
            return False
        return True

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_main()

    def show_main(self) -> None:
        """Restore a minimized or hidden main window and request foreground focus."""
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def close(self) -> None:
        """Hide the icon and release the hotkey before normal application shutdown."""
        self.tray.hide()
        self.hotkey.close()
