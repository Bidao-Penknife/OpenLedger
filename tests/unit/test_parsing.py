"""Rules preserve money, uncertainty, original evidence and explicit account choices."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL, uuid5

import pytest

from openledger.application.dto.parsing import (
    ChannelAccountMapping,
    ParseChoice,
    ParseRequest,
    ParseResult,
)
from openledger.application.parsing import LocalParser


def _id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"openledger.test.parsing.{name}"))


FOOD = _id("food")
TRANSIT = _id("transit")
STUDY = _id("study")
WAGES = _id("wages")
WALLET = _id("wallet")
WECHAT_ACCOUNT = _id("wechat-account")
WECHAT = _id("wechat")
ALIPAY = _id("alipay")
BOOK = _id("book")
_CATEGORIES = (
    ParseChoice(FOOD, "餐饮", "expense"),
    ParseChoice(TRANSIT, "交通", "expense"),
    ParseChoice(STUDY, "学习", "expense"),
    ParseChoice(WAGES, "工资", "income"),
)
_ACCOUNTS = (ParseChoice(WALLET, "钱包"), ParseChoice(WECHAT_ACCOUNT, "微信"))
_PAYMENT = (ParseChoice(WECHAT, "微信", aliases=("微信支付",)), ParseChoice(ALIPAY, "支付宝"))


def _request(text: str, /, **overrides: object) -> ParseRequest:
    base = ParseRequest(
        draft_id=_id("draft"),
        revision=1,
        text=text,
        reference_date=date(2026, 10, 2),
        time_zone="Asia/Shanghai",
        current_book_id=BOOK,
        category_choices=_CATEGORIES,
        account_choices=_ACCOUNTS,
        payment_method_choices=_PAYMENT,
    )
    # Deliberately exercise heterogeneous context and malformed request values.
    return replace(base, **overrides)  # type: ignore[arg-type]


def _parse(text: str, **overrides: object) -> ParseResult:
    return LocalParser().parse(_request(text, **overrides))


def _codes(result: ParseResult) -> set[str]:
    return {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    "prefix", ["下个月", "下周", "下星期", "明年", "大后天", "3天后", "三天以后"]
)
def test_unsupported_future_expressions_cannot_silently_default_to_today(prefix: str) -> None:
    result = _parse(prefix + "咖啡25元")
    assert "FUTURE_DATE" in _codes(result)
    assert result.drafts[0].occurred_on.value is None
    issue = next(issue for issue in result.issues if issue.code == "FUTURE_DATE")
    assert issue.blocking and issue.field == "occurred_on"
    assert issue.span is not None
    assert (prefix + "咖啡25元")[issue.span.start : issue.span.end] == prefix


def test_user_example_is_evidence_based_and_never_invents_firepot_location() -> None:
    text = "昨天晚上和朋友吃火锅花了128元，微信支付"
    result = _parse(text)
    draft = result.drafts[0]
    assert result.status == "single"
    assert draft.kind.value == "expense"
    assert draft.amount_minor.value == 12_800
    assert draft.occurred_on.value == date(2026, 10, 1)
    assert draft.time_period.value == "evening"
    assert draft.occurrence_precision == "period"
    assert draft.occurred_at_utc.value is None
    assert draft.category_id.value == FOOD
    assert draft.category_id.origin == "rule_suggestion"
    assert draft.payment_method_id.value == WECHAT
    assert draft.account_id.value is None
    assert draft.account_id.reason_code == "CHANNEL_NOT_ACCOUNT"
    assert draft.counterparty.value == "朋友"
    assert draft.location.value is None
    assert draft.merchant.value is None
    assert draft.note.value == text
    assert draft.note.evidence_spans[0].start == 0
    assert draft.note.evidence_spans[0].end == len(text)
    span = draft.amount_minor.evidence_spans[0]
    assert text[span.start : span.end] == "128元"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("咖啡 25元", 2500),
        ("咖啡25", 2500),
        ("咖啡￥25.50", 2550),
        ("咖啡 0.01元", 1),
        ("咖啡00025.50元", 2550),
        ("咖啡25块5", 2550),
        ("咖啡25元5角", 2550),
        ("咖啡25元5分", 2505),
        ("咖啡25角", 250),
        ("咖啡25分", 25),
        ("咖啡1.5角", 15),
        ("咖啡二十五元", 2500),
        ("吃饭一百二十八元", 12800),
        ("吃饭两百元", 20000),
        ("咖啡二十五点五元", 2550),
        ("咖啡二十五块五", 2550),
        ("花了一千二百三十四元", 123400),
        ("买电脑1.2万元", 1200000),
        ("工资收入一万元", 1000000),
        ("工资收入一万二千元", 1200000),
        ("买电脑1,280.50元", 128050),
        ("☕️ 咖啡 ２５．５０元", 2550),
    ],
)
def test_exact_money_formats(text: str, expected: int) -> None:
    result = _parse(text)
    assert len(result.drafts) == 1
    assert result.drafts[0].amount_minor.value == expected
    assert not result.issues


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("咖啡0元", "INVALID_AMOUNT"),
        ("咖啡-25元", "INVALID_AMOUNT"),
        ("咖啡+25元", "INVALID_AMOUNT"),
        ("咖啡−25元", "INVALID_AMOUNT"),
        ("咖啡- 25元", "INVALID_AMOUNT"),
        ("咖啡负25元", "INVALID_AMOUNT"),
        ("咖啡.25元", "INVALID_AMOUNT"),
        ("咖啡0.5分", "AMOUNT_PRECISION"),
        ("咖啡25.555元", "AMOUNT_PRECISION"),
        ("咖啡9999999999999元", "AMOUNT_OUT_OF_RANGE"),
        ("咖啡负二十五元", "INVALID_AMOUNT"),
        ("花了一万万元", "INVALID_AMOUNT"),
        ("花了一二百元", "INVALID_AMOUNT"),
        ("咖啡二十五点五五五元", "AMOUNT_PRECISION"),
    ],
)
def test_invalid_amounts_never_become_positive_rounded_suggestions(text: str, code: str) -> None:
    result = _parse(text)
    assert result.status == "ambiguous"
    assert result.drafts[0].amount_minor.value is None
    assert code in _codes(result)


@pytest.mark.parametrize(
    "text",
    [
        "下午3点花25",
        "昨天下午三点半喝咖啡25元",
        "15:30花25元",
        "买2杯咖啡25元",
        "2026-10-01吃饭25元",
        "10月1日吃饭25元",
        "折扣20%买咖啡25元",
    ],
)
def test_clock_date_quantity_and_percentage_numbers_are_not_money(text: str) -> None:
    result = _parse(text)
    assert len(result.drafts) == 1
    assert result.drafts[0].amount_minor.value == 2500
    assert "MULTIPLE_AMOUNTS" not in _codes(result)


def test_exact_local_time_converts_to_utc_and_clears_period() -> None:
    result = _parse("昨天下午3点半花25元")
    draft = result.drafts[0]
    assert draft.occurred_at_utc.value == datetime(2026, 10, 1, 7, 30, tzinfo=UTC)
    assert draft.time_period.value is None
    assert draft.occurrence_precision == "exact"
    assert draft.occurred_on.value == date(2026, 10, 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("前天咖啡25", date(2026, 9, 30)),
        ("昨天咖啡25", date(2026, 10, 1)),
        ("今天咖啡25", date(2026, 10, 2)),
        ("咖啡25", date(2026, 10, 2)),
    ],
)
def test_reference_date_is_injected_and_resolved_without_wall_clock(
    text: str, expected: date
) -> None:
    assert _parse(text).drafts[0].occurred_on.value == expected


def test_vague_period_does_not_invent_an_exact_utc_instant() -> None:
    draft = _parse("昨晚咖啡25").drafts[0]
    assert draft.occurred_on.value == date(2026, 10, 1)
    assert draft.time_period.value == "evening"
    assert draft.occurred_at_utc.value is None


@pytest.mark.parametrize("text", ["明天咖啡25", "后天咖啡25", "2026年10月3日咖啡25"])
def test_future_dates_are_retained_but_blocked(text: str) -> None:
    result = _parse(text)
    assert "FUTURE_DATE" in _codes(result)
    assert result.status == "ambiguous"
    assert result.drafts[0].occurred_on.value is not None


def test_invalid_calendar_date_is_not_silently_replaced_with_today() -> None:
    result = _parse("2026年2月30日咖啡25")
    assert "INVALID_DATE" in _codes(result)
    assert result.drafts[0].occurred_on.value is None


def test_conflicting_dates_are_retained_as_alternatives() -> None:
    result = _parse("昨天还是今天咖啡25")
    assert result.drafts[0].occurred_on.value is None
    assert set(result.drafts[0].occurred_on.alternatives) == {date(2026, 10, 1), date(2026, 10, 2)}
    assert "FIELD_CONFLICT" in _codes(result)


@pytest.mark.parametrize(
    ("text", "reference", "code"),
    [
        ("2026-11-01 01:30咖啡25", date(2026, 11, 1), "AMBIGUOUS_LOCAL_TIME"),
        ("2026-03-08 02:30咖啡25", date(2026, 3, 8), "NONEXISTENT_LOCAL_TIME"),
    ],
)
def test_dst_fold_and_gap_require_an_explicit_choice(text: str, reference: date, code: str) -> None:
    result = _parse(text, reference_date=reference, time_zone="America/New_York")
    assert code in _codes(result)
    assert result.drafts[0].occurred_at_utc.value is None


@pytest.mark.parametrize("text", ["昨天吃饭128，朋友转我50", "买咖啡25打车40"])
def test_multiple_events_keep_every_amount_and_never_merge(text: str) -> None:
    result = _parse(text)
    assert result.status == "multiple_events"
    assert len(result.drafts) == 2
    assert "MULTIPLE_AMOUNTS" in _codes(result)
    assert len({draft.candidate_id for draft in result.drafts}) == 2


def test_second_income_event_has_inherited_date_but_independent_kind_and_note() -> None:
    result = _parse("昨天吃饭128，朋友转我50")
    first, second = result.drafts
    assert first.amount_minor.value == 12800
    assert first.kind.value == "expense"
    assert first.category_id.value == FOOD
    assert second.amount_minor.value == 5000
    assert second.kind.value == "income"
    assert second.occurred_on.value == date(2026, 10, 1)
    assert second.category_id.value is None
    assert second.note.value == "朋友转我50"


def test_coupon_or_unexplained_second_amount_is_ambiguous() -> None:
    result = _parse("花128元，优惠20元")
    assert result.status == "ambiguous"
    assert [draft.amount_minor.value for draft in result.drafts] == [12800, 2000]


def test_channel_same_named_account_is_not_an_implicit_mapping() -> None:
    draft = _parse("咖啡25微信支付").drafts[0]
    assert draft.payment_method_id.value == WECHAT
    assert draft.account_id.value is None


def test_explicit_channel_mapping_produces_visible_default() -> None:
    draft = _parse(
        "咖啡25微信支付", channel_account_mappings=(ChannelAccountMapping(WECHAT, WECHAT_ACCOUNT),)
    ).drafts[0]
    assert draft.account_id.value == WECHAT_ACCOUNT
    assert draft.account_id.origin == "default"
    assert draft.account_id.requires_confirmation
    assert draft.account_id.reason_code == "EXPLICIT_CHANNEL_MAPPING"


def test_unknown_mapping_target_cannot_create_an_account_candidate() -> None:
    draft = _parse(
        "咖啡25微信支付", channel_account_mappings=(ChannelAccountMapping(WECHAT, _id("missing")),)
    ).drafts[0]
    assert draft.account_id.value is None


def test_explicit_account_text_takes_precedence_over_mapping_and_default() -> None:
    draft = _parse(
        "钱包咖啡25微信支付",
        default_account_id=WECHAT_ACCOUNT,
        channel_account_mappings=(ChannelAccountMapping(WECHAT, WECHAT_ACCOUNT),),
    ).drafts[0]
    assert draft.account_id.value == WALLET
    assert draft.account_id.origin == "explicit"


def test_account_name_longer_than_channel_is_an_explicit_account() -> None:
    choices = (ParseChoice(WECHAT_ACCOUNT, "微信零钱账户"),)
    draft = _parse("微信零钱账户咖啡25", account_choices=choices).drafts[0]
    assert draft.account_id.value == WECHAT_ACCOUNT
    assert draft.account_id.origin == "explicit"


def test_default_account_does_not_claim_a_channel_association() -> None:
    draft = _parse("咖啡25微信支付", default_account_id=WALLET).drafts[0]
    assert draft.account_id.value == WALLET
    assert draft.account_id.origin == "default"
    assert draft.account_id.reason_code == "CHANNEL_NOT_ACCOUNT"


def test_conflicting_accounts_or_channels_are_not_resolved_by_first_match() -> None:
    draft = _parse(
        "钱包和备用卡咖啡25", account_choices=(*_ACCOUNTS, ParseChoice(_id("card"), "备用卡"))
    ).drafts[0]
    assert draft.account_id.value is None
    assert set(draft.account_id.alternatives) == {WALLET, _id("card")}
    result = _parse("咖啡25微信还是支付宝")
    assert result.drafts[0].payment_method_id.value is None
    assert set(result.drafts[0].payment_method_id.alternatives) == {WECHAT, ALIPAY}
    assert "FIELD_CONFLICT" in _codes(result)


def test_category_candidates_are_kind_compatible_and_known_identifiers_only() -> None:
    result = _parse("收到工资5000元")
    assert result.drafts[0].kind.value == "income"
    assert result.drafts[0].category_id.value == WAGES
    result = _parse("咖啡25", category_choices=())
    assert result.drafts[0].category_id.value is None


def test_category_conflict_is_not_resolved_by_first_keyword() -> None:
    draft = _parse("吃饭打车共50元").drafts[0]
    assert draft.category_id.value is None
    assert set(draft.category_id.alternatives) == {FOOD, TRANSIT}


def test_missing_money_still_returns_an_editable_draft() -> None:
    result = _parse("买了2杯咖啡")
    assert "MISSING_AMOUNT" in _codes(result)
    assert len(result.drafts) == 1
    assert result.drafts[0].amount_minor.value is None
    assert result.drafts[0].note.value == "买了2杯咖啡"


@pytest.mark.parametrize("text", ["咖啡大约25元", "咖啡25元左右", "咖啡不到25元", "咖啡约25元"])
def test_approximate_amount_is_never_presented_as_a_resolved_amount(text: str) -> None:
    result = _parse(text)
    assert result.drafts[0].amount_minor.value == 2500
    assert "APPROXIMATE_AMOUNT" in _codes(result)
    assert result.status == "ambiguous"


@pytest.mark.parametrize("text", ["转账25到钱包", "退款25元", "咖啡25美元", "咖啡$25"])
def test_special_event_or_foreign_currency_requires_manual_resolution(text: str) -> None:
    result = _parse(text)
    assert result.status == "ambiguous"
    assert _codes(result) & {"SPECIAL_EVENT_REQUIRED", "UNSUPPORTED_CURRENCY"}


def test_raw_unicode_evidence_offsets_survive_spaces_emoji_and_fullwidth_digits() -> None:
    text = "  ☕️ 昨天  咖啡２５．５０元，微信支付  "
    result = _parse(text)
    draft = result.drafts[0]
    assert (
        text[draft.amount_minor.evidence_spans[0].start : draft.amount_minor.evidence_spans[0].end]
        == "２５．５０元"
    )
    for field in (
        draft.amount_minor,
        draft.kind,
        draft.category_id,
        draft.payment_method_id,
        draft.occurred_on,
        draft.note,
    ):
        for span in field.evidence_spans:
            assert 0 <= span.start < span.end <= len(text)


def test_result_is_immutable_deterministic_and_revision_scoped() -> None:
    request = _request("咖啡25")
    first = LocalParser().parse(request)
    assert first == LocalParser().parse(request)
    second = LocalParser().parse(replace(request, revision=2))
    assert first.draft_id == second.draft_id == request.draft_id
    assert first.revision == 1 and second.revision == 2
    assert first.drafts[0].candidate_id != second.drafts[0].candidate_id
    with pytest.raises(FrozenInstanceError):
        first.status = "unsupported"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"text": " "}, "INVALID_TEXT"),
        ({"text": "a" * 4001}, "INVALID_TEXT"),
        ({"draft_id": "invalid"}, "INVALID_DRAFT_ID"),
        ({"revision": 0}, "INVALID_REVISION"),
        ({"revision": True}, "INVALID_REVISION"),
        ({"time_zone": "Unknown/Zone"}, "INVALID_TIMEZONE"),
        ({"locale": "en_US"}, "UNSUPPORTED_LOCALE"),
    ],
)
def test_invalid_request_returns_a_safe_issue(overrides: dict[str, object], code: str) -> None:
    result = LocalParser().parse(_request("咖啡25", **overrides))
    assert result.status == "unsupported"
    assert not result.drafts
    assert _codes(result) == {code}
