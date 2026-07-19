import asyncio

import pytest

from model_health_assistant.ai_error_handling import (
    categorize_ai_error,
    format_redacted_ai_diagnostic,
    redact_ai_diagnostic_text,
)
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
    AIRateLimitError,
    AIRequestCancelledError,
    AIRequestTimeoutError,
    AIUnsupportedModelError,
    AIUnsupportedSDKError,
)

_RAW_SECRET = "s" + "k-" + "UNIT_TEST_SECRET_DO_NOT_LOG"


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (AIMissingCredentialError(_RAW_SECRET), "api_key_not_configured", False),
        (
            AICredentialBackendUnavailableError(_RAW_SECRET),
            "secure_credential_backend_unavailable",
            False,
        ),
        (AIAuthenticationError(_RAW_SECRET), "invalid_authentication", False),
        (AIPermissionError(_RAW_SECRET), "insufficient_permissions", False),
        (AIUnsupportedModelError(_RAW_SECRET), "unsupported_model", False),
        (AIInvalidRequestError(_RAW_SECRET), "invalid_request", False),
        (AIRateLimitError(_RAW_SECRET), "rate_limit_reached", True),
        (AIConnectionError(_RAW_SECRET), "connection_failure", True),
        (AIRequestTimeoutError(_RAW_SECRET), "timeout", True),
        (AIProviderServerError(_RAW_SECRET), "provider_server_error", True),
        (AIProviderRefusalError(_RAW_SECRET), "provider_refusal", False),
        (AIOutputLimitError(_RAW_SECRET), "output_limit_reached", False),
        (
            AIInvalidResponseError(_RAW_SECRET),
            "malformed_structured_response",
            False,
        ),
        (AIDependencyMissingError(_RAW_SECRET), "dependency_missing", False),
        (AIUnsupportedSDKError(_RAW_SECRET), "dependency_missing", False),
        (AIRequestCancelledError(_RAW_SECRET), "cancelled_request", False),
        (asyncio.CancelledError(_RAW_SECRET), "cancelled_request", False),
        (RuntimeError(_RAW_SECRET), "unknown_provider_failure", False),
    ],
)
def test_all_error_categories_are_clear_and_never_echo_raw_errors(
    error: BaseException,
    category: str,
    retryable: bool,
) -> None:
    result = categorize_ai_error(error)

    assert result.category == category
    assert result.retryable is retryable
    assert result.user_message
    assert _RAW_SECRET not in result.user_message
    assert "traceback" not in result.user_message.casefold()
    assert "C:\\" not in result.user_message


@pytest.mark.parametrize(
    ("status_code", "category"),
    [
        ("credential_missing", "api_key_not_configured"),
        (
            "credential_backend_unavailable",
            "secure_credential_backend_unavailable",
        ),
        (
            "credential_storage_error",
            "secure_credential_backend_unavailable",
        ),
        ("unsupported_model", "unsupported_model"),
        ("empty_model", "invalid_request"),
        ("malformed_model", "invalid_request"),
        ("dependency_missing", "dependency_missing"),
        ("unsupported_provider", "unknown_provider_failure"),
    ],
)
def test_workflow_readiness_codes_map_without_parsing_message_text(
    status_code: str,
    category: str,
) -> None:
    error = RuntimeError(_RAW_SECRET)
    error.status_code = status_code

    assert categorize_ai_error(error).category == category


def test_redaction_removes_headers_keys_paths_tracebacks_and_raw_objects() -> None:
    raw = (
        "Authorization: " + "Bearer " + "UNIT_TEST_BEARER_DO_NOT_LOG\n"
        '"api_key": "ordinary-secret-value"\n'
        "x-api-key=another-secret\n"
        f"provider key {_RAW_SECRET}\n"
        r"path=C:\Users\private-user\FreeCAD\private.FCStd" + "\n"
        "path=/home/private-user/models/private.FCStd\n"
        'Traceback (most recent call last):\n  File "C:\\private.py", line 7\n'
        'response={"output":"complete private provider response"}\n'
        'context={"cad_label":"private CAD label"}'
    )

    redacted = redact_ai_diagnostic_text(raw)

    for forbidden in (
        "super-secret-bearer",
        "ordinary-secret-value",
        "another-secret",
        _RAW_SECRET,
        "private-user",
        "private.FCStd",
        "private.py",
        "complete private provider response",
        "private CAD label",
    ):
        assert forbidden not in redacted
    assert "[REDACTED]" in redacted
    assert "[REDACTED_PATH]" in redacted
    assert "[REDACTED_TRACEBACK]" in redacted


def test_diagnostic_is_metadata_only_and_never_formats_exception_text() -> None:
    error = AIAuthenticationError(
        _RAW_SECRET
        + r" Authorization: Bearer private C:\Users\private\model.FCStd"
    )

    diagnostic = format_redacted_ai_diagnostic(
        error,
        request_type="custom-question",
        provider_identifier="openai",
        model_identifier="gpt-5.6-terra",
        duration_seconds=1.234,
    )

    assert diagnostic == (
        "AI provider diagnostic; category=invalid_authentication; "
        "request_type=custom-question; provider=openai; model=gpt-5.6-terra; "
        "duration_ms=1234."
    )
    assert _RAW_SECRET not in diagnostic
    assert "Authorization" not in diagnostic
    assert "Users" not in diagnostic


def test_diagnostic_identifier_is_scrubbed_and_duration_is_validated() -> None:
    diagnostic = format_redacted_ai_diagnostic(
        RuntimeError("private"),
        provider_identifier=_RAW_SECRET,
        model_identifier=r"C:\Users\private\model",
    )

    assert _RAW_SECRET not in diagnostic
    assert "private" not in diagnostic
    with pytest.raises(ValueError, match="duration_seconds"):
        format_redacted_ai_diagnostic(RuntimeError(), duration_seconds=float("inf"))
    with pytest.raises(TypeError):
        redact_ai_diagnostic_text(None)
