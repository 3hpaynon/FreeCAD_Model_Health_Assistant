"""Validated non-secret preferences for optional explanation providers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

LOCAL_MOCK_PROVIDER_IDENTIFIER = "local-mock"
OPENAI_PROVIDER_IDENTIFIER = "openai"
SUPPORTED_PROVIDER_IDENTIFIERS = frozenset(
    {LOCAL_MOCK_PROVIDER_IDENTIFIER, OPENAI_PROVIDER_IDENTIFIER}
)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_MAXIMUM_RETRIES = 2
DEFAULT_MAXIMUM_OUTPUT_TOKENS = 1200
DEFAULT_EXPLANATION_MODE = "full-analysis"

MINIMUM_REQUEST_TIMEOUT_SECONDS = 1
MAXIMUM_REQUEST_TIMEOUT_SECONDS = 300
MINIMUM_OUTPUT_TOKENS = 64
MAXIMUM_OUTPUT_TOKENS = 32_000
MAXIMUM_RETRY_COUNT = 5

SUPPORTED_REASONING_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
SUPPORTED_RESPONSE_VERBOSITIES = frozenset({"low", "medium", "high"})
SUPPORTED_EXPLANATION_MODES = frozenset(
    {"selected-warning", "full-analysis", "custom-question"}
)

_MODEL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_STORED_SETTING_KEYS = (
    "provider_identifier",
    "preferred_model_identifier",
    "request_timeout_seconds",
    "maximum_retries",
    "maximum_output_tokens",
    "reasoning_effort",
    "response_verbosity",
    "real_provider_enabled",
    "last_explanation_mode",
)

NonSecretSettingValue = str | int | bool | None


class ProviderSettingsError(RuntimeError):
    """Base class for controlled non-secret settings failures."""


class ProviderSettingsStorageError(ProviderSettingsError):
    """Raised when the injected non-secret settings backend fails."""


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Stored preferences, separate from capabilities and response metadata.

    ``preferred_model_identifier`` is only the user's stored preference.  It is
    not proof that a provider supports the model, and it is not the actual
    model identifier returned by a provider response.
    """

    provider_identifier: str = LOCAL_MOCK_PROVIDER_IDENTIFIER
    preferred_model_identifier: str = ""
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    maximum_retries: int = DEFAULT_MAXIMUM_RETRIES
    maximum_output_tokens: int = DEFAULT_MAXIMUM_OUTPUT_TOKENS
    reasoning_effort: str | None = None
    response_verbosity: str | None = None
    real_provider_enabled: bool = False
    last_explanation_mode: str = DEFAULT_EXPLANATION_MODE

    def __post_init__(self) -> None:
        if self.provider_identifier not in SUPPORTED_PROVIDER_IDENTIFIERS:
            raise ValueError("Unsupported provider identifier.")
        if self.preferred_model_identifier and not _MODEL_IDENTIFIER_PATTERN.fullmatch(
            self.preferred_model_identifier
        ):
            raise ValueError("Invalid preferred model identifier.")
        _require_bounded_integer(
            self.request_timeout_seconds,
            MINIMUM_REQUEST_TIMEOUT_SECONDS,
            MAXIMUM_REQUEST_TIMEOUT_SECONDS,
            "request timeout",
        )
        _require_bounded_integer(
            self.maximum_retries,
            0,
            MAXIMUM_RETRY_COUNT,
            "maximum retries",
        )
        _require_bounded_integer(
            self.maximum_output_tokens,
            MINIMUM_OUTPUT_TOKENS,
            MAXIMUM_OUTPUT_TOKENS,
            "maximum output tokens",
        )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in SUPPORTED_REASONING_EFFORTS
        ):
            raise ValueError("Unsupported reasoning effort.")
        if (
            self.response_verbosity is not None
            and self.response_verbosity not in SUPPORTED_RESPONSE_VERBOSITIES
        ):
            raise ValueError("Unsupported response verbosity.")
        if type(self.real_provider_enabled) is not bool:
            raise ValueError("real_provider_enabled must be a boolean.")
        if self.last_explanation_mode not in SUPPORTED_EXPLANATION_MODES:
            raise ValueError("Unsupported explanation mode.")

    def to_dict(self) -> dict[str, NonSecretSettingValue]:
        """Return only explicitly allowlisted, JSON-compatible preferences."""

        return asdict(self)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> ProviderSettings:
        """Normalize missing or unsupported persisted values to safe defaults."""

        if not isinstance(values, Mapping):
            values = {}

        provider_identifier = _supported_text(
            values.get("provider_identifier"),
            SUPPORTED_PROVIDER_IDENTIFIERS,
            LOCAL_MOCK_PROVIDER_IDENTIFIER,
        )
        preferred_model_identifier = _model_identifier(
            values.get("preferred_model_identifier")
        )
        real_provider_enabled = _strict_boolean(
            values.get("real_provider_enabled"),
            False,
        )
        if provider_identifier != OPENAI_PROVIDER_IDENTIFIER:
            real_provider_enabled = False
            preferred_model_identifier = ""

        return cls(
            provider_identifier=provider_identifier,
            preferred_model_identifier=preferred_model_identifier,
            request_timeout_seconds=_bounded_integer(
                values.get("request_timeout_seconds"),
                MINIMUM_REQUEST_TIMEOUT_SECONDS,
                MAXIMUM_REQUEST_TIMEOUT_SECONDS,
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            ),
            maximum_retries=_bounded_integer(
                values.get("maximum_retries"),
                0,
                MAXIMUM_RETRY_COUNT,
                DEFAULT_MAXIMUM_RETRIES,
            ),
            maximum_output_tokens=_bounded_integer(
                values.get("maximum_output_tokens"),
                MINIMUM_OUTPUT_TOKENS,
                MAXIMUM_OUTPUT_TOKENS,
                DEFAULT_MAXIMUM_OUTPUT_TOKENS,
            ),
            reasoning_effort=_optional_supported_text(
                values.get("reasoning_effort"),
                SUPPORTED_REASONING_EFFORTS,
            ),
            response_verbosity=_optional_supported_text(
                values.get("response_verbosity"),
                SUPPORTED_RESPONSE_VERBOSITIES,
            ),
            real_provider_enabled=real_provider_enabled,
            last_explanation_mode=_supported_text(
                values.get("last_explanation_mode"),
                SUPPORTED_EXPLANATION_MODES,
                DEFAULT_EXPLANATION_MODE,
            ),
        )


@runtime_checkable
class ProviderSettingsBackend(Protocol):
    """Persistence boundary for allowlisted non-secret preference values."""

    def read_values(self) -> Mapping[str, object]:
        """Return persisted preference values."""

        ...

    def write_values(self, values: Mapping[str, NonSecretSettingValue]) -> None:
        """Persist one complete allowlisted preference snapshot."""

        ...


class ProviderSettingsStore:
    """Load and save validated preferences through an injected adapter."""

    def __init__(self, backend: ProviderSettingsBackend) -> None:
        if not isinstance(backend, ProviderSettingsBackend):
            raise TypeError("backend must satisfy ProviderSettingsBackend")
        self._backend = backend

    def load(self) -> ProviderSettings:
        """Return safe defaults when persisted settings are missing or invalid."""

        backend_failed = False
        try:
            values = self._backend.read_values()
        except Exception:
            backend_failed = True
            values = {}
        if backend_failed:
            raise ProviderSettingsStorageError(
                "Non-secret provider settings could not be loaded."
            )
        return ProviderSettings.from_mapping(values)

    def save(self, settings: ProviderSettings) -> ProviderSettings:
        """Persist exactly the validated, non-secret fields in ``settings``."""

        if not isinstance(settings, ProviderSettings):
            raise TypeError("settings must be ProviderSettings")
        payload = {
            key: settings.to_dict()[key]
            for key in _STORED_SETTING_KEYS
        }
        backend_failed = False
        try:
            self._backend.write_values(payload)
        except Exception:
            backend_failed = True
        if backend_failed:
            raise ProviderSettingsStorageError(
                "Non-secret provider settings could not be saved."
            )
        return settings


def _require_bounded_integer(value: object, minimum: int, maximum: int, name: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}.")


def _bounded_integer(value: object, minimum: int, maximum: int, default: int) -> int:
    if type(value) is int and minimum <= value <= maximum:
        return value
    return default


def _strict_boolean(value: object, default: bool) -> bool:
    return value if type(value) is bool else default


def _supported_text(value: object, supported: frozenset[str], default: str) -> str:
    return value if isinstance(value, str) and value in supported else default


def _optional_supported_text(
    value: object,
    supported: frozenset[str],
) -> str | None:
    return value if isinstance(value, str) and value in supported else None


def _model_identifier(value: object) -> str:
    if isinstance(value, str) and (
        not value or _MODEL_IDENTIFIER_PATTERN.fullmatch(value)
    ):
        return value
    return ""


__all__ = [
    "DEFAULT_EXPLANATION_MODE",
    "DEFAULT_MAXIMUM_OUTPUT_TOKENS",
    "DEFAULT_MAXIMUM_RETRIES",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "LOCAL_MOCK_PROVIDER_IDENTIFIER",
    "MAXIMUM_OUTPUT_TOKENS",
    "MAXIMUM_REQUEST_TIMEOUT_SECONDS",
    "MAXIMUM_RETRY_COUNT",
    "MINIMUM_OUTPUT_TOKENS",
    "MINIMUM_REQUEST_TIMEOUT_SECONDS",
    "OPENAI_PROVIDER_IDENTIFIER",
    "ProviderSettings",
    "ProviderSettingsBackend",
    "ProviderSettingsError",
    "ProviderSettingsStorageError",
    "SUPPORTED_EXPLANATION_MODES",
    "SUPPORTED_PROVIDER_IDENTIFIERS",
    "SUPPORTED_REASONING_EFFORTS",
    "SUPPORTED_RESPONSE_VERBOSITIES",
    "ProviderSettingsStore",
]
