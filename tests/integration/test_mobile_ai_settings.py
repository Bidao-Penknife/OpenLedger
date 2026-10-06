"""AI is opt-in and read-only; time zones and Android releases remain independent."""

import json
from pathlib import Path
from typing import Any

import pytest

from openledger.application.ports.ai import CancelCheck
from openledger.infrastructure.ai import HTTPAITransport
from openledger.infrastructure.updates import ReleaseResponse
from openledger.mobile.bridge import MobileLedger
from openledger.mobile.updates import MobileUpdateService
from tests.integration.test_mobile_bridge import NOW, account, digest, invoke


def test_optional_ai_is_read_only_and_secret_is_not_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    snapshot = invoke(mobile, "snapshot")["data"]
    category = next(
        row["id"] for row in snapshot["categories"] if row["transaction_kind"] == "expense"
    )
    calls: list[str] = []

    def complete(
        _self: HTTPAITransport, endpoint: str, key: str, body: bytes, _cancel: CancelCheck
    ) -> bytes:
        calls.append(endpoint)
        assert key == "synthetic-test-key"
        context = json.loads(json.loads(body)["messages"][1]["content"])
        assert "balances" not in context and "transactions" not in context
        row = {
            "span": [0, 5],
            "kind": "expense",
            "amount": "25.00",
            "occurred_on": "2026-10-04",
            "account_id": snapshot["accounts"][0]["id"],
            "category_id": category,
        }
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"transactions": [row]})},
                    }
                ]
            }
        ).encode()

    monkeypatch.setattr(HTTPAITransport, "complete", complete)
    config = {"enabled": True, "base_url": "https://example.test/v1", "model": "synthetic-model"}
    before = digest(mobile)
    envelope: dict[str, Any] = {
        "api_version": 1,
        "action": "ai_preview",
        "body": {"text": "咖啡25元", "config": config},
    }
    missing = json.loads(mobile.call(json.dumps(envelope)))
    assert missing["error"]["code"] == "AI_KEY_MISSING"
    result = json.loads(mobile.call(json.dumps(envelope), "synthetic-test-key"))
    assert result["ok"], result
    assert result["data"]["drafts"][0]["amount_minor"]["value"] == 2500
    assert "synthetic-test-key" not in json.dumps(result)
    assert digest(mobile) == before and len(calls) == 1
    envelope["body"]["config"]["enabled"] = False
    assert (
        json.loads(mobile.call(json.dumps(envelope), "synthetic-test-key"))["error"]["code"]
        == "AI_DISABLED"
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test/v1",
        "https://user:pass@example.test/v1",
        "https://example.test/v1?key=abc",
    ],
)
def test_unsafe_ai_config_never_connects(tmp_path: Path, endpoint: str) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    result = invoke(
        mobile,
        "validate_ai_config",
        {"config": {"enabled": True, "base_url": endpoint, "model": "test"}},
    )
    assert result["error"]["code"] == "AI_INVALID_CONFIG"


def test_zone_validation_and_default_zone_are_explicit(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW, time_zone="America/New_York")
    assert invoke(mobile, "snapshot")["data"]["time_zone"] == "America/New_York"
    assert invoke(mobile, "validate_time_zone", {"time_zone": "UTC"})["ok"]
    assert (
        invoke(mobile, "validate_time_zone", {"time_zone": "../../outside"})["error"]["code"]
        == "INVALID_TIMEZONE"
    )


def test_android_updates_ignore_desktop_version_and_external_pages() -> None:
    releases: list[dict[str, Any]] = [
        {
            "draft": False,
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/Bidao-Penknife/OpenLedger/releases/tag/v99.0.0",
            "assets": [{"state": "uploaded", "name": "OpenLedger-1.0.0-windows.zip"}],
        },
        {
            "draft": False,
            "html_url": "https://github.com/Bidao-Penknife/OpenLedger/releases/tag/android-v0.3.0",
            "assets": [{"state": "uploaded", "name": "OpenLedger-0.3.0-android-preview.apk"}],
        },
        {
            "draft": False,
            "html_url": "https://example.test/releases/new",
            "assets": [{"state": "uploaded", "name": "OpenLedger-100.0.0-android-preview.apk"}],
        },
    ]

    def transport(url: str, headers: dict[str, str], cancelled: Any) -> ReleaseResponse:
        return ReleaseResponse(200, json.dumps(releases).encode())

    result = MobileUpdateService("0.2.0-beta1-preview", transport).check()
    assert result["status"] == "available" and result["version"] == "0.3.0"
    assert result["release_url"].startswith("https://github.com/Bidao-Penknife/OpenLedger/")
    assert MobileUpdateService("0.3.0-preview", transport).check()["status"] == "up_to_date"
