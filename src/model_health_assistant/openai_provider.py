"""Lazy OpenAI Responses API adapter for provider-neutral explanations."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from model_health_assistant.ai_model_capabilities import (
    AIResolvedModelOptions,
    SUPPORTED_OPENAI_MODEL_IDENTIFIERS,
    resolve_openai_model_options,
)
from model_health_assistant.ai_connection import (
    AI_CONNECTION_SCHEMA_VERSION,
    AIConnectionTestResult,
)
from model_health_assistant.ai_models import (
    AI_RESPONSE_SCHEMA_VERSION,
    AIExplanationRequest,
    AIExplanationResponse,
    AIUsageMetadata,
    AIWarningExplanation,
)
from model_health_assistant.ai_provider import (
    AIAuthenticationError,
    AIConnectionError,
    AICredentialBackendUnavailableError,
    AIDependencyMissingError,
    AIInvalidRequestError,
    AIInvalidResponseError,
    AIMissingCredentialError,
    AIPermissionError,
    AIProviderError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIRateLimitError,
    AIRequestTimeoutError,
    AIUnsupportedSDKError,
    AIUnsupportedModelError,
    AIUnknownProviderError,
)
from model_health_assistant.ai_prompts import serialize_grounded_question
from model_health_assistant.ai_service import validate_ai_explanation_response
from model_health_assistant.credentials import (
    CredentialBackendUnavailableError,
    CredentialNotConfiguredError,
    CredentialStorageError,
)
from model_health_assistant.runtime_dependencies import (
    activate_optional_dependency_directory,
)

OPENAI_PROVIDER_IDENTIFIER = "openai"
OPENAI_PROVIDER_DISPLAY_NAME = "OpenAI"
DEFAULT_OPENAI_MODEL_IDENTIFIER = "gpt-5.6-terra"
DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_OPENAI_READ_TIMEOUT_SECONDS = 45
DEFAULT_OPENAI_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_OPENAI_MAXIMUM_RETRIES = 2
DEFAULT_OPENAI_MAXIMUM_OUTPUT_TOKENS = 1200
DEFAULT_OPENAI_REASONING_EFFORT = "low"
DEFAULT_OPENAI_RESPONSE_VERBOSITY = "medium"
OPENAI_RETRY_POLICY_OWNER = "openai-sdk"
OPENAI_RETRYABLE_FAILURES = (
    "connection_error",
    "request_timeout",
    "http_408",
    "http_409",
    "rate_limit",
    "provider_server_error",
)
OPENAI_NON_RETRYABLE_FAILURES = (
    "authentication",
    "permission",
    "invalid_request",
    "unsupported_model",
    "provider_refusal",
    "malformed_response",
)

OPENAI_EXPLANATION_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": [AI_RESPONSE_SCHEMA_VERSION],
        },
        "overall_summary": {"type": "string", "minLength": 1},
        "warning_explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "warning_id": {"type": "string", "minLength": 1},
                    "rule_id": {"type": "string", "minLength": 1},
                    "affected_feature_internal_name": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "severity": {"type": "string", "minLength": 1},
                    "explanation": {"type": "string", "minLength": 1},
                    "why_it_matters": {"type": "string", "minLength": 1},
                    "manual_review_steps": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "confidence": {"type": "string"},
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "required": [
                    "warning_id",
                    "rule_id",
                    "affected_feature_internal_name",
                    "severity",
                    "explanation",
                    "why_it_matters",
                    "manual_review_steps",
                    "confidence",
                    "limitations",
                ],
            },
        },
        "limitations": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "schema_version",
        "overall_summary",
        "warning_explanations",
        "limitations",
    ],
}

OPENAI_CONNECTION_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": [AI_CONNECTION_SCHEMA_VERSION],
        },
        "status": {"type": "string", "enum": ["ok"]},
    },
    "required": ["schema_version", "status"],
}

ModuleImporter = Callable[[str], ModuleType]
DependencyPathActivator = Callable[[], object]
CredentialProvider = Callable[[], str]
ClientFactory = Callable[[str, "OpenAIProviderConfiguration"], object]


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfiguration:
    """Validated request defaults selected during the Step 6 audit."""

    model_identifier: str = DEFAULT_OPENAI_MODEL_IDENTIFIER
    connect_timeout_seconds: int = DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: int = DEFAULT_OPENAI_READ_TIMEOUT_SECONDS
    request_timeout_seconds: int = DEFAULT_OPENAI_REQUEST_TIMEOUT_SECONDS
    maximum_retries: int = DEFAULT_OPENAI_MAXIMUM_RETRIES
    maximum_output_tokens: int = DEFAULT_OPENAI_MAXIMUM_OUTPUT_TOKENS
    reasoning_effort: str | None = None
    response_verbosity: str | None = None
    resolved_options: AIResolvedModelOptions = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name, value, minimum, maximum in (
            ("connect timeout", self.connect_timeout_seconds, 1, 60),
            ("read timeout", self.read_timeout_seconds, 1, 300),
            ("request timeout", self.request_timeout_seconds, 1, 300),
            ("maximum retries", self.maximum_retries, 0, 5),
            ("maximum output tokens", self.maximum_output_tokens, 64, 32_000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"Invalid {name}.")
        resolved = resolve_openai_model_options(
            self.model_identifier,
            reasoning_effort=self.reasoning_effort,
            response_verbosity=self.response_verbosity,
            maximum_output_tokens=self.maximum_output_tokens,
            require_structured_outputs=True,
        )
        object.__setattr__(self, "resolved_options", resolved)
        object.__setattr__(self, "reasoning_effort", resolved.reasoning_effort)
        object.__setattr__(self, "response_verbosity", resolved.response_verbosity)


class OpenAIExplanationProvider:
    """Translate one privacy-safe explanation request through OpenAI."""

    def __init__(
        self,
        credential_provider: CredentialProvider,
        *,
        configuration: OpenAIProviderConfiguration | None = None,
        module_importer: ModuleImporter = importlib.import_module,
        dependency_path_activator: DependencyPathActivator = (
            activate_optional_dependency_directory
        ),
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not callable(credential_provider):
            raise TypeError("credential_provider must be callable")
        self._credential_provider = credential_provider
        self._configuration = configuration or OpenAIProviderConfiguration()
        self._module_importer = module_importer
        self._dependency_path_activator = dependency_path_activator
        self._client_factory = client_factory

    @property
    def provider_identifier(self) -> str:
        return OPENAI_PROVIDER_IDENTIFIER

    @property
    def display_name(self) -> str:
        return OPENAI_PROVIDER_DISPLAY_NAME

    @property
    def configuration(self) -> OpenAIProviderConfiguration:
        return self._configuration

    def is_available(self) -> bool:
        """Check the SDK surface without retrieving a credential or creating a client."""

        try:
            sdk_module = self._load_sdk_module()
        except AIProviderError:
            return False
        return callable(getattr(sdk_module, "OpenAI", None))

    def capability_description(self) -> str:
        return (
            "Explains privacy-reviewed model-health context with the OpenAI "
            "Responses API and strict structured output."
        )

    def explain(self, request: AIExplanationRequest) -> AIExplanationResponse:
        """Execute and validate one structured request with a short-lived client."""

        if not isinstance(request, AIExplanationRequest):
            raise TypeError("request must be an AIExplanationRequest")

        credential = self._retrieve_credential()
        client = None
        sdk_module = None
        sdk_response = None
        provider_response = None
        controlled_error: AIProviderError | None = None
        try:
            sdk_module = self._load_sdk_module()
            client = self._create_client(sdk_module, credential)
            create_response = _response_create_method(client)
            sdk_response = create_response(**self._request_arguments(request))
            provider_response = self._translate_response(request, sdk_response)
        except AIProviderError as error:
            controlled_error = error
        except Exception as error:
            controlled_error = _map_sdk_error(error, sdk_module)
        finally:
            credential = ""
            client = None
            sdk_response = None

        if controlled_error is not None:
            raise controlled_error
        if provider_response is None:
            raise AIInvalidResponseError(
                "OpenAI returned no provider-neutral explanation response."
            )
        return provider_response

    def test_connection(self) -> AIConnectionTestResult:
        """Run one explicit minimal request containing no CAD or report data."""

        result = self._execute_with_client(self._run_connection_request)
        if not isinstance(result, AIConnectionTestResult):
            raise AIInvalidResponseError(
                "OpenAI returned no validated connection-test result."
            )
        return result

    def _run_connection_request(self, client: object) -> AIConnectionTestResult:
        create_response = _response_create_method(client)
        sdk_response = create_response(**self._connection_request_arguments())
        request_identifier = _optional_text(getattr(sdk_response, "id", None))
        if _response_contains_refusal(sdk_response):
            raise AIProviderRefusalError(
                "OpenAI declined the minimal connection test.",
                request_identifier=request_identifier,
            )
        payload = _structured_payload(
            getattr(sdk_response, "output_text", None),
            "connection test",
        )
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema_version",
            "status",
        }:
            raise AIInvalidResponseError(
                "OpenAI returned an invalid connection-test response."
            )
        if (
            payload.get("schema_version") != AI_CONNECTION_SCHEMA_VERSION
            or payload.get("status") != "ok"
        ):
            raise AIInvalidResponseError(
                "OpenAI returned an unsupported connection-test response."
            )
        return AIConnectionTestResult(
            provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
            provider_display_name=OPENAI_PROVIDER_DISPLAY_NAME,
            actual_model_identifier=_optional_text(
                getattr(sdk_response, "model", None)
            ),
            request_identifier=request_identifier,
            usage=_usage_metadata(getattr(sdk_response, "usage", None)),
        )

    def _execute_with_client(
        self,
        operation: Callable[[object], object],
    ) -> object:
        """Execute one SDK operation while containing credentials and errors."""

        credential = self._retrieve_credential()
        client = None
        sdk_module = None
        result = None
        controlled_error: AIProviderError | None = None
        try:
            sdk_module = self._load_sdk_module()
            client = self._create_client(sdk_module, credential)
            result = operation(client)
        except AIProviderError as error:
            controlled_error = error
        except Exception as error:
            controlled_error = _map_sdk_error(error, sdk_module)
        finally:
            credential = ""
            client = None

        if controlled_error is not None:
            raise controlled_error
        return result

    def _connection_request_arguments(self) -> dict[str, object]:
        options = self._configuration.resolved_options
        arguments: dict[str, object] = {
            "model": self._configuration.model_identifier,
            "instructions": (
                "Return only the requested connection-test JSON. This request "
                "contains no CAD, document, feature, or model-health data."
            ),
            "input": "Return status ok using the required schema.",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "provider_connection_test",
                    "strict": True,
                    "schema": deepcopy(OPENAI_CONNECTION_RESPONSE_SCHEMA),
                },
            },
            "store": False,
            "background": False,
            "stream": False,
            "tools": [],
            "tool_choice": "none",
            "parallel_tool_calls": False,
        }
        if options.output_token_limit_parameter is not None:
            arguments[options.output_token_limit_parameter] = 64
        if "none" in options.capabilities.reasoning_efforts:
            arguments["reasoning"] = {"effort": "none"}
        text_options = arguments["text"]
        if (
            isinstance(text_options, dict)
            and "low" in options.capabilities.response_verbosities
        ):
            text_options["verbosity"] = "low"
        if options.capabilities.supports_request_metadata:
            arguments["metadata"] = {
                "application": "freecad-model-health-assistant",
                "purpose": "provider_connection_test",
                "response_schema_version": AI_CONNECTION_SCHEMA_VERSION,
            }
        return arguments

    def _retrieve_credential(self) -> str:
        controlled_error: AIProviderError | None = None
        credential: object = None
        try:
            credential = self._credential_provider()
        except CredentialNotConfiguredError:
            controlled_error = AIMissingCredentialError(
                "No secure OpenAI credential is configured."
            )
        except (CredentialBackendUnavailableError, CredentialStorageError):
            controlled_error = AICredentialBackendUnavailableError(
                "Secure OpenAI credential retrieval is unavailable."
            )
        except Exception:
            controlled_error = AICredentialBackendUnavailableError(
                "Secure OpenAI credential retrieval failed safely."
            )
        if controlled_error is not None:
            raise controlled_error
        if (
            not isinstance(credential, str)
            or not credential.strip()
            or "\x00" in credential
        ):
            raise AIMissingCredentialError(
                "No secure OpenAI credential is configured."
            )
        return credential

    def _load_sdk_module(self) -> ModuleType:
        try:
            self._dependency_path_activator()
        except Exception:
            pass

        import_failed = False
        missing_sdk = False
        try:
            sdk_module = self._module_importer("openai")
        except ModuleNotFoundError:
            missing_sdk = True
            sdk_module = None
        except Exception:
            import_failed = True
            sdk_module = None
        if missing_sdk:
            raise AIDependencyMissingError(
                "The optional OpenAI SDK is not installed."
            )
        if import_failed or sdk_module is None:
            raise AIDependencyMissingError(
                "The optional OpenAI SDK could not be loaded safely."
            )
        if not callable(getattr(sdk_module, "OpenAI", None)):
            raise AIUnsupportedSDKError(
                "The installed OpenAI SDK does not provide the required client."
            )
        return sdk_module

    def _create_client(self, sdk_module: ModuleType, credential: str) -> object:
        if self._client_factory is not None:
            return self._client_factory(credential, self._configuration)

        httpx_module = self._module_importer("httpx")
        timeout = httpx_module.Timeout(
            self._configuration.request_timeout_seconds,
            connect=self._configuration.connect_timeout_seconds,
            read=self._configuration.read_timeout_seconds,
        )
        return sdk_module.OpenAI(
            api_key=credential,
            max_retries=self._configuration.maximum_retries,
            timeout=timeout,
        )

    def _request_arguments(self, request: AIExplanationRequest) -> dict[str, object]:
        options = self._configuration.resolved_options
        metadata = {
            "application": "freecad-model-health-assistant",
            "purpose": request.metadata.purpose,
            "request_schema_version": request.schema_version,
            "response_schema_version": AI_RESPONSE_SCHEMA_VERSION,
        }
        if request.metadata.prompt_template_version:
            metadata["prompt_template_version"] = (
                request.metadata.prompt_template_version
            )
        text_options: dict[str, object] = {
            "format": {
                "type": "json_schema",
                "name": "model_health_explanation",
                "strict": True,
                "schema": deepcopy(OPENAI_EXPLANATION_RESPONSE_SCHEMA),
            },
        }
        if options.response_verbosity is not None:
            text_options["verbosity"] = options.response_verbosity
        arguments: dict[str, object] = {
            "model": self._configuration.model_identifier,
            "instructions": request.system_instruction,
            "input": _provider_input(request),
            "text": text_options,
            "store": False,
            "background": False,
            "stream": False,
            "tools": [],
            "tool_choice": "none",
            "parallel_tool_calls": False,
        }
        if (
            options.output_token_limit_parameter is not None
            and options.maximum_output_tokens is not None
        ):
            arguments[options.output_token_limit_parameter] = (
                options.maximum_output_tokens
            )
        if options.reasoning_effort is not None:
            arguments["reasoning"] = {"effort": options.reasoning_effort}
        if options.capabilities.supports_request_metadata:
            arguments["metadata"] = metadata
        return arguments

    def _translate_response(
        self,
        request: AIExplanationRequest,
        sdk_response: object,
    ) -> AIExplanationResponse:
        request_identifier = _optional_text(getattr(sdk_response, "id", None))
        if _response_contains_refusal(sdk_response):
            raise AIProviderRefusalError(
                "OpenAI declined to produce the requested explanation.",
                request_identifier=request_identifier,
            )

        output_text = getattr(sdk_response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise AIInvalidResponseError(
                "OpenAI returned no structured explanation content."
            )
        malformed_payload = False
        try:
            payload = json.loads(output_text)
        except (TypeError, ValueError):
            malformed_payload = True
            payload = None
        if malformed_payload:
            raise AIInvalidResponseError(
                "OpenAI returned malformed structured explanation content."
            )
        response = _provider_response_from_payload(
            payload,
            request_identifier=request_identifier,
            actual_model_identifier=_optional_text(
                getattr(sdk_response, "model", None)
            ),
            usage=_usage_metadata(getattr(sdk_response, "usage", None)),
        )
        validate_ai_explanation_response(
            request,
            response,
            expected_provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        )
        return response


def _provider_input(request: AIExplanationRequest) -> object:
    """Keep a one-shot user question distinct from untrusted CAD context."""

    if request.user_question is None:
        return request.user_prompt
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": request.user_prompt,
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": serialize_grounded_question(request.user_question),
                }
            ],
        },
    ]


def _response_create_method(client: object) -> Callable[..., object]:
    responses = getattr(client, "responses", None)
    create = getattr(responses, "create", None)
    if not callable(create):
        raise AIUnsupportedSDKError(
            "The installed OpenAI SDK does not provide the Responses API."
        )
    return create


def _structured_payload(output_text: object, purpose: str) -> object:
    if not isinstance(output_text, str) or not output_text.strip():
        raise AIInvalidResponseError(
            f"OpenAI returned no structured {purpose} content."
        )
    malformed_payload = False
    try:
        payload = json.loads(output_text)
    except (TypeError, ValueError):
        malformed_payload = True
        payload = None
    if malformed_payload:
        raise AIInvalidResponseError(
            f"OpenAI returned malformed structured {purpose} content."
        )
    return payload


def _provider_response_from_payload(
    payload: object,
    *,
    request_identifier: str | None,
    actual_model_identifier: str | None,
    usage: AIUsageMetadata | None,
) -> AIExplanationResponse:
    if not isinstance(payload, Mapping):
        raise AIInvalidResponseError("OpenAI returned an invalid response object.")
    expected_keys = {
        "schema_version",
        "overall_summary",
        "warning_explanations",
        "limitations",
    }
    if set(payload) != expected_keys:
        raise AIInvalidResponseError("OpenAI returned an unexpected response shape.")
    if payload.get("schema_version") != AI_RESPONSE_SCHEMA_VERSION:
        raise AIInvalidResponseError("OpenAI returned an unsupported response schema.")

    warning_values = payload.get("warning_explanations")
    if not isinstance(warning_values, list):
        raise AIInvalidResponseError("OpenAI returned invalid warning explanations.")
    warnings = [_warning_explanation(value) for value in warning_values]
    return AIExplanationResponse(
        provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        provider_display_name=OPENAI_PROVIDER_DISPLAY_NAME,
        overall_summary=_required_text(payload.get("overall_summary")),
        warning_explanations=warnings,
        limitations=_required_text_list(payload.get("limitations"), allow_empty=False),
        request_identifier=request_identifier,
        actual_model_identifier=actual_model_identifier,
        usage=usage,
    )


def _warning_explanation(value: object) -> AIWarningExplanation:
    if not isinstance(value, Mapping):
        raise AIInvalidResponseError("OpenAI returned an invalid warning explanation.")
    expected_keys = {
        "warning_id",
        "rule_id",
        "affected_feature_internal_name",
        "severity",
        "explanation",
        "why_it_matters",
        "manual_review_steps",
        "confidence",
        "limitations",
    }
    if set(value) != expected_keys:
        raise AIInvalidResponseError("OpenAI returned an unexpected warning shape.")
    confidence_value = value.get("confidence")
    if not isinstance(confidence_value, str):
        raise AIInvalidResponseError("OpenAI returned invalid confidence metadata.")
    return AIWarningExplanation(
        warning_id=_required_text(value.get("warning_id")),
        rule_id=_required_text(value.get("rule_id")),
        affected_feature_internal_name=_required_text(
            value.get("affected_feature_internal_name")
        ),
        severity=_required_text(value.get("severity")),
        explanation=_required_text(value.get("explanation")),
        why_it_matters=_required_text(value.get("why_it_matters")),
        manual_review_steps=_required_text_list(
            value.get("manual_review_steps"),
            allow_empty=True,
        ),
        confidence=confidence_value.strip() or None,
        limitations=_required_text_list(value.get("limitations"), allow_empty=True),
    )


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AIInvalidResponseError("OpenAI returned an invalid text field.")
    return value.strip()


def _required_text_list(value: object, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise AIInvalidResponseError("OpenAI returned an invalid text list.")
    return [_required_text(item) for item in value]


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _usage_metadata(value: object) -> AIUsageMetadata | None:
    if value is None:
        return None
    details = getattr(value, "output_tokens_details", None)
    usage = AIUsageMetadata(
        input_tokens=_optional_nonnegative_integer(getattr(value, "input_tokens", None)),
        output_tokens=_optional_nonnegative_integer(
            getattr(value, "output_tokens", None)
        ),
        total_tokens=_optional_nonnegative_integer(getattr(value, "total_tokens", None)),
        reasoning_tokens=_optional_nonnegative_integer(
            getattr(details, "reasoning_tokens", None)
        ),
    )
    return usage


def _optional_nonnegative_integer(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise AIInvalidResponseError("OpenAI returned invalid usage metadata.")
    return value


def _response_contains_refusal(response: object) -> bool:
    output = getattr(response, "output", ())
    if not isinstance(output, (list, tuple)):
        return False
    for item in output:
        content = getattr(item, "content", ())
        if not isinstance(content, (list, tuple)):
            continue
        if any(getattr(part, "type", None) == "refusal" for part in content):
            return True
    return False


def _map_sdk_error(error: Exception, sdk_module: ModuleType | None) -> AIProviderError:
    mappings = (
        ("AuthenticationError", AIAuthenticationError, "OpenAI authentication failed."),
        ("PermissionDeniedError", AIPermissionError, "OpenAI permission was denied."),
        ("RateLimitError", AIRateLimitError, "OpenAI rate limit was reached."),
        ("APITimeoutError", AIRequestTimeoutError, "The OpenAI request timed out."),
        ("APIConnectionError", AIConnectionError, "Could not connect to OpenAI."),
        ("BadRequestError", AIInvalidRequestError, "OpenAI rejected the request."),
        (
            "UnprocessableEntityError",
            AIInvalidRequestError,
            "OpenAI could not process the request.",
        ),
        (
            "NotFoundError",
            AIUnsupportedModelError,
            "The selected OpenAI model is unavailable or unsupported.",
        ),
        (
            "InternalServerError",
            AIProviderServerError,
            "OpenAI reported a server failure.",
        ),
    )
    for sdk_name, project_error_type, message in mappings:
        sdk_error_type = getattr(sdk_module, sdk_name, None) if sdk_module else None
        if isinstance(sdk_error_type, type) and isinstance(error, sdk_error_type):
            return project_error_type(message)

    status_code = getattr(error, "status_code", None)
    if type(status_code) is int and status_code >= 500:
        return AIProviderServerError("OpenAI reported a server failure.")
    if isinstance(error, ModuleNotFoundError):
        return AIDependencyMissingError("An OpenAI runtime dependency is missing.")
    return AIUnknownProviderError("OpenAI provider execution failed safely.")


__all__ = [
    "DEFAULT_OPENAI_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_OPENAI_MAXIMUM_OUTPUT_TOKENS",
    "DEFAULT_OPENAI_MAXIMUM_RETRIES",
    "DEFAULT_OPENAI_MODEL_IDENTIFIER",
    "DEFAULT_OPENAI_READ_TIMEOUT_SECONDS",
    "DEFAULT_OPENAI_REASONING_EFFORT",
    "DEFAULT_OPENAI_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_OPENAI_RESPONSE_VERBOSITY",
    "OPENAI_EXPLANATION_RESPONSE_SCHEMA",
    "OPENAI_CONNECTION_RESPONSE_SCHEMA",
    "OPENAI_PROVIDER_DISPLAY_NAME",
    "OPENAI_PROVIDER_IDENTIFIER",
    "OPENAI_NON_RETRYABLE_FAILURES",
    "OPENAI_RETRYABLE_FAILURES",
    "OPENAI_RETRY_POLICY_OWNER",
    "SUPPORTED_OPENAI_MODEL_IDENTIFIERS",
    "OpenAIExplanationProvider",
    "OpenAIProviderConfiguration",
]
