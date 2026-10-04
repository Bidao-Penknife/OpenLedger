"""GitHub metadata checking uses fixed URLs and never uploads financial context."""

import json
from collections.abc import Callable
from dataclasses import replace
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from openledger.domain.errors import LedgerError
from openledger.infrastructure import updates
from openledger.infrastructure.updates import (
    MAX_RESPONSE_BYTES,
    ReleaseResponse,
    UpdateService,
    UpdateSettings,
    UpdateSettingsStore,
    github_transport,
    validate_repository,
)


class FakeTransport:
    def __init__(self, value: object, status: int = 200) -> None:
        self.value = value
        self.status = status
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self, url: str, headers: dict[str, str], cancelled: Callable[[], bool]
    ) -> ReleaseResponse:
        self.calls.append((url, headers))
        body = self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()
        return ReleaseResponse(self.status, body)


def release(tag: str = "v0.3.0") -> dict[str, object]:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/example/OpenLedger/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
    }


def test_unconfigured_check_performs_no_http() -> None:
    transport = FakeTransport(release())
    result = UpdateService("0.3.0.dev0", transport).check("", "")
    assert result.status == "unconfigured"
    assert transport.calls == []


def test_stable_release_is_newer_than_current_development_build() -> None:
    transport = FakeTransport(release())
    result = UpdateService("0.3.0.dev0", transport).check("example", "OpenLedger")
    assert result.status == "available" and result.latest_version == "0.3.0"
    assert result.release_url == release()["html_url"]
    assert len(transport.calls) == 1
    url, headers = transport.calls[0]
    assert url == "https://api.github.com/repos/example/OpenLedger/releases/latest"
    assert "Authorization" not in headers and "Cookie" not in headers
    assert set(headers) == {"Accept", "X-GitHub-Api-Version", "User-Agent"}


@pytest.mark.parametrize("current", ["0.3.0", "0.4.0", "0.4.0.dev0"])
def test_equal_or_older_stable_release_needs_no_update(current: str) -> None:
    result = UpdateService(current, FakeTransport(release())).check("example", "OpenLedger")
    assert result.status == "up_to_date"


@pytest.mark.parametrize(
    "owner,repo",
    [
        ("", "repo"),
        ("owner", ""),
        ("../evil", "repo"),
        ("a--b", "repo"),
        ("https://github.com", "repo"),
        ("owner", ".."),
        ("owner", "repo?secret=1"),
        ("owner", "repo/other"),
        ("owner", "repo%2fother"),
    ],
)
def test_invalid_repository_rejected_before_http(owner: str, repo: str) -> None:
    transport = FakeTransport(release())
    with pytest.raises(LedgerError, match="INVALID_UPDATE_REPOSITORY"):
        UpdateService("0.3.0", transport).check(owner, repo)
    assert transport.calls == []


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_draft_and_prerelease_ignored(field: str) -> None:
    value = release()
    value[field] = True
    result = UpdateService("0.2.0", FakeTransport(value)).check("example", "OpenLedger")
    assert result.status == "ignored" and result.release_url is None


@pytest.mark.parametrize("tag", ["v0.5.0rc1", "0.5.0.dev0", "0.5.0+local"])
def test_mislabeled_preview_tags_are_also_ignored(tag: str) -> None:
    result = UpdateService("0.2.0", FakeTransport(release(tag))).check("example", "OpenLedger")
    assert result.status == "ignored"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/OpenLedger/releases/tag/v0.3.0",
        "https://evil.example/example/OpenLedger/releases/tag/v0.3.0",
        "https://github.com/example/Other/releases/tag/v0.3.0",
        "https://github.com/other/OpenLedger/releases/tag/v0.3.0",
        "https://github.com/example/OpenLedger/releases/tag/v0.4.0",
        "https://github.com/example/OpenLedger/releases/tag/v0.3.0?download=1",
        "https://github.com:443/example/OpenLedger/releases/tag/v0.3.0",
        "https://github.com@example.com/example/OpenLedger/releases/tag/v0.3.0",
    ],
)
def test_release_link_must_match_configured_owner_repository_and_tag(url: str) -> None:
    value = release()
    value["html_url"] = url
    with pytest.raises(LedgerError, match="INVALID_UPDATE_RELEASE_URL"):
        UpdateService("0.2.0", FakeTransport(value)).check("example", "OpenLedger")


def test_repository_case_is_insensitive_but_tag_case_is_preserved() -> None:
    result = UpdateService("0.2.0", FakeTransport(release())).check("Example", "openledger")
    assert result.status == "available"


@pytest.mark.parametrize("value", [b"not json", b"\xff", [], {}, {"draft": 0, "prerelease": False}])
def test_invalid_metadata_rejected(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_UPDATE_RESPONSE"):
        UpdateService("0.2.0", FakeTransport(value)).check("example", "OpenLedger")


@pytest.mark.parametrize(
    "status,code",
    [
        (302, "UPDATE_REDIRECT_REJECTED"),
        (429, "UPDATE_RATE_LIMITED"),
        (403, "UPDATE_RATE_LIMITED"),
        (500, "UPDATE_NETWORK_FAILED"),
    ],
)
def test_http_failures_are_safe_codes(status: int, code: str) -> None:
    with pytest.raises(LedgerError, match=code):
        UpdateService("0.2.0", FakeTransport({}, status)).check("example", "OpenLedger")


def test_missing_public_stable_release_is_not_a_version_claim() -> None:
    result = UpdateService("0.2.0", FakeTransport({}, 404)).check("example", "OpenLedger")
    assert result.status == "no_release" and result.latest_version is None


def test_cancellation_before_and_after_transport() -> None:
    transport = FakeTransport(release())
    with pytest.raises(LedgerError, match="UPDATE_CANCELLED"):
        UpdateService("0.2.0", transport).check("example", "OpenLedger", lambda: True)
    assert transport.calls == []
    flags = iter((False, True))
    with pytest.raises(LedgerError, match="UPDATE_CANCELLED"):
        UpdateService("0.2.0", transport).check("example", "OpenLedger", lambda: next(flags))
    assert len(transport.calls) == 1


def test_oversized_transport_body_rejected() -> None:
    with pytest.raises(LedgerError, match="UPDATE_RESPONSE_TOO_LARGE"):
        UpdateService("0.2.0", FakeTransport(b" " * (MAX_RESPONSE_BYTES + 1))).check(
            "example", "OpenLedger"
        )


def test_settings_defaults_validation_and_atomic_failed_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = UpdateSettingsStore(tmp_path / "update-settings.json")
    assert store.load() == UpdateSettings()
    value = UpdateSettings("example", "OpenLedger")
    store.save(value)
    assert store.load() == value
    previous = store.path.read_bytes()

    def reject_replace(self: Path, target: Path) -> Path:
        raise PermissionError("Private path must not enter GUI errors")

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(LedgerError, match="UPDATE_SETTINGS_WRITE_FAILED"):
        store.save(replace(value, repo="Other"))
    assert store.path.read_bytes() == previous
    assert not tuple(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("data", [b"invalid", b" " * 4097, b'{"owner":4,"repo":[]}', b"{}"])
def test_settings_invalid_files_fail_closed(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "updates.json"
    path.write_bytes(data)
    assert UpdateSettingsStore(path).load() == UpdateSettings()


def test_direct_transport_rejects_custom_host_without_opening_socket() -> None:
    with pytest.raises(LedgerError, match="INVALID_UPDATE_REPOSITORY"):
        github_transport("https://custom.example/", {}, lambda: False)


class _Response(BytesIO):
    def __init__(self, data: bytes, length: str | None = None) -> None:
        super().__init__(data)
        self.status = 200
        self.headers = {} if length is None else {"Content-Length": length}


class _Opener:
    def __init__(self, value: _Response | Exception) -> None:
        self.value = value

    def open(self, request: Request, timeout: float) -> _Response:
        assert request.full_url.startswith("https://api.github.com/")
        assert 0 < timeout <= 4
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.mark.parametrize("length", [str(MAX_RESPONSE_BYTES + 1), "-1", "bad"])
def test_transport_checks_advertised_length_and_closes_response(
    monkeypatch: pytest.MonkeyPatch, length: str
) -> None:
    response = _Response(b"{}", length)
    monkeypatch.setattr(updates, "build_opener", lambda *handlers: _Opener(response))
    with pytest.raises(LedgerError):
        github_transport(
            "https://api.github.com/repos/example/OpenLedger/releases/latest", {}, lambda: False
        )
    assert response.closed


def test_transport_stream_size_limit_closes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(b"x" * (MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(updates, "build_opener", lambda *handlers: _Opener(response))
    with pytest.raises(LedgerError, match="UPDATE_RESPONSE_TOO_LARGE"):
        github_transport(
            "https://api.github.com/repos/example/OpenLedger/releases/latest", {}, lambda: False
        )
    assert response.closed


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("secret"), "UPDATE_TIMEOUT"),
        (URLError("secret"), "UPDATE_NETWORK_FAILED"),
        (
            HTTPError("https://secret", 302, "secret", Message(), None),
            "UPDATE_REDIRECT_REJECTED",
        ),
    ],
)
def test_transport_sanitizes_connection_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    monkeypatch.setattr(updates, "build_opener", lambda *handlers: _Opener(error))
    with pytest.raises(LedgerError, match=code) as result:
        github_transport(
            "https://api.github.com/repos/example/OpenLedger/releases/latest", {}, lambda: False
        )
    assert "secret" not in str(result.value)


def test_redirect_handler_closes_original_response() -> None:
    response = _Response(b"redirect")
    handler = updates._RejectRedirect()
    with pytest.raises(LedgerError, match="UPDATE_REDIRECT_REJECTED"):
        handler.redirect_request(
            Request("https://api.github.com/"),
            response,
            302,
            "redirect",
            {},
            "https://evil.example/",
        )
    assert response.closed


def test_repository_accepts_normal_organization_and_hyphens() -> None:
    validate_repository("open-ledger", "OpenLedger.desktop")
