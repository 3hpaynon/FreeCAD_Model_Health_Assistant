import json

import pytest

from model_health_assistant.ai_consent import (
    AIConsentAlreadyUsedError,
    AIConsentInvalidatedError,
    AIConsentNotApprovedError,
    AIRequestConsent,
    build_ai_request_review,
)
from model_health_assistant.ai_context import AIContextLimits
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _report(warning_count: int = 2) -> ModelHealthReport:
    features = [
        FeatureInfo(
            internal_name=f"Feature{index:03d}",
            label=f"Feature {index}",
            category="additive_feature",
            dependency_names=[f"Support{index:03d}"],
            support_name=f"Support{index:03d}",
            metadata={"geometry_count": index},
        )
        for index in range(1, warning_count + 1)
    ]
    warnings = [
        RiskWarning(
            warning_id=f"WARN-{index:03d}",
            rule_id=f"rule-{index}",
            severity="high" if index == 1 else "medium",
            affected_feature_name=f"Feature{index:03d}",
            message=(
                f"Review Feature{index:03d}; diagnostic at "
                r"C:\Users\private\model.FCStd"
            ),
            technical_reason="The deterministic analyzer observed a risk.",
            suggested_practice="Review the feature manually.",
        )
        for index in range(1, warning_count + 1)
    ]
    return ModelHealthReport(
        document_name=r"C:\private\customer-model.FCStd",
        generated_at="2026-07-17T12:00:00+00:00",
        health_score=72,
        features=features,
        warnings=warnings,
        metadata={"private_path": r"C:\private\report.json"},
        health_score_breakdown={
            "formula_version": "1.0",
            "starting_score": 100,
            "total_penalty": 28,
        },
    )


def _review(report: ModelHealthReport, **kwargs):
    return build_ai_request_review(
        report,
        provider_identifier="openai",
        provider_display_name="OpenAI",
        model_identifier="gpt-5.6-terra",
        request_type="full-analysis",
        **kwargs,
    )


def test_preview_displays_all_required_metadata_and_exact_request_context() -> None:
    review = _review(_report())
    payload = review.preview_payload()

    assert payload["provider"] == {
        "identifier": "openai",
        "display_name": "OpenAI",
    }
    assert payload["model_identifier"] == "gpt-5.6-terra"
    assert payload["request_type"] == "full-analysis"
    assert payload["context_schema_version"] == "1.0"
    assert payload["prompt_template_version"] == "model-health-explanation-v2"
    assert payload["included_warning_count"] == 2
    assert payload["omitted_warning_count"] == 0
    assert payload["truncated"] is False
    assert payload["document_name_included"] is False
    assert payload["user_question_included"] is False
    assert payload["provider_request"]["context"] == payload["privacy_safe_context"]
    notices = " ".join(payload["privacy_notices"]).casefold()
    assert "no raw cad file" in notices
    assert "no cad geometry" in notices
    assert "transmitted to the selected provider" in notices


def test_cancel_never_approves_or_returns_request() -> None:
    report = _report()
    consent = AIRequestConsent(_review(report))

    consent.cancel()

    assert consent.approved is False
    with pytest.raises(AIConsentNotApprovedError):
        consent.consume(report)


def test_explicit_approval_returns_same_reviewed_request_once() -> None:
    report = _report()
    review = _review(report)
    consent = AIRequestConsent(review)

    consent.approve()
    transmitted_request = consent.consume(report)

    assert transmitted_request is review.request
    assert transmitted_request.to_dict() == review.preview_payload()["provider_request"]
    with pytest.raises(AIConsentAlreadyUsedError):
        consent.consume(report)


def test_context_changed_after_preview_invalidates_consent() -> None:
    report = _report()
    review = _review(report)
    consent = AIRequestConsent(review)
    consent.approve()
    review.request.context.health_score = 1

    with pytest.raises(AIConsentInvalidatedError, match="request changed"):
        consent.consume(report)


def test_report_changed_after_preview_invalidates_consent() -> None:
    report = _report()
    consent = AIRequestConsent(_review(report))
    consent.approve()
    report.health_score = 10

    with pytest.raises(AIConsentInvalidatedError, match="report changed"):
        consent.consume(report)


def test_selected_warning_change_invalidates_consent_and_scope_is_minimal() -> None:
    report = _report(3)
    review = build_ai_request_review(
        report,
        provider_identifier="openai",
        provider_display_name="OpenAI",
        model_identifier="gpt-5.6-terra",
        request_type="selected-warning",
        selected_warning_identifier="WARN-002",
    )
    consent = AIRequestConsent(review)
    consent.approve()

    assert [warning.warning_id for warning in review.request.context.warnings] == [
        "WARN-002"
    ]
    assert review.request.context.total_warning_count == 3
    assert review.request.context.metadata.included_warning_count == 1
    assert review.request.context.metadata.omitted_warning_count == 2
    with pytest.raises(AIConsentInvalidatedError, match="selected warning changed"):
        consent.consume(report, selected_warning_identifier="WARN-003")


def test_truncation_and_omissions_are_displayed() -> None:
    limits = AIContextLimits(maximum_warning_count=1)
    review = _review(_report(3), limits=limits)
    payload = review.preview_payload()

    assert payload["included_warning_count"] == 1
    assert payload["omitted_warning_count"] == 2
    assert payload["truncated"] is True
    assert payload["privacy_safe_context"]["metadata"]["was_truncated"] is True


def test_preview_has_no_hidden_fields_or_path_leakage() -> None:
    review = _review(_report())
    preview_json = review.preview_json()
    payload = json.loads(preview_json)

    for forbidden in (
        r"C:\private",
        r"C:\Users",
        "customer-model.FCStd",
        "report_path",
        "api_key",
        "credential",
        "raw_report",
        "source_report_fingerprint",
    ):
        assert forbidden not in preview_json
    assert set(payload) == {
        "provider",
        "model_identifier",
        "request_type",
        "request_fingerprint",
        "context_schema_version",
        "prompt_template_version",
        "included_warning_count",
        "omitted_warning_count",
        "truncated",
        "document_name_included",
        "user_question_included",
        "privacy_notices",
        "privacy_safe_context",
        "provider_request",
    }
