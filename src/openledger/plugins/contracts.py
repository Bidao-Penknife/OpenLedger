"""Versioned contracts for explicitly registered, trusted in-process adapters.

These contracts provide no ledger, database, file discovery or write authority.
They are an API boundary, not an isolation sandbox for hostile Python code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Literal, Protocol

from packaging.version import InvalidVersion, Version

from openledger.application.dto.analytics import ReportData
from openledger.application.dto.exchange import ExchangeMapping, FileTable
from openledger.application.dto.parsing import ParseRequest, ParseResult
from openledger.domain.errors import LedgerError

if TYPE_CHECKING:
    from openledger.application.dto.ai import AIConfig

PluginKind = Literal["ai", "analytics", "import", "theme"]
PLUGIN_API_VERSION = 1
PLUGIN_KINDS: frozenset[str] = frozenset({"ai", "analytics", "import", "theme"})


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """A stable identifier, supported API version and one adapter capability."""

    id: str
    name: str
    kind: PluginKind
    version: str
    api_version: int = PLUGIN_API_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.api_version) is not int
            or self.api_version != PLUGIN_API_VERSION
            or not isinstance(self.id, str)
            or re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", self.id) is None
            or len(self.id) > 100
            or not isinstance(self.name, str)
            or not self.name.strip()
            or len(self.name) > 100
            or self.kind not in PLUGIN_KINDS
            or not isinstance(self.version, str)
            or len(self.version) > 80
        ):
            raise LedgerError("INVALID_PLUGIN_MANIFEST")
        try:
            Version(self.version)
        except InvalidVersion as error:
            raise LedgerError("INVALID_PLUGIN_MANIFEST") from error


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Minimal display context without financial history or persistence handles."""

    app_version: str
    locale: str = "zh_CN"
    time_zone: str = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class PluginInsight:
    """An analytical suggestion, never a command to mutate financial records."""

    code: str
    title: str
    explanation: str


@dataclass(frozen=True, slots=True)
class PluginImportCandidate:
    """A read-only proposed row; application validation and user review remain mandatory."""

    source_row_number: int
    fields: tuple[tuple[str, str], ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ThemePalette:
    """Named color tokens, without executable code or arbitrary style imports."""

    colors: tuple[tuple[str, str], ...]


class AIPlugin(Protocol):
    """Parse a user-selected draft; suggestions are checked before any financial write."""

    def parse(
        self,
        request: ParseRequest,
        config: AIConfig,
        key: str,
        cancel: Event | Callable[[], bool] | None = None,
    ) -> ParseResult:
        """Return revision-bound suggestions using explicit caller configuration."""
        ...


class AnalyticsPlugin(Protocol):
    """Produce suggestions from an already selected immutable report."""

    def analyze(self, report: ReportData, context: PluginContext) -> tuple[PluginInsight, ...]:
        """Read a report without receiving database handles or financial commands."""
        ...


class ImportPlugin(Protocol):
    """Preview a supplied table without writing records or opening source paths."""

    def preview(
        self, table: FileTable, mapping: ExchangeMapping, context: PluginContext
    ) -> tuple[PluginImportCandidate, ...]:
        """Return candidate rows that the application must validate and confirm."""
        ...


class ThemePlugin(Protocol):
    """Supply display tokens to the application's theme renderer."""

    def palette(self, context: PluginContext) -> ThemePalette:
        """Return color tokens without modifying application state."""
        ...
