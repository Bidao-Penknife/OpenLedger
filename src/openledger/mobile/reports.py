"""Exact analytical JSON without desktop rendering dependencies."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any, cast

from openledger.application.dto.analytics import AnalyticsFilter
from openledger.domain.errors import LedgerError
from openledger.infrastructure.analytics import AnalyticsService


def exact_json(value: Any, key: str = "") -> Any:
    """Keep arbitrarily large financial totals and ratios out of Android doubles."""
    if isinstance(value, dict):
        return {name: exact_json(item, name) for name, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [exact_json(item) for item in value]
    if isinstance(value, Decimal) or (type(value) is int and key.endswith("_minor")):
        return str(value)
    return value


def report(service: AnalyticsService, body: dict[str, Any]) -> dict[str, Any]:
    """Build one immutable report snapshot for UI, PDF and image export alike."""
    if set(body) - {
        "start_on",
        "end_on",
        "book_ids",
        "account_ids",
        "category_ids",
        "tag_ids",
        "ranking_dimension",
        "ranking_metric",
        "rank_limit",
    }:
        raise LedgerError("INVALID_FILTER")
    dimensions: dict[str, tuple[str, ...]] = {}
    for key in ("book_ids", "account_ids", "category_ids", "tag_ids"):
        values = body.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 200
            or any(not isinstance(item, str) for item in values)
        ):
            raise LedgerError("INVALID_FILTER")
        dimensions[key] = tuple(values)
    start, end = body.get("start_on"), body.get("end_on")
    if not isinstance(start, str) or not isinstance(end, str):
        raise LedgerError("INVALID_FILTER")
    filters = AnalyticsFilter(date.fromisoformat(start), date.fromisoformat(end), **dimensions)
    return cast(
        dict[str, Any],
        exact_json(
            asdict(
                service.build_report(
                    filters,
                    ranking_dimension=body.get("ranking_dimension", "merchant"),
                    ranking_metric=body.get("ranking_metric", "amount"),
                    rank_limit=body.get("rank_limit", 10),
                )
            )
        ),
    )
