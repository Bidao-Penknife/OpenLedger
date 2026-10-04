"""Exercise the real shared SQLite core through the Android JSON contract."""

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from openledger.domain.errors import LedgerError
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.mobile.bridge import MobileLedger

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


@pytest.fixture
def mobile(tmp_path: Path) -> MobileLedger:
    """Create a private synthetic ledger without importing a desktop window."""
    return MobileLedger(str(tmp_path / "手机账本 space"), clock=lambda: NOW)


def invoke(mobile: MobileLedger, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Decode the same UTF-8 envelope used by the APK."""
    result: dict[str, Any] = json.loads(
        mobile.call(json.dumps({"api_version": 1, "action": action, "body": body or {}}))
    )
    return result


def account(mobile: MobileLedger, opening: str = "1000.00") -> str:
    result = invoke(
        mobile,
        "create_account",
        {
            "request_id": str(uuid4()),
            "name": "合成现金",
            "account_type": "cash",
            "opening_amount": opening,
            "balance_start_on": "2026-01-01",
        },
    )
    assert result["ok"], result
    return str(result["data"]["data"]["id"])


def record_body(mobile: MobileLedger, kind: str = "expense") -> dict[str, Any]:
    snapshot = invoke(mobile, "snapshot")["data"]
    return {
        "request_id": str(uuid4()),
        "fields": {
            "kind": kind,
            "amount": "25.00",
            "account_id": snapshot["accounts"][0]["id"],
            "book_id": snapshot["books"][0]["id"],
            "category_id": next(
                row["id"] for row in snapshot["categories"] if row["transaction_kind"] == kind
            ),
            "occurred_on": "2026-10-04",
            "note": "合成咖啡",
        },
    }


def digest(mobile: MobileLedger) -> str:
    """No-write checks compare actual SQLite file bytes."""
    return hashlib.sha256(mobile.database.path.read_bytes()).hexdigest()


def test_empty_start_invents_no_accounts_or_balance(mobile: MobileLedger) -> None:
    snapshot = invoke(mobile, "snapshot")["data"]
    assert snapshot["accounts"] == []
    assert snapshot["overview"]["total_assets_minor"] == 0
    assert snapshot["books"][0]["name"] == "我的账本"
    assert snapshot["schema_version"] == 1


def test_preview_is_read_only_and_uses_desktop_rules(mobile: MobileLedger) -> None:
    account(mobile)
    before = digest(mobile)
    result = invoke(mobile, "preview", {"text": "昨天晚上和朋友吃火锅花了128元，微信支付"})
    assert result["ok"]
    draft = result["data"]["drafts"][0]
    assert draft["amount_minor"]["value"] == 12_800
    assert draft["occurred_on"]["value"] == "2026-10-03"
    assert draft["kind"]["value"] == "expense"
    assert draft["time_period"]["value"] == "evening"
    assert digest(mobile) == before


def test_record_retry_concurrency_and_restart_preserve_one_debit(mobile: MobileLedger) -> None:
    account(mobile)
    body = record_body(mobile)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: invoke(mobile, "record", body), range(4)))
    assert all(result["ok"] for result in results)
    assert sum(not result["data"]["replayed"] for result in results) == 1
    reopened = MobileLedger(str(mobile.database.path.parent.parent), clock=lambda: NOW)
    assert invoke(reopened, "record", body)["data"]["replayed"] is True
    snapshot = invoke(reopened, "snapshot")["data"]
    assert snapshot["overview"]["total_assets_minor"] == 97_500
    assert snapshot["overview"]["expense_minor"] == 2500
    assert len([row for row in snapshot["transactions"]["rows"] if row["kind"] == "expense"]) == 1
    with reopened.database.read() as connection:
        validate_financial_integrity(connection)


def test_reusing_receipt_for_changed_fields_is_rejected(mobile: MobileLedger) -> None:
    account(mobile)
    body = record_body(mobile)
    assert invoke(mobile, "record", body)["ok"]
    body["fields"]["amount"] = "26.00"
    before = digest(mobile)
    result = invoke(mobile, "record", body)
    assert result["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert digest(mobile) == before


def test_income_and_paginated_literal_search(mobile: MobileLedger) -> None:
    account(mobile, "0.00")
    assert invoke(mobile, "record", record_body(mobile, "income"))["ok"]
    snapshot = invoke(mobile, "snapshot", {"page_size": 1, "search": "合成咖啡"})["data"]
    assert snapshot["overview"]["income_minor"] == 2500
    assert snapshot["overview"]["total_assets_minor"] == 2500
    assert snapshot["transactions"]["total"] == 1


@pytest.mark.parametrize("opening,expected", [("0", 0), ("-25.01", -2501), ("0.01", 1)])
def test_opening_amounts_keep_integer_precision(
    mobile: MobileLedger, opening: str, expected: int
) -> None:
    account(mobile, opening)
    assert invoke(mobile, "snapshot")["data"]["overview"]["total_assets_minor"] == expected


@pytest.mark.parametrize(
    "raw,code",
    [
        ("{}", "UNSUPPORTED_API_VERSION"),
        ("[]", "INVALID_ENVELOPE"),
        ('{"api_version":true,"action":"snapshot"}', "UNSUPPORTED_API_VERSION"),
        ('{"api_version":2,"action":"snapshot"}', "UNSUPPORTED_API_VERSION"),
        ('{"api_version":1,"api_version":1,"action":"snapshot"}', "INVALID_ENVELOPE"),
        ('{"api_version":1,"action":"snapshot","body":{"page":NaN}}', "INVALID_ENVELOPE"),
        ('{"api_version":1,"action":"snapshot","body":{"page":true}}', "INVALID_ENVELOPE"),
        ('{"api_version":1,"action":"snapshot","body":{"page":1.5}}', "INVALID_ENVELOPE"),
        ('{"api_version":1,"action":"snapshot","body":{"page_size":201}}', "INVALID_FILTER"),
        ('{"api_version":1,"action":"snapshot","body":{"query":"DELETE"}}', "INVALID_ENVELOPE"),
        ('{"api_version":1,"action":"transfer"}', "UNSUPPORTED_ACTION"),
        ('{"api_version":1,"action":"snapshot","body":[]}', "INVALID_ENVELOPE"),
        (
            '{"api_version":1,"action":"snapshot","body":{"search":"' + "x" * 33_000 + '"}}',
            "INVALID_ENVELOPE",
        ),
    ],
    ids=[
        "missing-version",
        "array",
        "bool-version",
        "future-version",
        "duplicate-key",
        "nan",
        "bool-page",
        "float-page",
        "oversized-page",
        "unknown-key",
        "unknown-action",
        "array-body",
        "oversized-request",
    ],
)
def test_malformed_json_never_changes_ledger(mobile: MobileLedger, raw: str, code: str) -> None:
    before = digest(mobile)
    result = json.loads(mobile.call(raw))
    assert result["error"]["code"] == code
    assert digest(mobile) == before


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("amount", 25.5, "INVALID_ENVELOPE"),
        ("amount", "25.001", "AMOUNT_PRECISION"),
        ("amount", "0.00", "INVALID_AMOUNT"),
        ("amount", "-1", "INVALID_AMOUNT"),
        ("amount", "1000000000000.00", "AMOUNT_OUT_OF_RANGE"),
        ("occurred_on", "2026-10-05", "FUTURE_DATE"),
        ("occurred_on", "20261004", "INVALID_DATE"),
        ("category_id", str(uuid4()), "ENTITY_NOT_FOUND"),
        ("currency_code", "USD", "INVALID_ENVELOPE"),
    ],
)
def test_invalid_confirmation_rolls_back(
    mobile: MobileLedger, field: str, value: object, code: str
) -> None:
    account(mobile)
    body = record_body(mobile)
    body["fields"][field] = value
    before = digest(mobile)
    result = invoke(mobile, "record", body)
    assert result["error"]["code"] == code
    assert digest(mobile) == before


def test_create_book_retry_and_filter(mobile: MobileLedger) -> None:
    body = {"request_id": str(uuid4()), "name": "旅行账本"}
    assert invoke(mobile, "create_book", body)["ok"]
    assert invoke(mobile, "create_book", body)["data"]["replayed"] is True
    assert len(invoke(mobile, "snapshot")["data"]["books"]) == 2


def test_relative_data_directory_refused() -> None:
    with pytest.raises(LedgerError, match="INVALID_DATA_DIRECTORY"):
        MobileLedger("relative-data")


def test_fresh_process_imports_no_qt_or_desktop_dependencies(tmp_path: Path) -> None:
    script = (
        "import sys; from openledger.mobile.bridge import MobileLedger; "
        "mobile=MobileLedger(sys.argv[1]); "
        "assert not any(name.startswith(('PySide6', 'openpyxl')) for name in sys.modules); "
        'print(mobile.call(\'{"api_version":1,"action":"snapshot"}\'))'
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "isolated")],
        text=True,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    assert json.loads(result.stdout)["ok"] is True
