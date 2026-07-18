"""Provider-neutral reviewed-request execution for explanation UI actions.

This module has no FreeCAD or Qt imports.  It loads the currently selected
provider, verifies request-time readiness without making a provider request,
builds an exact consent review, and executes only the consumed request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from model_health_assistant.ai_consent import (
    AIConsentInvalidatedError,
    AIRequestConsent,
    AIRequestReview,
    AIRequestType,
    build_ai_request_review,
)
from model_health_assistant.ai_models import (
    AIExplanationRequest,
    AIExplanationResponse,
)
from model_health_assistant.ai_model_capabilities import (
    get_openai_model_capabilities,
)
from model_health_assistant.ai_provider import (
    AIExplanationProvider,
    AIInvalidRequestError,
    AIUnsupportedModelError,
)
from model_health_assistant.ai_service import AIExplanationService
from model_health_assistant.mock_ai_provider import (
    LOCAL_MOCK_PROVIDER_DISPLAY_NAME,
    LOCAL_MOCK_PROVIDER_IDENTIFIER,
    LocalMockAIProvider,
)
from model_health_assistant.models import ModelHealthReport
from model_health_assistant.openai_provider import (
    DEFAULT_OPENAI_REASONING_EFFORT,
    DEFAULT_OPENAI_RESPONSE_VERBOSITY,
    OPENAI_PROVIDER_DISPLAY_NAME,
    OPENAI_PROVIDER_IDENTIFIER,
    OpenAIExplanationProvider,
    OpenAIProviderConfiguration,
)
from model_health_assistant.provider_settings import ProviderSettings

LOCAL_MOCK_MODEL_IDENTIFIER = "deterministic-local-v1"


class AIWorkflowUnavailableError(RuntimeError):
    """Raised when the selected provider is not ready for an explanation."""

    def __init__(self, message: str, *, status_code: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AIWorkflowStatus:
    """Non-secret readiness state used to enable and explain UI actions."""

    provider_identifier: str
    provider_display_name: str
    model_identifier: str
    available: bool
    status_code: str
    message: str


@dataclass(frozen=True, slots=True)
class AIExecutionPlan:
    """Request-time provider snapshot safe to execute outside the GUI thread.

    The plan contains no FreeCAD document, Qt widget, settings adapter, report
    path, or raw report.  Consent is consumed before this object is created, so
    worker execution performs only provider-neutral request execution.
    """

    request_type: AIRequestType
    provider_identifier: str
    model_identifier: str
    request: AIExplanationRequest = field(repr=False)
    provider: AIExplanationProvider = field(repr=False)

    def execute(self) -> AIExplanationResponse:
        """Execute and validate this already-reviewed provider request."""

        return AIExplanationService(self.provider).explain_request(self.request)


SettingsLoader = Callable[[], ProviderSettings]
MockProviderFactory = Callable[[], AIExplanationProvider]
OpenAIProviderFactory = Callable[
    [ProviderSettings, Callable[[], str]],
    AIExplanationProvider,
]


class AIExplanationWorkflow:
    """Build, review, consume, execute, and validate one explanation request."""

    def __init__(
        self,
        settings_loader: SettingsLoader,
        *,
        credential_store: Any | None = None,
        mock_provider_factory: MockProviderFactory = LocalMockAIProvider,
        openai_provider_factory: OpenAIProviderFactory | None = None,
    ) -> None:
        if not callable(settings_loader):
            raise TypeError("settings_loader must be callable")
        if not callable(mock_provider_factory):
            raise TypeError("mock_provider_factory must be callable")
        if openai_provider_factory is not None and not callable(
            openai_provider_factory
        ):
            raise TypeError("openai_provider_factory must be callable")
        self._settings_loader = settings_loader
        self._credential_store = credential_store
        self._mock_provider_factory = mock_provider_factory
        self._openai_provider_factory = (
            openai_provider_factory or _default_openai_provider_factory
        )

    def status(self) -> AIWorkflowStatus:
        """Return fail-closed readiness without sending a provider request."""

        try:
            settings = self._load_settings()
        except Exception:
            return AIWorkflowStatus(
                provider_identifier="",
                provider_display_name="Selected provider",
                model_identifier="",
                available=False,
                status_code="settings_unavailable",
                message="Provider settings could not be loaded safely.",
            )

        if settings.provider_identifier == LOCAL_MOCK_PROVIDER_IDENTIFIER:
            try:
                provider = self._mock_provider_factory()
                available = provider.is_available()
            except Exception:
                available = False
            return AIWorkflowStatus(
                provider_identifier=LOCAL_MOCK_PROVIDER_IDENTIFIER,
                provider_display_name=LOCAL_MOCK_PROVIDER_DISPLAY_NAME,
                model_identifier=LOCAL_MOCK_MODEL_IDENTIFIER,
                available=available,
                status_code="ready" if available else "provider_unavailable",
                message=(
                    "The deterministic local provider is ready."
                    if available
                    else "The deterministic local provider is unavailable."
                ),
            )

        if settings.provider_identifier != OPENAI_PROVIDER_IDENTIFIER:
            return _unavailable_openai_status(
                "unsupported_provider",
                "The selected explanation provider is unsupported.",
                settings.preferred_model_identifier,
            )
        if not settings.real_provider_enabled:
            return _unavailable_openai_status(
                "provider_disabled",
                "OpenAI must be explicitly enabled in Provider Settings.",
                settings.preferred_model_identifier,
            )
        try:
            get_openai_model_capabilities(settings.preferred_model_identifier)
        except AIUnsupportedModelError:
            return _unavailable_openai_status(
                "unsupported_model",
                "The configured OpenAI model is unsupported. Choose a supported "
                "model explicitly in Provider Settings.",
                settings.preferred_model_identifier,
            )
        except AIInvalidRequestError:
            status_code = (
                "empty_model"
                if not settings.preferred_model_identifier
                else "malformed_model"
            )
            return _unavailable_openai_status(
                status_code,
                "Configure a valid nonempty OpenAI model in Provider Settings.",
                settings.preferred_model_identifier,
            )
        if self._credential_store is None:
            return _unavailable_openai_status(
                "credential_backend_unavailable",
                "Secure credential storage is unavailable.",
                settings.preferred_model_identifier,
            )
        try:
            credential_status = self._credential_store.credential_status(
                OPENAI_PROVIDER_IDENTIFIER
            )
        except Exception:
            return _unavailable_openai_status(
                "credential_backend_unavailable",
                "Secure credential status could not be read safely.",
                settings.preferred_model_identifier,
            )
        if not getattr(credential_status, "backend_available", False):
            return _unavailable_openai_status(
                "credential_backend_unavailable",
                "Secure credential storage is unavailable.",
                settings.preferred_model_identifier,
            )
        if not getattr(credential_status, "configured", False):
            status_code = getattr(credential_status, "status_code", "")
            message = (
                "Secure credential status could not be read safely."
                if status_code == "storage_error"
                else "No OpenAI API key is configured in secure storage."
            )
            return _unavailable_openai_status(
                "credential_storage_error"
                if status_code == "storage_error"
                else "credential_missing",
                message,
                settings.preferred_model_identifier,
            )

        try:
            provider = self._openai_provider(settings)
            sdk_available = provider.is_available()
        except Exception:
            sdk_available = False
        if not sdk_available:
            return _unavailable_openai_status(
                "dependency_missing",
                "The approved OpenAI SDK is unavailable in FreeCAD's Python runtime.",
                settings.preferred_model_identifier,
            )
        return AIWorkflowStatus(
            provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
            provider_display_name=OPENAI_PROVIDER_DISPLAY_NAME,
            model_identifier=settings.preferred_model_identifier,
            available=True,
            status_code="ready",
            message="OpenAI is ready for an explicitly reviewed request.",
        )

    def build_review(
        self,
        report: ModelHealthReport,
        *,
        request_type: AIRequestType,
        selected_warning_identifier: str | None = None,
        user_question: str | None = None,
    ) -> AIRequestReview:
        """Build an exact review for the currently ready provider and model."""

        status = self.status()
        if not status.available:
            raise AIWorkflowUnavailableError(
                status.message,
                status_code=status.status_code,
            )
        return build_ai_request_review(
            report,
            provider_identifier=status.provider_identifier,
            provider_display_name=status.provider_display_name,
            model_identifier=status.model_identifier,
            request_type=request_type,
            selected_warning_identifier=selected_warning_identifier,
            user_question=user_question,
        )

    def execute_review(
        self,
        consent: AIRequestConsent,
        current_report: ModelHealthReport,
        *,
        selected_warning_identifier: str | None = None,
    ) -> AIExplanationResponse:
        """Consume one approved review and execute that exact request once."""

        return self.prepare_execution(
            consent,
            current_report,
            selected_warning_identifier=selected_warning_identifier,
        ).execute()

    def prepare_execution(
        self,
        consent: AIRequestConsent,
        current_report: ModelHealthReport,
        *,
        selected_warning_identifier: str | None = None,
    ) -> AIExecutionPlan:
        """Consume consent and freeze provider/request state on the GUI thread."""

        if not isinstance(consent, AIRequestConsent):
            raise TypeError("consent must be an AIRequestConsent")
        status = self.status()
        if not status.available:
            raise AIWorkflowUnavailableError(
                status.message,
                status_code=status.status_code,
            )
        review = consent.review
        if (
            status.provider_identifier != review.provider_identifier
            or status.model_identifier != review.model_identifier
        ):
            raise AIConsentInvalidatedError(
                "The selected provider or model changed after preview; review it again."
            )
        provider = self._provider_for_identifier(status.provider_identifier)
        if provider.provider_identifier != review.provider_identifier:
            raise AIConsentInvalidatedError(
                "The provider implementation changed after preview; review it again."
            )
        request = consent.consume(
            current_report,
            selected_warning_identifier=selected_warning_identifier,
        )
        return AIExecutionPlan(
            request_type=review.request_type,
            provider_identifier=review.provider_identifier,
            model_identifier=review.model_identifier,
            request=request,
            provider=provider,
        )

    def _load_settings(self) -> ProviderSettings:
        settings = self._settings_loader()
        if not isinstance(settings, ProviderSettings):
            raise TypeError("settings loader returned an invalid value")
        return settings

    def _provider_for_identifier(
        self,
        provider_identifier: str,
    ) -> AIExplanationProvider:
        settings = self._load_settings()
        if provider_identifier == LOCAL_MOCK_PROVIDER_IDENTIFIER:
            provider = self._mock_provider_factory()
        elif provider_identifier == OPENAI_PROVIDER_IDENTIFIER:
            provider = self._openai_provider(settings)
        else:
            raise AIWorkflowUnavailableError(
                "The selected explanation provider is unsupported.",
                status_code="unsupported_provider",
            )
        if not isinstance(provider, AIExplanationProvider):
            raise TypeError("provider factory returned an invalid provider")
        return provider

    def _openai_provider(
        self,
        settings: ProviderSettings,
    ) -> AIExplanationProvider:
        if self._credential_store is None:
            raise AIWorkflowUnavailableError(
                "Secure credential storage is unavailable.",
                status_code="credential_backend_unavailable",
            )
        provider = self._openai_provider_factory(
            settings,
            lambda: self._credential_store.retrieve_for_provider_execution(
                OPENAI_PROVIDER_IDENTIFIER
            ),
        )
        if not isinstance(provider, AIExplanationProvider):
            raise TypeError("OpenAI provider factory returned an invalid provider")
        return provider


def _default_openai_provider_factory(
    settings: ProviderSettings,
    credential_provider: Callable[[], str],
) -> AIExplanationProvider:
    configuration = OpenAIProviderConfiguration(
        model_identifier=settings.preferred_model_identifier,
        request_timeout_seconds=settings.request_timeout_seconds,
        maximum_retries=settings.maximum_retries,
        maximum_output_tokens=settings.maximum_output_tokens,
        reasoning_effort=(
            settings.reasoning_effort or DEFAULT_OPENAI_REASONING_EFFORT
        ),
        response_verbosity=(
            settings.response_verbosity or DEFAULT_OPENAI_RESPONSE_VERBOSITY
        ),
    )
    return OpenAIExplanationProvider(
        credential_provider,
        configuration=configuration,
    )


def _unavailable_openai_status(
    status_code: str,
    message: str,
    model_identifier: str,
) -> AIWorkflowStatus:
    return AIWorkflowStatus(
        provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        provider_display_name=OPENAI_PROVIDER_DISPLAY_NAME,
        model_identifier=model_identifier,
        available=False,
        status_code=status_code,
        message=message,
    )


__all__ = [
    "AIExecutionPlan",
    "AIExplanationWorkflow",
    "AIWorkflowStatus",
    "AIWorkflowUnavailableError",
    "LOCAL_MOCK_MODEL_IDENTIFIER",
]
