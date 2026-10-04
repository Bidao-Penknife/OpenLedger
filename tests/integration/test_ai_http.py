"""Use only a synthetic loopback HTTP server; never call a real AI provider."""

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import sleep

import pytest

from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import HTTPAITransport

pytestmark = pytest.mark.integration


@dataclass
class Response:
    """Synthetic response and captured request headers."""

    body: bytes = b'{"choices":[]}'
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str | None, bytes]] = field(default_factory=list)
    delay: float = 0
    before_body: Event | None = None


@contextmanager
def serve(response: Response) -> Iterator[str]:
    """Start and close a loopback-only, isolated test endpoint."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            response.requests.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                )
            )
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.before_body is not None:
                response.before_body.set()
            if response.delay:
                sleep(response.delay)
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_actual_transport_sends_one_json_post_and_closes_response() -> None:
    response = Response()
    with serve(response) as endpoint:
        body = json.dumps({"model": "synthetic-model"}).encode()
        assert HTTPAITransport().complete(endpoint, "synthetic-key", body, None) == response.body
    assert response.requests == [("/v1/chat/completions", "Bearer synthetic-key", body)]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AI_AUTH_FAILED"),
        (403, "AI_AUTH_FAILED"),
        (429, "AI_RATE_LIMITED"),
        (500, "AI_HTTP_ERROR"),
        (302, "AI_HTTP_ERROR"),
    ],
)
def test_error_bodies_and_redirects_never_become_ui_messages(status: int, code: str) -> None:
    response = Response(
        b"secret synthetic-key private history", status, {"Location": "http://127.0.0.1:1/leaked"}
    )
    with serve(response) as endpoint, pytest.raises(LedgerError) as captured:
        HTTPAITransport().complete(endpoint, "synthetic-key", b"{}", None)
    assert captured.value.code == code and str(captured.value) == code
    assert len(response.requests) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(b" " * (256 * 1024 + 1)), "AI_RESPONSE_TOO_LARGE"),
        (Response(b"", headers={"Content-Length": "262145"}), "AI_RESPONSE_TOO_LARGE"),
        (Response(b"", headers={"Content-Length": "bad"}), "AI_INVALID_RESPONSE"),
        (Response(b"gzip", headers={"Content-Encoding": "gzip"}), "AI_INVALID_RESPONSE"),
    ],
)
def test_wire_response_limits_are_enforced_before_json(response: Response, code: str) -> None:
    with serve(response) as endpoint, pytest.raises(LedgerError, match=code):
        HTTPAITransport().complete(endpoint, "synthetic-key", b"{}", None)


def test_cancelled_transport_never_sends_a_request() -> None:
    response = Response()
    cancel = Event()
    cancel.set()
    with serve(response) as endpoint, pytest.raises(LedgerError, match="AI_CANCELLED"):
        HTTPAITransport().complete(endpoint, "synthetic-key", b"{}", cancel)
    assert not response.requests


def test_read_timeout_has_sanitized_code_and_finite_socket_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openledger.infrastructure.ai.HTTP_TIMEOUT_SECONDS", 0.05)
    response = Response(delay=0.2)
    with serve(response) as endpoint, pytest.raises(LedgerError, match="AI_TIMEOUT"):
        HTTPAITransport().complete(endpoint, "synthetic-key", b"{}", None)


def test_cancellation_observed_after_network_read_preserves_local_draft() -> None:
    cancel = Event()
    response = Response(delay=0.05, before_body=cancel)
    with serve(response) as endpoint, pytest.raises(LedgerError, match="AI_CANCELLED"):
        HTTPAITransport().complete(endpoint, "synthetic-key", b"{}", cancel)
    assert len(response.requests) == 1
