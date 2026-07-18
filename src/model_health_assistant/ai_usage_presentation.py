"""Pure presentation helpers for non-secret provider and usage metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from model_health_assistant.ai_connection import AIConnectionTestResult
from model_health_assistant.ai_models import AIExplanationResponse, AIUsageMetadata

AIResultWithUsage: TypeAlias = AIExplanationResponse | AIConnectionTestResult


@dataclass(frozen=True, slots=True)
class AIUsagePresentation:
    """Display-ready provider metadata and provider-reported token counts.

    Missing values remain ``None``.  In particular, this type never derives a
    total token count and never represents pricing or cost.
    """

    provider_identifier: str
    provider_display_name: str
    configured_model_identifier: str | None
    actual_model_identifier: str | None
    request_identifier: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

    @property
    def model_identifiers_differ(self) -> bool | None:
        """Return mismatch state only when both model identifiers are known."""

        if (
            self.configured_model_identifier is None
            or self.actual_model_identifier is None
        ):
            return None
        return self.configured_model_identifier != self.actual_model_identifier

    def usage_text(self) -> str:
        """Format only token counters explicitly returned by the provider."""

        values = (
            ("input", self.input_tokens),
            ("output", self.output_tokens),
            ("total", self.total_tokens),
        )
        reported = [f"{name}={value}" for name, value in values if value is not None]
        return ", ".join(reported) if reported else "Not reported"

    def lines(self) -> tuple[str, ...]:
        """Return deterministic display lines without logging or persistence."""

        provider = self.provider_display_name
        if self.provider_identifier:
            provider = (
                f"{provider} ({self.provider_identifier})"
                if provider
                else self.provider_identifier
            )
        lines = [
            f"Provider: {provider or 'Not reported'}",
            (
                "Configured model: "
                f"{self.configured_model_identifier or 'Not reported'}"
            ),
            f"Actual model: {self.actual_model_identifier or 'Not reported'}",
        ]
        if self.model_identifiers_differ is True:
            lines.append(
                "Model metadata warning: The configured and actual model "
                "identifiers differ."
            )
        lines.extend(
            (
                f"Request ID: {self.request_identifier or 'Not reported'}",
                f"Usage: {self.usage_text()}",
            )
        )
        return tuple(lines)

    def format_text(self) -> str:
        """Return the metadata block as plain read-only text."""

        return "\n".join(self.lines())


def build_ai_usage_presentation(
    result: AIResultWithUsage,
    *,
    configured_model_identifier: str | None,
) -> AIUsagePresentation:
    """Build display data from a validated explanation or connection result."""

    if not isinstance(result, (AIExplanationResponse, AIConnectionTestResult)):
        raise TypeError(
            "result must be an AIExplanationResponse or AIConnectionTestResult"
        )
    configured_model = _optional_text(
        configured_model_identifier,
        field_name="configured model identifier",
    )
    provider_identifier = _required_text(
        result.provider_identifier,
        field_name="provider identifier",
    )
    provider_display_name = _required_text(
        result.provider_display_name,
        field_name="provider display name",
    )
    actual_model_identifier = _optional_text(
        result.actual_model_identifier,
        field_name="actual model identifier",
    )
    request_identifier = _optional_text(
        result.request_identifier,
        field_name="request identifier",
    )

    usage = result.usage
    if usage is None:
        input_tokens = None
        output_tokens = None
        total_tokens = None
    else:
        if not isinstance(usage, AIUsageMetadata):
            raise TypeError("result usage must be AIUsageMetadata or None")
        input_tokens = _optional_token_count(usage.input_tokens, "input")
        output_tokens = _optional_token_count(usage.output_tokens, "output")
        total_tokens = _optional_token_count(usage.total_tokens, "total")

    return AIUsagePresentation(
        provider_identifier=provider_identifier,
        provider_display_name=provider_display_name,
        configured_model_identifier=configured_model,
        actual_model_identifier=actual_model_identifier,
        request_identifier=request_identifier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def format_ai_usage_presentation(
    result: AIResultWithUsage,
    *,
    configured_model_identifier: str | None,
) -> str:
    """Format provider/model/request/usage metadata as one plain-text block."""

    return build_ai_usage_presentation(
        result,
        configured_model_identifier=configured_model_identifier,
    ).format_text()


def _required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text or None")
    return value.strip() or None


def _optional_token_count(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"provider returned an invalid {name} token count")
    return value


__all__ = [
    "AIResultWithUsage",
    "AIUsagePresentation",
    "build_ai_usage_presentation",
    "format_ai_usage_presentation",
]
