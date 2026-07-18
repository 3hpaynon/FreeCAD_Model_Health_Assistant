"""Immutable request review and one-request consent for provider transmissions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from model_health_assistant.ai_context import (
    AIContextLimits,
    DEFAULT_AI_CONTEXT_LIMITS,
    build_ai_request_context,
    serialize_ai_request_context,
)
from model_health_assistant.ai_models import AIExplanationRequest, AIRequestContext
from model_health_assistant.ai_prompts import build_ai_explanation_request
from model_health_assistant.models import ModelHealthReport

AIRequestType = Literal["selected-warning", "full-analysis", "custom-question"]


class AIConsentError(RuntimeError):
    """Base class for controlled review and consent failures."""


class AIConsentInvalidatedError(AIConsentError):
    """Raised when reviewed request, report, or selection state has changed."""


class AIConsentNotApprovedError(AIConsentError):
    """Raised when transmission is attempted without explicit approval."""


class AIConsentAlreadyUsedError(AIConsentError):
    """Raised when one-request consent is reused."""


@dataclass(frozen=True, slots=True)
class AIRequestReview:
    """Exact provider-neutral request plus privacy metadata shown to the user."""

    provider_identifier: str
    provider_display_name: str
    model_identifier: str
    request_type: AIRequestType
    request: AIExplanationRequest
    request_fingerprint: str
    source_report_fingerprint: str
    selected_warning_identifier: str | None = None
    user_question_included: bool = False

    def preview_payload(self) -> dict[str, object]:
        context = self.request.context
        return {
            "provider": {
                "identifier": self.provider_identifier,
                "display_name": self.provider_display_name,
            },
            "model_identifier": self.model_identifier,
            "request_type": self.request_type,
            "request_fingerprint": self.request_fingerprint,
            "context_schema_version": context.schema_version,
            "prompt_template_version": self.request.metadata.prompt_template_version,
            "included_warning_count": context.metadata.included_warning_count,
            "omitted_warning_count": context.metadata.omitted_warning_count,
            "truncated": context.metadata.was_truncated,
            "document_name_included": context.metadata.document_name_included,
            "user_question_included": self.user_question_included,
            "privacy_notices": [
                "No raw CAD file is being sent.",
                "No CAD geometry is being sent.",
                (
                    "The displayed privacy-safe context and request will be "
                    "transmitted to the selected provider only after Send to "
                    "Provider is explicitly chosen."
                ),
            ],
            "privacy_safe_context": deepcopy(context.to_dict()),
            "provider_request": deepcopy(self.request.to_dict()),
        }

    def preview_json(self) -> str:
        return json.dumps(
            self.preview_payload(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


class AIRequestConsent:
    """Ephemeral consent valid for one exact reviewed request only."""

    def __init__(self, review: AIRequestReview) -> None:
        if not isinstance(review, AIRequestReview):
            raise TypeError("review must be an AIRequestReview")
        self.review = review
        self._approved = False
        self._cancelled = False
        self._used = False

    @property
    def approved(self) -> bool:
        return self._approved and not self._cancelled and not self._used

    def cancel(self) -> None:
        self._cancelled = True
        self._approved = False

    def approve(self) -> None:
        if self._cancelled:
            raise AIConsentNotApprovedError("The request review was cancelled.")
        self._approved = True

    def consume(
        self,
        current_report: ModelHealthReport,
        *,
        selected_warning_identifier: str | None = None,
    ) -> AIExplanationRequest:
        if self._used:
            raise AIConsentAlreadyUsedError("Request consent has already been used.")
        if not self.approved:
            raise AIConsentNotApprovedError(
                "Explicit Send to Provider approval is required."
            )
        validate_review_is_current(
            self.review,
            current_report,
            selected_warning_identifier=selected_warning_identifier,
        )
        self._used = True
        return self.review.request


def build_ai_request_review(
    report: ModelHealthReport,
    *,
    provider_identifier: str,
    provider_display_name: str,
    model_identifier: str,
    request_type: AIRequestType,
    selected_warning_identifier: str | None = None,
    user_question: str | None = None,
    limits: AIContextLimits = DEFAULT_AI_CONTEXT_LIMITS,
) -> AIRequestReview:
    """Build the exact request that a later explicit send may consume."""

    if not isinstance(report, ModelHealthReport):
        raise TypeError("report must be a ModelHealthReport")
    for name, value in (
        ("provider identifier", provider_identifier),
        ("provider display name", provider_display_name),
        ("model identifier", model_identifier),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be non-empty text")
    if request_type not in {
        "selected-warning",
        "full-analysis",
        "custom-question",
    }:
        raise ValueError("unsupported request type")
    if request_type == "selected-warning":
        context = _selected_warning_context(
            report,
            selected_warning_identifier,
            limits,
        )
    else:
        context = build_ai_request_context(report, limits=limits)
    request = build_ai_explanation_request(context)
    user_question_included = bool(
        isinstance(user_question, str) and user_question.strip()
    )
    if user_question_included:
        request.user_prompt += "\nUSER QUESTION:\n" + user_question.strip()

    fingerprint = _request_fingerprint(
        provider_identifier,
        model_identifier,
        request_type,
        request,
    )
    return AIRequestReview(
        provider_identifier=provider_identifier,
        provider_display_name=provider_display_name,
        model_identifier=model_identifier,
        request_type=request_type,
        request=request,
        request_fingerprint=fingerprint,
        source_report_fingerprint=_report_fingerprint(report),
        selected_warning_identifier=selected_warning_identifier,
        user_question_included=user_question_included,
    )


def validate_review_is_current(
    review: AIRequestReview,
    current_report: ModelHealthReport,
    *,
    selected_warning_identifier: str | None = None,
) -> None:
    """Reject stale report, selection, or in-memory request state."""

    if not isinstance(review, AIRequestReview):
        raise TypeError("review must be an AIRequestReview")
    if _report_fingerprint(current_report) != review.source_report_fingerprint:
        raise AIConsentInvalidatedError(
            "The analyzed report changed after preview; review it again."
        )
    if selected_warning_identifier != review.selected_warning_identifier:
        raise AIConsentInvalidatedError(
            "The selected warning changed after preview; review it again."
        )
    current_fingerprint = _request_fingerprint(
        review.provider_identifier,
        review.model_identifier,
        review.request_type,
        review.request,
    )
    if current_fingerprint != review.request_fingerprint:
        raise AIConsentInvalidatedError(
            "The provider request changed after preview; review it again."
        )


def _selected_warning_context(
    report: ModelHealthReport,
    selected_warning_identifier: str | None,
    limits: AIContextLimits,
) -> AIRequestContext:
    if not isinstance(selected_warning_identifier, str) or not (
        selected_warning_identifier.strip()
    ):
        raise ValueError("a selected warning identifier is required")
    matches = [
        warning
        for warning in report.warnings
        if warning.warning_id == selected_warning_identifier
    ]
    if len(matches) != 1:
        raise ValueError("the selected warning is missing or ambiguous")
    scoped_report = ModelHealthReport(
        document_name=report.document_name,
        generated_at=report.generated_at,
        health_score=report.health_score,
        features=list(report.features),
        warnings=matches,
        health_score_breakdown=deepcopy(report.health_score_breakdown),
    )
    context = build_ai_request_context(scoped_report, limits=limits)
    context.total_warning_count = report.total_warning_count
    context.warning_counts_by_severity = dict(report.warning_counts_by_severity)
    context.metadata.original_warning_count = report.total_warning_count
    context.metadata.included_warning_count = 1
    context.metadata.omitted_warning_count = max(0, report.total_warning_count - 1)
    if context.metadata.omitted_warning_count:
        field_identifier = "report.warnings.request_scope"
        if field_identifier not in context.metadata.omitted_fields:
            context.metadata.omitted_fields.append(field_identifier)
    _refresh_serialized_context_count(context)
    return context


def _refresh_serialized_context_count(context: AIRequestContext) -> None:
    context.metadata.serialized_context_characters = 0
    for _attempt in range(10):
        character_count = len(serialize_ai_request_context(context))
        if context.metadata.serialized_context_characters == character_count:
            return
        context.metadata.serialized_context_characters = character_count
    raise RuntimeError("AI context character count did not stabilize")


def _request_fingerprint(
    provider_identifier: str,
    model_identifier: str,
    request_type: AIRequestType,
    request: AIExplanationRequest,
) -> str:
    payload = {
        "provider_identifier": provider_identifier,
        "model_identifier": model_identifier,
        "request_type": request_type,
        "request": request.to_dict(),
    }
    return _sha256_json(payload)


def _report_fingerprint(report: ModelHealthReport) -> str:
    if not isinstance(report, ModelHealthReport):
        raise TypeError("current_report must be a ModelHealthReport")
    return _sha256_json(report.to_dict())


def _sha256_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "AIConsentAlreadyUsedError",
    "AIConsentError",
    "AIConsentInvalidatedError",
    "AIConsentNotApprovedError",
    "AIRequestConsent",
    "AIRequestReview",
    "AIRequestType",
    "build_ai_request_review",
    "validate_review_is_current",
]
