"""Build privacy-safe explanation context from completed local reports.

Only fields explicitly selected in this module can cross the future provider
boundary.  The builder does not read a FreeCAD document, write files, construct
prompts, or perform network activity.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from model_health_assistant.ai_models import (
    AIContextMetadata,
    AIRequestContext,
    AIWarningContext,
    JSONScalar,
    JSONValue,
)
from model_health_assistant.models import (
    FEATURE_CATEGORY_ORDER,
    REPORT_SCHEMA_VERSION,
    WARNING_SEVERITY_ORDER,
    FeatureInfo,
    ModelHealthReport,
    RiskWarning,
)

NEUTRAL_DOCUMENT_DISPLAY_NAME = "Active FreeCAD document"
AI_CONTEXT_LIMIT_POLICY_VERSION = "context-limits-v1"
TRUNCATION_MARKER = "… [truncated]"


@dataclass(frozen=True, slots=True)
class AIContextLimits:
    """Provider-neutral character and item limits for one AI context."""

    policy_version: str = AI_CONTEXT_LIMIT_POLICY_VERSION
    maximum_warning_count: int = 50
    maximum_feature_summary_count: int = 50
    maximum_text_characters: int = 2_000
    maximum_evidence_list_items: int = 10
    maximum_serialized_context_characters: int = 64_000

    def __post_init__(self) -> None:
        numeric_limits = (
            self.maximum_warning_count,
            self.maximum_feature_summary_count,
            self.maximum_text_characters,
            self.maximum_evidence_list_items,
            self.maximum_serialized_context_characters,
        )
        if any(value <= 0 for value in numeric_limits):
            raise ValueError("AI context limits must all be positive")
        if self.maximum_text_characters <= len(TRUNCATION_MARKER):
            raise ValueError("maximum_text_characters must exceed the truncation marker")


DEFAULT_AI_CONTEXT_LIMITS = AIContextLimits()


@dataclass(slots=True)
class _TruncationTracker:
    truncated_field_count: int = 0

    def record_field(self) -> None:
        self.truncated_field_count += 1


@dataclass(slots=True)
class _BuiltWarningContext:
    context: AIWarningContext
    omitted_fields: list[str]
    omitted_evidence_fields: list[str]
    truncated_field_count: int

_ALLOWED_FEATURE_METADATA_FIELDS = frozenset(
    {
        "constraint_count",
        "constraint_status",
        "external_geometry_count",
        "fully_constrained",
        "geometry_count",
        "hardcoded_dimension_count",
    }
)
_ALLOWED_SCORE_BREAKDOWN_FIELDS = frozenset(
    {
        "formula_version",
        "starting_score",
        "total_penalty",
        "warning_penalties",
    }
)
_ANALYSIS_LIMITATIONS = (
    "The analysis is a deterministic rule-based snapshot and may not identify every modeling problem.",
    "The health score is a heuristic engineering aid and does not prove model correctness.",
    "No complete CAD geometry or original CAD file is included in this context.",
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\)[^\r\n]*"
)
_POSIX_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home|var|tmp|opt|Applications|usr|etc)/[^\r\n]*"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_REDACTION = "[redacted local path]"


def build_ai_request_context(
    report: ModelHealthReport,
    *,
    include_document_name: bool = False,
    limits: AIContextLimits = DEFAULT_AI_CONTEXT_LIMITS,
) -> AIRequestContext:
    """Return a deterministic, explicitly allowlisted context for ``report``.

    The real document name is excluded by default.  Opt-in names are accepted
    only when they do not resemble an absolute path or a CAD filename.
    Document-originated text remains untrusted data and is never interpreted as
    an instruction.
    """

    if not isinstance(report, ModelHealthReport):
        raise TypeError("report must be a ModelHealthReport")
    if not isinstance(limits, AIContextLimits):
        raise TypeError("limits must be an AIContextLimits")

    base_omitted_fields: list[str] = []
    base_omitted_evidence_fields: list[str] = []
    base_tracker = _TruncationTracker()
    document_display_name, document_name_included = _document_display_name(
        report.document_name,
        include_document_name=include_document_name,
        omitted_fields=base_omitted_fields,
        limits=limits,
        tracker=base_tracker,
    )

    if report.generated_at:
        _record_once(base_omitted_fields, "report.generated_at")
    if report.notes:
        _record_once(base_omitted_fields, "report.notes")
    if report.metadata:
        _record_once(base_omitted_fields, "report.metadata")

    features_by_name = _features_by_internal_name(report.features)
    warning_penalties = _warning_penalty_entries(report.health_score_breakdown)
    ordered_warnings = _prioritized_warnings(report.warnings)
    maximum_inline_feature_summaries = min(
        limits.maximum_warning_count,
        limits.maximum_feature_summary_count,
    )
    selected_warnings = ordered_warnings[:maximum_inline_feature_summaries]
    built_warnings = [
        _build_warning_context(
            warning,
            features_by_name.get(warning.affected_feature_name),
            warning_penalties,
            original_index,
            limits,
        )
        for original_index, warning in selected_warnings
    ]

    score_breakdown = report.health_score_breakdown
    unknown_breakdown_fields = set(score_breakdown) - _ALLOWED_SCORE_BREAKDOWN_FIELDS
    if unknown_breakdown_fields:
        _record_once(
            base_omitted_fields,
            "report.health_score_breakdown.unallowlisted_fields",
        )

    total_warnings = report.total_warning_count
    health_score_formula_version = _optional_text(
        score_breakdown.get("formula_version"),
        "health_score_breakdown.formula_version",
        base_omitted_fields,
        limits,
        base_tracker,
    )
    context = AIRequestContext(
        report_schema_version=REPORT_SCHEMA_VERSION,
        health_score=report.health_score,
        total_document_object_count=report.total_document_object_count,
        meaningful_modeling_feature_count=report.meaningful_modeling_feature_count,
        total_sketch_count=report.total_sketch_count,
        category_counts={
            category: report.category_counts.get(category, 0)
            for category in FEATURE_CATEGORY_ORDER
        },
        total_warning_count=total_warnings,
        warning_counts_by_severity={
            severity: report.warning_counts_by_severity.get(severity, 0)
            for severity in WARNING_SEVERITY_ORDER
        },
        warnings=[built.context for built in built_warnings],
        analysis_limitations=[
            _required_text(
                limitation,
                "analysis_limitations",
                base_omitted_fields,
                limits,
                base_tracker,
            )
            for limitation in _ANALYSIS_LIMITATIONS
        ],
        health_score_formula_version=health_score_formula_version,
        starting_health_score=_optional_int(score_breakdown.get("starting_score")),
        total_score_penalty=_optional_int(score_breakdown.get("total_penalty")),
        document_display_name=document_display_name,
        metadata=AIContextMetadata(document_name_included=document_name_included),
    )
    _enforce_total_context_limit(
        context,
        built_warnings,
        base_omitted_fields,
        base_omitted_evidence_fields,
        base_tracker,
        total_warnings,
        limits,
    )
    return context


def _build_warning_context(
    warning: RiskWarning,
    feature: FeatureInfo | None,
    warning_penalties: list[dict[str, Any]],
    original_index: int,
    limits: AIContextLimits,
) -> _BuiltWarningContext:
    omitted_fields: list[str] = []
    omitted_evidence_fields: list[str] = []
    tracker = _TruncationTracker()
    field_prefix = f"warning[{original_index}]"
    context = AIWarningContext(
        warning_id=_required_text(
            warning.warning_id,
            f"{field_prefix}.warning_id",
            omitted_fields,
            limits,
            tracker,
        ),
        rule_id=_required_text(
            warning.rule_id,
            f"{field_prefix}.rule_id",
            omitted_fields,
            limits,
            tracker,
        ),
        severity=_required_text(
            warning.severity,
            f"{field_prefix}.severity",
            omitted_fields,
            limits,
            tracker,
        ),
        affected_feature_internal_name=_required_text(
            warning.affected_feature_name,
            f"{field_prefix}.affected_feature_internal_name",
            omitted_fields,
            limits,
            tracker,
        ),
        affected_feature_label=_feature_optional_text(
            feature,
            "label",
            omitted_evidence_fields,
            limits,
            tracker,
        ),
        affected_feature_category=_feature_optional_text(
            feature,
            "category",
            omitted_evidence_fields,
            limits,
            tracker,
        ),
        affected_feature_type=_feature_optional_text(
            feature,
            "type_id",
            omitted_evidence_fields,
            limits,
            tracker,
        ),
        message=_required_text(
            warning.message,
            f"{field_prefix}.message",
            omitted_fields,
            limits,
            tracker,
        ),
        technical_reason=_required_text(
            warning.technical_reason,
            f"{field_prefix}.technical_reason",
            omitted_fields,
            limits,
            tracker,
        ),
        suggested_practice=_required_text(
            warning.suggested_practice,
            f"{field_prefix}.suggested_practice",
            omitted_fields,
            limits,
            tracker,
        ),
        evidence=_build_warning_evidence(
            feature,
            omitted_evidence_fields,
            limits,
            tracker,
        ),
        raw_score_penalty=_matching_raw_penalty(warning, warning_penalties),
    )
    return _BuiltWarningContext(
        context=context,
        omitted_fields=omitted_fields,
        omitted_evidence_fields=omitted_evidence_fields,
        truncated_field_count=tracker.truncated_field_count,
    )


def _build_warning_evidence(
    feature: FeatureInfo | None,
    omitted_evidence_fields: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> dict[str, JSONValue]:
    """Copy only the reviewed feature facts used to explain warnings."""

    if feature is None:
        return {}

    evidence: dict[str, JSONValue] = {
        "is_sketch": bool(feature.is_sketch),
        "direct_upstream_dependency_count": _safe_length(feature.dependency_names),
        "direct_downstream_dependency_count": _safe_length(
            feature.downstream_dependent_names
        ),
        "attached_to_generated_geometry": bool(
            feature.attached_to_generated_geometry
        ),
        "has_confirmed_error": bool(feature.has_confirmed_error),
    }
    optional_text_fields = (
        "support_name",
        "support_kind",
        "support_subelement",
        "recompute_status",
        "error_status",
    )
    for field_name in optional_text_fields:
        value = _feature_optional_text(
            feature,
            field_name,
            omitted_evidence_fields,
            limits,
            tracker,
        )
        if value is not None:
            evidence[field_name] = value

    if feature.support_name or feature.support_kind or feature.support_subelement:
        evidence["support_is_stable_reference"] = bool(
            feature.support_is_stable_reference
        )
        support_confidence = _feature_optional_text(
            feature,
            "support_confidence",
            omitted_evidence_fields,
            limits,
            tracker,
        )
        if support_confidence is not None:
            evidence["support_confidence"] = support_confidence

    error_details = _safe_text_list(
        feature.error_details,
        "affected_feature.error_details",
        omitted_evidence_fields,
        limits,
        tracker,
    )
    if error_details:
        evidence["error_details"] = error_details

    notes = _safe_text_list(
        feature.notes,
        "affected_feature.notes",
        omitted_evidence_fields,
        limits,
        tracker,
    )
    if notes:
        evidence["notes"] = notes

    safe_metadata: dict[str, JSONValue] = {}
    if isinstance(feature.metadata, dict):
        if set(feature.metadata) - _ALLOWED_FEATURE_METADATA_FIELDS:
            _record_once(
                omitted_evidence_fields,
                "affected_feature.metadata.unallowlisted_fields",
            )
        for field_name in sorted(_ALLOWED_FEATURE_METADATA_FIELDS):
            if field_name not in feature.metadata:
                continue
            value = _safe_json_scalar(
                feature.metadata[field_name],
                f"affected_feature.metadata.{field_name}",
                omitted_evidence_fields,
                limits,
                tracker,
            )
            if value is not _UNSUPPORTED:
                safe_metadata[field_name] = value
    elif feature.metadata:
        _record_once(omitted_evidence_fields, "affected_feature.metadata")
    if safe_metadata:
        evidence["metadata"] = safe_metadata

    return evidence


def _document_display_name(
    document_name: Any,
    *,
    include_document_name: bool,
    omitted_fields: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> tuple[str, bool]:
    if not include_document_name:
        _record_once(omitted_fields, "report.document_name")
        return NEUTRAL_DOCUMENT_DISPLAY_NAME, False

    sanitized = _optional_text(
        document_name,
        "report.document_name",
        omitted_fields,
        limits,
        tracker,
    )
    if (
        not sanitized
        or _PATH_REDACTION in sanitized
        or sanitized.casefold().endswith(".fcstd")
    ):
        _record_once(omitted_fields, "report.document_name")
        return NEUTRAL_DOCUMENT_DISPLAY_NAME, False
    return sanitized, True


def _features_by_internal_name(features: list[FeatureInfo]) -> dict[str, FeatureInfo]:
    matches: dict[str, FeatureInfo] = {}
    for feature in features:
        if isinstance(feature.internal_name, str) and feature.internal_name:
            matches.setdefault(feature.internal_name, feature)
    return matches


def _warning_penalty_entries(score_breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    entries = score_breakdown.get("warning_penalties")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _matching_raw_penalty(
    warning: RiskWarning,
    entries: list[dict[str, Any]],
) -> int | None:
    for entry in entries:
        if (
            entry.get("warning_id") != warning.warning_id
            or entry.get("rule_id") != warning.rule_id
            or entry.get("affected_feature_name") != warning.affected_feature_name
        ):
            continue
        return _optional_int(entry.get("raw_penalty"))
    return None


def _prioritized_warnings(
    warnings: list[RiskWarning],
) -> list[tuple[int, RiskWarning]]:
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    indexed_warnings = list(enumerate(warnings))
    return sorted(
        indexed_warnings,
        key=lambda item: (severity_rank.get(item[1].severity, 3), item[0]),
    )


def _enforce_total_context_limit(
    context: AIRequestContext,
    built_warnings: list[_BuiltWarningContext],
    base_omitted_fields: list[str],
    base_omitted_evidence_fields: list[str],
    base_tracker: _TruncationTracker,
    original_warning_count: int,
    limits: AIContextLimits,
) -> None:
    while True:
        _refresh_context_metadata(
            context,
            built_warnings,
            base_omitted_fields,
            base_omitted_evidence_fields,
            base_tracker,
            original_warning_count,
            limits,
        )
        character_count = _stabilize_serialized_character_count(context)
        if character_count <= limits.maximum_serialized_context_characters:
            return
        if not built_warnings:
            raise ValueError(
                "maximum_serialized_context_characters is too small for the "
                "fixed AI context structure"
            )
        built_warnings.pop()
        context.warnings.pop()


def _refresh_context_metadata(
    context: AIRequestContext,
    built_warnings: list[_BuiltWarningContext],
    base_omitted_fields: list[str],
    base_omitted_evidence_fields: list[str],
    base_tracker: _TruncationTracker,
    original_warning_count: int,
    limits: AIContextLimits,
) -> None:
    omitted_fields = list(base_omitted_fields)
    omitted_evidence_fields = list(base_omitted_evidence_fields)
    truncated_field_count = base_tracker.truncated_field_count
    for built_warning in built_warnings:
        for field_id in built_warning.omitted_fields:
            _record_once(omitted_fields, field_id)
        for field_id in built_warning.omitted_evidence_fields:
            _record_once(omitted_evidence_fields, field_id)
        truncated_field_count += built_warning.truncated_field_count

    included_warning_count = len(built_warnings)
    omitted_warning_count = original_warning_count - included_warning_count
    if omitted_warning_count:
        _record_once(omitted_fields, "report.warnings.context_limits")

    context.metadata = AIContextMetadata(
        document_name_included=context.metadata.document_name_included,
        omitted_fields=omitted_fields,
        omitted_evidence_fields=omitted_evidence_fields,
        was_truncated=(
            omitted_warning_count > 0 or truncated_field_count > 0
        ),
        original_warning_count=original_warning_count,
        included_warning_count=included_warning_count,
        omitted_warning_count=omitted_warning_count,
        truncated_field_count=truncated_field_count,
        limit_policy_version=limits.policy_version,
        maximum_warning_count=limits.maximum_warning_count,
        maximum_feature_summary_count=limits.maximum_feature_summary_count,
        maximum_text_characters=limits.maximum_text_characters,
        maximum_evidence_list_items=limits.maximum_evidence_list_items,
        maximum_serialized_context_characters=(
            limits.maximum_serialized_context_characters
        ),
        serialized_context_characters=0,
    )


def serialize_ai_request_context(context: AIRequestContext) -> str:
    """Return the canonical JSON representation used for character limits."""

    if not isinstance(context, AIRequestContext):
        raise TypeError("context must be an AIRequestContext")
    return json.dumps(
        context.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stabilize_serialized_character_count(context: AIRequestContext) -> int:
    context.metadata.serialized_context_characters = 0
    for _attempt in range(10):
        character_count = len(serialize_ai_request_context(context))
        if context.metadata.serialized_context_characters == character_count:
            return character_count
        context.metadata.serialized_context_characters = character_count
    raise RuntimeError("AI context serialized character count did not stabilize")


def _truncate_unicode_text(value: str, maximum_characters: int) -> str:
    retained_character_count = maximum_characters - len(TRUNCATION_MARKER)
    prefix = value[:retained_character_count]
    while prefix and _is_unsafe_unicode_boundary(prefix[-1]):
        prefix = prefix[:-1]
    return prefix + TRUNCATION_MARKER


def _is_unsafe_unicode_boundary(character: str) -> bool:
    codepoint = ord(character)
    return (
        bool(unicodedata.combining(character))
        or unicodedata.category(character) in {"Mn", "Mc", "Me", "Cs"}
        or character == "\u200d"
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _feature_optional_text(
    feature: FeatureInfo | None,
    field_name: str,
    omissions: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> str | None:
    if feature is None:
        return None
    return _optional_text(
        getattr(feature, field_name, None),
        f"affected_feature.{field_name}",
        omissions,
        limits,
        tracker,
    )


def _required_text(
    value: Any,
    field_id: str,
    omissions: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> str:
    return _optional_text(value, field_id, omissions, limits, tracker) or ""


def _optional_text(
    value: Any,
    field_id: str,
    omissions: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        _record_once(omissions, field_id)
        return None

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARACTERS.sub(" ", text)
    text = "".join(
        "�" if unicodedata.category(character) == "Cs" else character
        for character in text
    )
    redacted = _WINDOWS_ABSOLUTE_PATH.sub(_PATH_REDACTION, text)
    redacted = _POSIX_PRIVATE_PATH.sub(_PATH_REDACTION, redacted)
    if redacted != text:
        _record_once(omissions, f"{field_id}.path_data")
    if len(redacted) <= limits.maximum_text_characters:
        return redacted

    tracker.record_field()
    return _truncate_unicode_text(redacted, limits.maximum_text_characters)


def _safe_text_list(
    values: Any,
    field_id: str,
    omissions: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> list[JSONValue]:
    if not isinstance(values, (list, tuple)):
        if values:
            _record_once(omissions, field_id)
        return []

    if len(values) > limits.maximum_evidence_list_items:
        tracker.record_field()
        _record_once(omissions, f"{field_id}.item_limit")

    safe_values: list[JSONValue] = []
    for value in values[: limits.maximum_evidence_list_items]:
        safe_value = _optional_text(value, field_id, omissions, limits, tracker)
        if safe_value is not None:
            safe_values.append(safe_value)
    return safe_values


class _UnsupportedValue:
    pass


_UNSUPPORTED = _UnsupportedValue()


def _safe_json_scalar(
    value: Any,
    field_id: str,
    omissions: list[str],
    limits: AIContextLimits,
    tracker: _TruncationTracker,
) -> JSONScalar | _UnsupportedValue:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _optional_text(value, field_id, omissions, limits, tracker)
    _record_once(omissions, field_id)
    return _UNSUPPORTED


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _safe_length(value: Any) -> int:
    try:
        return len(value)
    except (TypeError, ValueError):
        return 0


def _record_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


__all__ = [
    "AI_CONTEXT_LIMIT_POLICY_VERSION",
    "AIContextLimits",
    "DEFAULT_AI_CONTEXT_LIMITS",
    "NEUTRAL_DOCUMENT_DISPLAY_NAME",
    "TRUNCATION_MARKER",
    "build_ai_request_context",
    "serialize_ai_request_context",
]
