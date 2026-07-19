from copy import deepcopy

import pytest

from model_health_assistant.ai_consent import (
    AIConsentInvalidatedError,
    build_ai_request_review,
)
from model_health_assistant.ai_models import (
    AIExplanationResponse,
    AIWarningExplanation,
)
from model_health_assistant.ai_provider import AIInvalidResponseError
from model_health_assistant.ai_traceability import build_ai_response_traceability
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _report() -> ModelHealthReport:
    return ModelHealthReport(
        document_name="Private trace model",
        generated_at="2026-07-19T12:00:00+00:00",
        health_score=86,
        features=[
            FeatureInfo(
                internal_name="Sketch001",
                label="Mounting profile",
                category="sketch",
            ),
            FeatureInfo(
                internal_name="Pocket001",
                label="Drive pocket",
                category="subtractive_feature",
                has_confirmed_error=True,
            ),
        ],
        warnings=[
            RiskWarning(
                warning_id="WARN-SKETCH",
                severity="low",
                affected_feature_name="Sketch001",
                message="Sketch support could not be confirmed.",
                technical_reason="The extractor found no stable support fact.",
                suggested_practice="Review the support manually.",
                rule_id="unknown_sketch_support",
            ),
            RiskWarning(
                warning_id="WARN-ERROR",
                severity="high",
                affected_feature_name="Pocket001",
                message="Pocket001 reports a confirmed error.",
                technical_reason="FreeCAD exposed an explicit error state.",
                suggested_practice="Review the earliest failing feature.",
                rule_id="confirmed_recompute_error",
            ),
        ],
    )


def _review(report: ModelHealthReport, *, selected: bool = False):
    kwargs = {}
    request_type = "full-analysis"
    if selected:
        request_type = "selected-warning"
        kwargs = {
            "selected_warning_identifier": "WARN-SKETCH",
            "selected_warning_rule_identifier": "unknown_sketch_support",
            "selected_warning_feature_internal_name": "Sketch001",
        }
    return build_ai_request_review(
        report,
        provider_identifier="openai",
        provider_display_name="OpenAI",
        model_identifier="gpt-5.6-terra",
        request_type=request_type,
        **kwargs,
    )


def _response(review) -> AIExplanationResponse:
    return AIExplanationResponse(
        provider_identifier="openai",
        provider_display_name="OpenAI",
        actual_model_identifier="gpt-5.6-terra-2026-06-01",
        overall_summary="Grounded advisory summary.",
        warning_explanations=[
            AIWarningExplanation(
                warning_id=warning.warning_id,
                rule_id=warning.rule_id,
                affected_feature_internal_name=(
                    warning.affected_feature_internal_name
                ),
                severity=warning.severity,
                explanation="Review this local finding manually.",
                why_it_matters="It may indicate model fragility.",
                limitations=["Only supplied context was available."],
            )
            for warning in review.request.context.warnings
        ],
        limitations=["No geometry was inspected."],
    )


def test_traceability_retains_exact_source_mapping_and_versions() -> None:
    report = _report()
    before = deepcopy(report.to_dict())
    review = _review(report, selected=True)

    traceability = build_ai_response_traceability(
        review,
        _response(review),
        report,
        selected_warning_identifier="WARN-SKETCH",
        selected_warning_rule_identifier="unknown_sketch_support",
        selected_warning_feature_internal_name="Sketch001",
    )

    assert traceability.request_context_fingerprint == review.request_fingerprint
    assert traceability.response_schema_version == "1.0"
    assert traceability.provider_identifier == "openai"
    assert traceability.configured_model_identifier == "gpt-5.6-terra"
    assert traceability.actual_model_identifier == "gpt-5.6-terra-2026-06-01"
    assert traceability.prompt_template_version == "model-health-explanation-v3"
    assert traceability.context_schema_version == "1.0"
    assert len(traceability.source_warnings) == 1
    source = traceability.source_warnings[0]
    assert source.warning_id == "WARN-SKETCH"
    assert source.rule_id == "unknown_sketch_support"
    assert source.affected_feature_internal_name == "Sketch001"
    assert source.affected_feature_label == "Mounting profile"
    assert source.source_severity == "low"
    assert report.to_dict() == before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: setattr(item, "warning_id", "UNKNOWN"), "unknown warning"),
        (
            lambda item: setattr(
                item,
                "affected_feature_internal_name",
                "InventedFeature",
            ),
            "unknown warning",
        ),
        (lambda item: setattr(item, "severity", "high"), "severity"),
    ],
)
def test_unknown_wrong_feature_or_severity_mismatch_is_rejected(
    mutation,
    message: str,
) -> None:
    report = _report()
    review = _review(report, selected=True)
    response = _response(review)
    mutation(response.warning_explanations[0])

    with pytest.raises(AIInvalidResponseError, match=message):
        build_ai_response_traceability(
            review,
            response,
            report,
            selected_warning_identifier="WARN-SKETCH",
            selected_warning_rule_identifier="unknown_sketch_support",
            selected_warning_feature_internal_name="Sketch001",
        )


def test_duplicate_warning_explanation_is_rejected() -> None:
    report = _report()
    review = _review(report, selected=True)
    response = _response(review)
    response.warning_explanations.append(
        deepcopy(response.warning_explanations[0])
    )

    with pytest.raises(AIInvalidResponseError, match="duplicate explanations"):
        build_ai_response_traceability(
            review,
            response,
            report,
            selected_warning_identifier="WARN-SKETCH",
            selected_warning_rule_identifier="unknown_sketch_support",
            selected_warning_feature_internal_name="Sketch001",
        )


def test_stale_request_context_fingerprint_is_rejected() -> None:
    report = _report()
    review = _review(report)
    response = _response(review)
    review.request.user_prompt += "changed after review"

    with pytest.raises(AIConsentInvalidatedError, match="request changed"):
        build_ai_response_traceability(review, response, report)


def test_report_changed_while_provider_was_running_is_rejected() -> None:
    report = _report()
    review = _review(report)
    response = _response(review)
    report.health_score -= 1

    with pytest.raises(AIConsentInvalidatedError, match="report changed"):
        build_ai_response_traceability(review, response, report)


def test_selected_warning_changed_while_provider_was_running_is_rejected() -> None:
    report = _report()
    review = _review(report, selected=True)

    with pytest.raises(AIConsentInvalidatedError, match="selected warning changed"):
        build_ai_response_traceability(
            review,
            _response(review),
            report,
            selected_warning_identifier="WARN-ERROR",
            selected_warning_rule_identifier="confirmed_recompute_error",
            selected_warning_feature_internal_name="Pocket001",
        )


def test_valid_partial_response_retains_source_traceability() -> None:
    report = _report()
    review = _review(report)
    response = _response(review)
    response.warning_explanations = response.warning_explanations[:1]

    traceability = build_ai_response_traceability(review, response, report)

    assert len(traceability.source_warnings) == 2
    assert len(response.warning_explanations) == 1
