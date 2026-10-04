"""Test branch-sensitive WAL eligibility and the real runtime feature probe."""

import pytest

from openledger.infrastructure.runtime import sqlite_wal_supported, verify_sqlite_capabilities


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("3.44.5", False),
        ("3.44.6", True),
        ("3.44.7", True),
        ("3.45.0", False),
        ("3.45.3", False),
        ("3.50.6", False),
        ("3.50.7", True),
        ("3.51.0", False),
        ("3.51.2", False),
        ("3.51.3", True),
        ("3.52.0", True),
        ("custom", False),
        ("3.51.3-custom", False),
    ],
)
def test_wal_policy(version: str, supported: bool) -> None:
    assert sqlite_wal_supported(version) is supported


def test_sqlite_supports_target_schema_features() -> None:
    verify_sqlite_capabilities()
