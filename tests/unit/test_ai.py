"""Remote suggestions are bounded, revisioned and never given persistence authority."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event

import pytest

from openledger.application.dto.ai import AIConfig
from openledger.application.dto.parsing import ParseChoice, ParseRequest
from openledger.application.ports.ai import CancelCheck
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AIParser, AISettingsStore, validate_config

BOOK = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "22222222-2222-4222-8222-222222222222"
EXPENSE = "33333333-3333-4333-8333-333333333333"
INCOME = "44444444-4444-4444-8444-444444444444"
CHANNEL = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
CONFIG = AIConfig(enabled=True, model="user-selected-model")


def request() -> ParseRequest:
    """Minimal synthetic context, containing no database or account balances."""
    return ParseRequest(
        BOOK,
        7,
        "昨天咖啡25元，微信支付",
        date(2026, 10, 3),
        "Asia/Shanghai",
        current_book_id=BOOK,
        default_account_id=ACCOUNT,
        account_choices=(ParseChoice(ACCOUNT, "钱包"),),
        category_choices=(
            ParseChoice(EXPENSE, "餐饮", "expense"),
            ParseChoice(INCOME, "工资", "income"),
        ),
        payment_method_choices=(ParseChoice(CHANNEL, "微信"),),
    )


def row() -> dict[str, object]:
    """A valid suggestion with explicit source evidence and string-valued money."""
    return {
        "span": [0, len(request().text)],
        "kind": "expense",
        "amount_minor": "2500",
        "occurred_on": "2026-10-02",
        "account_id": ACCOUNT,
        "category_id": EXPENSE,
        "payment_method_id": CHANNEL,
        "merchant": "咖啡店",
    }


def reply(content: object, *, finish: str = "stop") -> bytes:
    """Encode the documented compatible Chat Completions response boundary."""
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {"content": json.dumps(content, ensure_ascii=False)},
                }
            ]
        }
    ).encode("utf-8")


class FakeTransport:
    """Capture only the request an explicitly invoked parser submits."""

    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls: list[tuple[str, str, bytes]] = []
        self.on_complete: Event | None = None

    def complete(self, endpoint: str, key: str, body: bytes, cancel: CancelCheck) -> bytes:
        self.calls.append((endpoint, key, body))
        if self.on_complete is not None:
            self.on_complete.set()
        return self.response


def parser(value: object) -> AIParser:
    """Create an isolated parser which cannot contact a remote service."""
    return AIParser(FakeTransport(reply(value)), clock=lambda: NOW)


def test_ai_returns_reviewable_draft_identity_and_minimal_explicit_request() -> None:
    transport = FakeTransport(reply({"transactions": [row()]}))
    service = AIParser(transport, clock=lambda: NOW)
    assert transport.calls == []
    result = service.parse(request(), CONFIG, "synthetic-key")
    assert (result.draft_id, result.revision, result.provider_id) == (BOOK, 7, "openai-compatible")
    assert result.status == "single" and result.issues == ()
    draft = result.drafts[0]
    assert draft.amount_minor.value == 2500
    assert draft.occurred_on.value == date(2026, 10, 2)
    assert draft.category_id.value == EXPENSE and draft.account_id.value == ACCOUNT
    assert draft.book_id.value == BOOK
    for candidate in [
        draft.kind,
        draft.amount_minor,
        draft.occurred_on,
        draft.book_id,
        draft.account_id,
    ]:
        assert candidate.origin == "ai_suggestion" and candidate.requires_confirmation
        assert candidate.evidence_spans == (draft.span,)
    endpoint, key, encoded = transport.calls[0]
    assert endpoint == "https://api.openai.com/v1/chat/completions" and key == "synthetic-key"
    body = json.loads(encoded)
    context = json.loads(body["messages"][1]["content"])
    assert context["minor_unit_digits"] == 2
    assert set(context) == {
        "text",
        "reference_date",
        "time_zone",
        "currency_code",
        "minor_unit_digits",
        "current_book_id",
        "categories",
        "accounts",
        "payment_methods",
    }
    assert "synthetic-key" not in encoded.decode()
    assert body["response_format"] == {"type": "json_object"} and body["stream"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "transfer"},
        {"kind": "expense_refund"},
        {"kind": "balance_adjustment"},
        {"kind": True},
        {"amount_minor": 2500},
        {"amount_minor": 25.0},
        {"amount_minor": True},
        {"amount_minor": "-25"},
        {"amount_minor": "0"},
        {"amount_minor": "1e3"},
        {"amount_minor": "999999999999999"},
        {"amount": "25.001"},
        {"amount_minor": "2500", "amount": "25"},
        {"amount_minor": "1" * 5000},
        {"occurred_on": "2026-10-04"},
        {"occurred_on": "2026-02-30"},
        {"occurred_on": "20261002"},
        {"occurred_on": False},
        {"time_zone": "UTC"},
        {"time_zone": "Missing/Zone"},
        {"occurrence_precision": "exact"},
        {"time_period": "sometime"},
        {"occurrence_precision": "date", "time_period": "evening"},
        {"occurred_at_utc": "2026-10-02T12:00:00"},
        {"occurred_at_utc": "2026-10-03T12:00:00Z"},
        {"account_id": "66666666-6666-4666-8666-666666666666"},
        {"category_id": INCOME},
        {"payment_method_id": "not-a-uuid"},
        {"book_id": ACCOUNT},
        {"source": "import"},
        {"tools": [{"delete": True}]},
        {"note": ["invalid"]},
        {"merchant": "长" * 201},
        {"span": [-1, 4]},
        {"span": [0, 999]},
        {"span": [False, 4]},
        {"span": [4, 4]},
    ],
)
def test_invalid_or_unauthorized_remote_values_are_rejected(change: dict[str, object]) -> None:
    value = row()
    if "amount" in change and "amount_minor" not in change:
        value.pop("amount_minor")
    value.update(change)
    with pytest.raises(LedgerError, match="AI_INVALID_(SUGGESTION|RESPONSE)"):
        parser({"transactions": [value]}).parse(request(), CONFIG, "synthetic-key")


@pytest.mark.parametrize(
    "value",
    [
        [],
        None,
        {"transactions": "bad"},
        {"transactions": [], "commands": []},
        {"transactions": [row()] * 21},
    ],
)
def test_invalid_shape_and_too_many_events_fail(value: object) -> None:
    with pytest.raises(LedgerError, match="AI_INVALID_RESPONSE"):
        parser(value).parse(request(), CONFIG, "synthetic-key")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff",
        b'{"choices":[],"choices":[]}',
        b"[]",
        b"{}",
        reply({"transactions": [row()]}, finish="length"),
        reply({"transactions": [row()]}, finish="tool_calls"),
        b'{"choices":[{"finish_reason":"stop","message":{"content":"{bad"}}]}',
    ],
)
def test_malformed_duplicate_or_incomplete_response_is_never_applied(raw: bytes) -> None:
    service = AIParser(FakeTransport(raw), clock=lambda: NOW)
    with pytest.raises(LedgerError, match="AI_INVALID_RESPONSE"):
        service.parse(request(), CONFIG, "synthetic-key")


def test_empty_response_means_unsupported_and_missing_ids_remain_user_decisions() -> None:
    assert (
        parser({"transactions": []}).parse(request(), CONFIG, "synthetic-key").status
        == "unsupported"
    )
    value = row()
    value.pop("account_id")
    value.pop("category_id")
    result = parser({"transactions": [value]}).parse(request(), CONFIG, "synthetic-key")
    assert result.drafts[0].account_id.value is None
    assert result.drafts[0].category_id.value is None
    assert {issue.field for issue in result.issues} == {"account_id", "category_id"}


def test_decimal_money_and_vague_period_remain_exact_money_and_vague_time() -> None:
    value = row()
    value.pop("amount_minor")
    value.update(amount="25.30", time_period="evening", occurrence_precision="period")
    draft = parser({"transactions": [value]}).parse(request(), CONFIG, "synthetic-key").drafts[0]
    assert draft.amount_minor.value == 2530
    assert draft.occurrence_precision == "period" and draft.occurred_at_utc.value is None


def test_multi_event_spans_must_not_overlap() -> None:
    value, second = row(), row()
    value["span"], second["span"] = [0, 5], [5, len(request().text)]
    result = parser({"transactions": [value, second]}).parse(request(), CONFIG, "synthetic-key")
    assert result.status == "multiple_events" and len(result.drafts) == 2
    second["span"] = [4, len(request().text)]
    with pytest.raises(LedgerError, match="AI_INVALID_SUGGESTION"):
        parser({"transactions": [value, second]}).parse(request(), CONFIG, "synthetic-key")


@pytest.mark.parametrize(
    "key,code",
    [
        ("", "AI_KEY_MISSING"),
        ("x\ny", "AI_INVALID_KEY"),
        (" key", "AI_INVALID_KEY"),
        ("中", "AI_INVALID_KEY"),
    ],
)
def test_invalid_key_never_reaches_transport(key: str, code: str) -> None:
    transport = FakeTransport(b"{}")
    service = AIParser(transport)
    with pytest.raises(LedgerError, match=code):
        service.parse(request(), CONFIG, key)
    assert transport.calls == []


def test_disabled_and_cancelled_calls_never_connect_and_late_result_is_discarded() -> None:
    transport = FakeTransport(reply({"transactions": [row()]}))
    service = AIParser(transport)
    with pytest.raises(LedgerError, match="AI_DISABLED"):
        service.parse(request(), AIConfig(), "synthetic-key")
    cancel = Event()
    cancel.set()
    with pytest.raises(LedgerError, match="AI_CANCELLED"):
        service.parse(request(), CONFIG, "synthetic-key", cancel)
    assert not transport.calls
    cancel.clear()
    transport.on_complete = cancel
    with pytest.raises(LedgerError, match="AI_CANCELLED"):
        service.parse(request(), CONFIG, "synthetic-key", cancel.is_set)
    assert len(transport.calls) == 1


def test_oversized_response_and_source_are_rejected_without_unbounded_processing() -> None:
    transport = FakeTransport(b" " * (256 * 1024 + 1))
    with pytest.raises(LedgerError, match="AI_RESPONSE_TOO_LARGE"):
        AIParser(transport).parse(request(), CONFIG, "synthetic-key")
    transport.calls.clear()
    with pytest.raises(LedgerError, match="AI_INVALID_SUGGESTION"):
        AIParser(transport).parse(replace(request(), text="x" * 4001), CONFIG, "synthetic-key")
    assert not transport.calls


@pytest.mark.parametrize(
    "config",
    [
        AIConfig(True, "http://example.com/v1", "model"),
        AIConfig(True, "http://example.com/v1", "model", True),
        AIConfig(True, "https://user:secret@example.com/v1", "model"),
        AIConfig(True, "https://example.com/v1?token=secret", "model"),
        AIConfig(True, "https://example.com/v1#fragment", "model"),
        AIConfig(True, "https://example.com:0/v1", "model"),
        AIConfig(True, "file:///tmp/endpoint", "model"),
        AIConfig(True, "https://example.com/v1/chat/completions", "model"),
        AIConfig(True, "https://example.com/v1", ""),
        AIConfig(True, "https://example.com/v1", "model\nsecret"),
    ],
)
def test_invalid_configuration_is_rejected(config: AIConfig) -> None:
    with pytest.raises(LedgerError, match="AI_INVALID_CONFIG"):
        validate_config(config)


@pytest.mark.parametrize(
    "base", ["http://localhost:8000/v1", "http://127.0.0.1:9000/v1", "http://[::1]:8000/v1"]
)
def test_http_requires_explicit_local_opt_in(base: str) -> None:
    assert validate_config(AIConfig(True, base, "model", True)) == base + "/chat/completions"
    with pytest.raises(LedgerError, match="AI_INVALID_CONFIG"):
        validate_config(AIConfig(True, base, "model", False))


def test_settings_are_non_secret_atomic_and_disabled_on_invalid_input(tmp_path: Path) -> None:
    store = AISettingsStore(tmp_path / "不存在")
    assert store.load() == AIConfig() and not store.data_dir.exists()
    store.save(CONFIG)
    assert store.load() == CONFIG
    assert set(json.loads(store.path.read_text())) == {
        "enabled",
        "base_url",
        "model",
        "allow_local_http",
    }
    before = store.path.read_bytes()
    with pytest.raises(LedgerError, match="AI_INVALID_CONFIG"):
        store.save(AIConfig(True))
    assert store.path.read_bytes() == before
    store.path.write_bytes(b'{"enabled":true,"api_key":"synthetic-key"}')
    assert store.load() == AIConfig()


def test_preferences_publish_failure_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AISettingsStore(tmp_path)
    store.save(CONFIG)
    before = store.path.read_bytes()

    def denied(source: Path, target: str | Path) -> Path:
        raise PermissionError("Injected failure with private path")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(LedgerError, match="AI_SETTINGS_IO_ERROR"):
        store.save(replace(CONFIG, model="other-model"))
    assert store.path.read_bytes() == before and list(tmp_path.iterdir()) == [store.path]
