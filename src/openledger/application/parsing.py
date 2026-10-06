"""Conservative local Chinese parsing: suggestions only, no writes or network calls."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger.application.dto.parsing import (
    ChannelAccountMapping,
    FieldCandidate,
    Origin,
    ParseChoice,
    ParsedDraft,
    ParseIssue,
    ParseRequest,
    ParseResult,
    ParseStatus,
    Span,
)
from openledger.domain.currencies import currency, format_minor
from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount, validate_minor
from openledger.domain.values import normalize_id

_DIGITS = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
_ZH = "零〇一二两三四五六七八九十百千万亿"
_ZH_DIGITS = "零〇一二两三四五六七八九"
_WIDTH = str.maketrans("０１２３４５６７８９．，￥＋－−﹣", "0123456789.,¥+---")
_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:万|千)?")
_CHINESE_MONEY = re.compile(
    rf"[负正]?[{_ZH}]+(?:点[{_ZH_DIGITS}]+)?(?:元|块)"
    rf"(?:[{_ZH_DIGITS}](?:角|毛|分)?)?"
)
_DATE = re.compile(r"(?:(\d{4})[年/-])?(\d{1,2})[月/-](\d{1,2})(?:日|号)?")
_TIME = re.compile(
    rf"(?P<hour>\d{{1,2}}|[{_ZH}]+)(?:[:：](?P<colon>\d{{2}})|"
    rf"点(?:(?P<half>半)|(?P<minute>\d{{1,2}}|[{_ZH}]+)(?:分)?)?)"
)
_QUANTITY = re.compile(r"\d+(?:\.\d+)?(?:杯|份|个|斤|瓶|张|次|人|天|个月|%)")
_RELATIVE = (
    ("大前天", -3),
    ("前天", -2),
    ("昨天", -1),
    ("昨日", -1),
    ("昨晚", -1),
    ("今天", 0),
    ("今日", 0),
    ("今晚", 0),
    ("明天", 1),
    ("明日", 1),
    ("明晚", 1),
    ("后天", 2),
)
_UNSUPPORTED_FUTURE = re.compile(
    rf"大后天|下(?:个)?(?:周|星期|礼拜|月|季度)|明年|后年|下半年|"
    rf"(?:\d+|[{_ZH}]+)(?:天|周|个月|年)(?:以)?后"
)
_PERIODS = (
    ("凌晨", "night"),
    ("半夜", "night"),
    ("早晨", "morning"),
    ("早上", "morning"),
    ("上午", "morning"),
    ("中午", "noon"),
    ("下午", "afternoon"),
    ("傍晚", "evening"),
    ("晚上", "evening"),
    ("昨晚", "evening"),
    ("今晚", "evening"),
    ("明晚", "evening"),
    ("夜里", "night"),
)
_CATEGORY_RULES = {
    "餐饮": ("火锅", "咖啡", "吃饭", "吃", "饭", "餐", "奶茶", "外卖", "早餐", "午餐", "晚餐"),
    "交通": ("打车", "出租车", "公交", "地铁", "火车", "高铁", "机票", "车费", "滴滴"),
    "学习": ("书", "课程", "培训", "学费", "学习", "教材"),
    "工资": ("工资", "薪资", "薪水", "奖金"),
}
_INCOME = re.compile(r"工资|薪资|薪水|奖金|收入|收款|到账|收到|转给我|转我|入账|赚")
_EXPENSE = re.compile(r"支出|消费|花了?|买|支付|付款|付了?|吃|喝|咖啡|打车|车费|餐")
_EVENT_VERB = re.compile(r"吃|喝|买|花|打车|工资|收入|到账|收款|转我|转给我|付款|付了|咖啡")


@dataclass(frozen=True)
class _Amount:
    span: Span
    value: int | None
    issue: str | None = None


def _candidate[T](
    value: T | None,
    origin: Origin = "explicit",
    *,
    spans: tuple[Span, ...] = (),
    rule: str | None = None,
    alternatives: tuple[T, ...] = (),
    reason: str | None = None,
) -> FieldCandidate[T]:
    return FieldCandidate(value, origin, spans, rule, alternatives, True, reason)


def _chinese_integer(text: str) -> int:
    """Convert a bounded Chinese integer; malformed repeated units are rejected."""
    if not text or len(text) > 24:
        raise LedgerError("INVALID_AMOUNT")
    if all(character in _DIGITS for character in text):
        return int("".join(str(_DIGITS[character]) for character in text))
    total = section = number = 0
    previous_small_unit = 10_000
    previous_large_unit = 1_000_000_000
    previous_digit = False
    for character in text:
        if character in _DIGITS:
            if previous_digit and number and _DIGITS[character]:
                raise LedgerError("INVALID_AMOUNT")
            number = _DIGITS[character]
            previous_digit = True
        elif character in _UNITS:
            unit = _UNITS[character]
            if unit < 10_000:
                if unit >= previous_small_unit:
                    raise LedgerError("INVALID_AMOUNT")
                section += (number or 1) * unit
                previous_small_unit = unit
            else:
                if unit >= previous_large_unit:
                    raise LedgerError("INVALID_AMOUNT")
                section += number
                total += (section or 1) * unit
                section = 0
                previous_small_unit = 10_000
                previous_large_unit = unit
            number = 0
            previous_digit = False
        else:
            raise LedgerError("INVALID_AMOUNT")
    return total + section + number


def _chinese_amount(text: str) -> int:
    if text.startswith(("负", "正")):
        raise LedgerError("INVALID_AMOUNT")
    yuan, fraction = re.split("[元块]", text, maxsplit=1)
    if "点" in yuan:
        integer, decimal = yuan.split("点", maxsplit=1)
        decimal_digits = "".join(str(_DIGITS[c]) for c in decimal)
        amount = parse_amount(f"{_chinese_integer(integer)}.{decimal_digits}")
        if fraction:
            raise LedgerError("FIELD_CONFLICT")
        return amount
    amount = _chinese_integer(yuan) * 100
    if fraction:
        amount += _DIGITS[fraction[0]] * (1 if fraction.endswith("分") else 10)
    return validate_minor(amount)


def _time_matches(text: str) -> tuple[re.Match[str], ...]:
    chinese_money = tuple(
        Span(match.start(), match.end()) for match in _CHINESE_MONEY.finditer(text)
    )
    return tuple(
        match
        for match in _TIME.finditer(text)
        if not _overlaps(Span(match.start(), match.end()), chinese_money)
    )


def _protected_spans(text: str) -> tuple[Span, ...]:
    return tuple(
        Span(match.start(), match.end())
        for expression in (_DATE, _QUANTITY)
        for match in expression.finditer(text)
    ) + tuple(Span(match.start(), match.end()) for match in _time_matches(text))


def _overlaps(span: Span, protected: tuple[Span, ...]) -> bool:
    return any(span.start < other.end and span.end > other.start for other in protected)


def _amounts(text: str, code: str = "CNY") -> tuple[_Amount, ...]:
    normalized = text.translate(_WIDTH)  # Every replacement is one character: indices stay valid.
    protected = _protected_spans(normalized)
    amounts: list[_Amount] = []
    for match in _NUMBER.finditer(normalized):
        span = Span(match.start(), match.end())
        if _overlaps(span, protected):
            continue
        if any(_overlaps(span, (amount.span,)) for amount in amounts):
            continue
        token = match.group()
        end = span.end
        negative_prefix = re.search(r"(?:[+-]|负|正)\s*$", normalized[: span.start])
        leading_decimal = span.start > 0 and normalized[span.start - 1] == "."
        divisor = 1
        # "25块5" and "25元5角" are one amount, never two independent expenses.
        fraction = re.match(r"[元块](\d)(?!\d)(?:角|毛|分)?", normalized[end:])
        if fraction and "." not in token and token[-1].isdigit():
            token += "." + ("0" if fraction.group().endswith("分") else "") + fraction[1]
            end += fraction.end()
        elif end < len(normalized) and normalized[end] in "元块":
            end += 1
        elif end < len(normalized) and normalized[end] in "角毛分":
            divisor = 100 if normalized[end] == "分" else 10
            end += 1
        multiplier = 10000 if token.endswith("万") else 1000 if token.endswith("千") else 1
        try:
            if negative_prefix or leading_decimal:
                raise LedgerError("INVALID_AMOUNT")
            value = parse_amount(token.rstrip("万千").replace(",", ""), code) * multiplier
            if value % divisor:
                raise LedgerError("AMOUNT_PRECISION")
            value //= divisor
            validate_minor(value)
            amounts.append(_Amount(Span(span.start, end), value))
        except LedgerError as error:
            amounts.append(_Amount(Span(span.start, end), None, error.code))
    for match in _CHINESE_MONEY.finditer(normalized):
        span = Span(match.start(), match.end())
        if _overlaps(span, protected) or any(_overlaps(span, (a.span,)) for a in amounts):
            continue
        try:
            chinese_value = _chinese_amount(match.group())
            chinese_text = format_minor(chinese_value).removesuffix(".00")
            native = parse_amount(chinese_text, code)
            amounts.append(_Amount(span, native))
        except LedgerError as error:
            amounts.append(_Amount(span, None, error.code))
    return tuple(sorted(amounts, key=lambda item: item.span.start))


def _match_choices(
    text: str, offset: int, choices: tuple[ParseChoice, ...]
) -> tuple[tuple[ParseChoice, Span], ...]:
    hits: list[tuple[ParseChoice, Span]] = []
    for choice in choices:
        # Prefer the longest label to avoid aliases taking a prefix of another label.
        for label in sorted({choice.name, *choice.aliases}, key=len, reverse=True):
            if label and (position := text.find(label)) >= 0:
                hits.append((choice, Span(offset + position, offset + position + len(label))))
                break
    return tuple(hits)


def _choice_candidate(
    hits: tuple[tuple[ParseChoice, Span], ...], *, rule: str
) -> FieldCandidate[str]:
    if not hits:
        return _candidate(None, reason="MISSING_REQUIRED_FIELD")
    unique = tuple(dict.fromkeys(choice.id for choice, _ in hits))
    if len(unique) > 1:
        return _candidate(
            None,
            spans=tuple(span for _, span in hits),
            rule=rule,
            alternatives=unique,
            reason="FIELD_CONFLICT",
        )
    return _candidate(unique[0], spans=tuple(span for _, span in hits), rule=rule)


def _date_candidates(
    text: str, offset: int, reference: date
) -> tuple[tuple[date | None, Span, str], ...]:
    hits: list[tuple[date | None, Span, str]] = []
    occupied: list[Span] = []
    for word, delta in _RELATIVE:
        for match in re.finditer(word, text):
            span = Span(offset + match.start(), offset + match.end())
            if _overlaps(span, tuple(occupied)):
                continue
            occupied.append(span)
            try:
                value: date | None = reference + timedelta(days=delta)
            except OverflowError:
                value = None
            hits.append((value, span, "date.relative.v1"))
    for match in _DATE.finditer(text.translate(_WIDTH)):
        try:
            value = date(int(match[1] or reference.year), int(match[2]), int(match[3]))
        except ValueError:
            value = None
        hits.append((value, Span(offset + match.start(), offset + match.end()), "date.calendar.v1"))
    return tuple(hits)


def _occurrence(
    text: str, offset: int, request: ParseRequest, candidate_id: str, issues: list[ParseIssue]
) -> tuple[FieldCandidate[date], FieldCandidate[str], FieldCandidate[datetime]]:
    dates = _date_candidates(text, offset, request.reference_date)
    # A shared date prefix is context for subsequent events in the same sentence.
    if not dates and offset:
        dates = _date_candidates(request.text[:offset], 0, request.reference_date)
    unique_dates = tuple(dict.fromkeys(value for value, _, _ in dates if value is not None))
    date_field: FieldCandidate[date]
    future = _UNSUPPORTED_FUTURE.search(text)
    future_offset = offset
    if future is None and offset and not dates:
        future = _UNSUPPORTED_FUTURE.search(request.text[:offset])
        future_offset = 0
    if future is not None:
        span = Span(future_offset + future.start(), future_offset + future.end())
        date_field = _candidate(None, spans=(span,), reason="FUTURE_DATE")
        issues.append(
            ParseIssue(
                "FUTURE_DATE",
                "occurred_on",
                "检测到未来时间，不能默认为今天；请手动核对日期。",
                span=span,
                candidate_id=candidate_id,
            )
        )
    elif any(value is None for value, _, _ in dates):
        date_field = _candidate(None, reason="INVALID_DATE")
        issues.append(
            ParseIssue(
                "INVALID_DATE", "occurred_on", "日期无效，请手动核对。", candidate_id=candidate_id
            )
        )
    elif len(unique_dates) > 1:
        date_field = _candidate(None, alternatives=unique_dates, reason="FIELD_CONFLICT")
        issues.append(
            ParseIssue(
                "FIELD_CONFLICT",
                "occurred_on",
                "检测到不同日期，请选择日期。",
                candidate_id=candidate_id,
            )
        )
    elif dates:
        date_field = _candidate(
            unique_dates[0], spans=tuple(s for _, s, _ in dates), rule=dates[0][2]
        )
    else:
        date_field = _candidate(request.reference_date, "default", rule="date.today.v1")
    if date_field.value and date_field.value > request.reference_date:
        issues.append(
            ParseIssue(
                "FUTURE_DATE",
                "occurred_on",
                "未来日期不能记账，请核对日期。",
                candidate_id=candidate_id,
            )
        )

    periods = tuple(
        (value, Span(offset + position, offset + position + len(word)))
        for word, value in _PERIODS
        if (position := text.find(word)) >= 0
    )
    unique_periods = tuple(dict.fromkeys(value for value, _ in periods))
    period = _candidate(
        unique_periods[0] if len(unique_periods) == 1 else None,
        spans=tuple(span for _, span in periods),
        rule="time.period.v1" if periods else None,
        alternatives=unique_periods if len(unique_periods) > 1 else (),
        reason="FIELD_CONFLICT" if len(unique_periods) > 1 else None,
    )
    if len(unique_periods) > 1:
        issues.append(
            ParseIssue(
                "FIELD_CONFLICT",
                "time_period",
                "检测到不同时间段，请手动核对。",
                candidate_id=candidate_id,
            )
        )
    time_hits = _time_matches(text.translate(_WIDTH))
    if not time_hits:
        return date_field, period, _candidate(None)
    if len(time_hits) > 1:
        issues.append(
            ParseIssue(
                "FIELD_CONFLICT",
                "occurred_at_utc",
                "检测到多个时间，请手动核对。",
                candidate_id=candidate_id,
            )
        )
        return date_field, _candidate(None), _candidate(None, reason="FIELD_CONFLICT")
    match = time_hits[0]
    span = Span(offset + match.start(), offset + match.end())
    if date_field.value is None:
        return date_field, _candidate(None), _candidate(None, spans=(span,), reason="INVALID_DATE")
    hour_text, minute_text = match["hour"], match["colon"] or match["minute"]
    hour = int(hour_text) if hour_text.isdigit() else _chinese_integer(hour_text)
    minute = (
        30
        if match["half"]
        else (
            int(minute_text)
            if minute_text and minute_text.isdigit()
            else _chinese_integer(minute_text)
            if minute_text
            else 0
        )
    )
    if (period.value in ("afternoon", "evening") and 1 <= hour < 12) or (
        period.value == "noon" and 1 <= hour <= 3
    ):
        hour += 12
    elif period.value == "night" and hour == 12:
        hour = 0
    try:
        local = datetime.combine(date_field.value, time(hour, minute))
    except ValueError:
        issues.append(
            ParseIssue(
                "INVALID_TIME", "occurred_at_utc", "时间无效，请手动核对。", span, candidate_id
            )
        )
        return date_field, _candidate(None), _candidate(None, spans=(span,), reason="INVALID_TIME")
    zone = ZoneInfo(request.time_zone)
    valid: set[datetime] = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            valid.add(instant)
    if len(valid) != 1:
        code = "AMBIGUOUS_LOCAL_TIME" if valid else "NONEXISTENT_LOCAL_TIME"
        issues.append(
            ParseIssue(
                code,
                "occurred_at_utc",
                "此时刻遇到夏令时变化，请手动确认时间。",
                span,
                candidate_id,
            )
        )
        return (
            date_field,
            _candidate(None),
            _candidate(None, spans=(span,), alternatives=tuple(sorted(valid)), reason=code),
        )
    return (
        date_field,
        _candidate(None),
        _candidate(next(iter(valid)), spans=(span,), rule="time.exact.v1"),
    )


def _kind(text: str, offset: int) -> FieldCandidate[str]:
    income, expense = _INCOME.search(text), _EXPENSE.search(text)
    if income and expense:
        return _candidate(None, alternatives=("expense", "income"), reason="FIELD_CONFLICT")
    match = income or expense
    if match:
        return _candidate(
            "income" if income else "expense",
            spans=(Span(offset + match.start(), offset + match.end()),),
            rule="kind.keywords.v1",
        )
    return _candidate("expense", "rule_suggestion", rule="kind.default-expense.v1")


def _category(
    text: str, offset: int, kind: str | None, choices: tuple[ParseChoice, ...]
) -> FieldCandidate[str]:
    compatible = tuple(choice for choice in choices if choice.kind is None or choice.kind == kind)
    explicit = _match_choices(text, offset, compatible)
    if explicit:
        return _choice_candidate(explicit, rule="category.label.v1")
    hits: list[tuple[ParseChoice, Span]] = []
    for choice in compatible:
        for label in (choice.name, *choice.aliases):
            words = _CATEGORY_RULES.get(label, ())
            positions = tuple((text.find(word), word) for word in words if word in text)
            if positions:
                position, word = min(positions, key=lambda value: (value[0], -len(value[1])))
                hits.append((choice, Span(offset + position, offset + position + len(word))))
                break
    if not hits:
        return _candidate(None, reason="MISSING_REQUIRED_FIELD")
    result = _choice_candidate(tuple(hits), rule="category.keywords.v1")
    return _candidate(
        result.value,
        "rule_suggestion",
        spans=result.evidence_spans,
        rule=result.rule_id,
        alternatives=result.alternatives,
        reason=result.reason_code,
    )


def _account(
    text: str, offset: int, request: ParseRequest, payment: FieldCandidate[str]
) -> FieldCandidate[str]:
    # Channel labels alone do not identify the real account, even if named identically.
    channel_spans = payment.evidence_spans
    hits = tuple(
        (choice, span)
        for choice, span in _match_choices(text, offset, request.account_choices)
        if not any(
            channel.start <= span.start and channel.end >= span.end for channel in channel_spans
        )
    )
    if hits:
        return _choice_candidate(hits, rule="account.label.v1")
    mapping: tuple[ChannelAccountMapping, ...] = tuple(
        item
        for item in request.channel_account_mappings
        if item.payment_method_id == payment.value
        and any(choice.id == item.account_id for choice in request.account_choices)
    )
    if mapping:
        mapped = tuple(dict.fromkeys(item.account_id for item in mapping))
        if len(mapped) == 1:
            return _candidate(
                mapped[0],
                "default",
                rule="account.channel-mapping.v1",
                reason="EXPLICIT_CHANNEL_MAPPING",
            )
        return _candidate(None, alternatives=mapped, reason="FIELD_CONFLICT")
    if request.default_account_id and any(
        choice.id == request.default_account_id for choice in request.account_choices
    ):
        return _candidate(
            request.default_account_id,
            "default",
            rule="account.default.v1",
            reason="CHANNEL_NOT_ACCOUNT" if payment.value else None,
        )
    return _candidate(
        None, reason="CHANNEL_NOT_ACCOUNT" if payment.value else "MISSING_REQUIRED_FIELD"
    )


def _party(text: str, offset: int) -> FieldCandidate[str]:
    match = re.search(r"朋友|同事|家人|父母|妈妈|爸爸|老板", text)
    if match:
        return _candidate(
            match.group(),
            spans=(Span(offset + match.start(), offset + match.end()),),
            rule="party.relationship.v1",
        )
    return _candidate(None)


class LocalParser:
    """Parse bounded natural-language text using deterministic, inspectable local rules."""

    provider_id = "openledger.builtin.local-parser"
    api_version = 1

    def parse(self, request: ParseRequest) -> ParseResult:
        """Return editable candidates with raw evidence; never persist or send text."""
        invalid = self._request_issue(request)
        if invalid:
            return ParseResult(
                request.draft_id, request.revision, self.provider_id, "unsupported", (), (invalid,)
            )
        amounts = _amounts(request.text, request.currency_code)
        issues: list[ParseIssue] = []
        drafts: list[ParsedDraft] = []
        explicit_codes = {
            code
            for code, labels in {
                "CNY": r"人民币|CNY|RMB|￥",
                "USD": r"美元|美金|USD|\$",
                "EUR": r"欧元|EUR|€",
                "GBP": r"英镑|GBP|£",
                "JPY": r"日元|JPY",
                "HKD": r"港币|港元|HKD",
                "KWD": r"KWD",
            }.items()
            if re.search(labels, request.text, re.IGNORECASE)
        }
        if re.search(r"转账|转到|转入|转出|退款|退回", request.text):
            issues.append(
                ParseIssue(
                    "SPECIAL_EVENT_REQUIRED", "kind", "转账和退款需要专用表单，请选择对应操作。"
                )
            )
        if re.search(
            r"大约|约莫|大概|差不多|不到|超过|左右|多元|多块|约\s*[0-9０-９]", request.text
        ):
            issues.append(
                ParseIssue(
                    "APPROXIMATE_AMOUNT", "amount_minor", "金额描述不确定，请填写实际金额后确认。"
                )
            )
        if not amounts:
            issues.append(
                ParseIssue("MISSING_AMOUNT", "amount_minor", "未识别到金额，请手动补充。")
            )
            amounts = (_Amount(Span(0, 0), None),)
        boundaries = [0]
        for previous in amounts[:-1]:
            position = previous.span.end
            while position < len(request.text) and request.text[position] in "，,。；; \n\t":
                position += 1
            boundaries.append(position)
        boundaries.append(len(request.text))
        for index, amount in enumerate(amounts):
            start, end = boundaries[index], boundaries[index + 1]
            span = Span(start, end)
            text = request.text[start:end]
            candidate_id = str(
                uuid5(NAMESPACE_URL, f"{request.draft_id}:{request.revision}:{index}")
            )
            kind = _kind(text, start)
            if kind.reason_code == "FIELD_CONFLICT":
                issues.append(
                    ParseIssue(
                        "FIELD_CONFLICT",
                        "kind",
                        "同时出现收支描述，请选择类型或拆分记账。",
                        span,
                        candidate_id,
                    )
                )
            payment = _choice_candidate(
                _match_choices(text, start, request.payment_method_choices), rule="payment.label.v1"
            )
            if payment.value is None and not payment.alternatives:
                payment = _candidate(None)
            account = _account(text, start, request, payment)
            native_code = next(
                (
                    choice.currency_code
                    for choice in request.account_choices
                    if choice.id == account.value
                ),
                request.currency_code,
            )
            native_amount = next(
                (item for item in _amounts(request.text, native_code) if item.span == amount.span),
                amount,
            )
            amount = native_amount
            if amount.issue:
                issues.append(
                    ParseIssue(
                        amount.issue,
                        "amount_minor",
                        "金额格式或精度无效，请核对金额。",
                        amount.span,
                        candidate_id,
                    )
                )
            if explicit_codes and explicit_codes != {native_code}:
                issues.append(
                    ParseIssue(
                        "UNSUPPORTED_CURRENCY",
                        "currency_code",
                        "文字币种与所选账户不一致，请核对账户和金额。",
                        span,
                        candidate_id,
                    )
                )
            category = _category(text, start, kind.value, request.category_choices)
            for field_name, field_value in (
                ("payment_method_id", payment),
                ("account_id", account),
                ("category_id", category),
            ):
                if field_value.alternatives:
                    issues.append(
                        ParseIssue(
                            "FIELD_CONFLICT",
                            field_name,
                            "识别到多个候选，请手动选择。",
                            span,
                            candidate_id,
                        )
                    )
            business_date, period, instant = _occurrence(text, start, request, candidate_id, issues)
            drafts.append(
                ParsedDraft(
                    candidate_id=candidate_id,
                    span=span,
                    time_zone=request.time_zone,
                    kind=kind,
                    amount_minor=_candidate(
                        native_amount.value,
                        spans=(amount.span,) if amount.value is not None else (),
                        rule="amount.cny.v1" if native_code == "CNY" else "amount.native.v1",
                        reason=amount.issue or ("MISSING_AMOUNT" if amount.value is None else None),
                    ),
                    occurred_on=business_date,
                    time_period=period,
                    occurred_at_utc=instant,
                    book_id=_candidate(
                        request.current_book_id,
                        "default",
                        rule="book.context.v1",
                        reason=None if request.current_book_id else "MISSING_REQUIRED_FIELD",
                    ),
                    account_id=account,
                    category_id=category,
                    payment_method_id=payment,
                    counterparty=_party(text, start),
                    note=_candidate(
                        text.strip(), "rule_suggestion", spans=(span,), rule="note.original.v1"
                    ),
                    currency_code=native_code,
                )
            )
        status: ParseStatus = "single"
        if len(amounts) > 1:
            status = (
                "multiple_events"
                if all(
                    _EVENT_VERB.search(request.text[boundaries[i] : boundaries[i + 1]])
                    for i in range(len(amounts))
                )
                else "ambiguous"
            )
            issues.append(
                ParseIssue(
                    "MULTIPLE_AMOUNTS",
                    "amount_minor",
                    "识别到多个金额，请拆分或逐笔选择草稿后确认。",
                )
            )
        elif any(issue.blocking for issue in issues):
            status = "ambiguous"
        return ParseResult(
            request.draft_id,
            request.revision,
            self.provider_id,
            status,
            tuple(drafts),
            tuple(issues),
        )

    @staticmethod
    def _request_issue(request: ParseRequest) -> ParseIssue | None:
        try:
            normalize_id(request.draft_id)
        except LedgerError:
            return ParseIssue("INVALID_DRAFT_ID", "draft_id", "草稿标识无效。")
        if (
            isinstance(request.revision, bool)
            or not isinstance(request.revision, int)
            or request.revision < 1
        ):
            return ParseIssue("INVALID_REVISION", "revision", "草稿版本无效。")
        if (
            not isinstance(request.text, str)
            or not request.text.strip()
            or len(request.text) > 4000
        ):
            return ParseIssue("INVALID_TEXT", "text", "请输入 1–4000 个字符。")
        if isinstance(request.reference_date, datetime) or not isinstance(
            request.reference_date, date
        ):
            return ParseIssue("INVALID_DATE", "reference_date", "参考日期无效。")
        try:
            ZoneInfo(request.time_zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return ParseIssue("INVALID_TIMEZONE", "time_zone", "时区无效，请选择有效时区。")
        if request.locale not in ("zh_CN", "zh-CN"):
            return ParseIssue("UNSUPPORTED_LOCALE", "locale", "当前本地规则仅支持中文。")
        try:
            currency(request.currency_code)
            for choice in request.account_choices:
                currency(choice.currency_code)
        except LedgerError:
            return ParseIssue("UNSUPPORTED_CURRENCY", "currency_code", "账户币种无效。")
        return None
