import ast
import inspect
import json
import socket
from pathlib import Path

import pytest

from model_health_assistant.ai_context import build_ai_request_context
from model_health_assistant.ai_models import AIExplanationResponse
from model_health_assistant.ai_prompts import build_ai_explanation_request
from model_health_assistant.ai_provider import (
    AIExplanationProvider,
    AIRequestRejectedError,
)
from model_health_assistant.mock_ai_provider import (
    LOCAL_MOCK_LIMITATION,
    LOCAL_MOCK_PROVIDER_DISPLAY_NAME,
    LOCAL_MOCK_PROVIDER_IDENTIFIER,
    LocalMockAIProvider,
)
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _warning(index: int, rule_id: str, severity: str, feature_name: str) -> RiskWarning:
    return RiskWarning(
        warning_id=f"WARN-{index:03d}",
        severity=severity,  # type: ignore[arg-type]
        affected_feature_name=feature_name,
        message=f"Source warning for {feature_name}.",
        technical_reason=f"Source technical reason for rule {rule_id}.",
        suggested_practice="Review the source warning manually.",
        rule_id=rule_id,
    )


def _request(warnings: list[RiskWarning] | None = None):
    source_warnings = [] if warnings is None else warnings
    features = [
        FeatureInfo(
            internal_name=warning.affected_feature_name,
            label=f"Label for {warning.affected_feature_name}",
            category="other",
        )
        for warning in source_warnings
    ]
    report = ModelHealthReport(
        document_name="Private model",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=75,
        features=features,
        warnings=source_warnings,
    )
    return build_ai_explanation_request(build_ai_request_context(report))


def test_mock_provider_conforms_and_identifies_itself() -> None:
    provider = LocalMockAIProvider()

    assert isinstance(provider, AIExplanationProvider)
    assert provider.provider_identifier == LOCAL_MOCK_PROVIDER_IDENTIFIER
    assert provider.display_name == LOCAL_MOCK_PROVIDER_DISPLAY_NAME
    assert provider.provider_identifier == "local-mock"
    assert provider.display_name == "Local Mock Provider"
    assert provider.is_available() is True
    assert "without an AI model or network service" in provider.capability_description()


def test_mock_provider_output_is_deterministic_and_structured() -> None:
    request = _request(
        [_warning(1, "confirmed_recompute_error", "high", "Pocket001")]
    )
    provider = LocalMockAIProvider()

    first = provider.explain(request)
    second = provider.explain(request)

    assert isinstance(first, AIExplanationResponse)
    assert first.to_dict() == second.to_dict()
    assert json.loads(json.dumps(first.to_dict())) == first.to_dict()
    assert first.schema_version == "1.0"


def test_empty_warning_request_returns_clear_mock_result() -> None:
    response = LocalMockAIProvider().explain(_request())

    assert response.warning_explanations == []
    assert "no existing model-health warnings" in response.overall_summary
    assert "No AI model was used" in response.overall_summary
    assert LOCAL_MOCK_LIMITATION in response.limitations


def test_multiple_warnings_preserve_source_rule_feature_and_severity() -> None:
    warnings = [
        _warning(1, "unknown_sketch_support", "low", "Sketch001"),
        _warning(2, "confirmed_recompute_error", "high", "Pocket001"),
        _warning(3, "early_finishing_feature", "medium", "Fillet001"),
    ]
    request = _request(warnings)

    response = LocalMockAIProvider().explain(request)
    source_context = request.context.warnings

    assert len(response.warning_explanations) == len(source_context)
    assert [item.warning_id for item in response.warning_explanations] == [
        item.warning_id for item in source_context
    ]
    assert [item.rule_id for item in response.warning_explanations] == [
        item.rule_id for item in source_context
    ]
    assert [item.affected_feature_internal_name for item in response.warning_explanations] == [
        item.affected_feature_internal_name for item in source_context
    ]
    assert [item.severity for item in response.warning_explanations] == [
        item.severity for item in source_context
    ]
    assert "did not create new findings" in response.overall_summary


def test_rule_specific_review_steps_remain_advisory() -> None:
    response = LocalMockAIProvider().explain(
        _request([_warning(1, "confirmed_recompute_error", "high", "Pocket001")])
    )
    explanation = response.warning_explanations[0]

    assert explanation.manual_review_steps == [
        "Inspect the affected feature and the recorded FreeCAD error details.",
        "Review the feature tree from the earliest failing feature forward.",
        "After any manual correction, recompute the document and confirm the error state.",
    ]
    assert "no independent assessment" in explanation.confidence
    assert "No CAD geometry was inspected" in explanation.limitations[0]
    assert "does not add, verify, or replace" in explanation.limitations[1]


def test_mock_provider_does_not_mutate_request() -> None:
    request = _request(
        [_warning(1, "sketch_generated_geometry_support", "medium", "Sketch001")]
    )
    before = request.to_dict()

    LocalMockAIProvider().explain(request)

    assert request.to_dict() == before


def test_mock_provider_rejects_wrong_request_type() -> None:
    with pytest.raises(AIRequestRejectedError, match="AIExplanationRequest"):
        LocalMockAIProvider().explain(object())  # type: ignore[arg-type]


def test_mock_provider_requires_no_api_key_or_configuration() -> None:
    signature = inspect.signature(LocalMockAIProvider)

    assert list(signature.parameters) == []
    assert LocalMockAIProvider().is_available() is True


def test_mock_provider_performs_no_network_access(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    response = LocalMockAIProvider().explain(_request())

    assert response.provider_identifier == "local-mock"


def test_mock_provider_module_has_no_network_sdk_or_freecad_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "mock_ai_provider.py"
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
            "anthropic",
            "google",
            "http",
            "openai",
            "requests",
            "socket",
            "urllib",
        }
    )
