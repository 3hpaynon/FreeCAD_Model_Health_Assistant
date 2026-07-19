"""Read-only grounding and provenance for displayed AI explanations.

The provider response is grounded against the exact reviewed request and the
current local report before any advisory text is displayed.  The structures in
this module contain identifiers and schema metadata only; they do not modify or
replace local warning objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from model_health_assistant.ai_consent import (
    AIRequestReview,
    validate_review_is_current,
)
from model_health_assistant.ai_models import AIExplanationResponse
from model_health_assistant.ai_provider import AIInvalidResponseError
from model_health_assistant.ai_service import validate_ai_explanation_response
from model_health_assistant.models import ModelHealthReport


@dataclass(frozen=True, slots=True)
class AIWarningTraceability:
    """Immutable identity copied from one privacy-safe source warning."""

    warning_id: str
    rule_id: str
    affected_feature_internal_name: str
    affected_feature_label: str | None
    source_severity: str


@dataclass(frozen=True, slots=True)
class AIResponseTraceability:
    """Validated provenance retained with one advisory provider response."""

    request_context_fingerprint: str
    response_schema_version: str
    provider_identifier: str
    provider_display_name: str
    configured_model_identifier: str
    actual_model_identifier: str | None
    prompt_template_version: str
    context_schema_version: str
    source_warnings: tuple[AIWarningTraceability, ...]


def build_ai_response_traceability(
    review: AIRequestReview,
    response: AIExplanationResponse,
    current_report: ModelHealthReport,
    *,
    selected_warning_identifier: str | None = None,
    selected_warning_rule_identifier: str | None = None,
    selected_warning_feature_internal_name: str | None = None,
) -> AIResponseTraceability:
    """Validate a completed response against current local state and trace it.

    This completion-time check is intentionally separate from consent-time
    validation: a provider request can take long enough for the report or table
    selection to change while the worker is running.  Such a late response is
    rejected instead of being displayed against stale local findings.
    """

    if not isinstance(review, AIRequestReview):
        raise TypeError("review must be an AIRequestReview")
    if not isinstance(response, AIExplanationResponse):
        raise AIInvalidResponseError(
            "Provider returned the wrong response type; expected AIExplanationResponse."
        )
    validate_review_is_current(
        review,
        current_report,
        selected_warning_identifier=selected_warning_identifier,
        selected_warning_rule_identifier=selected_warning_rule_identifier,
        selected_warning_feature_internal_name=(
            selected_warning_feature_internal_name
        ),
    )
    validate_ai_explanation_response(
        review.request,
        response,
        expected_provider_identifier=review.provider_identifier,
    )

    context_schema_version = review.request.context.schema_version
    if review.request.metadata.context_schema_version != context_schema_version:
        raise AIInvalidResponseError(
            "The reviewed request has inconsistent context schema metadata."
        )
    prompt_template_version = review.request.metadata.prompt_template_version
    if not isinstance(prompt_template_version, str) or not prompt_template_version.strip():
        raise AIInvalidResponseError(
            "The reviewed request is missing prompt-template traceability."
        )

    source_warnings = tuple(
        AIWarningTraceability(
            warning_id=warning.warning_id,
            rule_id=warning.rule_id,
            affected_feature_internal_name=warning.affected_feature_internal_name,
            affected_feature_label=warning.affected_feature_label,
            source_severity=warning.severity,
        )
        for warning in review.request.context.warnings
    )
    return AIResponseTraceability(
        request_context_fingerprint=review.request_fingerprint,
        response_schema_version=response.schema_version,
        provider_identifier=response.provider_identifier,
        provider_display_name=response.provider_display_name,
        configured_model_identifier=review.model_identifier,
        actual_model_identifier=response.actual_model_identifier,
        prompt_template_version=prompt_template_version,
        context_schema_version=context_schema_version,
        source_warnings=source_warnings,
    )


__all__ = [
    "AIResponseTraceability",
    "AIWarningTraceability",
    "build_ai_response_traceability",
]
