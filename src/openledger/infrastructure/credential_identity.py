"""Platform-independent identity for one explicit AI endpoint and local data set."""

import hashlib
from pathlib import Path

from openledger.application.dto.ai import AIConfig


def credential_target(data_dir: Path, config: AIConfig) -> str:
    """Scope credentials without importing an operating-system credential backend."""
    identity = str(data_dir.resolve()).casefold() + "\n" + config.base_url.rstrip("/")
    return "OpenLedger/AI/" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
