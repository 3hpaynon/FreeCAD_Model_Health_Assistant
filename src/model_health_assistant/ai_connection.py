"""Provider-neutral result contract for explicit connection tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from model_health_assistant.ai_models import AIUsageMetadata, JSONValue

AI_CONNECTION_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class AIConnectionTestResult:
    """Validated non-secret metadata from one explicit provider test."""

    provider_identifier: str
    provider_display_name: str
    actual_model_identifier: str | None = None
    request_identifier: str | None = None
    usage: AIUsageMetadata | None = None
    schema_version: str = AI_CONNECTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JSONValue]:
        return asdict(self)


@runtime_checkable
class AIConnectionTester(Protocol):
    """Narrow interface implemented by providers that support a real test."""

    def is_available(self) -> bool:
        ...

    def test_connection(self) -> AIConnectionTestResult:
        ...


__all__ = [
    "AI_CONNECTION_SCHEMA_VERSION",
    "AIConnectionTestResult",
    "AIConnectionTester",
]
