"""Feature smoke exercises packaged capabilities without side effects or external accounts."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from openledger.application.dto.runtime import RuntimeInfo
from openledger.infrastructure.credentials import WindowsCredentialStore
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.settings import SettingsStore
from openledger.presentation.system_smoke import verify_system_features
from openledger.presentation.views.main_window import MainWindow

pytestmark = pytest.mark.ui


def test_offline_feature_smoke_preserves_ledger_settings_and_main_draft(
    qtbot: QtBot, ledger: LedgerService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_credentials() -> None:
        pytest.fail("Feature smoke must never access the native credential store")

    monkeypatch.setattr(WindowsCredentialStore, "_library", staticmethod(forbidden_credentials))
    runtime = RuntimeInfo(
        "0.3.0.dev0", "3.12.5", "6.11.2", "6.11.2", "3.45.3", str(tmp_path), False
    )
    window = MainWindow(runtime, ledger, SettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(window)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    try:
        window.input.setPlainText("用户未提交草稿，不能被验收覆盖")
        revision = window._revision
        with ledger.database.read() as connection:
            before = tuple(connection.iterdump())
        files_before = sorted(path.name for path in tmp_path.iterdir())
        result = verify_system_features(window)
        assert result["translations_loaded"] == {"en_US": True}
        assert result["ai_offline_suggestion"] is True
        assert result["manual_update_offline_result"] == "available"
        assert result["quick_confirm_only"] is True
        assert result["plugin_api_version"] == 1 and result["plugins_disabled_by_default"] is True
        assert result["native_integrations_disabled"] is True
        assert result["real_credentials_accessed"] is False
        assert result["network_requests"] == result["financial_writes"] == 0
        assert window.input.toPlainText() == "用户未提交草稿，不能被验收覆盖"
        assert window._revision == revision and not window.quick.isVisible()
        assert files_before == sorted(path.name for path in tmp_path.iterdir())
        with ledger.database.read() as connection:
            assert tuple(connection.iterdump()) == before
    finally:
        window.close()
