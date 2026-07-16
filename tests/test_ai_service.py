import ast
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from model_health_assistant.ai_models import (
    AIExplanationResponse,
    AIWarningExplanation,
)
from model_health_assistant.ai_provider import (
    AIInvalidResponseError,
    AIProviderUnavailableError,
    AIRequestRejectedError,
)
from model_health_assistant.ai_service import (
    AIContextBuildError,
    AIExplanationService,
)
from model_health_assistant.mock_ai_provider import LocalMockAIProvider
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _report(*, with_warning: bool = True) -> ModelHealthReport:
    features = []
    warnings = []
    if with_warning:
        features.append(
            FeatureInfo(
                internal_name="Pocket001",
                label="Drive pocket",
                category="subtractive_feature",
                has_confirmed_error=True,
                error_details=["State: Invalid"],
            )
        )
        warnings.append(
            RiskWarning(
                warning_id="WARN-001",
                severity="high",
                affected_feature_name="Pocket001",
                message="Pocket001 reports a confirmed error.",
                technical_reason="FreeCAD exposed an explicit error state.",
                suggested_practice="Review the earliest failing feature.",
                rule_id="confirmed_recompute_error",
            )
        )
    return ModelHealthReport(
        document_name="Private model",
        generated_at="2026-07-16T12:30:00+00:00",
        health_score=75,
        features=features,
        warnings=warnings,
    )


class _UnavailableProvider(LocalMockAIProvider):
    def is_available(self) -> bool:
        return False

    def explain(self, request):
        raise AssertionError("unavailable provider must not be called")


class _ControlledErrorProvider(LocalMockAIProvider):
    def explain(self, request):
        raise AIRequestRejectedError("controlled rejection")


class _ResponseProvider(LocalMockAIProvider):
    def __init__(self, response) -> None:
        self.response = response

    def explain(self, request):
        return self.response


def test_service_executes_successful_mock_request() -> None:
    response = AIExplanationService(LocalMockAIProvider()).explain_report(_report())

    assert response.provider_identifier == "local-mock"
    assert len(response.warning_explanations) == 1
    assert response.warning_explanations[0].rule_id == "confirmed_recompute_error"
    assert response.warning_explanations[0].affected_feature_internal_name == "Pocket001"


def test_service_handles_empty_warning_report() -> None:
    response = AIExplanationService(LocalMockAIProvider()).explain_report(
        _report(with_warning=False)
    )

    assert response.warning_explanations == []
    assert "no existing model-health warnings" in response.overall_summary


def test_context_builder_failure_is_wrapped_and_not_swallowed() -> None:
    def fail_context_builder(report, **kwargs):
        raise ValueError("malformed report evidence")

    service = AIExplanationService(
        LocalMockAIProvider(),
        context_builder=fail_context_builder,
    )

    with pytest.raises(AIContextBuildError, match="malformed report evidence") as exc_info:
        service.explain_report(_report())

    assert isinstance(exc_info.value.__cause__, ValueError)


def test_unavailable_provider_fails_before_execution() -> None:
    service = AIExplanationService(_UnavailableProvider())

    with pytest.raises(AIProviderUnavailableError, match="local-mock"):
        service.explain_report(_report())


def test_controlled_provider_error_propagates() -> None:
    with pytest.raises(AIRequestRejectedError, match="controlled rejection"):
        AIExplanationService(_ControlledErrorProvider()).explain_report(_report())


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (object(), "wrong response type"),
        (
            AIExplanationResponse(
                provider_identifier="local-mock",
                provider_display_name="Local Mock Provider",
                overall_summary="Summary",
                limitations=["Limitation"],
                schema_version="99.0",
            ),
            "unsupported response schema version",
        ),
        (
            AIExplanationResponse(
                provider_identifier="",
                provider_display_name="Local Mock Provider",
                overall_summary="Summary",
                limitations=["Limitation"],
            ),
            "missing a provider identifier",
        ),
        (
            AIExplanationResponse(
                provider_identifier="unsupported-future-provider",
                provider_display_name="Unsupported Future Provider",
                overall_summary="Summary",
                limitations=["Limitation"],
            ),
            "does not match the injected provider",
        ),
        (
            AIExplanationResponse(
                provider_identifier="local-mock",
                provider_display_name="Local Mock Provider",
                overall_summary="Summary",
                limitations=[],
            ),
            "limitation metadata",
        ),
    ],
)
def test_invalid_provider_response_is_rejected(response, message: str) -> None:
    with pytest.raises(AIInvalidResponseError, match=message):
        AIExplanationService(_ResponseProvider(response)).explain_report(_report())


def test_unknown_warning_rule_or_feature_reference_is_rejected() -> None:
    invalid_response = AIExplanationResponse(
        provider_identifier="local-mock",
        provider_display_name="Local Mock Provider",
        overall_summary="Summary",
        warning_explanations=[
            AIWarningExplanation(
                warning_id="WARN-001",
                rule_id="invented_rule",
                affected_feature_internal_name="InventedFeature",
                severity="high",
                explanation="Invented explanation.",
                why_it_matters="Invented impact.",
            )
        ],
        limitations=["Limitation"],
    )

    with pytest.raises(AIInvalidResponseError, match="unknown warning rule or feature"):
        AIExplanationService(_ResponseProvider(invalid_response)).explain_report(_report())


def test_valid_partial_response_does_not_require_every_warning_explanation() -> None:
    partial_response = AIExplanationResponse(
        provider_identifier="local-mock",
        provider_display_name="Local Mock Provider",
        overall_summary="A valid partial response.",
        warning_explanations=[],
        limitations=["The provider returned no per-warning explanation."],
    )

    result = AIExplanationService(_ResponseProvider(partial_response)).explain_report(
        _report()
    )

    assert result is partial_response


def test_input_report_remains_unchanged() -> None:
    report = _report()
    before = deepcopy(report.to_dict())

    AIExplanationService(LocalMockAIProvider()).explain_report(report)

    assert report.to_dict() == before


def test_service_module_has_no_freecad_qt_or_network_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "ai_service.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported_roots.isdisjoint(
        {
            "FreeCAD",
            "FreeCADGui",
            "PySide",
            "PySide2",
            "http",
            "requests",
            "socket",
            "urllib",
        }
    )


def test_service_and_mock_work_without_network_access(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    response = AIExplanationService(LocalMockAIProvider()).explain_report(_report())

    assert response.provider_identifier == "local-mock"
