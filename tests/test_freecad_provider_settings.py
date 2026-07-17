from model_health_assistant.freecad_provider_settings import (
    FREECAD_PROVIDER_SETTINGS_PARAMETER_PATH,
    FreeCADProviderSettingsBackend,
)
from model_health_assistant.provider_settings import (
    ProviderSettings,
    ProviderSettingsStore,
)


class _ParameterGroup:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def GetString(self, name: str, default: str) -> str:  # noqa: N802
        return self.values.get(name, default)

    def GetInt(self, name: str, default: int) -> int:  # noqa: N802
        return self.values.get(name, default)

    def GetBool(self, name: str, default: bool) -> bool:  # noqa: N802
        return self.values.get(name, default)

    def SetString(self, name: str, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def SetInt(self, name: str, value: int) -> None:  # noqa: N802
        self.values[name] = value

    def SetBool(self, name: str, value: bool) -> None:  # noqa: N802
        self.values[name] = value


class _FreeCADApp:
    def __init__(self) -> None:
        self.group = _ParameterGroup()
        self.paths: list[str] = []

    def ParamGet(self, path: str) -> _ParameterGroup:  # noqa: N802
        self.paths.append(path)
        return self.group


def test_freecad_adapter_round_trips_only_non_secret_settings() -> None:
    app = _FreeCADApp()
    store = ProviderSettingsStore(FreeCADProviderSettingsBackend(app))
    settings = ProviderSettings(
        provider_identifier="openai",
        preferred_model_identifier="gpt-5.6-terra",
        request_timeout_seconds=45,
        maximum_retries=1,
        maximum_output_tokens=900,
        reasoning_effort="low",
        response_verbosity="medium",
        real_provider_enabled=True,
        last_explanation_mode="selected-warning",
    )

    store.save(settings)

    assert store.load() == settings
    assert app.paths == [FREECAD_PROVIDER_SETTINGS_PARAMETER_PATH]
    assert app.group.values == {
        "ProviderIdentifier": "openai",
        "PreferredModelIdentifier": "gpt-5.6-terra",
        "ReasoningEffort": "low",
        "ResponseVerbosity": "medium",
        "LastExplanationMode": "selected-warning",
        "RequestTimeoutSeconds": 45,
        "MaximumRetries": 1,
        "MaximumOutputTokens": 900,
        "RealProviderEnabled": True,
    }
    serialized_names = " ".join(app.group.values).casefold()
    assert "key" not in serialized_names
    assert "credential" not in serialized_names
    assert "prompt" not in serialized_names
    assert "report" not in serialized_names


def test_missing_or_invalid_freecad_values_fall_back_safely() -> None:
    app = _FreeCADApp()
    app.group.values.update(
        {
            "ProviderIdentifier": "unknown",
            "PreferredModelIdentifier": "unsafe model identifier",
            "RequestTimeoutSeconds": -5,
            "MaximumRetries": 100,
            "MaximumOutputTokens": 1,
            "RealProviderEnabled": True,
        }
    )

    settings = ProviderSettingsStore(
        FreeCADProviderSettingsBackend(app)
    ).load()

    assert settings == ProviderSettings()


def test_freecad_adapter_rejects_unallowlisted_fields() -> None:
    backend = FreeCADProviderSettingsBackend(_FreeCADApp())

    try:
        backend.write_values({"api_key": "must-not-be-stored"})
    except ValueError as error:
        assert "Unexpected" in str(error)
    else:
        raise AssertionError("unallowlisted settings must be rejected")
