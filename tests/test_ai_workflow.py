from copy import deepcopy
from types import SimpleNamespace

import pytest

from model_health_assistant.ai_consent import (
    AIConsentInvalidatedError,
    AIConsentNotApprovedError,
    AIRequestConsent,
)
from model_health_assistant.ai_models import (
    AIExplanationResponse,
    AIUsageMetadata,
    AIWarningExplanation,
)
from model_health_assistant.ai_provider import (
    AIInvalidResponseError,
    AIProviderRefusalError,
    AIRequestRejectedError,
)
from model_health_assistant.ai_workflow import (
    AIExplanationWorkflow,
    AIWorkflowUnavailableError,
)
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning
from model_health_assistant.provider_settings import ProviderSettings


def _report() -> ModelHealthReport:
    return ModelHealthReport(
        document_name="Private assembly",
        generated_at="2026-07-17T12:00:00+00:00",
        health_score=72,
        features=[
            FeatureInfo(internal_name="Pad001", label="Base pad", category="pad"),
            FeatureInfo(
                internal_name="Pocket001",
                label="Private pocket label",
                category="pocket",
            ),
        ],
        warnings=[
            RiskWarning(
                warning_id="WARN-1",
                severity="medium",
                affected_feature_name="Pad001",
                message="First warning",
                technical_reason="First reason",
                suggested_practice="Review first feature",
                rule_id="high_downstream_dependency_count",
            ),
            RiskWarning(
                warning_id="WARN-2",
                severity="high",
                affected_feature_name="Pocket001",
                message="Second warning",
                technical_reason="Second reason",
                suggested_practice="Review second feature",
                rule_id="confirmed_recompute_error",
            ),
        ],
    )


class _CredentialStore:
    def __init__(self, *, configured: bool = True, backend: bool = True) -> None:
        self.configured = configured
        self.backend = backend
        self.retrieve_count = 0

    def credential_status(self, provider_identifier: str):
        assert provider_identifier == "openai"
        return SimpleNamespace(
            configured=self.configured,
            backend_available=self.backend,
            status_code="configured" if self.configured else "not_configured",
        )

    def retrieve_for_provider_execution(self, provider_identifier: str) -> str:
        assert provider_identifier == "openai"
        self.retrieve_count += 1
        return "secret-never-displayed"


class _RecordingProvider:
    provider_identifier = "openai"
    display_name = "OpenAI"

    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self.available = available
        self.fail = fail
        self.requests = []
        self.credential_provider = None

    def is_available(self) -> bool:
        return self.available

    def capability_description(self) -> str:
        return "Fake OpenAI test adapter"

    def explain(self, request):
        if self.credential_provider is not None:
            assert self.credential_provider() == "secret-never-displayed"
        self.requests.append(request)
        if self.fail:
            raise AIRequestRejectedError("fake controlled rejection")
        warning_explanations = [
            AIWarningExplanation(
                warning_id=warning.warning_id,
                rule_id=warning.rule_id,
                affected_feature_internal_name=(
                    warning.affected_feature_internal_name
                ),
                severity=warning.severity,
                explanation="Explanation",
                why_it_matters="Impact",
                limitations=["Fake adapter limitation"],
            )
            for warning in request.context.warnings
        ]
        return AIExplanationResponse(
            provider_identifier="openai",
            provider_display_name="OpenAI",
            overall_summary="Fake structured summary",
            warning_explanations=warning_explanations,
            limitations=["Only supplied context was considered."],
            request_identifier="resp_fake_123",
            actual_model_identifier="gpt-5.6-terra",
            usage=AIUsageMetadata(input_tokens=20, output_tokens=10, total_tokens=30),
        )


def _openai_settings() -> ProviderSettings:
    return ProviderSettings(
        provider_identifier="openai",
        preferred_model_identifier="gpt-5.6-terra",
        real_provider_enabled=True,
    )


def test_selected_warning_local_mock_executes_only_exact_reviewed_warning() -> None:
    report = _report()
    before = deepcopy(report.to_dict())
    workflow = AIExplanationWorkflow(lambda: ProviderSettings())
    review = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-2",
    )
    consent = AIRequestConsent(review)

    assert review.provider_identifier == "local-mock"
    assert review.model_identifier == "deterministic-local-v1"
    assert [warning.warning_id for warning in review.request.context.warnings] == [
        "WARN-2"
    ]
    assert review.request.context.document_display_name == "Active FreeCAD document"
    consent.approve()
    response = workflow.execute_review(
        consent,
        report,
        selected_warning_identifier="WARN-2",
    )

    assert [item.warning_id for item in response.warning_explanations] == ["WARN-2"]
    assert report.to_dict() == before


def test_selected_warning_requires_explicit_unused_consent() -> None:
    report = _report()
    workflow = AIExplanationWorkflow(lambda: ProviderSettings())
    review = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )

    with pytest.raises(AIConsentNotApprovedError):
        workflow.execute_review(
            AIRequestConsent(review),
            report,
            selected_warning_identifier="WARN-1",
        )

    cancelled = AIRequestConsent(review)
    cancelled.cancel()
    with pytest.raises(AIConsentNotApprovedError):
        workflow.execute_review(
            cancelled,
            report,
            selected_warning_identifier="WARN-1",
        )


def test_changed_selection_report_provider_or_model_invalidates_review() -> None:
    report = _report()
    selected = {"settings": ProviderSettings()}
    workflow = AIExplanationWorkflow(
        lambda: selected["settings"],
        credential_store=_CredentialStore(),
        openai_provider_factory=lambda _settings, _credential: _RecordingProvider(),
    )
    review = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )
    wrong_selection = AIRequestConsent(review)
    wrong_selection.approve()
    with pytest.raises(AIConsentInvalidatedError, match="selected warning changed"):
        workflow.execute_review(
            wrong_selection,
            report,
            selected_warning_identifier="WARN-2",
        )

    changed_report = AIRequestConsent(review)
    changed_report.approve()
    report.health_score = 71
    with pytest.raises(AIConsentInvalidatedError, match="report changed"):
        workflow.execute_review(
            changed_report,
            report,
            selected_warning_identifier="WARN-1",
        )

    fresh_report = _report()
    fresh_review = workflow.build_review(
        fresh_report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )
    provider_changed = AIRequestConsent(fresh_review)
    provider_changed.approve()
    selected["settings"] = _openai_settings()
    with pytest.raises(
        AIConsentInvalidatedError,
        match="selected provider or model changed",
    ):
        workflow.execute_review(
            provider_changed,
            fresh_report,
            selected_warning_identifier="WARN-1",
        )


def test_fake_openai_adapter_receives_same_reviewed_request_and_returns_provenance() -> None:
    report = _report()
    credentials = _CredentialStore()
    provider = _RecordingProvider()

    def provider_factory(_settings, credential_provider):
        provider.credential_provider = credential_provider
        return provider

    workflow = AIExplanationWorkflow(
        _openai_settings,
        credential_store=credentials,
        openai_provider_factory=provider_factory,
    )
    review = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-2",
    )
    consent = AIRequestConsent(review)
    consent.approve()
    response = workflow.execute_review(
        consent,
        report,
        selected_warning_identifier="WARN-2",
    )

    assert provider.requests == [review.request]
    assert provider.requests[0] is review.request
    assert credentials.retrieve_count == 1
    assert response.request_identifier == "resp_fake_123"
    assert response.actual_model_identifier == "gpt-5.6-terra"
    assert response.usage.total_tokens == 30


def test_prepared_execution_plan_has_no_report_or_settings_work_in_worker() -> None:
    report = _report()
    credentials = _CredentialStore()
    provider = _RecordingProvider()
    settings_loads = []

    def load_settings() -> ProviderSettings:
        settings_loads.append("load")
        return _openai_settings()

    workflow = AIExplanationWorkflow(
        load_settings,
        credential_store=credentials,
        openai_provider_factory=lambda _settings, credential_provider: (
            setattr(provider, "credential_provider", credential_provider) or provider
        ),
    )
    review = workflow.build_review(report, request_type="full-analysis")
    consent = AIRequestConsent(review)
    consent.approve()

    plan = workflow.prepare_execution(consent, report)
    loads_before_worker = len(settings_loads)

    assert plan.request_type == "full-analysis"
    assert plan.provider_identifier == "openai"
    assert plan.request is review.request
    assert not hasattr(plan, "report")
    assert not hasattr(plan, "document")
    assert not hasattr(plan, "settings_loader")

    response = plan.execute()

    assert response.request_identifier == "resp_fake_123"
    assert len(settings_loads) == loads_before_worker
    assert provider.requests == [review.request]


@pytest.mark.parametrize(
    ("credentials", "available", "code"),
    [
        (_CredentialStore(configured=False), True, "credential_missing"),
        (_CredentialStore(backend=False), True, "credential_backend_unavailable"),
        (_CredentialStore(), False, "dependency_missing"),
    ],
)
def test_openai_readiness_fails_closed(credentials, available: bool, code: str) -> None:
    provider = _RecordingProvider(available=available)
    workflow = AIExplanationWorkflow(
        _openai_settings,
        credential_store=credentials,
        openai_provider_factory=lambda _settings, _credential: provider,
    )

    status = workflow.status()

    assert status.available is False
    assert status.status_code == code
    with pytest.raises(AIWorkflowUnavailableError):
        workflow.build_review(
            _report(),
            request_type="selected-warning",
            selected_warning_identifier="WARN-1",
        )


def test_fake_openai_provider_error_propagates_without_mutating_report() -> None:
    report = _report()
    before = deepcopy(report.to_dict())
    provider = _RecordingProvider(fail=True)
    workflow = AIExplanationWorkflow(
        _openai_settings,
        credential_store=_CredentialStore(),
        openai_provider_factory=lambda _settings, _credential: provider,
    )
    review = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )
    consent = AIRequestConsent(review)
    consent.approve()

    with pytest.raises(AIRequestRejectedError, match="fake controlled rejection"):
        workflow.execute_review(
            consent,
            report,
            selected_warning_identifier="WARN-1",
        )
    assert report.to_dict() == before


def test_full_analysis_local_mock_handles_zero_and_many_warnings_without_mutation() -> None:
    for report in (
        ModelHealthReport(
            document_name="Empty",
            generated_at="2026-07-17T12:00:00+00:00",
            health_score=100,
        ),
        _report(),
    ):
        before = deepcopy(report.to_dict())
        workflow = AIExplanationWorkflow(lambda: ProviderSettings())
        review = workflow.build_review(report, request_type="full-analysis")
        consent = AIRequestConsent(review)
        consent.approve()

        response = workflow.execute_review(consent, report)

        assert len(response.warning_explanations) == len(
            review.request.context.warnings
        )
        assert report.to_dict() == before


def test_full_analysis_requires_a_fresh_review_distinct_from_selected_warning() -> None:
    report = _report()
    workflow = AIExplanationWorkflow(lambda: ProviderSettings())
    selected = workflow.build_review(
        report,
        request_type="selected-warning",
        selected_warning_identifier="WARN-1",
    )
    full_first = workflow.build_review(report, request_type="full-analysis")
    full_second = workflow.build_review(report, request_type="full-analysis")

    assert selected.request_type == "selected-warning"
    assert full_first.request_type == "full-analysis"
    assert selected.request_fingerprint != full_first.request_fingerprint
    assert len(selected.request.context.warnings) == 1
    assert len(full_first.request.context.warnings) == 2
    assert full_first is not full_second
    assert full_first.request is not full_second.request


def test_full_analysis_accepts_valid_partial_provider_output() -> None:
    report = _report()

    class PartialProvider(_RecordingProvider):
        def explain(self, request):
            self.requests.append(request)
            first = request.context.warnings[0]
            return AIExplanationResponse(
                provider_identifier="openai",
                provider_display_name="OpenAI",
                overall_summary="Valid partial full-analysis response",
                warning_explanations=[
                    AIWarningExplanation(
                        warning_id=first.warning_id,
                        rule_id=first.rule_id,
                        affected_feature_internal_name=(
                            first.affected_feature_internal_name
                        ),
                        severity=first.severity,
                        explanation="Partial explanation",
                        why_it_matters="Partial impact",
                    )
                ],
                limitations=["One included warning was not explained."],
            )

    provider = PartialProvider()
    workflow = AIExplanationWorkflow(
        _openai_settings,
        credential_store=_CredentialStore(),
        openai_provider_factory=lambda _settings, _credential: provider,
    )
    review = workflow.build_review(report, request_type="full-analysis")
    consent = AIRequestConsent(review)
    consent.approve()

    response = workflow.execute_review(consent, report)

    assert len(review.request.context.warnings) == 2
    assert len(response.warning_explanations) == 1


@pytest.mark.parametrize("failure", ["refusal", "duplicate"])
def test_full_analysis_refusal_or_invalid_duplicate_is_not_accepted(failure: str) -> None:
    report = _report()

    class FailingProvider(_RecordingProvider):
        def explain(self, request):
            if failure == "refusal":
                raise AIProviderRefusalError("controlled refusal")
            warning = request.context.warnings[0]
            item = AIWarningExplanation(
                warning_id=warning.warning_id,
                rule_id=warning.rule_id,
                affected_feature_internal_name=warning.affected_feature_internal_name,
                severity=warning.severity,
                explanation="Explanation",
                why_it_matters="Impact",
            )
            return AIExplanationResponse(
                provider_identifier="openai",
                provider_display_name="OpenAI",
                overall_summary="Invalid duplicate response",
                warning_explanations=[item, deepcopy(item)],
                limitations=["Limitation"],
            )

    provider = FailingProvider()
    workflow = AIExplanationWorkflow(
        _openai_settings,
        credential_store=_CredentialStore(),
        openai_provider_factory=lambda _settings, _credential: provider,
    )
    review = workflow.build_review(report, request_type="full-analysis")
    consent = AIRequestConsent(review)
    consent.approve()

    expected = AIProviderRefusalError if failure == "refusal" else AIInvalidResponseError
    with pytest.raises(expected):
        workflow.execute_review(consent, report)
