"""Bounded app-owned images; metadata uses the shared audited command writer."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from openledger.domain.errors import LedgerError
from openledger.domain.values import normalize_id
from openledger.infrastructure.ledger import LedgerService
from openledger.mobile.files import MobileFiles

_LIMIT = 20 * 1024 * 1024
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _image(path: Path) -> tuple[str, int, str]:
    size = path.stat().st_size
    if not 1 <= size <= _LIMIT:
        raise LedgerError("ATTACHMENT_TOO_LARGE")
    with path.open("rb") as stream:
        header = stream.read(32)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
        mime = "image/png"
    elif header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise LedgerError("INVALID_FILE_FORMAT")
    return mime, size, digest


class MobileAttachments:
    """Never accept arbitrary paths, overwrite existing images, or hard-delete history."""

    def __init__(self, ledger: LedgerService, files: MobileFiles) -> None:
        self.ledger = ledger
        self.files = files
        self.root = files.directory / "attachments"
        if self.root.is_symlink() or self.root.is_junction():
            raise LedgerError("INVALID_FILE_NAME")
        self.root.mkdir(exist_ok=True)

    def _path(self, relative: str) -> Path:
        # Old compatible backups may have nested image paths; validate every ancestor.
        if not relative or "\\" in relative or ":" in relative:
            raise LedgerError("INVALID_FILE_NAME")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise LedgerError("INVALID_FILE_NAME")
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise LedgerError("INVALID_FILE_NAME")
        if not current.resolve().is_relative_to(self.root.resolve()):
            raise LedgerError("INVALID_FILE_NAME")
        return current

    def rows(self, transaction_id: str) -> list[dict[str, Any]]:
        """Return immutable metadata without exposing private storage paths."""
        identifier = normalize_id(transaction_id)
        with self.ledger.database.read() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM attachments WHERE transaction_id=? ORDER BY created_at_utc,id",
                    (identifier,),
                )
            ]

    def dispatch(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if action == "attachment_prepare":
            if set(body) != {"filename", "transaction_id", "expected_transaction_version"}:
                raise LedgerError("INVALID_ENVELOPE")
            source = self.files.path(body["filename"])
            mime, size, digest = _image(source)
            transaction = self.ledger.transaction(normalize_id(body["transaction_id"]))
            if (
                type(body["expected_transaction_version"]) is not int
                or transaction["version"] != body["expected_transaction_version"]
            ):
                raise LedgerError("VERSION_CONFLICT")
            if transaction["deleted_at_utc"] is not None:
                raise LedgerError("TRANSACTION_DELETED")
            identifier = str(uuid4())
            relative = f"{identifier}.{_EXTENSIONS[mime]}"
            destination = self._path(relative)
            try:
                with source.open("rb") as incoming, destination.open("xb") as output:
                    shutil.copyfileobj(incoming, output, 64 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if _image(destination) != (mime, size, digest):
                    raise LedgerError("ATTACHMENT_CHANGED")
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return {
                "id": identifier,
                "transaction_id": transaction["id"],
                "expected_transaction_version": transaction["version"],
                "relative_path": relative,
                "original_file_name": relative,
                "mime_type": mime,
                "size_bytes": size,
                "sha256": digest,
            }
        if action == "attachment_commit":
            if set(body) != {"request_id", "payload"} or not isinstance(body["payload"], dict):
                raise LedgerError("INVALID_ENVELOPE")
            payload = body["payload"]
            if set(payload) != {
                "id",
                "transaction_id",
                "expected_transaction_version",
                "relative_path",
                "original_file_name",
                "mime_type",
                "size_bytes",
                "sha256",
            }:
                raise LedgerError("INVALID_ENVELOPE")
            mime, size, digest = _image(self._path(payload["relative_path"]))
            if (mime, size, digest) != (
                payload["mime_type"],
                payload["size_bytes"],
                payload["sha256"],
            ):
                raise LedgerError("ATTACHMENT_CHANGED")
            return asdict(self.ledger.execute(body["request_id"], "attachment.create.v1", payload))
        if action in {"attachment_delete", "attachment_restore"}:
            if set(body) != {"request_id", "id", "expected_version"}:
                raise LedgerError("INVALID_ENVELOPE")
            return asdict(
                self.ledger.execute(
                    body["request_id"],
                    "attachment.delete.v1"
                    if action.endswith("delete")
                    else "attachment.restore.v1",
                    {"id": body["id"], "expected_version": body["expected_version"]},
                )
            )
        if action == "attachment_view":
            if set(body) != {"id"}:
                raise LedgerError("INVALID_ENVELOPE")
            identifier = normalize_id(body["id"])
            with self.ledger.database.read() as connection:
                row = connection.execute(
                    "SELECT * FROM attachments WHERE id=?", (identifier,)
                ).fetchone()
            if row is None:
                raise LedgerError("ENTITY_NOT_FOUND")
            source = self._path(row["relative_path"])
            mime, size, digest = _image(source)
            if (mime, size, digest) != (row["mime_type"], row["size_bytes"], row["sha256"]):
                raise LedgerError("ATTACHMENT_CHANGED")
            filename = f"image-{uuid4()}.{_EXTENSIONS[mime]}"
            with source.open("rb") as incoming, self.files.path(filename).open("xb") as output:
                shutil.copyfileobj(incoming, output, 64 * 1024)
            return {"filename": filename, "mime_type": mime}
        raise LedgerError("UNSUPPORTED_ACTION")
