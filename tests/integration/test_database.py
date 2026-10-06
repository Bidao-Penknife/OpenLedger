"""Exercise actual SQLite migration, isolation, corruption and rollback behavior."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.domain.errors import LedgerError
from openledger.infrastructure.database import APPLICATION_ID, CURRENT_SCHEMA_VERSION, Database
from openledger.infrastructure.database import database as database_module
from openledger.infrastructure.database.database import Migration

pytestmark = pytest.mark.integration
STAMP = "2026-10-02T00:00:00.000Z"


@pytest.fixture
def database(tmp_path: Path) -> Database:
    result = Database(tmp_path / "中文 空间" / "ledger.sqlite3", timeout=0.05)
    result.initialize()
    return result


def _insert_book(connection: sqlite3.Connection, name: str = "测试账本") -> str:
    identifier = str(uuid4())
    connection.execute(
        "INSERT INTO books(id, name, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?)",
        (identifier, name, STAMP, STAMP),
    )
    connection.execute(
        "UPDATE app_preferences SET default_book_id=? WHERE default_book_id IS NULL", (identifier,)
    )
    return identifier


def _new_revision(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    known = database_module._load_migrations()
    revision = Migration(len(known) + 1, sql, sha256(sql.encode("utf-8")).hexdigest())
    monkeypatch.setattr(database_module, "CURRENT_SCHEMA_VERSION", len(known) + 1)
    monkeypatch.setattr(database_module, "_load_migrations", lambda: (*known, revision))


def test_real_schema_has_expected_objects_and_no_finance_seed(database: Database) -> None:
    with database.read() as connection:
        objects = connection.execute(
            "SELECT type, count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' GROUP BY type"
        ).fetchall()
        assert dict(objects) == {"index": 29, "table": 19, "view": 2}
        assert connection.execute("SELECT count(*) FROM books").fetchone()[0] == 0
        assert tuple(connection.execute("SELECT * FROM app_preferences").fetchone()) == (
            1,
            None,
            None,
        )
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 50
        assert connection.execute("SELECT json_type('{}')").fetchone()[0] == "object"
        assert all(
            row[5] == 1 for row in connection.execute("PRAGMA table_list") if row[1] == "books"
        )


def test_existing_schema_initialize_preserves_file_bytes_and_does_not_backup(
    database: Database,
) -> None:
    with database.write() as connection:
        _insert_book(connection)
    before = database.path.read_bytes()
    Database(database.path).initialize()
    assert database.path.read_bytes() == before
    assert not (database.path.parent / "migration-backups").exists()


def test_connections_require_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(LedgerError) as failure:
        Database(path).connect()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert not path.exists()


def test_write_atomic_rollback_and_closed_connection(database: Database) -> None:
    connection: sqlite3.Connection
    with pytest.raises(RuntimeError, match="injected failure"), database.write() as connection:
        _insert_book(connection)
        raise RuntimeError("injected failure")
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_strict_type_violation_rolls_back_prior_mutation(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError), database.write() as connection:
        _insert_book(connection)
        connection.execute("UPDATE books SET sort_order = 'invalid integer'")
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 0


def test_foreign_keys_are_enforced_on_every_connection(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError), database.write() as connection:
        connection.execute("UPDATE app_preferences SET default_account_id = ?", (str(uuid4()),))
    with closing(database.connect()) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_read_only_snapshot_refuses_writes_and_blocks_uncommitted_changes(
    database: Database,
) -> None:
    other = Database(database.path, timeout=0.01)
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _insert_book(reader)
        with pytest.raises(LedgerError) as failure, other.write() as writer:
            _insert_book(writer)
        assert failure.value.code == "DATABASE_BUSY"
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 0
    with other.write() as writer:
        _insert_book(writer)
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 1


def test_cross_instance_writer_lock_reports_busy_and_recovers(database: Database) -> None:
    other = Database(database.path, timeout=0.01)
    with database.write() as first:
        _insert_book(first)
        with pytest.raises(LedgerError) as failure, other.write():
            pytest.fail("A second BEGIN IMMEDIATE must not succeed.")
        assert failure.value.code == "DATABASE_BUSY"
    with other.write() as second:
        _insert_book(second, "另一账本")
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 2


def test_thread_confined_connections_serialize_instance_writers(database: Database) -> None:
    def create(index: int) -> None:
        with database.write() as connection:
            _insert_book(connection, f"账本 {index}")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(create, range(12)))
    with database.read() as reader:
        assert reader.execute("SELECT count(*) FROM books").fetchone()[0] == 12


def test_unknown_existing_sqlite_database_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE private_record(value TEXT)")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        Database(path).initialize()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert path.read_bytes() == before


def test_non_sqlite_file_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "invalid.sqlite3"
    path.write_bytes(b"private data that is not a database")
    before = path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        Database(path).initialize()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["checksum", "missing_history", "missing_table", "version"])
def test_modified_metadata_or_schema_is_rejected_without_mutation(
    database: Database,
    damage: str,
) -> None:
    with database.write() as connection:
        if damage == "checksum":
            connection.execute("UPDATE schema_migrations SET sha256 = ?", ("0" * 64,))
        elif damage == "missing_history":
            connection.execute("DROP TABLE schema_migrations")
        elif damage == "missing_table":
            connection.execute("DROP TABLE tags")
        else:
            connection.execute("PRAGMA user_version = 0")
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert database.path.read_bytes() == before


def test_future_schema_version_is_rejected_without_mutation(database: Database) -> None:
    with database.write() as connection:
        connection.execute("PRAGMA user_version = 99")
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "DATABASE_VERSION_TOO_NEW"
    assert database.path.read_bytes() == before


def test_invalid_foreign_key_data_is_rejected(database: Database) -> None:
    with closing(sqlite3.connect(database.path, isolation_level=None)) as connection:
        connection.execute("UPDATE app_preferences SET default_book_id = ?", (str(uuid4()),))
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert database.path.read_bytes() == before


def test_failed_migration_preserves_schema_data_version_and_backup(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with database.write() as connection:
        _insert_book(connection)
    before = database.path.read_bytes()
    _new_revision(
        monkeypatch,
        "CREATE TABLE newly_added(value INTEGER) STRICT; "
        "INSERT INTO newly_added VALUES ('invalid');",
    )
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "MIGRATION_FAILED"
    assert database.path.read_bytes() == before
    with database.read() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT count(*) FROM books").fetchone()[0] == 1
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='newly_added'").fetchone()
            is None
        )
    snapshots = list((database.path.parent / "migration-backups").glob("*.sqlite3"))
    assert len(snapshots) == 1
    with closing(sqlite3.connect(snapshots[0])) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT count(*) FROM books").fetchone()[0] == 1


def test_successful_migration_advances_once_with_checked_backup(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _new_revision(monkeypatch, "CREATE TABLE newly_added(value INTEGER) STRICT;")
    database.initialize()
    database.initialize()
    with database.read() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION + 1
        assert [
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        ] == list(range(1, CURRENT_SCHEMA_VERSION + 2))
    assert len(list((database.path.parent / "migration-backups").glob("*.sqlite3"))) == 1


def test_incomplete_migration_is_rolled_back(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _new_revision(monkeypatch, "CREATE TABLE newly_added(value INTEGER) STRICT; SELECT 1")
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "MIGRATION_FAILED"
    assert database.path.read_bytes() == before


@pytest.mark.parametrize(
    "runner_owned_statement", ["COMMIT;", "ROLLBACK;", "PRAGMA journal_mode = WAL;"]
)
def test_migration_cannot_escape_runner_transaction(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    runner_owned_statement: str,
) -> None:
    _new_revision(
        monkeypatch,
        "CREATE TABLE newly_added(value INTEGER) STRICT; " + runner_owned_statement,
    )
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "MIGRATION_FAILED"
    assert database.path.read_bytes() == before


def test_migration_splitter_keeps_quoted_semicolons_and_trigger_body() -> None:
    statements = list(
        database_module._statements(
            "CREATE TABLE text_values(value TEXT); "
            "CREATE TRIGGER add_value AFTER INSERT ON text_values BEGIN "
            "SELECT ';'; SELECT 'x'; END; -- trailing comment"
        )
    )
    assert len(statements) == 2
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO text_values VALUES ('original')")


def test_unsupported_wal_database_is_refused_without_conversion(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(database.path, isolation_level=None)) as connection:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    monkeypatch.setattr(database_module, "sqlite_wal_supported", lambda version: False)
    before = database.path.read_bytes()
    with pytest.raises(LedgerError) as failure:
        database.initialize()
    assert failure.value.code == "INTEGRITY_FAILED"
    assert database.path.read_bytes() == before
    with closing(sqlite3.connect(database.path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_supported_runtime_preserves_existing_wal_mode(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(database.path, isolation_level=None)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
    monkeypatch.setattr(database_module, "sqlite_wal_supported", lambda version: True)
    database.initialize()
    with database.read() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_invalid_constructor_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        Database(Path("ledger.sqlite3"))
    with pytest.raises(ValueError, match="timeout"):
        Database(tmp_path / "ledger.sqlite3", timeout=float("inf"))
