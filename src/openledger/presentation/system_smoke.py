"""Offline feature checks shared by source, frozen and extracted executable smoke runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib.resources import as_file, files
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, QTranslator
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from openledger._version import __version__
from openledger.application.dto.ai import AIConfig
from openledger.application.dto.parsing import ParseRequest
from openledger.application.ports.ai import CancelCheck
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AIParser
from openledger.infrastructure.credentials import MemoryCredentialStore, credential_target
from openledger.infrastructure.updates import ReleaseResponse, UpdateService
from openledger.plugins.contracts import PLUGIN_API_VERSION, PluginKind, PluginManifest
from openledger.plugins.registry import PluginRegistry
from openledger.presentation.views.quick_entry import QuickEntryWindow

if TYPE_CHECKING:
    from openledger.presentation.views.main_window import MainWindow


class _OfflineAI:
    """Return a synthetic suggestion; never construct a socket or accept financial history."""

    def __init__(self, day: str) -> None:
        self.day = day
        self.requests = 0

    def complete(self, endpoint: str, key: str, body: bytes, cancel: CancelCheck) -> bytes:
        self.requests += 1
        payload = json.loads(body)
        context = json.loads(payload["messages"][1]["content"])
        if key != "synthetic-smoke-key" or context["text"] != "咖啡25元":
            raise LedgerError("SYSTEM_FEATURE_SMOKE_FAILED")
        content = {
            "transactions": [
                {
                    "span": [0, len(context["text"])],
                    "kind": "expense",
                    "amount_minor": "2500",
                    "occurred_on": self.day,
                }
            ]
        }
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ]
            }
        ).encode("utf-8")


def _require(value: bool) -> None:
    if not value:
        raise LedgerError("SYSTEM_FEATURE_SMOKE_FAILED")


def _financial_state(window: MainWindow) -> tuple[tuple[str, int], ...]:
    with window.ledger.database.read() as connection:
        tables = (
            "transactions",
            "account_entries",
            "command_receipts",
            "audit_events",
            "change_log",
        )
        return tuple(
            (table, int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]))
            for table in tables
        )


def verify_system_features(window: MainWindow) -> dict[str, object]:
    """Check optional features without credentials, networking, native hooks or ledger writes."""
    before = _financial_state(window)
    _require(window.desktop is None)
    main_text = window.input.toPlainText()
    main_revision = window._revision
    main_settings = window.settings

    translator = QTranslator()
    resource = files("openledger.resources").joinpath("translations/openledger_en_US.qm")
    with as_file(resource) as path:
        translations_loaded = translator.load(str(path))
    _require(translations_loaded)
    translated_settings = translator.translate("MainWindow", "设置")
    _require(bool(translated_settings))

    registry = PluginRegistry()
    registry.register(
        PluginManifest("offline-smoke", "Offline feature smoke", "ai", "1.0.0"), object()
    )
    kinds: tuple[PluginKind, ...] = ("ai", "analytics", "import", "theme")
    disabled = all(registry.selected(kind) is None for kind in kinds)
    registered = any(
        entry.manifest.id == "openai-compatible" for entry in window.plugins.entries("ai")
    )
    _require(disabled and registered)

    quick = QuickEntryWindow(window.ledger, time_zone=window.settings.time_zone)
    quick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    emitted: list[tuple[str, object]] = []
    quick.commandRequested.connect(lambda command, payload: emitted.append((command, payload)))
    try:
        quick.input.setPlainText("咖啡25元")
        QApplication.sendEvent(
            quick.input,
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        quick_parsed = quick._candidate is not None and quick.form.amount.text() == "25.00"
        _require(quick_parsed and not emitted and not quick.isVisible())
    finally:
        quick.hide()
        quick.deleteLater()

    config = AIConfig(enabled=True, model="synthetic-offline-model")
    credentials = MemoryCredentialStore()
    target = credential_target(window.ai_store.data_dir, config)
    credentials.set(target, "synthetic-smoke-key")
    key = credentials.get(target)
    _require(key == "synthetic-smoke-key")
    transport = _OfflineAI(window.today().isoformat())
    request = ParseRequest(str(uuid4()), 1, "咖啡25元", window.today(), window.settings.time_zone)
    result = AIParser(transport, clock=window.ledger.clock).parse(request, config, key or "")
    ai_suggestion = (
        result.draft_id == request.draft_id
        and result.revision == request.revision
        and len(result.drafts) == 1
        and result.drafts[0].amount_minor.value == 2500
        and result.drafts[0].amount_minor.origin == "ai_suggestion"
        and result.drafts[0].amount_minor.requires_confirmation
    )
    _require(ai_suggestion and transport.requests == 1)
    credentials.delete(target)

    update_requests: list[tuple[str, dict[str, str]]] = []

    def offline_release(
        url: str, headers: dict[str, str], cancelled: Callable[[], bool]
    ) -> ReleaseResponse:
        _require(not cancelled() and "Authorization" not in headers)
        update_requests.append((url, headers))
        return ReleaseResponse(
            200,
            json.dumps(
                {
                    "draft": False,
                    "prerelease": False,
                    "tag_name": "v9.9.9",
                    "html_url": "https://github.com/openledger-smoke/synthetic/releases/tag/v9.9.9",
                }
            ).encode("utf-8"),
        )

    updates = UpdateService(__version__, offline_release)
    _require(updates.check("", "").status == "unconfigured" and not update_requests)
    update = updates.check("openledger-smoke", "synthetic")
    _require(update.status == "available" and len(update_requests) == 1)
    _require(
        update_requests[0][0]
        == "https://api.github.com/repos/openledger-smoke/synthetic/releases/latest"
    )
    _require(
        update.release_url == "https://github.com/openledger-smoke/synthetic/releases/tag/v9.9.9"
    )
    _require(_financial_state(window) == before)
    _require(
        window.input.toPlainText() == main_text
        and window._revision == main_revision
        and window.settings == main_settings
        and window.desktop is None
    )
    return {
        "translations_loaded": {"en_US": translations_loaded},
        "english_settings_translation": translated_settings,
        "ai_offline_suggestion": ai_suggestion,
        "manual_update_offline_result": update.status,
        "release_url_validated": True,
        "quick_confirm_only": quick_parsed and not emitted,
        "plugin_api_version": PLUGIN_API_VERSION,
        "plugin_registered": registered,
        "plugins_disabled_by_default": disabled,
        "native_integrations_disabled": True,
        "real_credentials_accessed": False,
        "network_requests": 0,
        "financial_writes": 0,
    }
