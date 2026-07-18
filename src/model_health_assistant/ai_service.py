"""Provider-independent orchestration and validation for explanations."""

from __future__ import annotations

from collections.abc import Callable

from model_health_assistant.ai_context import (
    DEFAULT_AI_CONTEXT_LIMITS,
    AIContextLimits,
    build_ai_request_context,
)
from model_health_assistant.ai_models import (
    AI_RESPONSE_SCHEMA_VERSION,
    AIExplanationRequest,
    AIExplanationResponse,
    AIRequestContext,
    AIUsageMetadata,
    AIWarningExplanation,
)
from model_health_assistant.ai_prompts import build_ai_explanation_request
from model_health_assistant.ai_provider import (
    AIExplanationProvider,
    AIInvalidResponseError,
    AIProviderUnavailableError,
)
from model_health_assistant.models import ModelHealthReport

ContextBuilder = Callable[..., AIRequestContext]
PromptBuilder = Callable[[AIRequestContext], AIExplanationRequest]


class AIExplanationServiceError(RuntimeError):
    """Base class for local explanation-orchestration failures."""


class AIContextBuildError(AIExplanationServiceError):
    """Raised when privacy-safe context construction fails."""


class AIRequestBuildError(AIExplanationServiceError):
    """Raised when deterministic prompt/request construction fails."""


class AIExplanationService:
    """Coordinate context, prompt, provider execution, and validation."""

    def __init__(
        self,
        provider: AIExplanationProvider,
        *,
        context_builder: ContextBuilder = build_ai_request_context,
        prompt_builder: PromptBuilder = build_ai_explanation_request,
    ) -> None:
        if not isinstance(provider, AIExplanationProvider):
            raise TypeError("provider must satisfy AIExplanationProvider")
        if not callable(context_builder):
            raise TypeError("context_builder must be callable")
        if not callable(prompt_builder):
            raise TypeError("prompt_builder must be callable")
        self._provider = provider
        self._context_builder = context_builder
        self._prompt_builder = prompt_builder

    @property
    def provider(self) -> AIExplanationProvider:
        """Return the explicitly injected provider."""

        return self._provider

    def explain_report(
        self,
        report: ModelHealthReport,
        *,
        include_document_name: bool = False,
        limits: AIContextLimits = DEFAULT_AI_CONTEXT_LIMITS,
    ) -> AIExplanationResponse:
        """Execute one provider-neutral explanation workflow for ``report``."""

        try:
            context = self._context_builder(
                report,
                include_document_name=include_document_name,
                limits=limits,
            )
        except Exception as exc:
            raise AIContextBuildError(
                f"Could not build privacy-safe AI context: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            request = self._prompt_builder(context)
        except Exception as exc:
            raise AIRequestBuildError(
                f"Could not build AI explanation request: {type(exc).__name__}: {exc}"
            ) from exc

        return self.explain_request(request)

    def explain_request(
        self,
        request: AIExplanationRequest,
    ) -> AIExplanationResponse:
        """Execute and validate one already-built, privacy-reviewed request."""

        if not isinstance(request, AIExplanationRequest):
            raise TypeError("request must be an AIExplanationRequest")
        if not self._provider.is_available():
            raise AIProviderUnavailableError(
                f"Provider '{self._provider.provider_identifier}' is unavailable."
            )

        response = self._provider.explain(request)
        validate_ai_explanation_response(
            request,
            response,
            expected_provider_identifier=self._provider.provider_identifier,
        )
        return response


def validate_ai_explanation_response(
    request: AIExplanationRequest,
    response: AIExplanationResponse,
    *,
    expected_provider_identifier: str,
) -> None:
    """Raise ``AIInvalidResponseError`` for malformed or unrelated output."""

    if not isinstance(request, AIExplanationRequest):
        raise TypeError("request must be an AIExplanationRequest")
    if not isinstance(response, AIExplanationResponse):
        raise AIInvalidResponseError(
            "Provider returned the wrong response type; expected AIExplanationResponse."
        )
    if response.schema_version != AI_RESPONSE_SCHEMA_VERSION:
        raise AIInvalidResponseError(
            "Provider returned unsupported response schema version "
            f"'{response.schema_version}'."
        )
    if not _nonempty_text(response.provider_identifier):
        raise AIInvalidResponseError("Provider response is missing a provider identifier.")
    if response.provider_identifier != expected_provider_identifier:
        raise AIInvalidResponseError(
            "Provider response identifier does not match the injected provider."
        )
    if not _nonempty_text(response.provider_display_name):
        raise AIInvalidResponseError("Provider response is missing a display name.")
    if not _nonempty_text(response.overall_summary):
        raise AIInvalidResponseError("Provider response is missing an overall summary.")
    if not _valid_text_list(response.limitations, require_nonempty=True):
        raise AIInvalidResponseError(
            "Provider response must include non-empty limitation metadata."
        )
    if not isinstance(response.warning_explanations, list):
        raise AIInvalidResponseError("Warning explanations must be a list.")
    if response.request_identifier is not None and not _nonempty_text(
        response.request_identifier
    ):
        raise AIInvalidResponseError("Provider response has an invalid request identifier.")
    if response.actual_model_identifier is not None and not _nonempty_text(
        response.actual_model_identifier
    ):
        raise AIInvalidResponseError("Provider response has an invalid model identifier.")
    _validate_usage_metadata(response.usage)

    source_warnings = {
        (
            warning.warning_id,
            warning.rule_id,
            warning.affected_feature_internal_name,
        ): warning
        for warning in request.context.warnings
    }
    seen_explanations: set[tuple[str, str, str]] = set()
    for explanation in response.warning_explanations:
        _validate_warning_explanation(
            explanation,
            source_warnings,
            seen_explanations,
        )


def _validate_usage_metadata(usage: AIUsageMetadata | None) -> None:
    if usage is None:
        return
    if not isinstance(usage, AIUsageMetadata):
        raise AIInvalidResponseError("Provider response has invalid usage metadata.")
    for value in (
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        usage.reasoning_tokens,
    ):
        if value is not None and (type(value) is not int or value < 0):
            raise AIInvalidResponseError("Provider response has invalid usage metadata.")


def _validate_warning_explanation(
    explanation: AIWarningExplanation,
    source_warnings: dict[tuple[str, str, str], object],
    seen_explanations: set[tuple[str, str, str]],
) -> None:
    if not isinstance(explanation, AIWarningExplanation):
        raise AIInvalidResponseError(
            "Provider returned a malformed warning explanation item."
        )
    identity = (
        explanation.warning_id,
        explanation.rule_id,
        explanation.affected_feature_internal_name,
    )
    source_warning = source_warnings.get(identity)
    if source_warning is None:
        raise AIInvalidResponseError(
            "Provider explanation refers to an unknown warning rule or feature target."
        )
    if identity in seen_explanations:
        raise AIInvalidResponseError(
            "Provider returned duplicate explanations for one source warning."
        )
    seen_explanations.add(identity)

    source_severity = getattr(source_warning, "severity", None)
    if explanation.severity != source_severity:
        raise AIInvalidResponseError(
            "Provider explanation severity does not match the source warning."
        )
    if not _nonempty_text(explanation.explanation):
        raise AIInvalidResponseError("Warning explanation text is required.")
    if not _nonempty_text(explanation.why_it_matters):
        raise AIInvalidResponseError("Warning impact text is required.")
    if not _valid_text_list(explanation.manual_review_steps):
        raise AIInvalidResponseError("Manual review steps must contain only text.")
    if explanation.confidence is not None and not _nonempty_text(
        explanation.confidence
    ):
        raise AIInvalidResponseError("Optional confidence wording must be non-empty text.")
    if not _valid_text_list(explanation.limitations):
        raise AIInvalidResponseError("Warning limitations must contain only text.")


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_text_list(value: object, *, require_nonempty: bool = False) -> bool:
    if not isinstance(value, list):
        return False
    if require_nonempty and not value:
        return False
    return all(_nonempty_text(item) for item in value)


__all__ = [
    "AIContextBuildError",
    "AIExplanationService",
    "AIExplanationServiceError",
    "AIRequestBuildError",
    "validate_ai_explanation_response",
]
