"""Capture sources are drafts; confirmations and attachments are atomic and durable."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.mobile.bridge import MobileLedger
from tests.integration.test_mobile_attachments import PNG
from tests.integration.test_mobile_bridge import account, invoke, record_body

pytestmark = pytest.mark.integration


@pytest.fixture
def mobile(tmp_path: Path) -> MobileLedger:
    service = MobileLedger(
        str(tmp_path / "synthetic"), clock=lambda: datetime(2026, 10, 6, 12, tzinfo=UTC)
    )
    account(service)
    return service


def stage(
    mobile: MobileLedger, text: str, key: str = "payment-event-1", **extra: object
) -> dict[str, Any]:
    result = invoke(
        mobile,
        "capture_stage",
        {
            "request_id": str(uuid4()),
            "source_kind": "notification",
            "source_label": "合成微信通知",
            "event_key": key,
            "text": text,
            **extra,
        },
    )
    assert result["ok"], result
    return cast(dict[str, Any], result["data"]["data"])


def test_notification_update_deduplicates_but_separate_events_survive(mobile: MobileLedger) -> None:
    before = mobile.ledger.balances()
    first = stage(mobile, "支付成功，咖啡 25元")
    duplicate = stage(mobile, "支付成功，商户：咖啡店，实付25元")
    assert duplicate["id"] == first["id"] and duplicate["duplicate"]
    separate = stage(mobile, "支付成功，咖啡 25元", "payment-event-2")
    assert separate["id"] != first["id"]
    reopened = MobileLedger(str(mobile.database.path.parent.parent), clock=mobile.clock)
    result = invoke(reopened, "capture_list")
    assert result["data"]["total"] == 2
    assert mobile.ledger.balances() == before
    assert invoke(mobile, "capture_detail", {"id": first["id"]})["data"]["version"] == 2


def test_confirm_retry_and_later_notification_do_not_debit_twice(mobile: MobileLedger) -> None:
    item = stage(mobile, "昨天晚上咖啡花了25元，微信支付")
    fields = record_body(mobile)["fields"]
    body = {"request_id": str(uuid4()), "id": item["id"], "expected_version": 1, "fields": fields}
    result = invoke(mobile, "capture_record", body)
    assert result["ok"], result
    assert invoke(mobile, "capture_record", body)["data"]["replayed"]
    assert mobile.ledger.balances()[fields["account_id"]] == 97500
    assert stage(mobile, "支付成功，25元")["state"] == "saved"
    body["request_id"] = str(uuid4())
    body["expected_version"] = 2
    assert invoke(mobile, "capture_record", body)["error"]["code"] == "CAPTURE_ALREADY_SAVED"
    with mobile.database.read() as connection:
        validate_financial_integrity(connection)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("退款成功，实收25元", "expense_refund"),
        ("转账成功，转入100元", "transfer"),
        ("收款成功，收入88元", "income"),
    ],
)
def test_special_events_are_labelled_without_writing(
    mobile: MobileLedger, text: str, kind: str
) -> None:
    before = mobile.ledger.balances()
    item = stage(mobile, text)
    detail = invoke(mobile, "capture_detail", {"id": item["id"]})["data"]
    assert detail["suggested"]["suggested_kind"] == kind
    assert mobile.ledger.balances() == before


def test_receipt_keeps_all_amounts_and_voice_understands_chinese(mobile: MobileLedger) -> None:
    item = stage(mobile, "商户：合成餐厅\n原价128元\n优惠28元\n实付100元")
    detail = invoke(mobile, "capture_detail", {"id": item["id"]})["data"]
    values = {row["amount_minor"] for row in detail["suggested"]["amount_candidates"]}
    assert values == {12800, 2800, 10000}
    voice = invoke(
        mobile,
        "capture_stage",
        {
            "request_id": str(uuid4()),
            "source_kind": "voice",
            "source_label": "合成语音",
            "event_key": str(uuid4()),
            "text": "今天午饭花了三十五元，微信支付",
        },
    )
    assert voice["ok"], voice
    voice_id = voice["data"]["data"]["id"]
    amount = invoke(mobile, "capture_detail", {"id": voice_id})["data"]["suggested"][
        "amount_candidates"
    ][0]
    assert amount["amount_minor"] == 3500


def test_ignore_restore_and_stale_confirmation_leave_money_untouched(mobile: MobileLedger) -> None:
    item = stage(mobile, "咖啡25元")
    before = mobile.ledger.balances()
    body = {"request_id": str(uuid4()), "id": item["id"], "expected_version": 1}
    assert invoke(mobile, "capture_ignore", body)["ok"]
    record = {**body, "request_id": str(uuid4()), "fields": record_body(mobile)["fields"]}
    assert invoke(mobile, "capture_record", record)["error"]["code"] == "VERSION_CONFLICT"
    assert invoke(
        mobile, "capture_restore", {**body, "request_id": str(uuid4()), "expected_version": 2}
    )["ok"]
    assert mobile.ledger.balances() == before


def test_refund_update_of_same_notification_is_a_separate_review(mobile: MobileLedger) -> None:
    payment = stage(mobile, "支付成功，咖啡25元")
    refund = stage(mobile, "退款成功，实收25元")
    assert payment["id"] != refund["id"]
    assert invoke(mobile, "capture_list")["data"]["total"] == 2
    assert mobile.ledger.total_assets() == 100000


def test_pending_original_image_backup_and_failed_confirmation_are_atomic(
    mobile: MobileLedger,
) -> None:
    (mobile.files.staging / "receipt.png").write_bytes(PNG)
    result = invoke(
        mobile,
        "capture_stage",
        {
            "request_id": str(uuid4()),
            "source_kind": "ocr",
            "source_label": "合成收据",
            "event_key": "receipt",
            "text": "商户：合成餐厅，实付100元",
            "filename": "receipt.png",
        },
    )
    assert result["ok"], result
    item = result["data"]["data"]
    before = mobile.ledger.balances()
    fields = record_body(mobile)["fields"]
    fields["category_id"] = str(uuid4())  # Invalid live reference must roll back everything.
    result = invoke(
        mobile,
        "capture_record",
        {
            "request_id": str(uuid4()),
            "id": item["id"],
            "expected_version": 1,
            "fields": fields,
        },
    )
    assert not result["ok"]
    assert mobile.ledger.balances() == before
    detail = invoke(mobile, "capture_detail", {"id": item["id"]})["data"]
    assert detail["state"] == "pending" and detail["version"] == 1
    assert invoke(mobile, "backup_create", {"filename": "pending.olbackup"})["ok"]
    result = invoke(
        mobile,
        "backup_restore",
        {"filename": "pending.olbackup", "request_id": str(uuid4())},
    )
    assert result["ok"], result
    other = MobileLedger(
        str(mobile.files.directory.parent / result["data"]["directory"]), clock=mobile.clock
    )
    restored = invoke(other, "capture_view", {"id": item["id"]})
    assert restored["ok"], restored
    assert (other.files.staging / restored["data"]["filename"]).read_bytes() == PNG
    assert other.ledger.balances() == before


def test_merchant_payee_label_does_not_turn_payment_into_income(mobile: MobileLedger) -> None:
    item = stage(mobile, "支付成功，收款方：合成餐厅，实付100元")
    assert (
        invoke(mobile, "capture_detail", {"id": item["id"]})["data"]["suggested"]["suggested_kind"]
        == "expense"
    )
