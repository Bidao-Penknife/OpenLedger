"""Manual anonymous GitHub Release checks with bounded, non-redirecting HTTPS."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from packaging.version import InvalidVersion, Version

from openledger.domain.errors import LedgerError

MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_SECONDS = 4.0
TOTAL_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    """An optional public GitHub repository; empty defaults make no network requests."""

    owner: str = ""
    repo: str = ""


def validate_repository(owner: str, repo: str) -> None:
    """Accept exact path segments rather than arbitrary URLs or credentials."""
    if (
        not isinstance(owner, str)
        or not isinstance(repo, str)
        or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner) is None
        or "--" in owner
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) is None
        or repo in {".", ".."}
    ):
        raise LedgerError("INVALID_UPDATE_REPOSITORY")


class UpdateSettingsStore:
    """Atomically save only public repository settings, separate from financial backups."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> UpdateSettings:
        """Use empty defaults for a missing, oversized or invalid optional configuration."""
        try:
            if self.path.stat().st_size > 4096:
                return UpdateSettings()
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or set(value) != {"owner", "repo"}:
                return UpdateSettings()
            settings = UpdateSettings(value["owner"], value["repo"])
            if settings != UpdateSettings():
                validate_repository(settings.owner, settings.repo)
            return settings
        except (OSError, ValueError, TypeError, LedgerError):
            return UpdateSettings()

    def save(self, settings: UpdateSettings) -> None:
        """Publish a complete file; a failed publication preserves the previous settings."""
        if settings != UpdateSettings():
            validate_repository(settings.owner, settings.repo)
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(settings), stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except OSError as error:
            raise LedgerError("UPDATE_SETTINGS_WRITE_FAILED") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ReleaseResponse:
    """A transport result containing bounded response bytes without private context."""

    status: int
    body: bytes


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """A checked stable release or a configuration state; never a download instruction."""

    status: Literal["unconfigured", "no_release", "up_to_date", "available", "ignored"]
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None


class ReleaseTransport(Protocol):
    """Inject a bounded byte transport for offline tests; production uses GitHub HTTPS."""

    def __call__(
        self, url: str, headers: dict[str, str], cancelled: Callable[[], bool]
    ) -> ReleaseResponse:
        """Return an HTTP status and bounded body without following redirects."""
        ...


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        close = getattr(fp, "close", None)
        if callable(close):
            close()
        raise LedgerError("UPDATE_REDIRECT_REJECTED")


def github_transport(
    url: str, headers: dict[str, str], cancelled: Callable[[], bool]
) -> ReleaseResponse:
    """Use system TLS verification and finite I/O limits; do not send tokens or cookies."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.query
        or parsed.fragment
        or re.fullmatch(r"/repos/[^/]+/[^/]+/releases/latest", parsed.path) is None
    ):
        raise LedgerError("INVALID_UPDATE_REPOSITORY")
    segments = parsed.path.split("/")
    validate_repository(segments[2], segments[3])
    if cancelled():
        raise LedgerError("UPDATE_CANCELLED")
    # Disable environment proxy routing: the configured destination is always GitHub.
    opener = build_opener(ProxyHandler({}), _RejectRedirect())
    request = Request(url, headers=headers, method="GET")
    started = monotonic()
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return ReleaseResponse(int(response.status), b"")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    length = int(content_length)
                except ValueError as error:
                    raise LedgerError("INVALID_UPDATE_RESPONSE") from error
                if length < 0 or length > MAX_RESPONSE_BYTES:
                    raise LedgerError("UPDATE_RESPONSE_TOO_LARGE")
            parts: list[bytes] = []
            total = 0
            while True:
                if cancelled():
                    raise LedgerError("UPDATE_CANCELLED")
                if monotonic() - started > TOTAL_TIMEOUT_SECONDS:
                    raise LedgerError("UPDATE_TIMEOUT")
                part = response.read(min(8192, MAX_RESPONSE_BYTES - total + 1))
                if not part:
                    break
                total += len(part)
                if total > MAX_RESPONSE_BYTES:
                    raise LedgerError("UPDATE_RESPONSE_TOO_LARGE")
                parts.append(part)
            return ReleaseResponse(200, b"".join(parts))
    except HTTPError as error:
        status = error.code
        error.close()
        if 300 <= status < 400:
            raise LedgerError("UPDATE_REDIRECT_REJECTED") from error
        return ReleaseResponse(status, b"")
    except TimeoutError as error:
        raise LedgerError("UPDATE_TIMEOUT") from error
    except (URLError, OSError) as error:
        raise LedgerError("UPDATE_NETWORK_FAILED") from error


class UpdateService:
    """Check only when explicitly called; no timers, automatic downloads or history upload."""

    def __init__(self, current_version: str, transport: ReleaseTransport | None = None) -> None:
        try:
            self._current = Version(current_version)
        except InvalidVersion as error:
            raise LedgerError("INVALID_APPLICATION_VERSION") from error
        self.current_version = current_version
        self._transport = transport if transport is not None else github_transport

    def check(
        self, owner: str, repo: str, cancel: Callable[[], bool] | None = None
    ) -> UpdateResult:
        """Retrieve and validate one stable release from the explicitly chosen repository."""
        if owner == repo == "":
            return UpdateResult("unconfigured", self.current_version)
        validate_repository(owner, repo)
        cancelled = cancel if cancel is not None else lambda: False
        if cancelled():
            raise LedgerError("UPDATE_CANCELLED")
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "OpenLedger-Update-Check",
        }
        response = self._transport(url, headers, cancelled)
        if cancelled():
            raise LedgerError("UPDATE_CANCELLED")
        if response.status == 404:
            return UpdateResult("no_release", self.current_version)
        if response.status in {403, 429}:
            raise LedgerError("UPDATE_RATE_LIMITED")
        if 300 <= response.status < 400:
            raise LedgerError("UPDATE_REDIRECT_REJECTED")
        if response.status != 200:
            raise LedgerError("UPDATE_NETWORK_FAILED")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise LedgerError("UPDATE_RESPONSE_TOO_LARGE")
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise LedgerError("INVALID_UPDATE_RESPONSE") from error
        if not isinstance(value, dict) or (
            type(value.get("draft")) is not bool or type(value.get("prerelease")) is not bool
        ):
            raise LedgerError("INVALID_UPDATE_RESPONSE")
        if value["draft"] or value["prerelease"]:
            return UpdateResult("ignored", self.current_version)
        tag = value.get("tag_name")
        release_url = value.get("html_url")
        if (
            not isinstance(tag, str)
            or not 1 <= len(tag) <= 80
            or not isinstance(release_url, str)
            or len(release_url) > 512
        ):
            raise LedgerError("INVALID_UPDATE_RESPONSE")
        try:
            latest = Version(tag)
        except InvalidVersion as error:
            raise LedgerError("INVALID_UPDATE_RESPONSE") from error
        if latest.is_prerelease or latest.is_devrelease or latest.local is not None:
            return UpdateResult("ignored", self.current_version)
        parsed_release = urlsplit(release_url)
        parts = parsed_release.path.split("/")
        if (
            parsed_release.scheme != "https"
            or parsed_release.netloc != "github.com"
            or parsed_release.query
            or parsed_release.fragment
            or len(parts) != 6
            or parts[1].casefold() != owner.casefold()
            or parts[2].casefold() != repo.casefold()
            or parts[3:5] != ["releases", "tag"]
            or parts[5] != quote(tag, safe="")
        ):
            raise LedgerError("INVALID_UPDATE_RELEASE_URL")
        status: Literal["available", "up_to_date"] = (
            "available" if latest > self._current else "up_to_date"
        )
        return UpdateResult(status, self.current_version, str(latest), release_url)
