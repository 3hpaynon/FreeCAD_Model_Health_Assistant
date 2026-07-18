import pytest

from model_health_assistant.ai_consent import build_ai_request_review
from model_health_assistant.ai_context import AIContextLimits
from model_health_assistant.ai_models import (
    AIExplanationResponse,
    AIUsageMetadata,
    AIWarningExplanation,
)
from model_health_assistant.ai_result_presentation import (
    AI_ADVISORY_DISCLAIMER,
    format_full_analysis_explanation,
    format_selected_warning_explanation,
)
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning
from model_health_assistant.presentation import build_analysis_presentation


def _source_data():
    report = ModelHealthReport(
        document_name="Private model",
        generated_at="2026-07-17T12:00:00+00:00",
        health_score=81,
        features=[
            FeatureInfo(
                internal_name="Pocket001",
                label="Drive pocket",
                category="pocket",
            )
        ],
        warnings=[
            RiskWarning(
                warning_id="WARN-1",
                severity="high",
                affected_feature_name="Pocket001",
                message="The source warning message.",
                technical_reason="The source technical reason.",
                suggested_practice="The source suggested practice.",
                rule_id="confirmed_recompute_error",
            )
        ],
    )
    warning = build_analysis_presentation(report).warning_items[0]
    review = build_ai_request_review(
        report,
        provider_identifier="openai",
        provider_display_name="OpenAI",
        model_identifier="gpt-5.6-terra",
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )
    return report, warning, review


def test_selected_warning_result_separates_source_and_advisory_with_provenance() -> None:
    _report, warning, review = _source_data()
    response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Advisory summary.",
        warning_explanations=[
            AIWarningExplanation(
                warning_id="WARN-1",
                rule_id="confirmed_recompute_error",
                affected_feature_internal_name="Pocket001",
                severity="high",
                explanation="Advisory explanation.",
                why_it_matters="Advisory impact.",
                manual_review_steps=["Inspect manually."],
                confidence="Limited to supplied context.",
                limitations=["Warning-specific limitation."],
            )
        ],
        limitations=["Overall limitation."],
        request_identifier="resp_123",
        actual_model_identifier="gpt-5.6-terra-2026-06-01",
        usage=AIUsageMetadata(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            reasoning_tokens=20,
        ),
    )

    text = format_selected_warning_explanation(response, warning, review)

    source_index = text.index("ORIGINAL MODEL-HEALTH WARNING (RULE-BASED)")
    ai_index = text.index("AI EXPLANATION (ADVISORY)")
    assert source_index < ai_index
    assert "The source warning message." in text[:ai_index]
    assert "Advisory explanation." in text[ai_index:]
    assert "Provider: OpenAI (openai)" in text
    assert "Configured model: gpt-5.6-terra" in text
    assert "Actual model: gpt-5.6-terra-2026-06-01" in text
    assert "configured and actual model identifiers differ" in text
    assert "Request ID: resp_123" in text
    assert "Usage: input=100, output=50, total=150" in text
    assert "reasoning=20" not in text
    assert "Warning-specific limitation." in text
    assert "Overall limitation." in text
    assert AI_ADVISORY_DISCLAIMER in text
    assert "No model changes or actions were performed." in text


def test_selected_warning_partial_response_is_explicit_and_safe() -> None:
    _report, warning, review = _source_data()
    response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="A partial response.",
        warning_explanations=[],
        limitations=["No per-warning explanation was supplied."],
    )

    text = format_selected_warning_explanation(response, warning, review)

    assert "returned no explanation for the selected source warning" in text
    assert "Actual model: Not reported" in text
    assert "Request ID: Not reported" in text
    assert "Usage: Not reported" in text


def _full_report(warning_count: int = 3) -> ModelHealthReport:
    features = []
    warnings = []
    severities = ("low", "high", "medium")
    for index in range(warning_count):
        feature_name = f"Feature{index + 1}"
        features.append(
            FeatureInfo(
                internal_name=feature_name,
                label=f"Private label {index + 1}",
                category="generic_feature",
            )
        )
        warnings.append(
            RiskWarning(
                warning_id=f"WARN-{index + 1}",
                severity=severities[index % len(severities)],
                affected_feature_name=feature_name,
                message=f"Source warning {index + 1}",
                technical_reason=f"Reason {index + 1}",
                suggested_practice=f"Review {index + 1}",
                rule_id=f"rule_{index + 1}",
            )
        )
    return ModelHealthReport(
        document_name="Private full model",
        generated_at="2026-07-17T12:00:00+00:00",
        health_score=67,
        features=features,
        warnings=warnings,
    )


def _full_review(report: ModelHealthReport, *, limits=None):
    kwargs = {} if limits is None else {"limits": limits}
    return build_ai_request_review(
        report,
        provider_identifier="openai",
        provider_display_name="OpenAI",
        model_identifier="gpt-5.6-terra",
        request_type="full-analysis",
        **kwargs,
    )


def _explanation_for(warning, *, suffix: str = "") -> AIWarningExplanation:
    return AIWarningExplanation(
        warning_id=warning.warning_id,
        rule_id=warning.rule_id,
        affected_feature_internal_name=warning.affected_feature_internal_name,
        severity=warning.severity,
        explanation=f"Explanation {warning.warning_id}{suffix}",
        why_it_matters=f"Concern {warning.warning_id}{suffix}",
        manual_review_steps=[f"Review {warning.warning_id}"],
        limitations=[f"Limitation {warning.warning_id}"],
    )


def test_full_analysis_formats_many_explanations_in_reviewed_source_order() -> None:
    review = _full_review(_full_report())
    source_order = [warning.warning_id for warning in review.request.context.warnings]
    shuffled = list(reversed(review.request.context.warnings))
    response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Full summary",
        warning_explanations=[_explanation_for(item) for item in shuffled],
        limitations=["Overall full limitation"],
        request_identifier="resp_full",
        actual_model_identifier="gpt-5.6-terra",
    )

    text = format_full_analysis_explanation(response, review)

    section = text.split("PER-WARNING EXPLANATIONS (REQUEST SOURCE ORDER)", 1)[1]
    positions = [section.index(f"Warning ID: {warning_id}") for warning_id in source_order]
    assert positions == sorted(positions)
    assert "KEY CONCERNS" in text
    assert "Included warnings explained: 3 of 3" in text
    assert "Included warnings without an explanation: 0" in text
    assert "No model changes or actions were performed." in text


@pytest.mark.parametrize("warning_count", [0, 1])
def test_full_analysis_handles_zero_or_one_warning(warning_count: int) -> None:
    review = _full_review(_full_report(warning_count))
    explanations = [
        _explanation_for(item) for item in review.request.context.warnings
    ]
    response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Summary",
        warning_explanations=explanations,
        limitations=["Limitation"],
    )

    text = format_full_analysis_explanation(response, review)

    assert f"Included warnings explained: {warning_count} of {warning_count}" in text
    if warning_count == 0:
        assert "No source warnings were included" in text
        assert "PER-WARNING EXPLANATIONS (REQUEST SOURCE ORDER)\nNone." in text
    else:
        assert "Warning 1" in text


def test_full_analysis_acknowledges_truncated_and_partial_context() -> None:
    report = _full_report(3)
    limits = AIContextLimits(
        maximum_warning_count=2,
        maximum_feature_summary_count=2,
        maximum_text_characters=80,
        maximum_evidence_list_items=2,
        maximum_serialized_context_characters=20_000,
    )
    review = _full_review(report, limits=limits)
    included = review.request.context.warnings
    response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Partial summary",
        warning_explanations=[_explanation_for(included[0])],
        limitations=["Partial provider output"],
    )

    text = format_full_analysis_explanation(response, review)

    assert "Warnings in completed analysis: 3" in text
    assert "Warnings included in request: 2" in text
    assert "Warnings omitted by limits/scope: 1" in text
    assert "Context truncated: Yes" in text
    assert "IMPORTANT: THE REVIEWED REQUEST WAS LIMITED OR TRUNCATED" in text
    assert "omitted or truncated material was not available" in text
    assert "Included warnings explained: 1 of 2" in text
    assert "response was partial for this warning" in text


def test_full_analysis_rejects_unknown_or_duplicate_explanations_defensively() -> None:
    review = _full_review(_full_report(1))
    warning = review.request.context.warnings[0]
    unknown = _explanation_for(warning)
    unknown.warning_id = "UNKNOWN"
    unknown_response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Summary",
        warning_explanations=[unknown],
        limitations=["Limitation"],
    )
    with pytest.raises(ValueError, match="unknown warning"):
        format_full_analysis_explanation(unknown_response, review)

    duplicate = _explanation_for(warning)
    duplicate_response = AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        overall_summary="Summary",
        warning_explanations=[duplicate, _explanation_for(warning)],
        limitations=["Limitation"],
    )
    with pytest.raises(ValueError, match="duplicate warning"):
        format_full_analysis_explanation(duplicate_response, review)
