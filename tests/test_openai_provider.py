import ast
import json
import traceback
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from model_health_assistant.ai_models import (
    AIExplanationRequest,
    AIRequestContext,
    AIWarningContext,
)
from model_health_assistant.ai_model_capabilities import (
    get_openai_model_capabilities,
    resolve_model_options,
)
from model_health_assistant.ai_prompts import build_ai_explanation_request
from model_health_assistant.ai_provider import (
    AIAuthenticationError,
    AIConnectionError,
    AICredentialBackendUnavailableError,
    AIDependencyMissingError,
    AIInvalidRequestError,
    AIInvalidResponseError,
    AIMissingCredentialError,
    AIPermissionError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIRequestTimeoutError,
    AIUnsupportedSDKError,
    AIUnsupportedModelError,
)
from model_health_assistant.openai_provider import (
    DEFAULT_OPENAI_MODEL_IDENTIFIER,
    OPENAI_EXPLANATION_RESPONSE_SCHEMA,
    OPENAI_CONNECTION_RESPONSE_SCHEMA,
    OPENAI_NON_RETRYABLE_FAILURES,
    OPENAI_PROVIDER_IDENTIFIER,
    OPENAI_RETRYABLE_FAILURES,
    OPENAI_RETRY_POLICY_OWNER,
    OpenAIExplanationProvider,
    OpenAIProviderConfiguration,
)

_TEST_CREDENTIAL = "UNIT_TEST_OPENAI_CREDENTIAL_DO_NOT_EXPOSE"


class _AuthenticationError(Exception):
    pass


class _PermissionDeniedError(Exception):
    pass


class _RateLimitError(Exception):
    pass


class _APITimeoutError(Exception):
    pass


class _APIConnectionError(Exception):
    pass


class _BadRequestError(Exception):
    pass


class _UnprocessableEntityError(Exception):
    pass


class _NotFoundError(Exception):
    pass


class _InternalServerError(Exception):
    pass


def _sdk_module() -> ModuleType:
    module = ModuleType("openai")
    module.OpenAI = lambda **_kwargs: None
    module.AuthenticationError = _AuthenticationError
    module.PermissionDeniedError = _PermissionDeniedError
    module.RateLimitError = _RateLimitError
    module.APITimeoutError = _APITimeoutError
    module.APIConnectionError = _APIConnectionError
    module.BadRequestError = _BadRequestError
    module.UnprocessableEntityError = _UnprocessableEntityError
    module.NotFoundError = _NotFoundError
    module.InternalServerError = _InternalServerError
    return module


def _warning(index: int) -> AIWarningContext:
    return AIWarningContext(
        warning_id=f"WARN-{index:03d}",
        rule_id=f"rule-{index}",
        severity="high" if index == 1 else "medium",
        affected_feature_internal_name=f"Feature{index:03d}",
        message=f"Finding {index}",
        technical_reason="The deterministic analyzer observed this condition.",
        suggested_practice="Review the feature manually.",
        evidence={"has_confirmed_error": index == 1},
    )


def _request(warning_count: int = 1) -> AIExplanationRequest:
    warnings = [_warning(index) for index in range(1, warning_count + 1)]
    context = AIRequestContext(
        report_schema_version="0.3",
        health_score=65,
        total_document_object_count=warning_count,
        meaningful_modeling_feature_count=warning_count,
        total_sketch_count=0,
        category_counts={"other": warning_count},
        total_warning_count=warning_count,
        warning_counts_by_severity={
            "low": 0,
            "medium": max(0, warning_count - 1),
            "high": min(1, warning_count),
        },
        warnings=warnings,
        analysis_limitations=["The local analysis is advisory."],
    )
    return build_ai_explanation_request(context)


def _payload_for(request: AIExplanationRequest) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "overall_summary": "The supplied findings need manual review.",
        "warning_explanations": [
            {
                "warning_id": warning.warning_id,
                "rule_id": warning.rule_id,
                "affected_feature_internal_name": (
                    warning.affected_feature_internal_name
                ),
                "severity": warning.severity,
                "explanation": "This explains only the supplied local finding.",
                "why_it_matters": "It may affect downstream modeling operations.",
                "manual_review_steps": ["Inspect the feature in FreeCAD."],
                "confidence": "medium",
                "limitations": ["No CAD geometry was sent or inspected."],
            }
            for warning in request.context.warnings
        ],
        "limitations": ["The response is advisory and may be incomplete."],
    }


def _response_for(request: AIExplanationRequest) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_test_123",
        model="gpt-5.6-terra-test-snapshot",
        output_text=json.dumps(_payload_for(request)),
        output=[],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=40,
            total_tokens=140,
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
    )


class _FakeResponses:
    def __init__(self, *, response: object = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _provider_with_responses(responses: _FakeResponses) -> OpenAIExplanationProvider:
    client = SimpleNamespace(responses=responses)
    return OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        module_importer=lambda _name: _sdk_module(),
        dependency_path_activator=lambda: None,
        client_factory=lambda _credential, _configuration: client,
    )


def test_construction_is_lazy_and_does_not_read_key_or_load_sdk() -> None:
    calls: list[str] = []
    provider = OpenAIExplanationProvider(
        lambda: calls.append("credential") or _TEST_CREDENTIAL,
        module_importer=lambda name: calls.append(name) or _sdk_module(),
        dependency_path_activator=lambda: calls.append("activate"),
        client_factory=lambda _credential, _configuration: calls.append("client"),
    )

    assert provider.provider_identifier == OPENAI_PROVIDER_IDENTIFIER
    assert provider.display_name == "OpenAI"
    assert provider.configuration.model_identifier == DEFAULT_OPENAI_MODEL_IDENTIFIER
    assert calls == []
    assert _TEST_CREDENTIAL not in repr(provider)


def test_valid_response_translates_model_request_usage_and_warnings() -> None:
    request = _request(2)
    responses = _FakeResponses(response=_response_for(request))

    result = _provider_with_responses(responses).explain(request)

    assert result.provider_identifier == "openai"
    assert result.provider_display_name == "OpenAI"
    assert result.request_identifier == "resp_test_123"
    assert result.actual_model_identifier == "gpt-5.6-terra-test-snapshot"
    assert result.usage is not None
    assert result.usage.to_dict() == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
        "reasoning_tokens": 10,
    }
    assert [item.warning_id for item in result.warning_explanations] == [
        "WARN-001",
        "WARN-002",
    ]


def test_request_translation_is_deterministic_minimal_and_non_storing() -> None:
    request = _request(2)
    responses = _FakeResponses(response=_response_for(request))
    provider = _provider_with_responses(responses)

    provider.explain(request)
    provider.explain(request)

    first, second = responses.calls
    assert first == second
    assert first["model"] == DEFAULT_OPENAI_MODEL_IDENTIFIER
    assert first["instructions"] == request.system_instruction
    assert first["input"] == request.user_prompt
    assert first["store"] is False
    assert first["background"] is False
    assert first["stream"] is False
    assert first["tools"] == []
    assert first["tool_choice"] == "none"
    assert first["parallel_tool_calls"] is False
    assert first["reasoning"] == {"effort": "low"}
    assert first["text"]["verbosity"] == "medium"
    assert first["text"]["format"] == {
        "type": "json_schema",
        "name": "model_health_explanation",
        "strict": True,
        "schema": OPENAI_EXPLANATION_RESPONSE_SCHEMA,
    }
    assert set(first["metadata"]) == {
        "application",
        "purpose",
        "request_schema_version",
        "response_schema_version",
        "prompt_template_version",
    }
    assert {
        "safety_identifier",
        "user",
        "conversation",
        "previous_response_id",
        "include",
    }.isdisjoint(first)
    assert _TEST_CREDENTIAL not in json.dumps(first, sort_keys=True)


@pytest.mark.parametrize("warning_count", [1, 3])
def test_single_warning_and_full_analysis_contexts_are_supported(
    warning_count: int,
) -> None:
    request = _request(warning_count)
    responses = _FakeResponses(response=_response_for(request))

    result = _provider_with_responses(responses).explain(request)

    assert len(result.warning_explanations) == warning_count
    for warning in request.context.warnings:
        assert warning.warning_id in str(responses.calls[0]["input"])


def test_refusal_becomes_controlled_error_without_refusal_text() -> None:
    request = _request()
    response = _response_for(request)
    response.output = [
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="refusal",
                    refusal="RAW REFUSAL CONTENT MUST NOT ESCAPE",
                )
            ]
        )
    ]

    with pytest.raises(AIProviderRefusalError) as captured:
        _provider_with_responses(_FakeResponses(response=response)).explain(request)

    assert captured.value.request_identifier == "resp_test_123"
    assert "RAW REFUSAL" not in str(captured.value)


@pytest.mark.parametrize(
    ("sdk_error", "expected_error"),
    [
        (_AuthenticationError, AIAuthenticationError),
        (_PermissionDeniedError, AIPermissionError),
        (_RateLimitError, AIRateLimitError),
        (_APITimeoutError, AIRequestTimeoutError),
        (_APIConnectionError, AIConnectionError),
        (_BadRequestError, AIInvalidRequestError),
        (_UnprocessableEntityError, AIInvalidRequestError),
        (_NotFoundError, AIUnsupportedModelError),
        (_InternalServerError, AIProviderServerError),
    ],
)
def test_sdk_errors_map_to_provider_neutral_errors(
    sdk_error: type[Exception],
    expected_error: type[Exception],
) -> None:
    responses = _FakeResponses(error=sdk_error(_TEST_CREDENTIAL))

    with pytest.raises(expected_error) as captured:
        _provider_with_responses(responses).explain(_request())

    rendered_traceback = "".join(
        traceback.format_exception(captured.value)
    )
    assert _TEST_CREDENTIAL not in str(captured.value)
    assert _TEST_CREDENTIAL not in rendered_traceback
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert len(responses.calls) == 1


def test_retry_policy_is_sdk_owned_bounded_and_excludes_auth_and_permission() -> None:
    assert OPENAI_RETRY_POLICY_OWNER == "openai-sdk"
    assert "authentication" in OPENAI_NON_RETRYABLE_FAILURES
    assert "permission" in OPENAI_NON_RETRYABLE_FAILURES
    assert "connection_error" in OPENAI_RETRYABLE_FAILURES
    assert "rate_limit" in OPENAI_RETRYABLE_FAILURES
    assert "provider_server_error" in OPENAI_RETRYABLE_FAILURES
    assert OpenAIProviderConfiguration(maximum_retries=0).maximum_retries == 0
    assert OpenAIProviderConfiguration(maximum_retries=5).maximum_retries == 5
    with pytest.raises(ValueError, match="maximum retries"):
        OpenAIProviderConfiguration(maximum_retries=6)


def test_configuration_rejects_empty_malformed_and_unsupported_models_exactly() -> None:
    with pytest.raises(AIInvalidRequestError, match="nonempty model"):
        OpenAIProviderConfiguration(model_identifier="")
    with pytest.raises(AIInvalidRequestError, match="malformed"):
        OpenAIProviderConfiguration(model_identifier="bad/model")
    with pytest.raises(AIUnsupportedModelError, match="user-selected-model"):
        OpenAIProviderConfiguration(model_identifier="user-selected-model")


def test_omitted_model_controls_use_registered_safe_defaults() -> None:
    configuration = OpenAIProviderConfiguration(
        model_identifier="gpt-5.6-luna",
        reasoning_effort=None,
        response_verbosity=None,
    )

    assert configuration.model_identifier == "gpt-5.6-luna"
    assert configuration.reasoning_effort == "low"
    assert configuration.response_verbosity == "medium"


def test_request_builder_omits_options_not_supported_by_capabilities() -> None:
    capabilities = replace(
        get_openai_model_capabilities("gpt-5.6-terra"),
        reasoning_efforts=frozenset(),
        default_reasoning_effort=None,
        response_verbosities=frozenset(),
        default_response_verbosity=None,
        output_token_limit_parameter=None,
        maximum_output_tokens=None,
        supports_request_metadata=False,
    )
    resolved = resolve_model_options(
        capabilities,
        reasoning_effort=None,
        response_verbosity=None,
        maximum_output_tokens=1_200,
    )
    configuration = OpenAIProviderConfiguration()
    object.__setattr__(configuration, "resolved_options", resolved)
    provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        configuration=configuration,
    )

    arguments = provider._request_arguments(_request())

    assert "reasoning" not in arguments
    assert "metadata" not in arguments
    assert "max_output_tokens" not in arguments
    assert set(arguments["text"]) == {"format"}


def test_request_builder_uses_capability_specific_output_limit_parameter() -> None:
    capabilities = replace(
        get_openai_model_capabilities("gpt-5.6-terra"),
        output_token_limit_parameter="max_completion_tokens",
    )
    resolved = resolve_model_options(
        capabilities,
        reasoning_effort="low",
        response_verbosity="medium",
        maximum_output_tokens=777,
    )
    configuration = OpenAIProviderConfiguration()
    object.__setattr__(configuration, "resolved_options", resolved)
    provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        configuration=configuration,
    )

    arguments = provider._request_arguments(_request())

    assert arguments["max_completion_tokens"] == 777
    assert "max_output_tokens" not in arguments


def test_connection_builder_also_omits_unsupported_optional_controls() -> None:
    capabilities = replace(
        get_openai_model_capabilities("gpt-5.6-terra"),
        reasoning_efforts=frozenset(),
        default_reasoning_effort=None,
        response_verbosities=frozenset(),
        default_response_verbosity=None,
        output_token_limit_parameter=None,
        maximum_output_tokens=None,
        supports_request_metadata=False,
    )
    resolved = resolve_model_options(
        capabilities,
        reasoning_effort=None,
        response_verbosity=None,
        maximum_output_tokens=1_200,
    )
    configuration = OpenAIProviderConfiguration()
    object.__setattr__(configuration, "resolved_options", resolved)
    provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        configuration=configuration,
    )

    arguments = provider._connection_request_arguments()

    assert "reasoning" not in arguments
    assert "metadata" not in arguments
    assert "max_output_tokens" not in arguments
    assert set(arguments["text"]) == {"format"}


def test_malformed_json_is_rejected_without_echoing_raw_output() -> None:
    response = _response_for(_request())
    response.output_text = "{RAW PRIVATE PROVIDER OUTPUT"

    with pytest.raises(AIInvalidResponseError) as captured:
        _provider_with_responses(_FakeResponses(response=response)).explain(_request())

    assert "RAW PRIVATE" not in str(captured.value)
    assert captured.value.__context__ is None


def test_unknown_warning_identity_is_rejected() -> None:
    request = _request()
    payload = _payload_for(request)
    payload["warning_explanations"][0]["warning_id"] = "UNKNOWN"
    response = _response_for(request)
    response.output_text = json.dumps(payload)

    with pytest.raises(AIInvalidResponseError, match="unknown warning"):
        _provider_with_responses(_FakeResponses(response=response)).explain(request)


@pytest.mark.parametrize("credential", ["", "   ", None])
def test_missing_or_invalid_credential_fails_before_sdk_or_client(
    credential: object,
) -> None:
    calls: list[str] = []
    provider = OpenAIExplanationProvider(
        lambda: credential,
        module_importer=lambda name: calls.append(name) or _sdk_module(),
        dependency_path_activator=lambda: calls.append("activate"),
    )

    with pytest.raises(AIMissingCredentialError):
        provider.explain(_request())

    assert calls == []


def test_credential_backend_and_dependency_failures_remain_distinct() -> None:
    from model_health_assistant.credentials import (
        CredentialBackendUnavailableError,
    )

    backend_provider = OpenAIExplanationProvider(
        lambda: (_ for _ in ()).throw(CredentialBackendUnavailableError("private")),
        module_importer=lambda _name: _sdk_module(),
        dependency_path_activator=lambda: None,
    )
    with pytest.raises(AICredentialBackendUnavailableError):
        backend_provider.explain(_request())

    dependency_provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        module_importer=lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)),
        dependency_path_activator=lambda: None,
    )
    with pytest.raises(AIDependencyMissingError):
        dependency_provider.explain(_request())


def test_unsupported_sdk_and_missing_sdk_are_controlled() -> None:
    unsupported = ModuleType("openai")
    unsupported.OpenAI = None
    provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        module_importer=lambda _name: unsupported,
        dependency_path_activator=lambda: None,
    )

    assert provider.is_available() is False
    with pytest.raises(AIUnsupportedSDKError):
        provider.explain(_request())

    missing_provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        module_importer=lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)),
        dependency_path_activator=lambda: None,
    )
    with pytest.raises(AIProviderUnavailableError) as captured:
        missing_provider.explain(_request())
    assert captured.value.__context__ is None


def test_default_client_uses_separate_timeouts_retries_and_ephemeral_key() -> None:
    request = _request()
    responses = _FakeResponses(response=_response_for(request))
    client = SimpleNamespace(responses=responses)
    captured: dict[str, object] = {}
    sdk = _sdk_module()

    def openai_client(**kwargs: object) -> object:
        captured["client"] = kwargs
        return client

    def timeout(overall: int, **kwargs: object) -> object:
        captured["timeout"] = {"overall": overall, **kwargs}
        return "fake-timeout"

    sdk.OpenAI = openai_client
    httpx = ModuleType("httpx")
    httpx.Timeout = timeout
    provider = OpenAIExplanationProvider(
        lambda: _TEST_CREDENTIAL,
        module_importer=lambda name: sdk if name == "openai" else httpx,
        dependency_path_activator=lambda: None,
    )

    provider.explain(request)

    assert captured["timeout"] == {"overall": 60, "connect": 10, "read": 45}
    assert captured["client"] == {
        "api_key": _TEST_CREDENTIAL,
        "max_retries": 2,
        "timeout": "fake-timeout",
    }
    assert _TEST_CREDENTIAL not in json.dumps(responses.calls[0])


def test_adapter_source_has_no_eager_sdk_freecad_network_or_ui_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "openai_provider.py"
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
            "httpx",
            "openai",
            "requests",
            "socket",
            "urllib",
        }
    )


def test_absolute_report_path_never_reaches_provider_arguments() -> None:
    request = _request()
    request.context.document_display_name = "Active FreeCAD document"
    responses = _FakeResponses(response=_response_for(request))

    _provider_with_responses(responses).explain(request)

    serialized_arguments = json.dumps(responses.calls[0], sort_keys=True)
    assert "C:\\\\private\\\\model.FCStd" not in serialized_arguments
    assert "/home/private/model.FCStd" not in serialized_arguments


def test_explicit_connection_test_uses_minimal_non_cad_structured_request() -> None:
    response = SimpleNamespace(
        id="resp_connection_123",
        model="gpt-5.6-terra-connection-snapshot",
        output_text=json.dumps({"schema_version": "1.0", "status": "ok"}),
        output=[],
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=5,
            total_tokens=17,
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )
    responses = _FakeResponses(response=response)

    result = _provider_with_responses(responses).test_connection()

    assert result.provider_identifier == "openai"
    assert result.actual_model_identifier == "gpt-5.6-terra-connection-snapshot"
    assert result.request_identifier == "resp_connection_123"
    assert result.usage is not None
    assert result.usage.total_tokens == 17
    arguments = responses.calls[0]
    assert arguments["input"] == "Return status ok using the required schema."
    assert arguments["max_output_tokens"] == 64
    assert arguments["store"] is False
    assert arguments["tools"] == []
    assert arguments["text"]["format"]["schema"] == (
        OPENAI_CONNECTION_RESPONSE_SCHEMA
    )
    serialized = json.dumps(arguments, sort_keys=True).casefold()
    for forbidden in (
        ".fcstd",
        "document_name",
        "feature001",
        "health_score",
        "model-health report",
        _TEST_CREDENTIAL.casefold(),
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "output_text",
    [
        "not-json",
        json.dumps({"schema_version": "1.0", "status": "wrong"}),
        json.dumps({"schema_version": "1.0", "status": "ok", "extra": True}),
    ],
)
def test_connection_test_rejects_invalid_structured_output(output_text: str) -> None:
    response = SimpleNamespace(
        id="resp_invalid_connection",
        model="gpt-5.6-terra",
        output_text=output_text,
        output=[],
        usage=None,
    )

    with pytest.raises(AIInvalidResponseError):
        _provider_with_responses(_FakeResponses(response=response)).test_connection()
