"""Validated local backups and restoration into a separate data directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import zipfile
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any

from openledger._version import __version__
from openledger.domain.errors import LedgerError
from openledger.infrastructure.database.database import (
    APPLICATION_ID,
    CURRENT_SCHEMA_VERSION,
    Database,
)
from openledger.infrastructure.integrity import validate_financial_integrity

_FORMAT_VERSION = 1
_CHUNK_SIZE = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    *(f"COM{index}" for index in ("¹", "²", "³")),
    *(f"LPT{index}" for index in ("¹", "²", "³")),
    "CONIN$",
    "CONOUT$",
}


@dataclass(frozen=True, slots=True)
class BackupLimits:
    """Bound extraction before trusting a local or downloaded ZIP archive."""

    database_bytes: int = 512 * 1024 * 1024
    attachment_bytes: int = 20 * 1024 * 1024
    total_bytes: int = 1024 * 1024 * 1024
    manifest_bytes: int = 2 * 1024 * 1024
    entries: int = 10_002

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.database_bytes,
                self.attachment_bytes,
                self.total_bytes,
                self.manifest_bytes,
                self.entries,
            )
        ):
            raise ValueError("Backup limits must be positive integers")


@dataclass(frozen=True, slots=True)
class _Attachment:
    relative_path: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class _Manifest:
    app_version: str
    schema_version: int
    created_at_utc: str
    database_sha256: str
    database_size_bytes: int
    attachments: tuple[_Attachment, ...]

    def encode(self) -> bytes:
        return _json_bytes(
            {
                "format_version": _FORMAT_VERSION,
                "app_version": self.app_version,
                "schema_version": self.schema_version,
                "created_at_utc": self.created_at_utc,
                "database_sha256": self.database_sha256,
                "database_size_bytes": self.database_size_bytes,
                "attachments": [item.as_dict() for item in self.attachments],
            }
        )


class BackupService:
    """Back up a consistent SQLite snapshot without overwriting existing data.

    Only database-referenced attachment files are included. Restoration never
    replaces an open database: it publishes a validated, separate data directory.
    """

    def __init__(self, database: Database, *, limits: BackupLimits | None = None) -> None:
        self.database = database
        self.limits = limits or BackupLimits()

    def backup(
        self,
        destination: Path,
        attachments_directory: Path | None = None,
    ) -> Path:
        """Create a new ``.olbackup`` ZIP using SQLite's online backup API."""
        destination = _absolute_path(destination)
        _check_parent(destination)
        if destination.exists():
            raise LedgerError("BACKUP_TARGET_EXISTS", "备份目标已存在，请选择新文件。")
        _reject_reparse_points(self.database.path)
        if not self.database.path.is_file():
            raise LedgerError("BACKUP_SOURCE_INVALID", "源数据库不存在。")
        try:
            with tempfile.TemporaryDirectory(
                prefix=".openledger-backup-",
                dir=destination.parent,
            ) as temporary:
                staging = Path(temporary)
                snapshot = staging / "database.sqlite3"
                with (
                    closing(self.database.connect(read_only=True)) as source,
                    closing(sqlite3.connect(snapshot)) as target,
                ):
                    deadline = time.monotonic() + max(self.database.timeout, 1.0)

                    def progress(status: int, remaining: int, total: int) -> None:
                        if (
                            status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                            and time.monotonic() >= deadline
                        ):
                            raise LedgerError("DATABASE_BUSY", "数据库正在使用，请稍后备份。")

                    source.backup(target, pages=256, progress=progress, sleep=0.01)
                    target.execute("PRAGMA journal_mode = DELETE")
                attachments = _validate_database(snapshot, self.limits)
                manifest = _Manifest(
                    __version__,
                    CURRENT_SCHEMA_VERSION,
                    _utc_now(),
                    _sha256(snapshot),
                    snapshot.stat().st_size,
                    attachments,
                )
                encoded = manifest.encode()
                _check_total_size(manifest, len(encoded), self.limits)
                archive = staging / "backup.tmp"
                with zipfile.ZipFile(
                    archive,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as output:
                    output.writestr("manifest.json", encoded)
                    output.write(snapshot, "database.sqlite3")
                    for item in attachments:
                        source_path = _attachment_source(attachments_directory, item)
                        with (
                            source_path.open("rb") as input_file,
                            output.open(f"attachments/{item.relative_path}", "w") as entry,
                        ):
                            _copy_checked(input_file, entry, item.size_bytes, item.sha256)
                with archive.open("r+b") as archive_file:
                    os.fsync(archive_file.fileno())
                _publish_file(archive, destination)
            return destination
        except LedgerError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError) as error:
            raise LedgerError("BACKUP_FAILED", "无法完成备份，源数据库未被替换。") from error

    def restore(self, archive: Path, destination: Path) -> Database:
        """Validate and restore an archive into a new or empty data directory."""
        archive = _absolute_path(archive)
        destination = _absolute_path(destination)
        _reject_reparse_points(archive)
        _check_restore_target(destination)
        try:
            if archive.stat().st_size > self.limits.total_bytes + 64 * 1024 * 1024:
                raise LedgerError("BACKUP_INVALID", "备份归档超过大小限制。")
            archive_digest = _sha256(archive)
            with tempfile.TemporaryDirectory(
                prefix=".openledger-restore-",
                dir=destination.parent,
            ) as temporary:
                staging = Path(temporary)
                database_path = staging / "database" / "openledger.sqlite3"
                database_path.parent.mkdir()
                (staging / "attachments").mkdir()
                with zipfile.ZipFile(archive, "r") as source:
                    entries = _validate_entries(source.infolist(), self.limits)
                    manifest_entry = entries.get("manifest.json")
                    if manifest_entry is None:
                        raise LedgerError("BACKUP_INVALID", "备份缺少清单。")
                    with source.open(manifest_entry) as input_file:
                        encoded = input_file.read(self.limits.manifest_bytes + 1)
                    manifest = _parse_manifest(encoded, self.limits)
                    _check_total_size(manifest, len(encoded), self.limits)
                    expected_names = {
                        "manifest.json",
                        "database.sqlite3",
                        *(f"attachments/{item.relative_path}" for item in manifest.attachments),
                    }
                    if set(entries) != expected_names:
                        raise LedgerError("BACKUP_INVALID", "备份文件与清单不一致。")
                    _extract_checked(
                        source,
                        entries["database.sqlite3"],
                        database_path,
                        manifest.database_size_bytes,
                        manifest.database_sha256,
                    )
                    for item in manifest.attachments:
                        path = staging / "attachments" / PurePosixPath(item.relative_path)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        _extract_checked(
                            source,
                            entries[f"attachments/{item.relative_path}"],
                            path,
                            item.size_bytes,
                            item.sha256,
                        )
                actual_attachments = _validate_database(database_path, self.limits)
                if actual_attachments != manifest.attachments:
                    raise LedgerError("BACKUP_INVALID", "附件清单与数据库不一致。")
                if _sha256(database_path) != manifest.database_sha256:
                    raise LedgerError("BACKUP_INVALID", "数据库验证改变了快照，恢复已取消。")
                if _sha256(archive) != archive_digest:
                    raise LedgerError("BACKUP_INVALID", "备份归档在恢复过程中发生变化。")
                (staging / "restore-receipt.json").write_bytes(
                    _json_bytes(
                        {
                            "format_version": _FORMAT_VERSION,
                            "restored_at_utc": _utc_now(),
                            "backup_sha256": archive_digest,
                            "database_sha256": manifest.database_sha256,
                            "schema_version": manifest.schema_version,
                            "source_created_at_utc": manifest.created_at_utc,
                            "app_version": __version__,
                        }
                    )
                )
                _publish_directory(staging, destination)
            return Database(destination / "database" / "openledger.sqlite3")
        except LedgerError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError, RuntimeError) as error:
            raise LedgerError("BACKUP_INVALID", "备份验证失败，现有数据未被替换。") from error


def _absolute_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise LedgerError("BACKUP_INVALID_PATH", "请使用不含上级目录跳转的绝对路径。")
    return path


def _reject_reparse_points(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise LedgerError("BACKUP_INVALID_PATH", "备份路径不能包含符号链接或目录联接。")
        if component.exists():
            attributes = getattr(component.lstat(), "st_file_attributes", 0)
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise LedgerError("BACKUP_INVALID_PATH", "备份路径不能包含重解析点。")


def _check_parent(path: Path) -> None:
    _reject_reparse_points(path)
    if not path.parent.is_dir():
        raise LedgerError("BACKUP_INVALID_PATH", "备份目标的父目录必须已存在。")


def _check_restore_target(destination: Path) -> None:
    _check_parent(destination)
    if destination.exists() and (
        not destination.is_dir() or next(destination.iterdir(), None) is not None
    ):
        raise LedgerError("BACKUP_TARGET_EXISTS", "恢复目标必须为新目录或空目录。")


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or len(value) > 1024 or any(ord(char) < 32 for char in value):
        raise LedgerError("BACKUP_INVALID", "备份包含无效路径。")
    parts = value.split("/")
    if any(
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or any(char in part for char in '\\:<>"|?*')
        or part.split(".", 1)[0].upper() in _RESERVED_NAMES
        for part in parts
    ):
        raise LedgerError("BACKUP_INVALID", "备份包含不安全的文件路径。")
    return PurePosixPath(value)


def _validate_entries(
    entries: Iterable[zipfile.ZipInfo],
    limits: BackupLimits,
) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    folded_names: set[str] = set()
    total = 0
    for entry in entries:
        _safe_relative_path(entry.filename)
        file_type = stat.S_IFMT(entry.external_attr >> 16)
        folded_name = entry.filename.casefold()
        if (
            folded_name in folded_names
            or entry.is_dir()
            or entry.external_attr & 0x10
            or file_type not in (0, stat.S_IFREG)
            or entry.flag_bits & 1
            or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
        ):
            raise LedgerError("BACKUP_INVALID", "备份包含重复、加密或无效文件。")
        maximum = (
            limits.manifest_bytes
            if entry.filename == "manifest.json"
            else limits.database_bytes
            if entry.filename == "database.sqlite3"
            else limits.attachment_bytes
        )
        if entry.file_size < 0 or entry.file_size > maximum:
            raise LedgerError("BACKUP_INVALID", "备份文件超过大小限制。")
        total += entry.file_size
        if total > limits.total_bytes or len(result) >= limits.entries:
            raise LedgerError("BACKUP_INVALID", "备份解压总量超过限制。")
        folded_names.add(folded_name)
        result[entry.filename] = entry
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError("BACKUP_INVALID", "备份清单包含重复字段。")
        result[key] = value
    return result


def _parse_manifest(encoded: bytes, limits: BackupLimits) -> _Manifest:
    if len(encoded) > limits.manifest_bytes:
        raise LedgerError("BACKUP_INVALID", "备份清单超过大小限制。")
    try:
        data: object = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LedgerError("BACKUP_INVALID", "备份清单不是有效的 UTF-8 JSON。") from error
    keys = {
        "format_version",
        "app_version",
        "schema_version",
        "created_at_utc",
        "database_sha256",
        "database_size_bytes",
        "attachments",
    }
    if not isinstance(data, dict) or set(data) != keys:
        raise LedgerError("BACKUP_INVALID", "备份清单字段不完整或版本不受支持。")
    if type(data["format_version"]) is not int or data["format_version"] != _FORMAT_VERSION:
        raise LedgerError("BACKUP_INVALID", "不支持此备份格式版本。")
    if type(data["schema_version"]) is not int or data["schema_version"] != CURRENT_SCHEMA_VERSION:
        raise LedgerError("BACKUP_INVALID", "不支持此数据库版本。")
    app_version = data["app_version"]
    created_at = data["created_at_utc"]
    digest = data["database_sha256"]
    size = data["database_size_bytes"]
    raw_attachments = data["attachments"]
    if (
        not isinstance(app_version, str)
        or not 1 <= len(app_version) <= 80
        or not isinstance(created_at, str)
        or not created_at.endswith("Z")
        or not isinstance(digest, str)
        or _HASH.fullmatch(digest) is None
        or type(size) is not int
        or not 1 <= size <= limits.database_bytes
        or not isinstance(raw_attachments, list)
        or len(raw_attachments) + 2 > limits.entries
    ):
        raise LedgerError("BACKUP_INVALID", "备份清单包含无效值。")
    try:
        datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as error:
        raise LedgerError("BACKUP_INVALID", "备份时间格式无效。") from error
    attachments: list[_Attachment] = []
    folded_names: set[str] = set()
    for item in raw_attachments:
        if not isinstance(item, dict) or set(item) != {"relative_path", "size_bytes", "sha256"}:
            raise LedgerError("BACKUP_INVALID", "附件清单字段无效。")
        path, attachment_size, attachment_hash = (
            item["relative_path"],
            item["size_bytes"],
            item["sha256"],
        )
        if (
            not isinstance(path, str)
            or type(attachment_size) is not int
            or not 1 <= attachment_size <= limits.attachment_bytes
            or not isinstance(attachment_hash, str)
            or _HASH.fullmatch(attachment_hash) is None
        ):
            raise LedgerError("BACKUP_INVALID", "附件清单值无效。")
        _safe_relative_path(path)
        if path.casefold() in folded_names:
            raise LedgerError("BACKUP_INVALID", "附件路径重复。")
        folded_names.add(path.casefold())
        attachments.append(_Attachment(path, attachment_size, attachment_hash))
    return _Manifest(
        app_version,
        CURRENT_SCHEMA_VERSION,
        created_at,
        digest,
        size,
        tuple(sorted(attachments, key=lambda item: item.relative_path)),
    )


def _check_total_size(manifest: _Manifest, manifest_size: int, limits: BackupLimits) -> None:
    if (
        not 1 <= manifest.database_size_bytes <= limits.database_bytes
        or manifest_size > limits.manifest_bytes
        or len(manifest.attachments) + 2 > limits.entries
        or manifest_size
        + manifest.database_size_bytes
        + sum(item.size_bytes for item in manifest.attachments)
        > limits.total_bytes
    ):
        raise LedgerError("BACKUP_INVALID", "备份大小超过限制。")


def _validate_database(path: Path, limits: BackupLimits) -> tuple[_Attachment, ...]:
    if not 1 <= path.stat().st_size <= limits.database_bytes:
        raise LedgerError("BACKUP_INVALID", "数据库大小无效。")
    database = Database(path)
    with closing(database.connect(read_only=True)) as connection:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application_id != APPLICATION_ID or version != CURRENT_SCHEMA_VERSION:
            raise LedgerError("BACKUP_INVALID", "备份不是受支持的 OpenLedger 数据库。")
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            raise LedgerError("BACKUP_INVALID", "数据库完整性检查失败。")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LedgerError("BACKUP_INVALID", "数据库包含无效关联。")
    database.initialize()
    with closing(database.connect(read_only=True)) as connection:
        validate_financial_integrity(connection)
        rows = connection.execute(
            "SELECT relative_path, size_bytes, sha256 FROM attachments ORDER BY relative_path",
        ).fetchall()
    attachments: list[_Attachment] = []
    folded_names: set[str] = set()
    for row in rows:
        item = _Attachment(str(row[0]), int(row[1]), str(row[2]))
        _safe_relative_path(item.relative_path)
        if (
            not 1 <= item.size_bytes <= limits.attachment_bytes
            or _HASH.fullmatch(item.sha256) is None
            or item.relative_path.casefold() in folded_names
        ):
            raise LedgerError("BACKUP_INVALID", "数据库附件元数据无效。")
        folded_names.add(item.relative_path.casefold())
        attachments.append(item)
    return tuple(attachments)


def _attachment_source(root: Path | None, item: _Attachment) -> Path:
    if root is None:
        raise LedgerError("BACKUP_INVALID", "数据库引用了附件，请提供附件目录。")
    root = _absolute_path(root)
    path = root / _safe_relative_path(item.relative_path)
    _reject_reparse_points(path)
    if not path.is_file() or path.stat().st_size != item.size_bytes:
        raise LedgerError("BACKUP_INVALID", "数据库引用的附件缺失或大小不匹配。")
    return path


def _extract_checked(
    source: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    path: Path,
    size: int,
    digest: str,
) -> None:
    if entry.file_size != size:
        raise LedgerError("BACKUP_INVALID", "备份文件大小与清单不一致。")
    with source.open(entry) as input_file, path.open("xb") as output_file:
        _copy_checked(input_file, output_file, size, digest)
        output_file.flush()
        os.fsync(output_file.fileno())


def _copy_checked(source: IO[bytes], destination: IO[bytes], size: int, digest: str) -> None:
    checksum = hashlib.sha256()
    copied = 0
    while chunk := source.read(min(_CHUNK_SIZE, size - copied + 1)):
        copied += len(chunk)
        if copied > size:
            raise LedgerError("BACKUP_INVALID", "备份内容超过声明大小。")
        checksum.update(chunk)
        destination.write(chunk)
    if copied != size or checksum.hexdigest() != digest:
        raise LedgerError("BACKUP_INVALID", "备份内容摘要或大小不匹配。")


def _publish_file(staging: Path, destination: Path) -> None:
    try:
        if os.name == "nt":
            staging.rename(destination)
        else:
            os.link(staging, destination)
            staging.unlink()
    except FileExistsError as error:
        raise LedgerError("BACKUP_TARGET_EXISTS", "备份目标已存在，请选择新文件。") from error


def _publish_directory(staging: Path, destination: Path) -> None:
    _check_restore_target(destination)
    was_empty_directory = destination.exists()
    if was_empty_directory:
        destination.rmdir()
    try:
        staging.rename(destination)
    except OSError:
        if was_empty_directory and not destination.exists():
            destination.mkdir()
        raise


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
