"""Provider-neutral interface and errors for explanation execution.

This module defines contracts only.  It contains no provider implementation,
network behavior, credentials, FreeCAD types, or provider SDK terminology.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from model_health_assistant.ai_models import (
    AIExplanationRequest,
    AIExplanationResponse,
)


class AIProviderError(RuntimeError):
    """Base class for controlled provider-neutral explanation failures."""


class AIProviderUnavailableError(AIProviderError):
    """Raised when the selected provider cannot execute requests."""


class AIRequestRejectedError(AIProviderError):
    """Raised when a provider declines a structurally valid request."""


class AIInvalidResponseError(AIProviderError):
    """Raised when provider output does not satisfy the response contract."""


class AIRequestTimeoutError(AIProviderError):
    """Reserved for a future provider request that exceeds its time limit."""


class AIAuthenticationError(AIProviderError):
    """Reserved for future provider authentication failures."""


class AIRateLimitError(AIProviderError):
    """Reserved for future provider rate-limit responses."""


@runtime_checkable
class AIExplanationProvider(Protocol):
    """Structural contract implemented by explanation providers.

    Providers receive only a completed ``AIExplanationRequest``.  They never
    receive a live FreeCAD document, a report path, a raw report file, or an API
    key from this interface.
    """

    @property
    def provider_identifier(self) -> str:
        """Return a stable provider-neutral identifier."""

        ...

    @property
    def display_name(self) -> str:
        """Return a user-facing provider name."""

        ...

    def is_available(self) -> bool:
        """Return whether the provider can currently accept a request."""

        ...

    def capability_description(self) -> str:
        """Describe the provider's explanation capability without executing."""

        ...

    def explain(self, request: AIExplanationRequest) -> AIExplanationResponse:
        """Execute one structured explanation request."""

        ...


__all__ = [
    "AIAuthenticationError",
    "AIExplanationProvider",
    "AIInvalidResponseError",
    "AIProviderError",
    "AIProviderUnavailableError",
    "AIRateLimitError",
    "AIRequestRejectedError",
    "AIRequestTimeoutError",
]
