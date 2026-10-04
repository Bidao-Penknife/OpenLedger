"""Observe the OS appearance without overriding the system-wide Qt hints."""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication


class ThemeController(QObject):
    """Keep the persisted selection separate from the effective chart and QSS theme."""

    themeChanged = Signal(str)

    def __init__(self, selection: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._system = QGuiApplication.styleHints().colorScheme()
        self.selection = selection
        self.effective = self._resolve(selection)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_changed)

    def _resolve(self, selection: str) -> str:
        if selection not in {"light", "dark", "system"}:
            raise ValueError("Invalid theme selection")
        if selection == "system":
            return "dark" if self._system == Qt.ColorScheme.Dark else "light"
        return selection

    def set_selection(self, selection: str) -> None:
        """Apply a manual choice immediately; retain system updates for later use."""
        effective = self._resolve(selection)
        self.selection = selection
        self._publish(effective)

    def _system_changed(self, scheme: Qt.ColorScheme) -> None:
        self._system = scheme
        if self.selection == "system":
            self._publish(self._resolve("system"))

    def _publish(self, effective: str) -> None:
        if self.effective != effective:
            self.effective = effective
            self.themeChanged.emit(effective)
