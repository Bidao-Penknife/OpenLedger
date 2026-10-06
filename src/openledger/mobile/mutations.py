"""Allowlisted mobile commands reuse the audited desktop ledger writer."""

import re
from dataclasses import asdict
from typing import Any

from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount, validate_minor
from openledger.infrastructure.ledger import LedgerService

COMMANDS = {
    f"{entity}.{action}.v1"
    for entity in ("book", "account", "category", "tag", "payment_method")
    for action in ("create", "update", "archive")
} | {
    "transaction.update.v1",
    "transaction.delete.v1",
    "transaction.restore.v1",
    "refund.record.v1",
    "refund.update.v1",
    "transfer.record.v1",
    "transfer.update.v1",
    "book.default.set.v1",
    "account.default.set.v1",
    "account.opening.set.v1",
    "account.adjust.v1",
    "adjustment.metadata.update.v1",
    "import.revert.v1",
}


def signed_amount(value: object) -> int:
    """Parse explicit zero/signed display amounts without floating point."""
    if not isinstance(value, str) or len(value) > 40:
        raise LedgerError("INVALID_ENVELOPE")
    text = value.strip()
    if re.fullmatch(r"-?0+(?:\.0{1,2})?", text):
        return 0
    return validate_minor(
        -parse_amount(text[1:]) if text.startswith("-") else parse_amount(text),
        signed=True,
        allow_zero=True,
    )


class MobileMutations:
    """Adapt decimal display text, while core commands enforce references and versions."""

    def __init__(self, ledger: LedgerService) -> None:
        self.ledger = ledger

    def execute(self, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"request_id", "command", "payload"}:
            raise LedgerError("INVALID_ENVELOPE")
        command = body["command"]
        request_id = body["request_id"]
        payload = body["payload"]
        if not isinstance(command, str) or command not in COMMANDS:
            raise LedgerError("UNSUPPORTED_COMMAND")
        if not isinstance(request_id, str) or not isinstance(payload, dict):
            raise LedgerError("INVALID_ENVELOPE")
        payload = dict(payload)
        if "fields" in payload:
            if not isinstance(payload["fields"], dict):
                raise LedgerError("INVALID_ENVELOPE")
            fields = dict(payload["fields"])
            if "amount" in fields:
                if "amount_minor" in fields or not isinstance(fields["amount"], str):
                    raise LedgerError("INVALID_ENVELOPE")
                fields["amount_minor"] = parse_amount(fields.pop("amount"))
            payload["fields"] = fields
        for display, stored in (
            ("opening_amount", "opening_balance_minor"),
            ("target_balance", "target_balance_minor"),
        ):
            if display in payload:
                if stored in payload:
                    raise LedgerError("INVALID_ENVELOPE")
                payload[stored] = signed_amount(payload.pop(display))
        return asdict(self.ledger.execute(request_id, command, payload))
