"""Durable, audited capture inbox; only explicit confirmation changes money."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast
from uuid import NAMESPACE_URL, uuid5

from openledger.domain.errors import LedgerError

if TYPE_CHECKING:
    from openledger.infrastructure.ledger import LedgerService, _Work


def mutate(
    ledger: LedgerService, work: _Work, action: str, payload: dict[str, object]
) -> dict[str, object]:
    """Deduplicate source events and atomically confirm one transaction plus image."""
    from openledger.infrastructure.ledger import _id, _keys, _row, _snapshot

    connection = work.connection
    if action == "stage":
        _keys(
            payload,
            {
                "source_kind",
                "source_key",
                "source_label",
                "text",
                "suggested_json",
                "image_relative_path",
                "image_size_bytes",
                "image_sha256",
            },
        )
        source = payload.get("source_kind")
        key = payload.get("source_key")
        if (
            source not in {"notification", "ocr", "voice"}
            or not isinstance(key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", key)
        ):
            raise LedgerError("INVALID_ENVELOPE")
        text = ledger._text(payload, "text", 4000, required=True)
        label = ledger._text(payload, "source_label", 160, required=True)
        suggested = payload.get("suggested_json")
        if not isinstance(suggested, str) or len(suggested) > 16000:
            raise LedgerError("INVALID_ENVELOPE")
        try:
            if not isinstance(json.loads(suggested), dict):
                raise ValueError("Expected object")
        except (ValueError, TypeError) as error:
            raise LedgerError("INVALID_ENVELOPE") from error
        identifier = str(uuid5(NAMESPACE_URL, f"openledger:capture:{key}"))
        previous = connection.execute(
            "SELECT * FROM captured_inputs WHERE source_key=?", (key,)
        ).fetchone()
        if previous is not None and previous["source_kind"] != source:
            raise LedgerError("IDEMPOTENCY_KEY_REUSED")
        if previous is not None and previous["state"] != "pending":
            return {"id": previous["id"], "duplicate": True, "state": previous["state"]}
        values = {
            "source_label": label,
            "text": text,
            "suggested_json": suggested,
            "image_relative_path": payload.get("image_relative_path"),
            "image_size_bytes": payload.get("image_size_bytes"),
            "image_sha256": payload.get("image_sha256"),
        }
        relative, size, digest = (
            values[k] for k in ("image_relative_path", "image_size_bytes", "image_sha256")
        )
        if relative is not None:
            if (
                source != "ocr"
                or not isinstance(relative, str)
                or not re.fullmatch(r"[0-9a-f-]{36}\.(png|jpg|webp)", relative)
                or type(size) is not int
                or not 1 <= size <= 20 * 1024 * 1024
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise LedgerError("INVALID_ENVELOPE")
        elif size is not None or digest is not None:
            raise LedgerError("INVALID_ENVELOPE")
        if previous is None:
            connection.execute(
                "INSERT INTO captured_inputs(id,source_kind,source_key,source_label,text,"
                "suggested_json,"
                "image_relative_path,image_size_bytes,image_sha256,"
                "created_at_utc,updated_at_utc) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    source,
                    key,
                    label,
                    text,
                    suggested,
                    relative,
                    size,
                    digest,
                    work.timestamp,
                    work.timestamp,
                ),
            )
            work.journal("capture", identifier, "create", None)
        elif any(previous[k] != value for k, value in values.items()):
            before = _snapshot(connection, "capture", identifier)
            ledger._update_row(work, "capture", identifier, values)
            work.journal("capture", identifier, "update", before)
        return {"id": identifier, "duplicate": previous is not None, "state": "pending"}
    _keys(
        payload,
        {"id", "expected_version", "fields"} if action == "record" else {"id", "expected_version"},
    )
    identifier = _id(payload)
    row = _row(connection, "capture", identifier)
    ledger._version(row, payload)
    if action in {"ignore", "restore"}:
        if row["state"] == "saved":
            raise LedgerError("CAPTURE_ALREADY_SAVED")
        desired = "ignored" if action == "ignore" else "pending"
        if desired != row["state"]:
            before = _snapshot(connection, "capture", identifier)
            ledger._update_row(work, "capture", identifier, {"state": desired})
            work.journal("capture", identifier, "update", before)
        return {"id": identifier, "state": desired}
    if action != "record":
        raise LedgerError("INVALID_ENVELOPE")
    if row["state"] != "pending":
        raise LedgerError(
            "CAPTURE_ALREADY_SAVED" if row["state"] == "saved" else "CAPTURE_NOT_PENDING"
        )
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise LedgerError("INVALID_ENVELOPE")
    kind = fields.get("kind")
    entity = {
        "income": "transaction",
        "expense": "transaction",
        "transfer": "transfer",
        "expense_refund": "refund",
    }.get(cast(str, kind))
    if entity is None:
        raise LedgerError("INVALID_ENVELOPE")
    fields = {**fields, "source": "local_rule", "source_text": row["text"]}
    if entity != "transaction":
        fields.pop("kind", None)
    transaction_id = str(uuid5(NAMESPACE_URL, f"openledger:capture:transaction:{identifier}"))
    ledger._financial(work, entity, "record", {"id": transaction_id, "fields": fields})
    if row["image_relative_path"]:
        path = row["image_relative_path"]
        attachment_id = path.rsplit(".", 1)[0]
        mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[
            path.rsplit(".", 1)[1]
        ]
        ledger._attachment(
            work,
            "create",
            {
                "id": attachment_id,
                "transaction_id": transaction_id,
                "expected_transaction_version": 1,
                "relative_path": path,
                "original_file_name": path,
                "mime_type": mime,
                "size_bytes": row["image_size_bytes"],
                "sha256": row["image_sha256"],
            },
        )
    before = _snapshot(connection, "capture", identifier)
    ledger._update_row(
        work, "capture", identifier, {"state": "saved", "transaction_id": transaction_id}
    )
    work.journal("capture", identifier, "update", before)
    return {"id": transaction_id, "capture_id": identifier, "state": "saved"}
