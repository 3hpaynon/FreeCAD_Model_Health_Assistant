"""Static, network-free provider/model capability validation.

The registry is intentionally explicit and local.  Importing or querying it
never creates a provider client, reads a credential, or fetches a model list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from model_health_assistant.ai_provider import (
    AIInvalidRequestError,
    AIUnsupportedModelError,
)

OPENAI_PROVIDER_IDENTIFIER = "openai"
_MODEL_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class AIModelCapabilities:
    """Provider options known to be accepted by one exact model identifier."""

    provider_identifier: str
    model_identifier: str
    supports_responses_api: bool
    supports_structured_outputs: bool
    reasoning_efforts: frozenset[str]
    default_reasoning_effort: str | None
    response_verbosities: frozenset[str]
    default_response_verbosity: str | None
    output_token_limit_parameter: str | None
    maximum_output_tokens: int | None
    supports_request_metadata: bool

    def __post_init__(self) -> None:
        if self.provider_identifier != OPENAI_PROVIDER_IDENTIFIER:
            raise AIInvalidRequestError("The provider identifier is unsupported.")
        if (
            not isinstance(self.model_identifier, str)
            or not _MODEL_IDENTIFIER_PATTERN.fullmatch(self.model_identifier)
        ):
            raise AIInvalidRequestError("The model identifier is malformed.")
        for name in (
            "supports_responses_api",
            "supports_structured_outputs",
            "supports_request_metadata",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if (
            self.default_reasoning_effort is not None
            and self.default_reasoning_effort not in self.reasoning_efforts
        ):
            raise ValueError("Default reasoning effort is not supported.")
        if (
            self.default_response_verbosity is not None
            and self.default_response_verbosity not in self.response_verbosities
        ):
            raise ValueError("Default response verbosity is not supported.")
        if self.output_token_limit_parameter is not None and (
            not isinstance(self.output_token_limit_parameter, str)
            or not self.output_token_limit_parameter.strip()
        ):
            raise ValueError("Output-token parameter must be nonempty text or None.")
        if self.maximum_output_tokens is not None and (
            type(self.maximum_output_tokens) is not int
            or self.maximum_output_tokens < 1
        ):
            raise ValueError("Maximum output tokens must be positive or None.")


@dataclass(frozen=True, slots=True)
class AIResolvedModelOptions:
    """Validated provider options; ``None`` means the option must be omitted."""

    capabilities: AIModelCapabilities
    reasoning_effort: str | None
    response_verbosity: str | None
    output_token_limit_parameter: str | None
    maximum_output_tokens: int | None


_COMMON_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
_COMMON_RESPONSE_VERBOSITIES = frozenset({"low", "medium", "high"})


def _gpt_5_6_capabilities(model_identifier: str) -> AIModelCapabilities:
    return AIModelCapabilities(
        provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        model_identifier=model_identifier,
        supports_responses_api=True,
        supports_structured_outputs=True,
        reasoning_efforts=_COMMON_REASONING_EFFORTS,
        default_reasoning_effort="low",
        response_verbosities=_COMMON_RESPONSE_VERBOSITIES,
        default_response_verbosity="medium",
        output_token_limit_parameter="max_output_tokens",
        maximum_output_tokens=128_000,
        supports_request_metadata=True,
    )


_OPENAI_MODEL_CAPABILITIES = {
    model_identifier: _gpt_5_6_capabilities(model_identifier)
    for model_identifier in (
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    )
}
OPENAI_MODEL_CAPABILITIES: Mapping[str, AIModelCapabilities] = MappingProxyType(
    _OPENAI_MODEL_CAPABILITIES
)
SUPPORTED_OPENAI_MODEL_IDENTIFIERS = tuple(_OPENAI_MODEL_CAPABILITIES)


def validate_provider_identifier(provider_identifier: object) -> str:
    """Require the exact provider identifier represented by this registry."""

    if not isinstance(provider_identifier, str) or not provider_identifier:
        raise AIInvalidRequestError("A nonempty provider identifier is required.")
    if provider_identifier != OPENAI_PROVIDER_IDENTIFIER:
        raise AIInvalidRequestError("The provider identifier is unsupported.")
    return provider_identifier


def validate_model_identifier_syntax(model_identifier: object) -> str:
    """Reject empty or clearly malformed model identifiers without rewriting."""

    if not isinstance(model_identifier, str):
        raise AIInvalidRequestError("A model identifier must be text.")
    if not model_identifier:
        raise AIInvalidRequestError("A nonempty model identifier is required.")
    if not _MODEL_IDENTIFIER_PATTERN.fullmatch(model_identifier):
        raise AIInvalidRequestError("The model identifier is malformed.")
    return model_identifier


def get_openai_model_capabilities(
    model_identifier: object,
) -> AIModelCapabilities:
    """Return static capabilities or raise a useful unsupported-model error."""

    validated = validate_model_identifier_syntax(model_identifier)
    capabilities = OPENAI_MODEL_CAPABILITIES.get(validated)
    if capabilities is None:
        raise AIUnsupportedModelError(
            f"The configured OpenAI model '{validated}' is unsupported."
        )
    return capabilities


def resolve_model_options(
    capabilities: AIModelCapabilities,
    *,
    reasoning_effort: str | None,
    response_verbosity: str | None,
    maximum_output_tokens: int | None,
    require_structured_outputs: bool = True,
) -> AIResolvedModelOptions:
    """Validate requested controls and apply defaults only when omitted."""

    if not isinstance(capabilities, AIModelCapabilities):
        raise TypeError("capabilities must be AIModelCapabilities")
    if not capabilities.supports_responses_api:
        raise AIUnsupportedModelError(
            "The configured model does not support the required Responses API."
        )
    if require_structured_outputs and not capabilities.supports_structured_outputs:
        raise AIUnsupportedModelError(
            "The configured model does not support required structured output."
        )

    resolved_reasoning = _resolve_text_option(
        reasoning_effort,
        capabilities.reasoning_efforts,
        capabilities.default_reasoning_effort,
        "reasoning effort",
    )
    resolved_verbosity = _resolve_text_option(
        response_verbosity,
        capabilities.response_verbosities,
        capabilities.default_response_verbosity,
        "response verbosity",
    )

    if maximum_output_tokens is not None and (
        type(maximum_output_tokens) is not int or maximum_output_tokens < 1
    ):
        raise AIInvalidRequestError("Maximum output tokens must be positive.")
    output_parameter = capabilities.output_token_limit_parameter
    if output_parameter is None:
        resolved_maximum_output = None
    else:
        resolved_maximum_output = maximum_output_tokens
        if (
            resolved_maximum_output is not None
            and capabilities.maximum_output_tokens is not None
            and resolved_maximum_output > capabilities.maximum_output_tokens
        ):
            raise AIInvalidRequestError(
                "Maximum output tokens exceed the configured model capability."
            )

    return AIResolvedModelOptions(
        capabilities=capabilities,
        reasoning_effort=resolved_reasoning,
        response_verbosity=resolved_verbosity,
        output_token_limit_parameter=output_parameter,
        maximum_output_tokens=resolved_maximum_output,
    )


def resolve_openai_model_options(
    model_identifier: object,
    *,
    reasoning_effort: str | None,
    response_verbosity: str | None,
    maximum_output_tokens: int | None,
    require_structured_outputs: bool = True,
) -> AIResolvedModelOptions:
    """Resolve one exact registered OpenAI model without any network lookup."""

    return resolve_model_options(
        get_openai_model_capabilities(model_identifier),
        reasoning_effort=reasoning_effort,
        response_verbosity=response_verbosity,
        maximum_output_tokens=maximum_output_tokens,
        require_structured_outputs=require_structured_outputs,
    )


def _resolve_text_option(
    requested: str | None,
    supported: frozenset[str],
    default: str | None,
    name: str,
) -> str | None:
    if requested is None:
        return default
    if not isinstance(requested, str) or requested not in supported:
        raise AIInvalidRequestError(f"Unsupported {name} for the configured model.")
    return requested


__all__ = [
    "AIModelCapabilities",
    "AIResolvedModelOptions",
    "OPENAI_MODEL_CAPABILITIES",
    "OPENAI_PROVIDER_IDENTIFIER",
    "SUPPORTED_OPENAI_MODEL_IDENTIFIERS",
    "get_openai_model_capabilities",
    "resolve_model_options",
    "resolve_openai_model_options",
    "validate_model_identifier_syntax",
    "validate_provider_identifier",
]
