"""Local, deterministic rules for model-health analysis."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

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


def analyze_model_health(
    features: Sequence[FeatureInfo],
    document_name: str = "Untitled",
) -> ModelHealthReport:
    """Run all local rules and return a model-health report."""

    feature_list = list(features)
    warnings = run_all_rules(feature_list)
    score = calculate_health_score(feature_list, warnings)

    return ModelHealthReport(
        document_name=document_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        health_score=score,
        features=feature_list,
        warnings=warnings,
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
    return warnings


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
            )
        )
    return warnings


def rule_feature_recompute_error_placeholder(
    features: Sequence[FeatureInfo],
) -> list[RiskWarning]:
    """Backward-compatible alias for the confirmed-error rule."""

    return rule_confirmed_feature_error(features)


def rule_early_fillet_or_chamfer(features: Sequence[FeatureInfo]) -> list[RiskWarning]:
    """Warn when a fillet or chamfer appears early and has downstream dependents."""

    warnings: list[RiskWarning] = []
    if not features:
        return warnings

    midpoint = max(1, len(features) // 2)
    for index, feature in enumerate(features):
        if not _is_fillet_or_chamfer(feature):
            continue
        if index >= midpoint or not feature.downstream_dependent_names:
            continue

        warnings.append(
            RiskWarning(
                warning_id="MHA-FEATURE-ORDER-001",
                severity="medium",
                affected_feature_name=feature.internal_name,
                message=f"{feature.internal_name} is an early fillet/chamfer with later dependents.",
                technical_reason=(
                    "Fillets and chamfers often reference generated edges. Placing them early "
                    "can make many later features depend on unstable topology."
                ),
                suggested_practice=(
                    "Consider adding fillets and chamfers near the end of the feature tree unless "
                    "they are part of the core design intent."
                ),
                rule_name="rule_early_fillet_or_chamfer",
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
            )
        )
    return warnings


def calculate_health_score(
    features: Sequence[FeatureInfo],
    warnings: Sequence[RiskWarning],
) -> int:
    """Calculate a simple 0-100 health score from warning severity."""

    if not features and not warnings:
        return 100

    penalties = {"low": 5, "medium": 10, "high": 20}
    total_penalty = sum(penalties[warning.severity] for warning in warnings)
    return max(0, min(100, 100 - total_penalty))


def _is_fillet_or_chamfer(feature: FeatureInfo) -> bool:
    text = " ".join(
        [feature.internal_name, feature.label, feature.type_id]
    ).lower()
    return "fillet" in text or "chamfer" in text
