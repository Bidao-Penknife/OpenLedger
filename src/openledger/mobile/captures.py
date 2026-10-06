"""Local notification/OCR/voice suggestions and a persistent confirmation inbox."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from openledger.application.parsing import LocalParser, _amounts
from openledger.domain.currencies import format_minor
from openledger.domain.errors import LedgerError
from openledger.domain.values import normalize_id
from openledger.mobile.attachments import _EXTENSIONS, MobileAttachments, _image

if TYPE_CHECKING:
    from openledger.mobile.bridge import MobileLedger


def suggestions(mobile: MobileLedger, text: str, timestamp: str | None) -> dict[str, Any]:
    """Keep every detected amount with evidence; never choose a debit silently."""
    request = mobile._parse_request({"text": text})
    if timestamp:
        try:
            instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant > mobile.clock():
                raise ValueError("Invalid instant")
            request = replace(
                request, reference_date=instant.astimezone(ZoneInfo(mobile.time_zone)).date()
            )
        except (ValueError, TypeError) as error:
            raise LedgerError("INVALID_DATE") from error
    result = asdict(LocalParser().parse(request))
    result["drafts"] = result["drafts"][:1]
    result["issues"] = result["issues"][:20]
    candidates = []
    code = result["drafts"][0]["currency_code"] if result["drafts"] else request.currency_code
    for amount in _amounts(text, code)[:40]:
        if amount.value is None:
            continue
        line_start = text.rfind("\n", 0, amount.span.start) + 1
        line_end = text.find("\n", amount.span.end)
        if line_end < 0:
            line_end = len(text)
        context = text[
            max(line_start, amount.span.start - 24) : min(line_end, amount.span.end + 12)
        ]
        actual = bool(
            re.search(r"实付|实收|实际支付|实际付款|付款金额|支付金额|合计支付|到账金额", context)
        )
        candidates.append(
            {
                "amount_minor": amount.value,
                "amount": format_minor(amount.value, code),
                "context": context,
                "actual": actual,
            }
        )
    candidates.sort(key=lambda item: not item["actual"])
    kind = (
        "expense_refund"
        if re.search(r"退款|退回|退货", text)
        else "transfer"
        if re.search(r"转账|转入|转出|转到", text)
        else "income"
        if re.search(r"收款成功|收入|已到账|已收款|收到.*元|实收", text)
        else "expense"
    )
    merchant_match = re.search(
        r"(?:商户|商家|店铺|付款给|支付给|收款方)\s*[:：]?\s*([^\n，,。；;]{1,80})", text
    )
    result.update(
        {
            "amount_candidates": candidates,
            "suggested_kind": kind,
            "suggested_merchant": merchant_match.group(1).strip() if merchant_match else "",
            "currency_code": code,
            "reference_date": request.reference_date.isoformat(),
        }
    )
    return result


class MobileCaptures:
    """Only app-owned files and audited core commands cross this boundary."""

    def __init__(self, mobile: MobileLedger) -> None:
        self.mobile = mobile
        self.ledger = mobile.ledger

    def dispatch(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        from openledger.mobile.bridge import _json_default, _keys, _text

        if action == "capture_stage":
            _keys(
                body,
                {
                    "request_id",
                    "source_kind",
                    "event_key",
                    "source_label",
                    "text",
                    "filename",
                    "timestamp",
                },
            )
            source = _text(body, "source_kind", maximum=20)
            if source not in {"notification", "ocr", "voice"}:
                raise LedgerError("INVALID_ENVELOPE")
            text = _text(body, "text", maximum=4000)
            if not text.strip():
                raise LedgerError("INVALID_TEXT")
            event = _text(body, "event_key", maximum=1024)
            label = _text(body, "source_label", maximum=160)
            stamp = body.get("timestamp")
            if stamp is not None and not isinstance(stamp, str):
                raise LedgerError("INVALID_ENVELOPE")
            suggested = suggestions(self.mobile, text, stamp)
            payload: dict[str, Any] = {
                "source_kind": source,
                "source_label": label,
                "text": text,
                "suggested_json": json.dumps(suggested, ensure_ascii=False, default=_json_default),
            }
            if source == "ocr":
                path = self.mobile.files.path(_text(body, "filename", maximum=120))
                mime, size, digest = _image(path)
                event = digest
                identifier = str(uuid5(NAMESPACE_URL, f"openledger:capture:image:{digest}"))
                relative = f"{identifier}.{_EXTENSIONS[mime]}"
                destination = MobileAttachments(self.ledger, self.mobile.files)._path(relative)
                if not destination.exists():
                    try:
                        with path.open("rb") as incoming, destination.open("xb") as output:
                            shutil.copyfileobj(incoming, output, 64 * 1024)
                            output.flush()
                            os.fsync(output.fileno())
                        if _image(destination) != (mime, size, digest):
                            raise LedgerError("ATTACHMENT_CHANGED")
                    except BaseException:
                        destination.unlink(missing_ok=True)
                        raise
                elif _image(destination) != (mime, size, digest):
                    raise LedgerError("ATTACHMENT_CHANGED")
                payload.update(
                    {
                        "image_relative_path": relative,
                        "image_size_bytes": size,
                        "image_sha256": digest,
                    }
                )
            elif "filename" in body:
                raise LedgerError("INVALID_ENVELOPE")
            kind_suffix = ":" + suggested["suggested_kind"] if source == "notification" else ""
            payload["source_key"] = hashlib.sha256(
                f"{source}:{event}{kind_suffix}".encode()
            ).hexdigest()
            return asdict(
                self.ledger.execute(_text(body, "request_id"), "capture.stage.v1", payload)
            )
        if action == "capture_list":
            _keys(body, {"state", "page"})
            state = body.get("state", "pending")
            page = body.get("page", 0)
            if (
                state not in {"pending", "saved", "ignored"}
                or type(page) is not int
                or not 0 <= page <= 40000
            ):
                raise LedgerError("INVALID_FILTER")
            with self.mobile.database.read() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM captured_inputs WHERE state=?", (state,)
                ).fetchone()[0]
                rows = [
                    self._row(dict(row))
                    for row in connection.execute(
                        "SELECT * FROM captured_inputs WHERE state=? "
                        "ORDER BY created_at_utc DESC,id LIMIT 25 OFFSET ?",
                        (state, page * 25),
                    )
                ]
            return {"rows": rows, "total": count, "page": page, "state": state}
        if action in {"capture_detail", "capture_view"}:
            _keys(body, {"id"})
            row = self._get(normalize_id(_text(body, "id")))
            if action == "capture_detail":
                return self._row(row)
            image_relative = row.get("image_relative_path")
            if not image_relative:
                raise LedgerError("ENTITY_NOT_FOUND")
            image_source = MobileAttachments(self.ledger, self.mobile.files)._path(image_relative)
            mime, size, digest = _image(image_source)
            if (size, digest) != (row["image_size_bytes"], row["image_sha256"]):
                raise LedgerError("ATTACHMENT_CHANGED")
            name = f"capture-{uuid4()}.{_EXTENSIONS[mime]}"
            with (
                image_source.open("rb") as incoming,
                self.mobile.files.path(name).open("xb") as output,
            ):
                shutil.copyfileobj(incoming, output, 64 * 1024)
            return {"filename": name, "mime_type": mime}
        if action in {"capture_ignore", "capture_restore", "capture_record"}:
            _keys(
                body,
                {"request_id", "id", "expected_version", "fields"}
                if action == "capture_record"
                else {"request_id", "id", "expected_version"},
            )
            payload = {key: value for key, value in body.items() if key != "request_id"}
            if action == "capture_record":
                if not isinstance(payload.get("fields"), dict):
                    raise LedgerError("INVALID_ENVELOPE")
                payload["fields"] = self.mobile.mutations.fields(payload["fields"])
                row = self._get(normalize_id(_text(body, "id")))
                if row["image_relative_path"]:
                    image = MobileAttachments(self.ledger, self.mobile.files)._path(
                        row["image_relative_path"]
                    )
                    _, size, digest = _image(image)
                    if (size, digest) != (row["image_size_bytes"], row["image_sha256"]):
                        raise LedgerError("ATTACHMENT_CHANGED")
            return asdict(
                self.ledger.execute(
                    _text(body, "request_id"), action.replace("_", ".") + ".v1", payload
                )
            )
        raise LedgerError("UNSUPPORTED_ACTION")

    def _get(self, identifier: str) -> dict[str, Any]:
        with self.mobile.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM captured_inputs WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise LedgerError("ENTITY_NOT_FOUND")
        return dict(row)

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        row["suggested"] = json.loads(row.pop("suggested_json"))
        return row
