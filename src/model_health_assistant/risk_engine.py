"""Local, deterministic rules for model-health analysis."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from model_health_assistant import __version__
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning

Rule = Callable[[Sequence[FeatureInfo]], list[RiskWarning]]

_DEPENDENCY_RELEVANT_CATEGORIES = {
    "sketch",
    "additive_feature",
    "subtractive_feature",
    "finishing_feature",
    "pattern_feature",
}

_FIXED_RULE_PENALTIES = {
    "confirmed_recompute_error": 25,
    "sketch_generated_geometry_support": 8,
    "early_finishing_feature": 5,
    "underdefined_sketch": 4,
    "unknown_sketch_support": 0,
    "hardcoded_repeated_dimensions": 0,
}
_RULE_PENALTY_CAPS: dict[str, int | None] = {
    "confirmed_recompute_error": None,
    "sketch_generated_geometry_support": 32,
    "early_finishing_feature": 20,
    "underdefined_sketch": 20,
    "high_downstream_dependency_count": 16,
    "unknown_sketch_support": 0,
    "hardcoded_repeated_dimensions": 0,
}
_RULE_PENALTY_REASONS = {
    "confirmed_recompute_error": (
        "Confirmed FreeCAD failure; its base penalty is uncapped and never diluted by model size."
    ),
    "sketch_generated_geometry_support": "High-confidence generated-topology dependency.",
    "early_finishing_feature": "Graph-confirmed early finishing-operation caution.",
    "underdefined_sketch": "Sketch metadata indicates possible parametric instability.",
    "high_downstream_dependency_count": "Dependency centrality; impact information only.",
    "unknown_sketch_support": "Informational only because support evidence is insufficient.",
    "hardcoded_repeated_dimensions": "Unscored while the rule remains placeholder-only.",
}


def analyze_model_health(
    features: Sequence[FeatureInfo],
    document_name: str = "Untitled",
) -> ModelHealthReport:
    """Run all local rules and return a model-health report."""

    feature_list = list(features)
    warnings = run_all_rules(feature_list)
    score_breakdown = calculate_health_score_breakdown(feature_list, warnings)
    score = int(score_breakdown["final_score"])

    return ModelHealthReport(
        document_name=document_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        health_score=score,
        features=feature_list,
        warnings=warnings,
        health_score_breakdown=score_breakdown,
        notes=[
            "Local-only analysis. No CAD data was uploaded.",
            "Deterministic rules only; no AI or machine learning was used.",
        ],
        metadata={"local_only": True, "engine_version": __version__},
    )


def run_all_rules(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Run every deterministic local rule in a stable order."""

    rules: list[Rule] = [
        rule_sketch_attached_to_generated_geometry,
        rule_underdefined_sketch_placeholder,
        rule_confirmed_feature_error,
        rule_early_fillet_or_chamfer,
        rule_high_downstream_dependency_count,
        rule_missing_sketch_support,
        rule_hardcoded_repeated_dimensions_placeholder,
    ]

    warnings: list[RiskWarning] = []
    for rule in rules:
        warnings.extend(rule(features))
    return deduplicate_warnings(warnings)


def deduplicate_warnings(warnings: Sequence[RiskWarning]) -> list[RiskWarning]:
    """Keep the first warning for each stable rule ID and affected feature."""

    unique_warnings: list[RiskWarning] = []
    seen: set[tuple[str, str]] = set()
    for warning in warnings:
        rule_identity = (
            warning.rule_id or warning.warning_id or warning.rule_name or warning.message
        )
        identity = (rule_identity, warning.affected_feature_name)
        if identity in seen:
            continue
        seen.add(identity)
        unique_warnings.append(warning)
    return unique_warnings


def rule_sketch_attached_to_generated_geometry(
    features: Sequence[FeatureInfo],
) -> list[RiskWarning]:
    """Warn for confirmed sketch support on a generated face, edge, or vertex."""

    warnings: list[RiskWarning] = []
    for feature in features:
        support_subelement = feature.support_subelement or ""
        has_generated_support = (
            feature.is_sketch
            and feature.support_kind == "generated_geometry"
            and feature.support_confidence == "high"
            and not feature.support_is_stable_reference
            and bool(feature.support_name)
            and support_subelement.lower().startswith(("face", "edge", "vertex"))
        )
        if not has_generated_support:
            continue

        warnings.append(
            RiskWarning(
                warning_id="MHA-SKETCH-SUPPORT-001",
                severity="medium",
                affected_feature_name=feature.internal_name,
                message=(
                    f"{feature.internal_name} appears to depend on generated geometry from "
                    f"{support_subelement} of {feature.support_name}. Changes to upstream "
                    "geometry may invalidate or remap this reference."
                ),
                technical_reason=(
                    "The sketch support is an explicit generated face, edge, or vertex. Such "
                    "topological references can change when upstream features are edited."
                ),
                suggested_practice=(
                    "Attach sketches to a datum plane, origin plane, or other stable reference "
                    "instead of generated model geometry."
                ),
                rule_name="rule_sketch_attached_to_generated_geometry",
                rule_id="sketch_generated_geometry_support",
            )
        )
    return warnings


def rule_underdefined_sketch_placeholder(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Warn when available sketch metadata suggests the sketch is underdefined."""

    warnings: list[RiskWarning] = []
    for feature in features:
        if not feature.is_sketch:
            continue

        fully_constrained = feature.metadata.get("fully_constrained")
        constraint_status = str(feature.metadata.get("constraint_status", "")).lower()
        appears_underdefined = fully_constrained is False or "under" in constraint_status

        if appears_underdefined:
            warnings.append(
                RiskWarning(
                    warning_id="MHA-SKETCH-CONSTRAINT-001",
                    severity="medium",
                    affected_feature_name=feature.internal_name,
                    message=f"{feature.internal_name} may be underdefined.",
                    technical_reason=(
                        "Underdefined sketches can move unexpectedly when dimensions or "
                        "upstream references change."
                    ),
                    suggested_practice=(
                        "Fully constrain important sketches with stable dimensions and "
                        "geometric constraints."
                    ),
                    rule_name="rule_underdefined_sketch_placeholder",
                    rule_id="underdefined_sketch",
                )
            )
    return warnings


def rule_confirmed_feature_error(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Warn only when extraction found confirmed FreeCAD error evidence."""

    warnings: list[RiskWarning] = []
    for feature in features:
        if not feature.has_confirmed_error:
            continue

        error_detail = "; ".join(feature.error_details) or "FreeCAD reported an error state."
        warnings.append(
            RiskWarning(
                warning_id="MHA-FEATURE-ERROR-001",
                severity="high",
                affected_feature_name=feature.internal_name,
                message=(
                    f"{feature.internal_name} reports a confirmed FreeCAD error: {error_detail}"
                ),
                technical_reason=(
                    "FreeCAD exposed an explicit error state or invalid non-null shape for this "
                    "object. Confirmed failures can invalidate downstream geometry."
                ),
                suggested_practice=(
                    "Resolve recompute errors from the earliest failing feature before editing "
                    "later operations."
                ),
                rule_name="rule_confirmed_feature_error",
                rule_id="confirmed_recompute_error",
            )
        )
    return warnings


def rule_feature_recompute_error_placeholder(
    features: Sequence[FeatureInfo],
) -> list[RiskWarning]:
    """Backward-compatible alias for the confirmed-error rule."""

    return rule_confirmed_feature_error(features)


def rule_early_fillet_or_chamfer(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Warn when meaningful downstream modeling directly depends on a fillet/chamfer."""

    warnings: list[RiskWarning] = []
    features_by_name = {
        feature.internal_name: feature for feature in features if feature.internal_name
    }

    for feature in features:
        if not _is_fillet_or_chamfer(feature):
            continue

        meaningful_downstream_names: list[str] = []
        for dependent_name in feature.downstream_dependent_names:
            dependent = features_by_name.get(dependent_name)
            if (
                dependent is None
                or dependent is feature
                or dependent.category not in _DEPENDENCY_RELEVANT_CATEGORIES
                or dependent_name in meaningful_downstream_names
            ):
                continue
            meaningful_downstream_names.append(dependent_name)

        downstream_count = len(meaningful_downstream_names)
        if downstream_count == 0:
            continue

        dependent_label = "feature" if downstream_count == 1 else "features"
        warnings.append(
            RiskWarning(
                warning_id="MHA-FEATURE-ORDER-001",
                severity="medium",
                affected_feature_name=feature.internal_name,
                message=(
                    f"{feature.internal_name} has {downstream_count} direct downstream modeling "
                    f"{dependent_label}. Early finishing operations can increase fragility when "
                    "later features depend on modified edge topology."
                ),
                technical_reason=(
                    "The dependency graph confirms that later modeling operations directly use "
                    "this fillet or chamfer. Changes to its generated edges may affect those "
                    "operations."
                ),
                suggested_practice=(
                    "Consider adding fillets and chamfers near the end of the feature tree unless "
                    "they are part of the core design intent."
                ),
                rule_name="rule_early_fillet_or_chamfer",
                rule_id="early_finishing_feature",
            )
        )
    return warnings


def rule_high_downstream_dependency_count(
    features: Sequence[FeatureInfo],
    threshold: int = 5,
) -> list[RiskWarning]:
    """Identify meaningful features with many direct downstream dependents."""

    warnings: list[RiskWarning] = []
    for feature in features:
        downstream_count = feature.direct_downstream_count
        if (
            feature.category not in _DEPENDENCY_RELEVANT_CATEGORIES
            or downstream_count < threshold
        ):
            continue

        severity = "medium" if downstream_count >= threshold * 2 else "low"
        warnings.append(
            RiskWarning(
                warning_id="MHA-HIGH-IMPACT-001",
                severity=severity,
                affected_feature_name=feature.internal_name,
                message=(
                    f"{feature.internal_name} has {downstream_count} direct downstream "
                    "dependents. Changes to this feature may affect a large portion of the model."
                ),
                technical_reason=(
                    "This feature is directly referenced by many document objects. This is a "
                    "dependency-centrality observation, not evidence that the feature is modeled "
                    "incorrectly."
                ),
                suggested_practice=(
                    "Review downstream impact before changing this feature, and keep its design "
                    "intent and references clear and stable."
                ),
                rule_name="rule_high_downstream_dependency_count",
                rule_id="high_downstream_dependency_count",
            )
        )
    return warnings


def rule_missing_sketch_support(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Warn when sketch support could not be identified."""

    warnings: list[RiskWarning] = []
    for feature in features:
        if not feature.is_sketch:
            continue
        support_is_known = bool(feature.support_name) and feature.support_kind not in {
            None,
            "",
            "unknown",
        }
        if support_is_known:
            continue

        warnings.append(
            RiskWarning(
                warning_id="MHA-SKETCH-SUPPORT-UNKNOWN-001",
                severity="low",
                affected_feature_name=feature.internal_name,
                message=f"{feature.internal_name} has missing or unknown support information.",
                technical_reason=(
                    "The extractor could not determine whether the sketch is attached to a "
                    "stable reference or generated geometry."
                ),
                suggested_practice=(
                    "Verify the sketch attachment in FreeCAD and prefer datum or origin planes "
                    "for important references."
                ),
                rule_name="rule_missing_sketch_support",
                rule_id="unknown_sketch_support",
            )
        )
    return warnings


def rule_hardcoded_repeated_dimensions_placeholder(
    features: Sequence[FeatureInfo],
) -> list[RiskWarning]:
    """Placeholder rule for future dimension-pattern analysis.

    The extractor does not parse dimension expressions deeply. If a future
    extractor adds repeated-dimension metadata, this rule will already have a
    reporting path.
    """

    warnings: list[RiskWarning] = []
    for feature in features:
        repeated_values = feature.metadata.get("repeated_dimension_values")
        try:
            hardcoded_count = int(feature.metadata.get("hardcoded_dimension_count", 0) or 0)
        except (TypeError, ValueError):
            hardcoded_count = 0
        if not repeated_values and hardcoded_count < 3:
            continue

        warnings.append(
            RiskWarning(
                warning_id="MHA-DIMENSIONS-PLACEHOLDER-001",
                severity="low",
                affected_feature_name=feature.internal_name,
                message=f"{feature.internal_name} may contain repeated hardcoded dimensions.",
                technical_reason=(
                    "Repeated manual dimensions can drift apart when design intent changes."
                ),
                suggested_practice=(
                    "Consider expressions, named constraints, or spreadsheet-driven parameters "
                    "for repeated design values."
                ),
                rule_name="rule_hardcoded_repeated_dimensions_placeholder",
                rule_id="hardcoded_repeated_dimensions",
            )
        )
    return warnings


def calculate_health_score(
    features: Sequence[FeatureInfo],
    warnings: Sequence[RiskWarning],
) -> int:
    """Calculate the approved transparent 0-100 rule-based health score."""

    return int(calculate_health_score_breakdown(features, warnings)["final_score"])


def calculate_health_score_breakdown(
    features: Sequence[FeatureInfo],
    warnings: Sequence[RiskWarning],
) -> dict[str, Any]:
    """Return every component used by the approved health-score formula."""

    feature_list = list(features)
    warning_list = deduplicate_warnings(warnings)
    warning_penalties: list[dict[str, Any]] = []
    raw_totals_by_rule: dict[str, int] = {}

    for warning in warning_list:
        rule_id = warning.rule_id or "unidentified_rule"
        raw_penalty = _raw_warning_penalty(warning)
        raw_totals_by_rule[rule_id] = raw_totals_by_rule.get(rule_id, 0) + raw_penalty
        warning_penalties.append(
            {
                "rule_id": rule_id,
                "warning_id": warning.warning_id,
                "affected_feature_name": warning.affected_feature_name,
                "severity": warning.severity,
                "raw_penalty": raw_penalty,
                "reason": _RULE_PENALTY_REASONS.get(
                    rule_id,
                    "Unrecognized rules are unscored until assigned a reviewed weight.",
                ),
            }
        )

    rule_penalty_totals: list[dict[str, Any]] = []
    applied_rule_penalty = 0
    for rule_id, raw_penalty in raw_totals_by_rule.items():
        cap = _RULE_PENALTY_CAPS.get(rule_id)
        applied_penalty = raw_penalty if cap is None else min(raw_penalty, cap)
        applied_rule_penalty += applied_penalty
        rule_penalty_totals.append(
            {
                "rule_id": rule_id,
                "raw_penalty": raw_penalty,
                "cap": cap,
                "applied_penalty": applied_penalty,
            }
        )

    analyzable_features = [
        feature
        for feature in feature_list
        if feature.category in _DEPENDENCY_RELEVANT_CATEGORIES
    ]
    analyzable_names = {
        feature.internal_name for feature in analyzable_features if feature.internal_name
    }
    affected_names = {
        entry["affected_feature_name"]
        for entry in warning_penalties
        if entry["raw_penalty"] > 0 and entry["affected_feature_name"] in analyzable_names
    }
    analyzable_count = len(analyzable_features)
    affected_count = len(affected_names)
    prevalence_ratio = affected_count / max(1, analyzable_count)
    prevalence_penalty = min(10, int((10 * prevalence_ratio) + 0.5))

    starting_score = 100
    total_penalty = applied_rule_penalty + prevalence_penalty
    final_score = max(0, min(100, starting_score - total_penalty))

    return {
        "formula_version": "1.0",
        "method": "rule_weighted_with_bounded_prevalence",
        "starting_score": starting_score,
        "warning_penalties": warning_penalties,
        "rule_penalty_totals": rule_penalty_totals,
        "analyzable_feature_count": analyzable_count,
        "affected_feature_count": affected_count,
        "prevalence_ratio": round(prevalence_ratio, 6),
        "prevalence_penalty": prevalence_penalty,
        "total_penalty": total_penalty,
        "final_score": final_score,
        "notes": [
            "Deterministic heuristic; not a statistical prediction.",
            "Containers, reference geometry, and other objects are excluded from prevalence.",
            "Unrecognized and explicitly informational rules receive zero points.",
        ],
    }


def _raw_warning_penalty(warning: RiskWarning) -> int:
    if warning.rule_id == "high_downstream_dependency_count":
        return 4 if warning.severity in {"medium", "high"} else 2
    return _FIXED_RULE_PENALTIES.get(warning.rule_id, 0)


def _is_fillet_or_chamfer(feature: FeatureInfo) -> bool:
    if feature.category != "finishing_feature":
        return False
    type_name = feature.type_id.rsplit("::", 1)[-1].lower()
    return type_name in {"fillet", "chamfer"}
