from dataclasses import fields

import pytest

from model_health_assistant.provider_settings import (
    DEFAULT_MAXIMUM_OUTPUT_TOKENS,
    DEFAULT_MAXIMUM_RETRIES,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    LOCAL_MOCK_PROVIDER_IDENTIFIER,
    OPENAI_PROVIDER_IDENTIFIER,
    ProviderSettings,
    ProviderSettingsStorageError,
    ProviderSettingsStore,
)


class _MemorySettingsBackend:
    def __init__(self, values=None, *, fail_read=False, fail_write=False) -> None:
        self.values = dict(values or {})
        self.fail_read = fail_read
        self.fail_write = fail_write

    def read_values(self):
        if self.fail_read:
            raise RuntimeError("backend details")
        return dict(self.values)

    def write_values(self, values):
        if self.fail_write:
            raise RuntimeError("backend details")
        self.values = dict(values)


def test_valid_settings_round_trip_only_non_secret_values() -> None:
    backend = _MemorySettingsBackend()
    store = ProviderSettingsStore(backend)
    settings = ProviderSettings(
        provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        preferred_model_identifier="verified-model-id",
        request_timeout_seconds=75,
        maximum_retries=1,
        maximum_output_tokens=1600,
        reasoning_effort="low",
        response_verbosity="medium",
        real_provider_enabled=True,
        last_explanation_mode="selected-warning",
    )

    assert store.save(settings) is settings
    assert store.load() == settings
    assert backend.values == settings.to_dict()


def test_missing_settings_use_local_only_safe_defaults() -> None:
    settings = ProviderSettingsStore(_MemorySettingsBackend()).load()

    assert settings == ProviderSettings()
    assert settings.provider_identifier == LOCAL_MOCK_PROVIDER_IDENTIFIER
    assert settings.preferred_model_identifier == ""
    assert settings.real_provider_enabled is False


@pytest.mark.parametrize("value", [0, -1, 301, "60", True, None])
def test_invalid_timeout_falls_back_safely(value) -> None:
    settings = ProviderSettings.from_mapping({"request_timeout_seconds": value})

    assert settings.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize("value", [-1, 6, "2", True, None])
def test_invalid_retry_count_falls_back_safely(value) -> None:
    settings = ProviderSettings.from_mapping({"maximum_retries": value})

    assert settings.maximum_retries == DEFAULT_MAXIMUM_RETRIES


@pytest.mark.parametrize("value", [0, 63, 32_001, "1200", True, None])
def test_invalid_output_limit_falls_back_safely(value) -> None:
    settings = ProviderSettings.from_mapping({"maximum_output_tokens": value})

    assert settings.maximum_output_tokens == DEFAULT_MAXIMUM_OUTPUT_TOKENS


def test_unsupported_provider_disables_real_provider_and_model_preference() -> None:
    settings = ProviderSettings.from_mapping(
        {
            "provider_identifier": "unknown-provider",
            "preferred_model_identifier": "some-model",
            "real_provider_enabled": True,
        }
    )

    assert settings.provider_identifier == LOCAL_MOCK_PROVIDER_IDENTIFIER
    assert settings.preferred_model_identifier == ""
    assert settings.real_provider_enabled is False


def test_invalid_optional_values_and_model_identifier_fall_back_safely() -> None:
    settings = ProviderSettings.from_mapping(
        {
            "provider_identifier": OPENAI_PROVIDER_IDENTIFIER,
            "preferred_model_identifier": "invalid model/value",
            "reasoning_effort": "extreme",
            "response_verbosity": "verbose-secret-dump",
            "last_explanation_mode": "automatic-action",
            "real_provider_enabled": "yes",
        }
    )

    assert settings.preferred_model_identifier == ""
    assert settings.reasoning_effort is None
    assert settings.response_verbosity is None
    assert settings.last_explanation_mode == "full-analysis"
    assert settings.real_provider_enabled is False


def test_direct_construction_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        ProviderSettings(request_timeout_seconds=0)
    with pytest.raises(ValueError):
        ProviderSettings(maximum_retries=99)
    with pytest.raises(ValueError):
        ProviderSettings(maximum_output_tokens=1)
    with pytest.raises(ValueError):
        ProviderSettings(provider_identifier="unsupported")


def test_settings_schema_has_no_secret_or_runtime_result_fields() -> None:
    field_names = {item.name for item in fields(ProviderSettings)}
    forbidden_fragments = {
        "api_key",
        "credential",
        "prompt",
        "context",
        "ai_response",
        "response_content",
        "request_header",
        "report_path",
        "actual_model",
        "provider_capability",
    }

    assert field_names.isdisjoint(forbidden_fragments)
    assert all(
        not any(fragment in field_name for fragment in forbidden_fragments)
        for field_name in field_names
    )


def test_serialization_contains_only_json_compatible_non_secret_values() -> None:
    payload = ProviderSettings(
        provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
        preferred_model_identifier="verified-model-id",
        real_provider_enabled=True,
    ).to_dict()

    assert set(payload) == {item.name for item in fields(ProviderSettings)}
    assert all(
        value is None or isinstance(value, (str, int, bool))
        for value in payload.values()
    )


def test_settings_backend_failures_use_project_owned_errors() -> None:
    with pytest.raises(ProviderSettingsStorageError, match="could not be loaded"):
        ProviderSettingsStore(_MemorySettingsBackend(fail_read=True)).load()

    with pytest.raises(ProviderSettingsStorageError, match="could not be saved"):
        ProviderSettingsStore(_MemorySettingsBackend(fail_write=True)).save(
            ProviderSettings()
        )
