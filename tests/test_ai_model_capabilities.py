import ast
from dataclasses import replace
from pathlib import Path

import pytest

from model_health_assistant.ai_model_capabilities import (
    AIModelCapabilities,
    OPENAI_MODEL_CAPABILITIES,
    SUPPORTED_OPENAI_MODEL_IDENTIFIERS,
    get_openai_model_capabilities,
    resolve_model_options,
    resolve_openai_model_options,
    validate_model_identifier_syntax,
    validate_provider_identifier,
)
from model_health_assistant.ai_provider import (
    AIInvalidRequestError,
    AIUnsupportedModelError,
)


@pytest.mark.parametrize("model_identifier", SUPPORTED_OPENAI_MODEL_IDENTIFIERS)
def test_supported_openai_configuration_resolves_exact_static_capabilities(
    model_identifier: str,
) -> None:
    resolved = resolve_openai_model_options(
        model_identifier,
        reasoning_effort="max",
        response_verbosity="high",
        maximum_output_tokens=1_200,
    )

    assert resolved.capabilities.model_identifier == model_identifier
    assert resolved.capabilities.supports_responses_api is True
    assert resolved.capabilities.supports_structured_outputs is True
    assert resolved.reasoning_effort == "max"
    assert resolved.response_verbosity == "high"
    assert resolved.output_token_limit_parameter == "max_output_tokens"
    assert resolved.maximum_output_tokens == 1_200


@pytest.mark.parametrize("model_identifier", ["", " ", "bad/model", "../model"])
def test_empty_or_malformed_model_is_rejected_before_registry_lookup(
    model_identifier: str,
) -> None:
    with pytest.raises(AIInvalidRequestError):
        validate_model_identifier_syntax(model_identifier)


def test_well_formed_unsupported_model_is_not_silently_switched() -> None:
    configured = "gpt-5.7-user-choice"

    with pytest.raises(AIUnsupportedModelError, match=configured):
        get_openai_model_capabilities(configured)

    assert configured not in SUPPORTED_OPENAI_MODEL_IDENTIFIERS


@pytest.mark.parametrize("provider_identifier", ["", "OpenAI", "other"])
def test_provider_identifier_is_exactly_validated(provider_identifier: str) -> None:
    with pytest.raises(AIInvalidRequestError):
        validate_provider_identifier(provider_identifier)
    assert validate_provider_identifier("openai") == "openai"


def test_structured_output_unavailable_fails_closed() -> None:
    capabilities = replace(
        get_openai_model_capabilities("gpt-5.6-terra"),
        supports_structured_outputs=False,
    )

    with pytest.raises(AIUnsupportedModelError, match="structured output"):
        resolve_model_options(
            capabilities,
            reasoning_effort=None,
            response_verbosity=None,
            maximum_output_tokens=1_200,
        )


def test_provider_capability_mismatch_for_responses_api_fails_closed() -> None:
    capabilities = replace(
        get_openai_model_capabilities("gpt-5.6-terra"),
        supports_responses_api=False,
    )

    with pytest.raises(AIUnsupportedModelError, match="Responses API"):
        resolve_model_options(
            capabilities,
            reasoning_effort=None,
            response_verbosity=None,
            maximum_output_tokens=1_200,
        )


def test_invalid_explicit_reasoning_or_verbosity_never_falls_back() -> None:
    capabilities = get_openai_model_capabilities("gpt-5.6-terra")

    with pytest.raises(AIInvalidRequestError, match="reasoning effort"):
        resolve_model_options(
            capabilities,
            reasoning_effort="minimal",
            response_verbosity=None,
            maximum_output_tokens=1_200,
        )
    with pytest.raises(AIInvalidRequestError, match="response verbosity"):
        resolve_model_options(
            capabilities,
            reasoning_effort=None,
            response_verbosity="very-high",
            maximum_output_tokens=1_200,
        )


def test_omitted_options_use_declared_safe_defaults_only() -> None:
    resolved = resolve_openai_model_options(
        "gpt-5.6-luna",
        reasoning_effort=None,
        response_verbosity=None,
        maximum_output_tokens=1_200,
    )

    assert resolved.reasoning_effort == "low"
    assert resolved.response_verbosity == "medium"


def test_unsupported_optional_controls_resolve_to_omission() -> None:
    capabilities = AIModelCapabilities(
        provider_identifier="openai",
        model_identifier="custom-no-controls",
        supports_responses_api=True,
        supports_structured_outputs=True,
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

    assert resolved.reasoning_effort is None
    assert resolved.response_verbosity is None
    assert resolved.output_token_limit_parameter is None
    assert resolved.maximum_output_tokens is None


def test_registry_is_immutable_and_lookup_has_no_network_side_effect() -> None:
    with pytest.raises(TypeError):
        OPENAI_MODEL_CAPABILITIES["new-model"] = get_openai_model_capabilities(
            "gpt-5.6-terra"
        )

    before = tuple(OPENAI_MODEL_CAPABILITIES)
    for model_identifier in before:
        get_openai_model_capabilities(model_identifier)
    assert tuple(OPENAI_MODEL_CAPABILITIES) == before


def test_capability_registry_source_has_no_sdk_or_network_imports() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "ai_model_capabilities.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])

    assert roots.isdisjoint({"openai", "httpx", "requests", "socket", "urllib"})
