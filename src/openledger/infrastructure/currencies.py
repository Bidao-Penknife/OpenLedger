"""Dated manual quotations and explicit valuations from one SQLite snapshot."""

import sqlite3
from datetime import date

from openledger.domain.currencies import convert_minor, currency, rate_fraction
from openledger.domain.errors import LedgerError

SETTINGS_ID = "ce8f77b2-0eb1-5c8b-9072-0d1fb6613491"


def display_currency(connection: sqlite3.Connection) -> str:
    """Read the persisted presentation currency without changing account units."""
    row = connection.execute(
        "SELECT display_currency FROM currency_settings WHERE id=?", (SETTINGS_ID,)
    ).fetchone()
    if row is None:
        raise LedgerError("INTEGRITY_FAILED")
    return currency(row[0]).code


class Valuation:
    """Cache effective historical CNY-per-unit rates inside the caller's read snapshot."""

    def __init__(self, connection: sqlite3.Connection, target: str) -> None:
        self.connection = connection
        self.target = currency(target).code
        self._cache: dict[tuple[str, str], str] = {}

    def quotation(self, code: str, day: date) -> str:
        currency(code)
        if code == "CNY":
            return "1"
        key = (code, day.isoformat())
        if key not in self._cache:
            row = self.connection.execute(
                "SELECT rate_text FROM exchange_rates WHERE currency_code=? AND effective_on<=? "
                "ORDER BY effective_on DESC LIMIT 1",
                key,
            ).fetchone()
            if row is None:
                raise LedgerError(
                    "EXCHANGE_RATE_MISSING", f"缺少 {code} 在 {key[1]} 或之前的手动汇率。"
                )
            rate_fraction(row[0])
            self._cache[key] = str(row[0])
        return self._cache[key]

    def convert(self, value: int, code: str, day: date) -> int:
        """Same-currency and zero balances need no quotation; other cases never guess."""
        currency(code)
        if code == self.target or value == 0:
            return value
        return convert_minor(
            value, code, self.target, self.quotation(code, day), self.quotation(self.target, day)
        )
