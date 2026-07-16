"""Pure presentation models derived from completed model-health reports.

This module deliberately has no FreeCAD or Qt dependencies.  It reshapes an
existing report for display without rerunning rules, recalculating scores, or
mutating the report.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Any

from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning

_SEVERITY_DISPLAY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True, slots=True)
class WarningDisplayItem:
    """Display-ready data for one warning from a completed report."""

    warning_id: str
    severity: str
    rule_id: str
    feature_internal_name: str
    feature_label: str | None
    message: str
    technical_reason: str
    suggested_practice: str
    evidence: dict[str, Any] | None
    raw_score_penalty: int | None


@dataclass(frozen=True, slots=True)
class AnalysisPresentation:
    """UI-neutral summary and warning items for a completed analysis."""

    document_name: str
    analysis_timestamp: str | None
    total_document_object_count: int
    meaningful_modeling_feature_count: int
    total_sketch_count: int
    total_warning_count: int
    high_warning_count: int
    medium_warning_count: int
    low_warning_count: int
    health_score: int
    report_path: Path | None
    warning_items: tuple[WarningDisplayItem, ...]


def build_analysis_presentation(
    report: ModelHealthReport,
    report_path: str | PathLike[str] | None = None,
) -> AnalysisPresentation:
    """Build display data from ``report`` without changing analysis results.

    Warning evidence consists only of structured facts already stored on the
    exactly matched feature.  ``raw_score_penalty`` is copied only from an
    exactly matched per-warning score-breakdown entry; rule caps and prevalence
    mean it must not be described as the warning's final applied penalty.
    """

    features_by_name = _features_by_internal_name(report.features)
    penalty_entries = _warning_penalty_entries(report.health_score_breakdown)
    used_penalty_entries: set[int] = set()

    ordered_warnings = sorted(
        report.warnings,
        key=lambda warning: _SEVERITY_DISPLAY_ORDER.get(warning.severity, 3),
    )
    warning_items = tuple(
        _build_warning_display_item(
            warning,
            features_by_name,
            penalty_entries,
            used_penalty_entries,
        )
        for warning in ordered_warnings
    )

    severity_counts = report.warning_counts_by_severity
    return AnalysisPresentation(
        document_name=report.document_name,
        analysis_timestamp=report.generated_at or None,
        total_document_object_count=report.total_document_object_count,
        meaningful_modeling_feature_count=report.meaningful_modeling_feature_count,
        total_sketch_count=report.total_sketch_count,
        total_warning_count=report.total_warning_count,
        high_warning_count=severity_counts.get("high", 0),
        medium_warning_count=severity_counts.get("medium", 0),
        low_warning_count=severity_counts.get("low", 0),
        health_score=report.health_score,
        report_path=_coerce_report_path(report_path),
        warning_items=warning_items,
    )


def _build_warning_display_item(
    warning: RiskWarning,
    features_by_name: dict[str, FeatureInfo],
    penalty_entries: list[dict[str, Any]],
    used_penalty_entries: set[int],
) -> WarningDisplayItem:
    feature = features_by_name.get(warning.affected_feature_name)
    return WarningDisplayItem(
        warning_id=warning.warning_id,
        severity=warning.severity,
        rule_id=warning.rule_id,
        feature_internal_name=warning.affected_feature_name,
        feature_label=(feature.label or None) if feature is not None else None,
        message=warning.message,
        technical_reason=warning.technical_reason,
        suggested_practice=warning.suggested_practice,
        evidence=_feature_evidence(feature),
        raw_score_penalty=_matching_raw_penalty(
            warning,
            penalty_entries,
            used_penalty_entries,
        ),
    )


def _features_by_internal_name(features: list[FeatureInfo]) -> dict[str, FeatureInfo]:
    features_by_name: dict[str, FeatureInfo] = {}
    for feature in features:
        if feature.internal_name:
            features_by_name.setdefault(feature.internal_name, feature)
    return features_by_name


def _feature_evidence(feature: FeatureInfo | None) -> dict[str, Any] | None:
    if feature is None:
        return None

    evidence: dict[str, Any] = {}
    if feature.support_name:
        evidence["support_name"] = feature.support_name
    if feature.support_kind:
        evidence["support_kind"] = feature.support_kind
    if feature.support_subelement:
        evidence["support_subelement"] = feature.support_subelement
    if feature.support_name or feature.support_kind or feature.support_subelement:
        evidence["support_is_stable_reference"] = feature.support_is_stable_reference
        evidence["support_confidence"] = feature.support_confidence
    if feature.attached_to_generated_geometry:
        evidence["attached_to_generated_geometry"] = True
    if feature.recompute_status:
        evidence["recompute_status"] = feature.recompute_status
    if feature.error_status:
        evidence["error_status"] = feature.error_status
    if feature.has_confirmed_error:
        evidence["has_confirmed_error"] = True
    if feature.error_details:
        evidence["error_details"] = list(feature.error_details)
    if feature.dependency_names:
        evidence["dependency_names"] = list(feature.dependency_names)
    if feature.downstream_dependent_names:
        evidence["downstream_dependent_names"] = list(
            feature.downstream_dependent_names
        )
    if feature.notes:
        evidence["notes"] = list(feature.notes)
    if feature.metadata:
        evidence["metadata"] = deepcopy(feature.metadata)
    return evidence or None


def _warning_penalty_entries(score_breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    entries = score_breakdown.get("warning_penalties")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _matching_raw_penalty(
    warning: RiskWarning,
    entries: list[dict[str, Any]],
    used_entries: set[int],
) -> int | None:
    for index, entry in enumerate(entries):
        if index in used_entries:
            continue
        if (
            entry.get("warning_id") != warning.warning_id
            or entry.get("rule_id") != warning.rule_id
            or entry.get("affected_feature_name") != warning.affected_feature_name
        ):
            continue

        used_entries.add(index)
        raw_penalty = entry.get("raw_penalty")
        if isinstance(raw_penalty, int) and not isinstance(raw_penalty, bool):
            return raw_penalty
        return None
    return None


def _coerce_report_path(report_path: str | PathLike[str] | None) -> Path | None:
    if report_path is None or (
        isinstance(report_path, str) and not report_path.strip()
    ):
        return None
    return Path(report_path)


__all__ = [
    "AnalysisPresentation",
    "WarningDisplayItem",
    "build_analysis_presentation",
]
