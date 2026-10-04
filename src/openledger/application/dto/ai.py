"""Non-secret configuration for an explicitly invoked, optional AI parser."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AIConfig:
    """Endpoint and model preferences; credentials are deliberately separate."""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    allow_local_http: bool = False
