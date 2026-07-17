import ast
import socket
from pathlib import Path

import pytest

from model_health_assistant.ai_context import build_ai_request_context
from model_health_assistant.ai_models import AIExplanationRequest, AIExplanationResponse
from model_health_assistant.ai_prompts import build_ai_explanation_request
from model_health_assistant.ai_provider import (
    AIAuthenticationError,
    AIConnectionError,
    AIExplanationProvider,
    AIInvalidResponseError,
    AIInvalidRequestError,
    AIMissingCredentialError,
    AIPermissionError,
    AIProviderError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIRequestRejectedError,
    AIRequestTimeoutError,
    AIUnsupportedSDKError,
)
from model_health_assistant.models import ModelHealthReport


def _request() -> AIExplanationRequest:
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=100,
    )
    return build_ai_explanation_request(build_ai_request_context(report))


class _MinimalProvider:
    @property
    def provider_identifier(self) -> str:
        return "minimal-test"

    @property
    def display_name(self) -> str:
        return "Minimal Test Provider"

    def is_available(self) -> bool:
        return True

    def capability_description(self) -> str:
        return "Returns a fixed structured response for interface tests."

    def explain(self, request: AIExplanationRequest) -> AIExplanationResponse:
        assert isinstance(request, AIExplanationRequest)
        return AIExplanationResponse(
            provider_identifier=self.provider_identifier,
            provider_display_name=self.display_name,
            overall_summary="No existing warnings required explanation.",
        )


class _UnavailableProvider(_MinimalProvider):
    def is_available(self) -> bool:
        return False

    def explain(self, request: AIExplanationRequest) -> AIExplanationResponse:
        raise AIProviderUnavailableError(
            f"Provider '{self.provider_identifier}' is unavailable."
        )


def test_minimal_provider_conforms_and_returns_structured_response() -> None:
    provider = _MinimalProvider()

    assert isinstance(provider, AIExplanationProvider)
    assert provider.provider_identifier == "minimal-test"
    assert provider.display_name == "Minimal Test Provider"
    assert provider.is_available() is True
    assert "fixed structured response" in provider.capability_description()
    response = provider.explain(_request())
    assert isinstance(response, AIExplanationResponse)
    assert response.provider_identifier == "minimal-test"


def test_unavailable_provider_uses_controlled_neutral_error() -> None:
    provider = _UnavailableProvider()

    assert isinstance(provider, AIExplanationProvider)
    assert provider.is_available() is False
    with pytest.raises(AIProviderUnavailableError, match="minimal-test"):
        provider.explain(_request())


def test_provider_errors_share_one_neutral_base_type() -> None:
    error_types = (
        AIProviderUnavailableError,
        AIRequestRejectedError,
        AIInvalidResponseError,
        AIRequestTimeoutError,
        AIAuthenticationError,
        AIMissingCredentialError,
        AIPermissionError,
        AIConnectionError,
        AIInvalidRequestError,
        AIProviderServerError,
        AIProviderRefusalError,
        AIUnsupportedSDKError,
        AIRateLimitError,
    )

    assert all(issubclass(error_type, AIProviderError) for error_type in error_types)
    assert all(issubclass(error_type, RuntimeError) for error_type in error_types)


def test_provider_interface_has_no_freecad_sdk_or_network_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "ai_provider.py"
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


def test_interface_use_performs_no_network_access(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    response = _MinimalProvider().explain(_request())

    assert response.overall_summary
