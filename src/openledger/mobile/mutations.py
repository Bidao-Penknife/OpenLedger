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
    "rate.set.v1",
    "currency.set.v1",
}


def signed_amount(value: object, currency_code: str = "CNY") -> int:
    """Parse explicit zero/signed display amounts without floating point."""
    if not isinstance(value, str) or len(value) > 40:
        raise LedgerError("INVALID_ENVELOPE")
    text = value.strip()
    from openledger.domain.currencies import currency

    precision = currency(currency_code).digits
    if re.fullmatch(r"-?0+(?:\.0{1," + str(max(precision, 1)) + r"})?", text):
        if "." in text and precision == 0:
            raise LedgerError("AMOUNT_PRECISION")
        return 0
    return validate_minor(
        -parse_amount(text[1:], currency_code)
        if text.startswith("-")
        else parse_amount(text, currency_code),
        signed=True,
        allow_zero=True,
    )


class MobileMutations:
    """Adapt decimal display text, while core commands enforce references and versions."""

    def __init__(self, ledger: LedgerService) -> None:
        self.ledger = ledger

    def account_currency(self, identifier: object) -> str:
        """Resolve an account's unit; archived accounts retain the same unit."""
        return next(
            (
                str(row["currency_code"])
                for row in self.ledger.entities("account", include_archived=True)
                if row["id"] == identifier
            ),
            "CNY",
        )

    def fields(self, value: dict[str, Any]) -> dict[str, Any]:
        """Translate display text to exact native minor units before confirmation."""
        fields = dict(value)
        code = self.account_currency(fields.get("from_account_id") or fields.get("account_id"))
        if fields.get("currency_code", code) != code:
            raise LedgerError("CURRENCY_MISMATCH")
        fields["currency_code"] = code
        for display, stored, unit in (
            ("amount", "amount_minor", code),
            ("to_amount", "to_amount_minor", self.account_currency(fields.get("to_account_id"))),
        ):
            if display in fields:
                if stored in fields or not isinstance(fields[display], str):
                    raise LedgerError("INVALID_ENVELOPE")
                fields[stored] = parse_amount(fields.pop(display), unit)
        return fields

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
            payload["fields"] = self.fields(payload["fields"])
        for display, stored in (
            ("opening_amount", "opening_balance_minor"),
            ("target_balance", "target_balance_minor"),
        ):
            if display in payload:
                if stored in payload:
                    raise LedgerError("INVALID_ENVELOPE")
                code = str(payload.get("currency_code") or self.account_currency(payload.get("id")))
                payload[stored] = signed_amount(payload.pop(display), code)
        return asdict(self.ledger.execute(request_id, command, payload))
