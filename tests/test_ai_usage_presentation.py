from __future__ import annotations

from model_health_assistant.ai_connection import AIConnectionTestResult
from model_health_assistant.ai_models import AIExplanationResponse, AIUsageMetadata
from model_health_assistant.ai_usage_presentation import (
    AIUsagePresentation,
    build_ai_usage_presentation,
    format_ai_usage_presentation,
)


def _explanation_response(
    *,
    actual_model_identifier: str | None = "gpt-5.6-terra-2026-07-01",
    request_identifier: str | None = "resp_usage_123",
    usage: AIUsageMetadata | None = None,
) -> AIExplanationResponse:
    return AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Grounded advisory response.",
        warning_explanations=[],
        limitations=["Only supplied context was used."],
        actual_model_identifier=actual_model_identifier,
        request_identifier=request_identifier,
        usage=usage,
    )


def test_explanation_metadata_formats_only_requested_usage_counts() -> None:
    result = _explanation_response(
        usage=AIUsageMetadata(
            input_tokens=100,
            output_tokens=40,
            total_tokens=140,
            reasoning_tokens=17,
        )
    )

    presentation = build_ai_usage_presentation(
        result,
        configured_model_identifier="gpt-5.6-terra",
    )

    assert isinstance(presentation, AIUsagePresentation)
    assert presentation.lines() == (
        "Provider: OpenAI (openai)",
        "Configured model: gpt-5.6-terra",
        "Actual model: gpt-5.6-terra-2026-07-01",
        (
            "Model metadata warning: The configured and actual model "
            "identifiers differ."
        ),
        "Request ID: resp_usage_123",
        "Usage: input=100, output=40, total=140",
    )
    text = presentation.format_text()
    assert "reasoning" not in text.casefold()
    assert "cost" not in text.casefold()
    assert "$" not in text


def test_partial_usage_omits_missing_counts_and_never_derives_total() -> None:
    result = _explanation_response(
        usage=AIUsageMetadata(input_tokens=8, output_tokens=5)
    )

    text = format_ai_usage_presentation(
        result,
        configured_model_identifier="gpt-5.6-terra",
    )

    assert "Usage: input=8, output=5" in text
    assert "total=" not in text
    assert "13" not in text


def test_zero_counts_are_available_counts_and_are_displayed() -> None:
    result = _explanation_response(
        usage=AIUsageMetadata(input_tokens=0, output_tokens=0, total_tokens=0)
    )

    presentation = build_ai_usage_presentation(
        result,
        configured_model_identifier="gpt-5.6-terra",
    )

    assert presentation.usage_text() == "input=0, output=0, total=0"


def test_missing_optional_metadata_is_explicitly_not_reported() -> None:
    result = _explanation_response(
        actual_model_identifier=None,
        request_identifier=None,
        usage=None,
    )

    presentation = build_ai_usage_presentation(
        result,
        configured_model_identifier=None,
    )

    assert presentation.model_identifiers_differ is None
    assert presentation.lines() == (
        "Provider: OpenAI (openai)",
        "Configured model: Not reported",
        "Actual model: Not reported",
        "Request ID: Not reported",
        "Usage: Not reported",
    )


def test_connection_result_uses_the_same_pure_formatter() -> None:
    result = AIConnectionTestResult(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        actual_model_identifier="gpt-5.6-terra",
        request_identifier="resp_connection_456",
        usage=AIUsageMetadata(input_tokens=6, total_tokens=9),
    )

    presentation = build_ai_usage_presentation(
        result,
        configured_model_identifier="gpt-5.6-terra",
    )

    assert presentation.model_identifiers_differ is False
    assert presentation.input_tokens == 6
    assert presentation.output_tokens is None
    assert presentation.total_tokens == 9
    assert presentation.lines()[-1] == "Usage: input=6, total=9"


def test_model_mismatch_is_represented_without_switching_either_identifier() -> None:
    result = _explanation_response(actual_model_identifier="gpt-5.6-terra-snapshot")

    presentation = build_ai_usage_presentation(
        result,
        configured_model_identifier="gpt-5.6-terra",
    )

    assert presentation.model_identifiers_differ is True
    assert presentation.configured_model_identifier == "gpt-5.6-terra"
    assert presentation.actual_model_identifier == "gpt-5.6-terra-snapshot"
    text = presentation.format_text()
    assert "configured and actual model identifiers differ" in text
    assert "Configured model: gpt-5.6-terra" in text
    assert "Actual model: gpt-5.6-terra-snapshot" in text


def test_equal_or_missing_actual_model_has_no_mismatch_warning() -> None:
    equal = build_ai_usage_presentation(
        _explanation_response(actual_model_identifier="gpt-5.6-terra"),
        configured_model_identifier="gpt-5.6-terra",
    )
    missing = build_ai_usage_presentation(
        _explanation_response(actual_model_identifier=None),
        configured_model_identifier="gpt-5.6-terra",
    )

    assert equal.model_identifiers_differ is False
    assert missing.model_identifiers_differ is None
    assert "model identifiers differ" not in equal.format_text()
    assert "model identifiers differ" not in missing.format_text()


def test_presentation_is_frozen_display_data_with_no_telemetry_fields() -> None:
    presentation = build_ai_usage_presentation(
        _explanation_response(usage=AIUsageMetadata(total_tokens=19)),
        configured_model_identifier="gpt-5.6-terra",
    )

    assert presentation.total_tokens == 19
    assert not hasattr(presentation, "cost")
    assert not hasattr(presentation, "history")
    assert not hasattr(presentation, "telemetry")
