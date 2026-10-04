"""Ports for optional AI providers and operating-system credential storage."""

from collections.abc import Callable
from threading import Event
from typing import Protocol

from openledger.application.dto.ai import AIConfig
from openledger.application.dto.parsing import ParseRequest, ParseResult

type CancelCheck = Event | Callable[[], bool] | None


class AIProvider(Protocol):
    """Produce editable suggestions without authority to write financial records."""

    def parse(
        self, request: ParseRequest, config: AIConfig, key: str, cancel: CancelCheck = None
    ) -> ParseResult:
        """Parse only the supplied draft and minimal reference metadata."""
        ...


class CredentialStore(Protocol):
    """Keep secrets out of configuration files, exports and financial backups."""

    def get(self, target: str) -> str | None:
        """Read one application-owned credential, if present."""
        ...

    def set(self, target: str, key: str) -> None:
        """Persist one application-owned credential."""
        ...

    def delete(self, target: str) -> None:
        """Remove one application-owned credential; absence is harmless."""
        ...
