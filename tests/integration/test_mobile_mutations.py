"""Real mobile maintenance operations must preserve balances and retry semantics."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from openledger.domain.errors import LedgerError
from openledger.mobile.bridge import MobileLedger
from openledger.mobile.mutations import signed_amount
from tests.integration.test_mobile_bridge import NOW, account, digest, invoke, record_body


def mutate(
    mobile: MobileLedger, command: str, payload: dict[str, Any], request_id: str | None = None
) -> dict[str, Any]:
    return invoke(
        mobile,
        "mutate",
        {"request_id": request_id or str(uuid4()), "command": command, "payload": payload},
    )


def test_edit_delete_restore_versions_and_retries(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    body = record_body(mobile)
    identifier = invoke(mobile, "record", body)["data"]["data"]["id"]
    detail = invoke(mobile, "transaction_detail", {"id": identifier})["data"]
    fields = {**body["fields"], "amount": "30.00", "time_zone": "Asia/Shanghai"}
    update = {"id": identifier, "expected_version": detail["version"], "fields": fields}
    request = str(uuid4())
    assert mutate(mobile, "transaction.update.v1", update, request)["ok"]
    assert mutate(mobile, "transaction.update.v1", update, request)["data"]["replayed"]
    assert mutate(mobile, "transaction.update.v1", update)["error"]["code"] == "VERSION_CONFLICT"
    detail = invoke(mobile, "transaction_detail", {"id": identifier})["data"]
    assert mutate(
        mobile, "transaction.delete.v1", {"id": identifier, "expected_version": detail["version"]}
    )["ok"]
    snapshot = invoke(mobile, "snapshot")["data"]
    assert snapshot["overview"]["total_assets_minor"] == 100000
    assert snapshot["transactions"]["total"] == 1
    assert (
        invoke(mobile, "snapshot", {"include_deleted": True})["data"]["transactions"]["total"] == 2
    )
    detail = invoke(mobile, "transaction_detail", {"id": identifier})["data"]
    assert mutate(
        mobile, "transaction.restore.v1", {"id": identifier, "expected_version": detail["version"]}
    )["ok"]
    assert invoke(mobile, "snapshot")["data"]["overview"]["total_assets_minor"] == 97000


def test_transfer_refund_and_failed_dependency_are_atomic(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    first = account(mobile)
    second = invoke(
        mobile,
        "create_account",
        {
            "request_id": str(uuid4()),
            "name": "合成银行卡",
            "account_type": "bank",
            "opening_amount": "0",
            "balance_start_on": "2026-01-01",
        },
    )["data"]["data"]["id"]
    fields = {
        "from_account_id": first,
        "to_account_id": second,
        "amount": "100.00",
        "occurred_on": "2026-10-04",
        "time_zone": "Asia/Shanghai",
    }
    assert mutate(mobile, "transfer.record.v1", {"id": str(uuid4()), "fields": fields})["ok"]
    overview = invoke(mobile, "snapshot")["data"]["overview"]
    assert overview["total_assets_minor"] == 100000 and overview["expense_minor"] == 0
    assert overview["balances"][second] == 10000
    identifier = invoke(mobile, "record", record_body(mobile))["data"]["data"]["id"]
    refund = {
        "original_transaction_id": identifier,
        "account_id": second,
        "amount": "10.00",
        "occurred_on": "2026-10-04",
        "time_zone": "Asia/Shanghai",
    }
    assert mutate(mobile, "refund.record.v1", {"id": str(uuid4()), "fields": refund})["ok"]
    assert invoke(mobile, "snapshot")["data"]["overview"]["expense_minor"] == 1500
    before = digest(mobile)
    detail = invoke(mobile, "transaction_detail", {"id": identifier})["data"]
    assert (
        mutate(
            mobile,
            "transaction.delete.v1",
            {"id": identifier, "expected_version": detail["version"]},
        )["error"]["code"]
        == "ACTIVE_REFUNDS_BLOCK_OPERATION"
    )
    assert not mutate(
        mobile, "refund.record.v1", {"id": str(uuid4()), "fields": {**refund, "amount": "16"}}
    )["ok"]
    assert digest(mobile) == before


def test_catalogs_tags_binding_archive_and_balance_commands(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    identifier = account(mobile)
    tag_id = str(uuid4())
    assert mutate(mobile, "tag.create.v1", {"id": tag_id, "name": "咖啡", "color": "#256F62"})["ok"]
    body = record_body(mobile)
    body["fields"]["tag_ids"] = [tag_id]
    assert invoke(mobile, "record", body)["ok"]
    assert invoke(mobile, "snapshot", {"tag_id": tag_id})["data"]["transactions"]["total"] == 1
    detail = invoke(mobile, "account_detail", {"id": identifier})["data"]
    assert detail["opening_balance_minor"] == 100000
    assert mutate(
        mobile,
        "account.opening.set.v1",
        {
            "account_id": identifier,
            "expected_account_version": detail["version"],
            "balance_start_on": "2026-01-01",
            "opening_amount": "500",
        },
    )["ok"]
    assert invoke(mobile, "snapshot")["data"]["overview"]["total_assets_minor"] == 47500
    assert mutate(
        mobile,
        "account.adjust.v1",
        {
            "account_id": identifier,
            "target_balance": "450",
            "occurred_on": "2026-10-04",
            "time_zone": "Asia/Shanghai",
            "reason": "合成校准",
        },
    )["ok"]
    overview = invoke(mobile, "snapshot")["data"]["overview"]
    assert overview["total_assets_minor"] == 45000 and overview["expense_minor"] == 2500
    tag = invoke(mobile, "snapshot")["data"]["tags"][0]
    assert mutate(
        mobile,
        "tag.archive.v1",
        {"id": tag_id, "expected_version": tag["version"], "archived": True},
    )["ok"]
    assert invoke(mobile, "snapshot")["data"]["tags"] == []
    assert invoke(mobile, "snapshot", {"tag_id": tag_id})["data"]["transactions"]["total"] == 1


def test_mutation_dispatch_cannot_run_arbitrary_commands(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    before = digest(mobile)
    assert mutate(mobile, "database.drop.v1", {})["error"]["code"] == "UNSUPPORTED_COMMAND"
    assert digest(mobile) == before


@pytest.mark.parametrize("amount", ["0", "0.00", "-0.00", "-12.34"])
def test_signed_balance_exact_zero_and_negative(amount: str) -> None:
    assert signed_amount(amount) == (-1234 if amount == "-12.34" else 0)


@pytest.mark.parametrize("amount", [".0", "0.", "0.000", "--1", "NaN", "1e4"])
def test_balance_parser_rejects_ambiguous_amounts(amount: str) -> None:
    with pytest.raises(LedgerError):
        signed_amount(amount)
