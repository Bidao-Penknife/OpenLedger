"""Saving AI preferences is explicit and does not expose stored credentials in the UI."""

import json
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from openledger.application.dto.ai import AIConfig
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AISettingsStore
from openledger.infrastructure.credentials import MemoryCredentialStore, credential_target
from openledger.presentation.views.ai_settings import AISettingsWidget

pytestmark = pytest.mark.ui


class TrackingCredentials(MemoryCredentialStore):
    """Record synthetic reads without ever contacting Windows Credential Manager."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.fail_write = False

    def get(self, target: str) -> str | None:
        self.reads.append(target)
        return super().get(target)

    def set(self, target: str, key: str) -> None:
        if self.fail_write:
            raise LedgerError("CREDENTIAL_UNAVAILABLE")
        super().set(target, key)


def test_opening_settings_never_reads_saved_key_or_connects(qtbot: QtBot, tmp_path: Path) -> None:
    store = AISettingsStore(tmp_path)
    credentials = TrackingCredentials()
    credentials.set(credential_target(tmp_path, AIConfig()), "synthetic-key")
    widget = AISettingsWidget(store, credentials)
    qtbot.addWidget(widget)
    assert not credentials.reads and widget.key.text() == ""
    assert widget.config == AIConfig() and not widget.enabled.isChecked()
    assert not store.path.exists()


def test_explicit_save_writes_only_non_secret_config_and_clears_input(
    qtbot: QtBot, tmp_path: Path
) -> None:
    store = AISettingsStore(tmp_path)
    credentials = TrackingCredentials()
    widget = AISettingsWidget(store, credentials)
    qtbot.addWidget(widget)
    events: list[object] = []
    widget.configChanged.connect(events.append)
    widget.enabled.setChecked(True)
    widget.model.setText("synthetic-model")
    widget.key.setText("synthetic-key")
    widget.save()
    assert len(events) == 1 and widget.config == store.load()
    assert widget.config.enabled and widget.key.text() == ""
    assert "synthetic-key" not in store.path.read_text()
    assert set(json.loads(store.path.read_text())) == {
        "enabled",
        "base_url",
        "model",
        "allow_local_http",
    }
    assert credentials.get(credential_target(tmp_path, widget.config)) == "synthetic-key"
    credentials.reads.clear()
    widget.model.setText("other-model")
    widget.save()
    assert not credentials.reads and widget.config.model == "other-model"


def test_invalid_or_unavailable_credentials_preserve_form_and_previous_config(
    qtbot: QtBot, tmp_path: Path
) -> None:
    store = AISettingsStore(tmp_path)
    credentials = TrackingCredentials()
    widget = AISettingsWidget(store, credentials)
    qtbot.addWidget(widget)
    events: list[object] = []
    widget.configChanged.connect(events.append)
    widget.enabled.setChecked(True)
    widget.key.setText("synthetic-key")
    widget.save()
    assert not store.path.exists() and widget.key.text() == "synthetic-key"
    widget.model.setText("synthetic-model")
    credentials.fail_write = True
    widget.save()
    assert not store.path.exists() and widget.config == AIConfig() and not events
    assert "凭据管理器" in widget.status.text()


def test_failed_config_publish_restores_previous_credential(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AISettingsStore(tmp_path)
    credentials = TrackingCredentials()
    initial = AIConfig(True, model="initial-model")
    store.save(initial)
    target = credential_target(tmp_path, initial)
    credentials.set(target, "old-synthetic-key")
    before = store.path.read_bytes()
    widget = AISettingsWidget(store, credentials)
    qtbot.addWidget(widget)
    widget.key.setText("new-synthetic-key")

    def fail(config: AIConfig) -> None:
        raise LedgerError("AI_SETTINGS_IO_ERROR")

    monkeypatch.setattr(store, "save", fail)
    widget.save()
    assert credentials.get(target) == "old-synthetic-key"
    assert store.path.read_bytes() == before and widget.config == initial
    assert widget.key.text() == "new-synthetic-key" and "保存失败" in widget.status.text()


def test_delete_removes_only_selected_endpoint_and_keeps_other_credentials(
    qtbot: QtBot, tmp_path: Path
) -> None:
    store = AISettingsStore(tmp_path)
    credentials = TrackingCredentials()
    first = credential_target(tmp_path, AIConfig())
    other = credential_target(tmp_path, AIConfig(base_url="https://example.com/v1"))
    credentials.set(first, "first-synthetic-key")
    credentials.set(other, "other-synthetic-key")
    widget = AISettingsWidget(store, credentials)
    qtbot.addWidget(widget)
    widget.delete_key()
    assert credentials.get(first) is None and credentials.get(other) == "other-synthetic-key"
    assert not store.path.exists() and widget.key.text() == ""
