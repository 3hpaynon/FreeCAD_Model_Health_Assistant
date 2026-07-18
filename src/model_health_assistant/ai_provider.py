"""Provider-neutral interface and errors for explanation execution.

This module defines contracts only.  It contains no provider implementation,
network behavior, credentials, FreeCAD types, or provider SDK terminology.

A future real-provider adapter owns exactly six responsibilities: identity,
availability, request translation, network execution, response translation,
and mapping provider-specific failures to the neutral errors below.  Credential
storage and retrieval remain a separate future component; credentials must not
be added to the provider-neutral request, context builder, prompt builder,
analysis engine, or FreeCAD results dialog.
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


class AIMissingCredentialError(AIProviderError):
    """Raised when provider execution has no configured secure credential."""


class AICredentialBackendUnavailableError(AIProviderUnavailableError):
    """Raised when an OS credential backend cannot safely retrieve a key."""


class AIDependencyMissingError(AIProviderUnavailableError):
    """Raised when an approved optional provider dependency is unavailable."""


class AIRequestCancelledError(AIProviderError):
    """Raised when a provider request is cancelled before its result is accepted."""


class AIUnknownProviderError(AIProviderError):
    """Raised for a contained provider failure with no safer specific mapping."""


class AIPermissionError(AIProviderError):
    """Raised when the provider denies access to a requested resource."""


class AIConnectionError(AIProviderError):
    """Raised when the provider service cannot be reached."""


class AIInvalidRequestError(AIProviderError):
    """Raised when the provider rejects request parameters or a model ID."""


class AIUnsupportedModelError(AIInvalidRequestError):
    """Raised when the selected provider model is unavailable or unsupported."""


class AIProviderServerError(AIProviderError):
    """Raised when the remote provider reports a server-side failure."""


class AIProviderRefusalError(AIRequestRejectedError):
    """Raised when the model returns a structured refusal instead of output."""

    def __init__(self, message: str, *, request_identifier: str | None = None) -> None:
        super().__init__(message)
        self.request_identifier = request_identifier


class AIUnsupportedSDKError(AIProviderUnavailableError):
    """Raised when the installed provider SDK lacks the required API surface."""


@runtime_checkable
class AIExplanationProvider(Protocol):
    """Structural contract implemented by explanation providers.

    Providers receive only a completed ``AIExplanationRequest``.  They never
    receive a live FreeCAD document, a report path, a raw report file, or an API
    key from this interface.  A future network adapter performs provider-specific
    translation and execution inside ``explain`` and must return the shared
    structured response or raise one of this module's neutral errors.
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
    "AIConnectionError",
    "AICredentialBackendUnavailableError",
    "AIDependencyMissingError",
    "AIExplanationProvider",
    "AIInvalidResponseError",
    "AIInvalidRequestError",
    "AIMissingCredentialError",
    "AIPermissionError",
    "AIProviderError",
    "AIProviderRefusalError",
    "AIProviderServerError",
    "AIProviderUnavailableError",
    "AIRateLimitError",
    "AIRequestCancelledError",
    "AIRequestRejectedError",
    "AIRequestTimeoutError",
    "AIUnsupportedSDKError",
    "AIUnsupportedModelError",
    "AIUnknownProviderError",
]
