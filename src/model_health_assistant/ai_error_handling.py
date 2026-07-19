"""Central user-safe AI error categories and redacted diagnostics.

Normal dialogs consume only ``AIErrorCategoryResult.user_message``.  The
diagnostic formatter deliberately does not include ``str(error)``, response
objects, headers, request context, tracebacks, credentials, or local paths.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Literal

from model_health_assistant.ai_provider import (
    AIAuthenticationError,
    AIConnectionError,
    AICredentialBackendUnavailableError,
    AIDependencyMissingError,
    AIInvalidRequestError,
    AIInvalidResponseError,
    AIMissingCredentialError,
    AIOutputLimitError,
    AIPermissionError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIRequestCancelledError,
    AIRequestTimeoutError,
    AIUnsupportedModelError,
    AIUnsupportedSDKError,
)

AIErrorCategory = Literal[
    "api_key_not_configured",
    "secure_credential_backend_unavailable",
    "invalid_authentication",
    "insufficient_permissions",
    "unsupported_model",
    "invalid_request",
    "rate_limit_reached",
    "connection_failure",
    "timeout",
    "provider_server_error",
    "provider_refusal",
    "output_limit_reached",
    "malformed_structured_response",
    "dependency_missing",
    "cancelled_request",
    "unknown_provider_failure",
]


@dataclass(frozen=True, slots=True)
class AIErrorCategoryResult:
    """A non-secret error category suitable for normal UI presentation."""

    category: AIErrorCategory
    user_message: str
    retryable: bool


_CATEGORY_RESULTS: dict[AIErrorCategory, AIErrorCategoryResult] = {
    "api_key_not_configured": AIErrorCategoryResult(
        "api_key_not_configured",
        "No provider API key is configured in secure storage.",
        False,
    ),
    "secure_credential_backend_unavailable": AIErrorCategoryResult(
        "secure_credential_backend_unavailable",
        "Secure credential storage is unavailable. No API key was read.",
        False,
    ),
    "invalid_authentication": AIErrorCategoryResult(
        "invalid_authentication",
        "The provider rejected the configured API key.",
        False,
    ),
    "insufficient_permissions": AIErrorCategoryResult(
        "insufficient_permissions",
        "The configured provider account lacks permission for this request.",
        False,
    ),
    "unsupported_model": AIErrorCategoryResult(
        "unsupported_model",
        "The configured model is unsupported or unavailable.",
        False,
    ),
    "invalid_request": AIErrorCategoryResult(
        "invalid_request",
        "The provider rejected the request as invalid.",
        False,
    ),
    "rate_limit_reached": AIErrorCategoryResult(
        "rate_limit_reached",
        "The provider rate limit was reached. Try again later.",
        True,
    ),
    "connection_failure": AIErrorCategoryResult(
        "connection_failure",
        "The provider could not be reached. Check the connection and try again.",
        True,
    ),
    "timeout": AIErrorCategoryResult(
        "timeout",
        "The provider request timed out.",
        True,
    ),
    "provider_server_error": AIErrorCategoryResult(
        "provider_server_error",
        "The provider reported a temporary server error.",
        True,
    ),
    "provider_refusal": AIErrorCategoryResult(
        "provider_refusal",
        "The provider declined to produce the requested answer.",
        False,
    ),
    "output_limit_reached": AIErrorCategoryResult(
        "output_limit_reached",
        "The provider reached the configured output-token limit before the "
        "structured response was complete. Increase Maximum output and try again.",
        False,
    ),
    "malformed_structured_response": AIErrorCategoryResult(
        "malformed_structured_response",
        "The provider returned malformed or invalid structured output.",
        False,
    ),
    "dependency_missing": AIErrorCategoryResult(
        "dependency_missing",
        "An approved optional provider dependency is unavailable.",
        False,
    ),
    "cancelled_request": AIErrorCategoryResult(
        "cancelled_request",
        "The provider request was cancelled.",
        False,
    ),
    "unknown_provider_failure": AIErrorCategoryResult(
        "unknown_provider_failure",
        "The provider request failed safely for an unknown reason.",
        False,
    ),
}


def categorize_ai_error(error: BaseException) -> AIErrorCategoryResult:
    """Map a controlled or unknown failure without exposing its raw details."""

    if not isinstance(error, BaseException):
        raise TypeError("error must be a BaseException")

    workflow_category = _workflow_status_category(error)
    if workflow_category is not None:
        return _CATEGORY_RESULTS[workflow_category]

    mappings: tuple[tuple[type[BaseException], AIErrorCategory], ...] = (
        (AIMissingCredentialError, "api_key_not_configured"),
        (
            AICredentialBackendUnavailableError,
            "secure_credential_backend_unavailable",
        ),
        (AIAuthenticationError, "invalid_authentication"),
        (AIPermissionError, "insufficient_permissions"),
        (AIUnsupportedModelError, "unsupported_model"),
        (AIInvalidRequestError, "invalid_request"),
        (AIRateLimitError, "rate_limit_reached"),
        (AIConnectionError, "connection_failure"),
        (AIRequestTimeoutError, "timeout"),
        (AIProviderServerError, "provider_server_error"),
        (AIProviderRefusalError, "provider_refusal"),
        (AIOutputLimitError, "output_limit_reached"),
        (AIInvalidResponseError, "malformed_structured_response"),
        (AIDependencyMissingError, "dependency_missing"),
        (AIUnsupportedSDKError, "dependency_missing"),
        (AIRequestCancelledError, "cancelled_request"),
        (asyncio.CancelledError, "cancelled_request"),
        (AIProviderUnavailableError, "unknown_provider_failure"),
    )
    for error_type, category in mappings:
        if isinstance(error, error_type):
            return _CATEGORY_RESULTS[category]
    return _CATEGORY_RESULTS["unknown_provider_failure"]


def _workflow_status_category(error: BaseException) -> AIErrorCategory | None:
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, str):
        return None
    return {
        "credential_missing": "api_key_not_configured",
        "credential_backend_unavailable": (
            "secure_credential_backend_unavailable"
        ),
        "credential_storage_error": "secure_credential_backend_unavailable",
        "unsupported_model": "unsupported_model",
        "empty_model": "invalid_request",
        "malformed_model": "invalid_request",
        "dependency_missing": "dependency_missing",
    }.get(status_code, "unknown_provider_failure")


_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)[\"']?\b(?:authorization|proxy-authorization)\b[\"']?\s*[:=]\s*"
    r"(?:bearer\s+)?(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\r\n]+)"
)
_KEY_FIELD_PATTERN = re.compile(
    r"(?i)[\"']?\b(?:api[-_ ]?key|x-api-key|access[-_ ]?token)\b"
    r"[\"']?\s*[:=]\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\r\n]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{4,}")
_OPENAI_KEY_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{4,}")
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Z]:\\|\\\\)[^\r\n,;]+"
)
_POSIX_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:/users/|/home/|/var/folders/|/tmp/)[^\s,;\r\n]+"
)
_TRACEBACK_LINE_PATTERN = re.compile(
    r"(?im)^(?:traceback \(most recent call last\):|\s*file \".*?\", line \d+.*)$"
)
_SENSITIVE_OBJECT_PATTERN = re.compile(
    r"(?im)[\"']?\b(?:response|raw_response|context|headers|request_headers)\b"
    r"[\"']?\s*[:=]\s*.*$"
)


def redact_ai_diagnostic_text(text: str) -> str:
    """Best-effort scrub text before an optional technical diagnostic display."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    redacted = _TRACEBACK_LINE_PATTERN.sub("[REDACTED_TRACEBACK]", text)
    redacted = _SENSITIVE_OBJECT_PATTERN.sub("sensitive_data=[REDACTED]", redacted)
    redacted = _AUTHORIZATION_PATTERN.sub("authorization=[REDACTED]", redacted)
    redacted = _KEY_FIELD_PATTERN.sub("api_key=[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = _OPENAI_KEY_PATTERN.sub("[REDACTED_SECRET]", redacted)
    redacted = _WINDOWS_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    redacted = _POSIX_PRIVATE_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    if len(redacted) > 2_000:
        redacted = redacted[:2_000] + "...[TRUNCATED]"
    return redacted


def format_redacted_ai_diagnostic(
    error: BaseException,
    *,
    request_type: str | None = None,
    provider_identifier: str | None = None,
    model_identifier: str | None = None,
    duration_seconds: float | None = None,
) -> str:
    """Format metadata-only diagnostics; raw exception text is never included."""

    result = categorize_ai_error(error)
    parts = [
        "AI provider diagnostic",
        f"category={result.category}",
    ]
    for name, value in (
        ("request_type", request_type),
        ("provider", provider_identifier),
        ("model", model_identifier),
    ):
        safe_value = _safe_diagnostic_identifier(value)
        if safe_value is not None:
            parts.append(f"{name}={safe_value}")
    if duration_seconds is not None:
        if (
            not isinstance(duration_seconds, (int, float))
            or isinstance(duration_seconds, bool)
            or not math.isfinite(duration_seconds)
            or duration_seconds < 0
        ):
            raise ValueError("duration_seconds must be a finite nonnegative number")
        parts.append(f"duration_ms={round(float(duration_seconds) * 1000)}")
    return "; ".join(parts) + "."


def _safe_diagnostic_identifier(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("diagnostic identifiers must be text or None")
    normalized = value.strip()
    if not normalized:
        return None
    normalized = redact_ai_diagnostic_text(normalized)
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "_", normalized)[:120]
    return normalized or None


__all__ = [
    "AIErrorCategory",
    "AIErrorCategoryResult",
    "categorize_ai_error",
    "format_redacted_ai_diagnostic",
    "redact_ai_diagnostic_text",
]
