"""Conservative SQLite capabilities and journal policy for local persistence."""

import re
import sqlite3
from contextlib import closing


def sqlite_wal_supported(version: str) -> bool:
    """Allow official WAL-reset fixes, including the documented backport branches."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return False
    parts = tuple(int(part) for part in version.split("."))
    return (
        parts >= (3, 51, 3) or (3, 44, 6) <= parts < (3, 45, 0) or (3, 50, 7) <= parts < (3, 51, 0)
    )


def verify_sqlite_capabilities() -> None:
    """Probe required SQL features without creating a user's finance database."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE capability_probe(value INTEGER NOT NULL) STRICT")
        row = connection.execute("SELECT json_valid(?), json_type(?)", ("{}", "{}")).fetchone()
        if row != (1, "object"):
            raise RuntimeError("当前 SQLite 不支持必需的 JSON 函数。")
