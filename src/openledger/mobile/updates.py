"""Check Android APK release assets, never compare mobile with the Windows version."""

from __future__ import annotations

import json
import re
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from packaging.version import InvalidVersion, Version

from openledger.domain.errors import LedgerError
from openledger.infrastructure.updates import ReleaseResponse, ReleaseTransport, _RejectRedirect

REPOSITORY = "Bidao-Penknife/OpenLedger"
_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"
_LIMIT = 512 * 1024
_APK = re.compile(r"OpenLedger-([0-9][A-Za-z0-9.+-]{0,39})-android-preview\.apk\Z")


def mobile_release_transport(url: str, headers: dict[str, str], _cancelled: Any) -> ReleaseResponse:
    """Use verified TLS, a fixed public API endpoint, no redirects and bounded bytes."""
    if url != _API:
        raise LedgerError("INVALID_UPDATE_REPOSITORY")
    try:
        with build_opener(ProxyHandler({}), _RejectRedirect()).open(
            Request(url, headers=headers), timeout=12
        ) as response:
            deadline = monotonic() + 20
            parts: list[bytes] = []
            size = 0
            while True:
                if monotonic() > deadline:
                    raise LedgerError("UPDATE_TIMEOUT")
                chunk = response.read1(min(8192, _LIMIT + 1 - size))
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
                if size > _LIMIT:
                    raise LedgerError("UPDATE_RESPONSE_TOO_LARGE")
            body = b"".join(parts)
            return ReleaseResponse(response.status, body)
    except HTTPError as error:
        code = error.code
        error.close()
        return ReleaseResponse(code, b"")
    except TimeoutError as error:
        raise LedgerError("UPDATE_TIMEOUT") from error
    except (OSError, URLError) as error:
        raise LedgerError("UPDATE_NETWORK_FAILED") from error


class MobileUpdateService:
    """Release discovery only: returning a page URL cannot download or install an APK."""

    def __init__(self, current_version: str, transport: ReleaseTransport | None = None) -> None:
        try:
            self.current = Version(current_version.removesuffix("-preview"))
        except InvalidVersion as error:
            raise LedgerError("INVALID_UPDATE_VERSION") from error
        self.transport = transport or mobile_release_transport

    def check(self, include_prerelease: bool = True) -> dict[str, Any]:
        """Find the newest usable APK, including APKs in a mixed desktop/mobile release."""
        response = self.transport(
            _API,
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "OpenLedger-Android-update",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            lambda: False,
        )
        if response.status == 404:
            return {"status": "no_release"}
        if response.status != 200 or len(response.body) > _LIMIT:
            raise LedgerError("UPDATE_NETWORK_FAILED")
        try:
            rows = json.loads(response.body)
        except (ValueError, UnicodeError) as error:
            raise LedgerError("INVALID_UPDATE_RESPONSE") from error
        if not isinstance(rows, list) or len(rows) > 30:
            raise LedgerError("INVALID_UPDATE_RESPONSE")
        choices: list[tuple[Version, str]] = []
        for release in rows:
            if not isinstance(release, dict) or release.get("draft") is not False:
                continue
            url = release.get("html_url")
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "github.com"
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith(f"/{REPOSITORY}/releases/tag/")
            ):
                continue
            assets = release.get("assets", [])
            if not isinstance(assets, list) or len(assets) > 100:
                continue
            for asset in assets:
                if not isinstance(asset, dict) or asset.get("state") != "uploaded":
                    continue
                name = asset.get("name")
                match = _APK.fullmatch(name) if isinstance(name, str) else None
                if match is None:
                    continue
                try:
                    version = Version(match[1])
                except InvalidVersion:
                    continue
                if (
                    version.local is not None
                    or version.is_devrelease
                    or (version.is_prerelease and not include_prerelease)
                ):
                    continue
                choices.append((version, url))
        if not choices:
            return {"status": "no_release"}
        latest, url = max(choices, key=lambda item: item[0])
        return {
            "status": "available" if latest > self.current else "up_to_date",
            "version": str(latest),
            "release_url": url,
        }
