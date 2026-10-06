"""Images are bounded, audited, recoverable and portable without changing money."""

import base64
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.mobile.bridge import MobileLedger
from tests.integration.test_mobile_bridge import NOW, account, digest, invoke, record_body

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK3sAAAAASUVORK5CYII="
)


def fixture(tmp_path: Path) -> tuple[MobileLedger, str]:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    transaction = invoke(mobile, "record", record_body(mobile))["data"]["data"]["id"]
    (mobile.files.staging / "receipt.png").write_bytes(PNG)
    return mobile, transaction


def prepare(mobile: MobileLedger, transaction: str) -> dict[str, object]:
    version = invoke(mobile, "transaction_detail", {"id": transaction})["data"]["version"]
    reply = invoke(
        mobile,
        "attachment_prepare",
        {
            "filename": "receipt.png",
            "transaction_id": transaction,
            "expected_transaction_version": version,
        },
    )
    assert reply["ok"], reply
    return dict(reply["data"])


def test_attach_reopen_retry_delete_restore_and_portable_backup(tmp_path: Path) -> None:
    mobile, transaction = fixture(tmp_path)
    before = digest(mobile)
    metadata = prepare(mobile, transaction)
    assert digest(mobile) == before
    body = {"request_id": str(uuid4()), "payload": metadata}
    mobile = MobileLedger(str(mobile.files.directory), clock=lambda: NOW)
    assert invoke(mobile, "attachment_commit", body)["ok"]
    assert invoke(mobile, "attachment_commit", body)["data"]["replayed"]
    row = invoke(mobile, "transaction_detail", {"id": transaction})["data"]["attachments"][0]
    viewed = invoke(mobile, "attachment_view", {"id": row["id"]})
    assert (mobile.files.staging / viewed["data"]["filename"]).read_bytes() == PNG
    for action in ("attachment_delete", "attachment_restore"):
        assert invoke(
            mobile,
            action,
            {"request_id": str(uuid4()), "id": row["id"], "expected_version": row["version"]},
        )["ok"]
        row = invoke(mobile, "transaction_detail", {"id": transaction})["data"]["attachments"][0]
    assert row["version"] == 3 and row["deleted_at_utc"] is None
    assert invoke(mobile, "snapshot")["data"]["overview"]["total_assets_minor"] == 97500
    assert invoke(mobile, "backup_create", {"filename": "images.olbackup"})["ok"]
    restored = invoke(
        mobile, "backup_restore", {"filename": "images.olbackup", "request_id": str(uuid4())}
    )
    assert restored["ok"], restored
    other = MobileLedger(
        str(mobile.files.directory.parent / restored["data"]["directory"]), clock=lambda: NOW
    )
    assert invoke(other, "attachment_view", {"id": row["id"]})["ok"]


@pytest.mark.parametrize(
    "content",
    [b"", b"not an image", b"<svg></svg>", b"x" * (20 * 1024 * 1024 + 1)],
    ids=["empty", "text", "svg", "oversize"],
)
def test_invalid_image_does_not_write_metadata(tmp_path: Path, content: bytes) -> None:
    mobile, transaction = fixture(tmp_path)
    (mobile.files.staging / "receipt.png").write_bytes(content)
    before = digest(mobile)
    assert not invoke(
        mobile,
        "attachment_prepare",
        {
            "filename": "receipt.png",
            "transaction_id": transaction,
            "expected_transaction_version": 1,
        },
    )["ok"]
    assert digest(mobile) == before


def test_changed_image_path_escape_and_failed_audit_are_atomic(tmp_path: Path) -> None:
    mobile, transaction = fixture(tmp_path)
    metadata = prepare(mobile, transaction)
    before = digest(mobile)
    body = {"request_id": str(uuid4()), "payload": metadata}
    path = mobile.files.directory / "attachments" / str(metadata["relative_path"])
    path.write_bytes(PNG + b"changed")
    assert invoke(mobile, "attachment_commit", body)["error"]["code"] == "ATTACHMENT_CHANGED"
    assert not invoke(
        mobile,
        "attachment_commit",
        {**body, "payload": {**metadata, "relative_path": "../receipt.png"}},
    )["ok"]
    path.write_bytes(PNG)

    def fail(stage: str) -> None:
        if stage == "after_audit":
            raise OSError("Synthetic full disk")

    mobile.ledger._fault = fail
    assert not invoke(mobile, "attachment_commit", body)["ok"]
    assert digest(mobile) == before
    mobile.ledger._fault = lambda _stage: None
    assert invoke(mobile, "attachment_commit", body)["ok"]
