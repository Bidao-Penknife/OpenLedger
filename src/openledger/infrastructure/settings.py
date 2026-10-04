"""Small, atomic desktop preferences kept separate from financial records."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger.domain.errors import LedgerError


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    """Presentation preferences; neither balances nor API credentials belong here."""

    theme: str = "light"
    time_zone: str = "Asia/Shanghai"
    language: str = "zh_CN"
    tray_enabled: bool = True
    close_to_tray: bool = True
    hotkey_enabled: bool = True
    shortcut: str = "Ctrl+Alt+L"


class SettingsStore:
    """Read a bounded preferences file and replace it atomically on save."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> DesktopSettings:
        """Use documented defaults if the optional settings file is absent or invalid."""
        try:
            if self.path.stat().st_size > 4096:
                return DesktopSettings()
            value = json.loads(self.path.read_text(encoding="utf-8"))
            # Existing two-field preferences remain valid; new options use defaults.
            settings = DesktopSettings(
                theme=value["theme"],
                time_zone=value["time_zone"],
                language=value.get("language", "zh_CN"),
                tray_enabled=value.get("tray_enabled", True),
                close_to_tray=value.get("close_to_tray", True),
                hotkey_enabled=value.get("hotkey_enabled", True),
                shortcut=value.get("shortcut", "Ctrl+Alt+L"),
            )
            self._validate(settings)
            return settings
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ZoneInfoNotFoundError):
            return DesktopSettings()

    @staticmethod
    def _validate(settings: DesktopSettings) -> None:
        if settings.theme not in {"light", "dark", "system"} or not isinstance(
            settings.time_zone, str
        ):
            raise ValueError("Invalid desktop settings")
        ZoneInfo(settings.time_zone)
        if settings.language not in {"zh_CN", "en_US"} or any(
            type(value) is not bool
            for value in (settings.tray_enabled, settings.close_to_tray, settings.hotkey_enabled)
        ):
            raise ValueError("Invalid desktop settings")
        from openledger.infrastructure.platform.hotkeys import normalize_shortcut

        try:
            normalize_shortcut(settings.shortcut)
        except LedgerError as error:
            raise ValueError("Invalid desktop shortcut") from error

    def save(self, settings: DesktopSettings) -> None:
        """Publish a complete file; a failed write leaves the previous file intact."""
        self._validate(settings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(settings), stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
