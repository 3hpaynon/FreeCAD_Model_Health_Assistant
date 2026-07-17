"""FreeCAD parameter adapter for non-secret provider preferences only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from model_health_assistant.provider_settings import NonSecretSettingValue

FREECAD_PROVIDER_SETTINGS_PARAMETER_PATH = (
    "User parameter:BaseApp/Preferences/Mod/ModelHealthAssistant/AIProvider"
)

_STRING_SETTINGS = {
    "provider_identifier": "ProviderIdentifier",
    "preferred_model_identifier": "PreferredModelIdentifier",
    "reasoning_effort": "ReasoningEffort",
    "response_verbosity": "ResponseVerbosity",
    "last_explanation_mode": "LastExplanationMode",
}
_INTEGER_SETTINGS = {
    "request_timeout_seconds": "RequestTimeoutSeconds",
    "maximum_retries": "MaximumRetries",
    "maximum_output_tokens": "MaximumOutputTokens",
}
_BOOLEAN_SETTINGS = {
    "real_provider_enabled": "RealProviderEnabled",
}


class FreeCADProviderSettingsBackend:
    """Read and write the allowlisted settings through ``FreeCAD.ParamGet``."""

    def __init__(self, app_module: Any) -> None:
        param_get = getattr(app_module, "ParamGet", None)
        if not callable(param_get):
            raise TypeError("app_module must provide FreeCAD.ParamGet")
        self._parameter_group = param_get(FREECAD_PROVIDER_SETTINGS_PARAMETER_PATH)

    def read_values(self) -> Mapping[str, object]:
        """Return the complete non-secret preference snapshot."""

        values: dict[str, object] = {}
        for setting_name, parameter_name in _STRING_SETTINGS.items():
            values[setting_name] = self._parameter_group.GetString(
                parameter_name,
                "",
            )
        for setting_name, parameter_name in _INTEGER_SETTINGS.items():
            values[setting_name] = self._parameter_group.GetInt(
                parameter_name,
                -1,
            )
        for setting_name, parameter_name in _BOOLEAN_SETTINGS.items():
            values[setting_name] = self._parameter_group.GetBool(
                parameter_name,
                False,
            )
        return values

    def write_values(
        self,
        values: Mapping[str, NonSecretSettingValue],
    ) -> None:
        """Persist only known non-secret fields using typed FreeCAD values."""

        unexpected_keys = set(values) - (
            set(_STRING_SETTINGS) | set(_INTEGER_SETTINGS) | set(_BOOLEAN_SETTINGS)
        )
        if unexpected_keys:
            raise ValueError("Unexpected provider setting fields.")

        for setting_name, parameter_name in _STRING_SETTINGS.items():
            value = values.get(setting_name)
            if value is not None and not isinstance(value, str):
                raise TypeError("Invalid string provider setting.")
            self._parameter_group.SetString(parameter_name, value or "")
        for setting_name, parameter_name in _INTEGER_SETTINGS.items():
            value = values.get(setting_name)
            if type(value) is not int:
                raise TypeError("Invalid integer provider setting.")
            self._parameter_group.SetInt(parameter_name, value)
        for setting_name, parameter_name in _BOOLEAN_SETTINGS.items():
            value = values.get(setting_name)
            if type(value) is not bool:
                raise TypeError("Invalid boolean provider setting.")
            self._parameter_group.SetBool(parameter_name, value)


__all__ = [
    "FREECAD_PROVIDER_SETTINGS_PARAMETER_PATH",
    "FreeCADProviderSettingsBackend",
]
