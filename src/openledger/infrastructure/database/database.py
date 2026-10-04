"""Protect local SQLite databases with checked migrations and short transactions."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
from math import isfinite
from pathlib import Path
from threading import RLock
from uuid import uuid4

from openledger.domain.errors import LedgerError
from openledger.infrastructure.runtime import sqlite_wal_supported, verify_sqlite_capabilities

APPLICATION_ID = 0x4F4C4447
CURRENT_SCHEMA_VERSION = 1
_MIGRATION_TABLE_SQL = (
    "CREATE TABLE schema_migrations ("
    "version INTEGER PRIMARY KEY CHECK (version >= 1), "
    "sha256 TEXT NOT NULL CHECK (length(sha256) = 64 "
    "AND sha256 NOT GLOB '*[^0-9a-f]*'), "
    "applied_at_utc TEXT NOT NULL) STRICT"
)


@dataclass(frozen=True)
class Migration:
    """A UTF-8 SQL revision whose exact bytes identify the applied migration."""

    version: int
    sql: str
    digest: str


def _load_migrations() -> tuple[Migration, ...]:
    resource_root = files("openledger.resources").joinpath("migrations")
    migrations: list[Migration] = []
    for version in range(1, CURRENT_SCHEMA_VERSION + 1):
        content = resource_root.joinpath(f"{version:04d}.sql").read_bytes()
        migrations.append(Migration(version, content.decode("utf-8"), sha256(content).hexdigest()))
    return tuple(migrations)


def _statements(sql: str) -> Iterator[str]:
    """Split SQL safely, including semicolons inside strings and trigger bodies."""
    pending = ""
    for character in sql:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        # A trailing comment is harmless; all executable statements require a semicolon.
        remaining = re.sub(r"/\*.*?\*/|--[^\n]*(?:\n|$)", "", pending, flags=re.DOTALL)
        if remaining.strip():
            raise ValueError("Migration contains an incomplete SQL statement.")


def _is_busy(error: sqlite3.Error) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _migration_authorizer(
    action: int,
    first: str | None,
    second: str | None,
    database_name: str | None,
    trigger_name: str | None,
) -> int:
    """Keep transaction, connection and version control exclusively in the runner."""
    if action in (
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    ):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and second is not None:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class Database:
    """Own a database path; each operation uses a new, thread-confined connection."""

    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        if not path.is_absolute():
            raise ValueError("Database path must be absolute.")
        if not isfinite(timeout) or timeout < 0:
            raise ValueError("Database timeout must be a finite nonnegative number.")
        self.path = path
        self.timeout = timeout
        self._lock = RLock()

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        """Return a configured connection; the caller must close it explicitly."""
        connection: sqlite3.Connection | None = None
        try:
            uri = self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rw")
            connection = sqlite3.connect(uri, uri=True, timeout=self.timeout, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise LedgerError("INTEGRITY_FAILED", "数据库无法启用外键约束。")
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if journal == "wal" and not sqlite_wal_supported(sqlite3.sqlite_version):
                raise LedgerError(
                    "INTEGRITY_FAILED",
                    "当前 SQLite 不能安全打开 WAL 数据库，请使用受支持的运行库。",
                )
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            if _is_busy(error):
                raise LedgerError("DATABASE_BUSY", "数据库正在使用，请稍后重试。") from error
            raise LedgerError("INTEGRITY_FAILED", "无法打开有效的 OpenLedger 数据库。") from error
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    def initialize(self) -> None:
        """Create an empty ledger or validate and atomically upgrade a known ledger."""
        with self._lock:
            try:
                verify_sqlite_capabilities()
                migrations = _load_migrations()
                self._check_migration_registry(migrations)
                is_new = not self.path.exists() or self.path.stat().st_size == 0
                if self.path.exists() and not self.path.is_file():
                    raise LedgerError("INTEGRITY_FAILED", "数据库路径不是文件。")
                if is_new:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    # SQLite's new file defaults to DELETE. No unvalidated WAL switch is made.
                    with closing(sqlite3.connect(self.path, isolation_level=None)) as initial:
                        initial.execute("PRAGMA journal_mode = DELETE")
                with closing(self.connect(read_only=True)) as connection:
                    version = self._validate(connection, migrations, allow_empty=is_new)
                if version == CURRENT_SCHEMA_VERSION:
                    return
                with closing(self.connect()) as connection:
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        # Another process may have completed the migration since the read check.
                        version = self._validate(connection, migrations, allow_empty=is_new)
                        if version == CURRENT_SCHEMA_VERSION:
                            connection.rollback()
                            return
                        if version:
                            self._snapshot_before_migration()
                        self._migrate(connection, migrations, version)
                        connection.commit()
                    except BaseException:
                        if connection.in_transaction:
                            connection.rollback()
                        raise
            except LedgerError:
                raise
            except sqlite3.Error as error:
                if _is_busy(error):
                    raise LedgerError("DATABASE_BUSY", "数据库正在使用，请稍后重试。") from error
                raise LedgerError("MIGRATION_FAILED", "数据库迁移失败，原有数据已保留。") from error
            except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                raise LedgerError("MIGRATION_FAILED", "无法读取或执行数据库迁移。") from error

    @staticmethod
    def _check_migration_registry(migrations: tuple[Migration, ...]) -> None:
        if tuple(migration.version for migration in migrations) != tuple(
            range(1, CURRENT_SCHEMA_VERSION + 1)
        ):
            raise LedgerError("MIGRATION_FAILED", "数据库迁移版本不连续。")
        for migration in migrations:
            if sha256(migration.sql.encode("utf-8")).hexdigest() != migration.digest:
                raise LedgerError("MIGRATION_FAILED", "数据库迁移资源摘要不一致。")

    @staticmethod
    def _validate(
        connection: sqlite3.Connection,
        migrations: tuple[Migration, ...],
        *,
        allow_empty: bool = False,
    ) -> int:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = connection.execute(
            "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if allow_empty and not objects and application_id == 0 and version == 0:
            return 0
        if application_id != APPLICATION_ID:
            raise LedgerError("INTEGRITY_FAILED", "文件不是 OpenLedger 数据库，已拒绝修改。")
        if version > CURRENT_SCHEMA_VERSION:
            raise LedgerError("DATABASE_VERSION_TOO_NEW", "数据库由更高版本创建，请升级软件。")
        if version < 1 or "schema_migrations" not in {row[0] for row in objects}:
            raise LedgerError("INTEGRITY_FAILED", "数据库迁移记录缺失。")
        try:
            records = connection.execute(
                "SELECT version, sha256 FROM schema_migrations ORDER BY version"
            ).fetchall()
        except sqlite3.Error as error:
            raise LedgerError("INTEGRITY_FAILED", "数据库迁移记录无法读取。") from error
        if [row[0] for row in records] != list(range(1, version + 1)):
            raise LedgerError("INTEGRITY_FAILED", "数据库版本与迁移记录不一致。")
        for row, migration in zip(records, migrations, strict=False):
            if row[1] != migration.digest:
                raise LedgerError("INTEGRITY_FAILED", "数据库迁移摘要不一致，已拒绝修改。")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise LedgerError("INTEGRITY_FAILED", "数据库结构检查失败。")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LedgerError("INTEGRITY_FAILED", "数据库外键检查失败。")
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as expected:
            expected.execute("PRAGMA foreign_keys = ON")
            Database._migrate(expected, migrations[:version], 0)
            if Database._schema_signature(connection) != Database._schema_signature(expected):
                raise LedgerError("INTEGRITY_FAILED", "数据库表、索引或视图与已知迁移不一致。")
        return version

    @staticmethod
    def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, ...], ...]:
        return tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )

    @staticmethod
    def _migrate(
        connection: sqlite3.Connection, migrations: tuple[Migration, ...], version: int
    ) -> None:
        if version == 0:
            connection.execute(_MIGRATION_TABLE_SQL)
        for migration in migrations[version:]:
            for statement in _statements(migration.sql):
                connection.set_authorizer(_migration_authorizer)
                try:
                    connection.execute(statement)
                finally:
                    connection.set_authorizer(None)
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256, applied_at_utc) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.digest,
                    datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                ),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LedgerError("MIGRATION_FAILED", "迁移后外键检查失败。")

    def _snapshot_before_migration(self) -> None:
        """Use SQLite backup, never a raw copy of a live database and journal."""
        backup_directory = self.path.parent / "migration-backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot = backup_directory / f"{self.path.stem}-{stamp}-{uuid4().hex}.sqlite3"
        try:
            with (
                closing(self.connect(read_only=True)) as source,
                closing(sqlite3.connect(snapshot, isolation_level=None)) as destination,
            ):
                source.backup(destination, pages=128, sleep=0.01)
        except BaseException:
            snapshot.unlink(missing_ok=True)
            raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Yield a read-only snapshot and release its lock at context exit."""
        with closing(self.connect(read_only=True)) as connection:
            try:
                connection.execute("BEGIN")
                yield connection
            except sqlite3.Error as error:
                if _is_busy(error):
                    raise LedgerError("DATABASE_BUSY", "数据库正在使用，请稍后重试。") from error
                raise
            finally:
                if connection.in_transaction:
                    connection.rollback()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Serialize this instance's writers and commit or roll back the whole use case."""
        with self._lock, closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException as error:
                if connection.in_transaction:
                    connection.rollback()
                if isinstance(error, sqlite3.Error) and _is_busy(error):
                    raise LedgerError("DATABASE_BUSY", "数据库正在使用，请稍后重试。") from error
                raise
