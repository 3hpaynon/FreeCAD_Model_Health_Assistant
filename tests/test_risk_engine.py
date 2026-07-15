from model_health_assistant.models import FeatureInfo, RiskWarning
from model_health_assistant.risk_engine import (
    analyze_model_health,
    calculate_health_score,
    calculate_health_score_breakdown,
    run_all_rules,
    rule_confirmed_feature_error,
    rule_early_fillet_or_chamfer,
    rule_high_downstream_dependency_count,
    rule_sketch_attached_to_generated_geometry,
)


def test_sketch_attached_to_generated_geometry_produces_warning() -> None:
    features = [
        FeatureInfo(
            internal_name="Sketch003",
            label="Mounting sketch",
            type_id="Sketcher::SketchObject",
            is_sketch=True,
            attached_to_generated_geometry=True,
            support_name="Pad001",
            support_kind="generated_geometry",
            support_subelement="Face3",
            support_is_stable_reference=False,
            support_confidence="high",
        )
    ]

    warnings = rule_sketch_attached_to_generated_geometry(features)

    assert len(warnings) == 1
    assert warnings[0].severity == "medium"
    assert warnings[0].affected_feature_name == "Sketch003"
    assert warnings[0].rule_id == "sketch_generated_geometry_support"
    assert warnings[0].message == (
        "Sketch003 appears to depend on generated geometry from Face3 of Pad001. "
        "Changes to upstream geometry may invalidate or remap this reference."
    )
    assert "datum plane" in warnings[0].suggested_practice


def test_stable_and_unknown_sketch_supports_do_not_produce_generated_geometry_warning() -> None:
    features = [
        FeatureInfo(
            internal_name="OriginSketch",
            is_sketch=True,
            support_name="XY_Plane",
            support_kind="origin_plane",
            support_is_stable_reference=True,
            support_confidence="high",
        ),
        FeatureInfo(
            internal_name="DatumSketch",
            is_sketch=True,
            support_name="DatumPlane",
            support_kind="datum_plane",
            support_is_stable_reference=True,
            support_confidence="high",
        ),
        FeatureInfo(
            internal_name="UnknownSketch",
            is_sketch=True,
            support_kind="unknown",
            support_confidence="low",
        ),
        FeatureInfo(
            internal_name="LegacyFlagSketch",
            is_sketch=True,
            attached_to_generated_geometry=True,
            support_name="Pad001",
            support_kind="unknown",
            support_confidence="low",
        ),
        FeatureInfo(
            internal_name="UncertainGeneratedSketch",
            is_sketch=True,
            support_name="Pad001",
            support_kind="generated_geometry",
            support_subelement="Face3",
            support_confidence="low",
        ),
    ]

    assert rule_sketch_attached_to_generated_geometry(features) == []


def test_confirmed_feature_error_produces_high_severity_warning() -> None:
    feature = FeatureInfo(
        internal_name="Pocket001",
        has_confirmed_error=True,
        error_details=["State: Invalid"],
    )

    warnings = rule_confirmed_feature_error([feature])

    assert len(warnings) == 1
    assert warnings[0].severity == "high"
    assert warnings[0].message == (
        "Pocket001 reports a confirmed FreeCAD error: State: Invalid"
    )
    assert warnings[0].rule_name == "rule_confirmed_feature_error"
    assert warnings[0].rule_id == "confirmed_recompute_error"


def test_unconfirmed_legacy_error_text_does_not_produce_error_warning() -> None:
    feature = FeatureInfo(
        internal_name="Pocket001",
        recompute_status="error",
        error_status="Invalid selection",
        has_confirmed_error=False,
    )

    assert rule_confirmed_feature_error([feature]) == []


def test_fillet_with_meaningful_direct_dependents_produces_warning() -> None:
    fillet = FeatureInfo(
        internal_name="Fillet001",
        type_id="PartDesign::Fillet",
        category="finishing_feature",
        downstream_dependent_names=["Body", "Pocket001", "Sketch002", "XY_Plane"],
    )
    features = [
        fillet,
        FeatureInfo(internal_name="Body", category="container"),
        FeatureInfo(internal_name="Pocket001", category="subtractive_feature"),
        FeatureInfo(internal_name="Sketch002", category="sketch", is_sketch=True),
        FeatureInfo(internal_name="XY_Plane", category="reference_geometry"),
    ]

    warnings = rule_early_fillet_or_chamfer(features)

    assert len(warnings) == 1
    assert warnings[0].severity == "medium"
    assert warnings[0].rule_id == "early_finishing_feature"
    assert warnings[0].message == (
        "Fillet001 has 2 direct downstream modeling features. Early finishing operations can "
        "increase fragility when later features depend on modified edge topology."
    )


def test_chamfer_with_one_meaningful_direct_dependent_produces_one_warning() -> None:
    chamfer = FeatureInfo(
        internal_name="Chamfer001",
        type_id="PartDesign::Chamfer",
        category="finishing_feature",
        downstream_dependent_names=["Pad002", "Pad002"],
    )
    pad = FeatureInfo(internal_name="Pad002", category="additive_feature")

    warnings = rule_early_fillet_or_chamfer([chamfer, pad])

    assert len(warnings) == 1
    assert "1 direct downstream modeling feature." in warnings[0].message


def test_fillet_without_meaningful_direct_dependents_does_not_warn() -> None:
    fillet = FeatureInfo(
        internal_name="Fillet001",
        type_id="PartDesign::Fillet",
        category="finishing_feature",
        downstream_dependent_names=["Body", "XY_Plane", "MissingFeature"],
    )
    body = FeatureInfo(internal_name="Body", category="container")
    plane = FeatureInfo(internal_name="XY_Plane", category="reference_geometry")

    assert rule_early_fillet_or_chamfer([fillet, body, plane]) == []


def test_fillet_name_or_other_finishing_type_does_not_trigger_rule() -> None:
    named_pad = FeatureInfo(
        internal_name="FilletLikePad",
        label="Fillet",
        type_id="PartDesign::Pad",
        category="additive_feature",
        downstream_dependent_names=["Pocket001"],
    )
    draft = FeatureInfo(
        internal_name="Draft001",
        type_id="PartDesign::Draft",
        category="finishing_feature",
        downstream_dependent_names=["Pocket001"],
    )
    pocket = FeatureInfo(internal_name="Pocket001", category="subtractive_feature")

    assert rule_early_fillet_or_chamfer([named_pad, draft, pocket]) == []


def test_health_score_decreases_for_high_severity_warning() -> None:
    features = [
        FeatureInfo(
            internal_name="Pocket001",
            category="subtractive_feature",
        )
    ]
    warnings = [
        RiskWarning(
            warning_id="TEST-001",
            severity="high",
            affected_feature_name="Pocket001",
            message="Test warning",
            technical_reason="Testing high severity penalty.",
            suggested_practice="Fix the modeled issue.",
            rule_id="confirmed_recompute_error",
        )
    ]

    assert calculate_health_score(features, []) == 100
    assert calculate_health_score(features, warnings) < 100


def test_health_score_matches_approved_model_size_examples() -> None:
    expected_scores = {5: 58, 50: 67, 500: 68}

    for feature_count, expected_score in expected_scores.items():
        features = [
            FeatureInfo(
                internal_name=f"Sketch{index}",
                category="sketch",
                is_sketch=True,
            )
            for index in range(feature_count)
        ]
        warnings = [
            RiskWarning(
                warning_id="MHA-SKETCH-SUPPORT-001",
                severity="medium",
                affected_feature_name=f"Sketch{index}",
                message="Generated support warning",
                technical_reason="Generated topology dependency.",
                suggested_practice="Use a stable reference.",
                rule_id="sketch_generated_geometry_support",
            )
            for index in range(5)
        ]

        breakdown = calculate_health_score_breakdown(features, warnings)

        assert breakdown["rule_penalty_totals"] == [
            {
                "rule_id": "sketch_generated_geometry_support",
                "raw_penalty": 40,
                "cap": 32,
                "applied_penalty": 32,
            }
        ]
        assert breakdown["final_score"] == expected_score
        assert calculate_health_score(features, warnings) == expected_score


def test_confirmed_error_penalties_are_uncapped_and_score_is_clamped() -> None:
    features = [
        FeatureInfo(
            internal_name=f"Pocket{index}",
            category="subtractive_feature",
        )
        for index in range(4)
    ]
    warnings = [
        RiskWarning(
            warning_id="MHA-FEATURE-ERROR-001",
            severity="high",
            affected_feature_name=feature.internal_name,
            message="Confirmed error",
            technical_reason="Confirmed FreeCAD failure.",
            suggested_practice="Repair the feature.",
            rule_id="confirmed_recompute_error",
        )
        for feature in features
    ]

    breakdown = calculate_health_score_breakdown(features, warnings)

    assert breakdown["rule_penalty_totals"] == [
        {
            "rule_id": "confirmed_recompute_error",
            "raw_penalty": 100,
            "cap": None,
            "applied_penalty": 100,
        }
    ]
    assert breakdown["prevalence_penalty"] == 10
    assert breakdown["total_penalty"] == 110
    assert breakdown["final_score"] == 0


def test_informational_and_unknown_rules_do_not_reduce_score() -> None:
    features = [
        FeatureInfo(internal_name="Sketch001", category="sketch", is_sketch=True)
    ]
    warnings = [
        RiskWarning(
            warning_id="MHA-SKETCH-SUPPORT-UNKNOWN-001",
            severity="low",
            affected_feature_name="Sketch001",
            message="Unknown support",
            technical_reason="Support could not be determined.",
            suggested_practice="Inspect the sketch.",
            rule_id="unknown_sketch_support",
        ),
        RiskWarning(
            warning_id="FUTURE-001",
            severity="high",
            affected_feature_name="Sketch001",
            message="Future warning",
            technical_reason="No reviewed score weight exists.",
            suggested_practice="Review manually.",
            rule_id="future_unreviewed_rule",
        ),
    ]

    breakdown = calculate_health_score_breakdown(features, warnings)

    assert [entry["raw_penalty"] for entry in breakdown["warning_penalties"]] == [0, 0]
    assert breakdown["affected_feature_count"] == 0
    assert breakdown["final_score"] == 100


def test_analyze_model_health_includes_matching_score_breakdown() -> None:
    feature = FeatureInfo(
        internal_name="Pocket001",
        category="subtractive_feature",
        has_confirmed_error=True,
        error_details=["State: Invalid"],
    )

    report = analyze_model_health([feature], document_name="Demo")

    assert report.health_score == report.health_score_breakdown["final_score"]
    assert report.health_score_breakdown["formula_version"] == "1.0"
    assert report.health_score_breakdown["starting_score"] == 100


def test_high_downstream_dependency_count_produces_warning() -> None:
    features = [
        FeatureInfo(
            internal_name="Pad001",
            type_id="PartDesign::Pad",
            category="additive_feature",
            downstream_dependent_names=[
                "Pocket001",
                "Sketch002",
                "Pad002",
                "Fillet001",
                "Chamfer001",
            ],
        )
    ]

    warnings = rule_high_downstream_dependency_count(features)

    assert len(warnings) == 1
    assert warnings[0].warning_id == "MHA-HIGH-IMPACT-001"
    assert warnings[0].rule_id == "high_downstream_dependency_count"
    assert warnings[0].severity == "low"
    assert warnings[0].message == (
        "Pad001 has 5 direct downstream dependents. Changes to this feature may affect a "
        "large portion of the model."
    )
    assert "not evidence" in warnings[0].technical_reason


def test_high_downstream_dependency_count_uses_medium_severity_at_double_threshold() -> None:
    feature = FeatureInfo(
        internal_name="MasterSketch",
        category="sketch",
        is_sketch=True,
        downstream_dependent_names=[f"Feature{index}" for index in range(10)],
    )

    warnings = rule_high_downstream_dependency_count([feature])

    assert len(warnings) == 1
    assert warnings[0].severity == "medium"


def test_high_downstream_dependency_count_ignores_nonmeaningful_objects() -> None:
    container = FeatureInfo(
        internal_name="Body",
        category="container",
        downstream_dependent_names=[f"Feature{index}" for index in range(10)],
    )
    reference = FeatureInfo(
        internal_name="XY_Plane",
        category="reference_geometry",
        downstream_dependent_names=[f"Feature{index}" for index in range(10)],
    )

    assert rule_high_downstream_dependency_count([container, reference]) == []


def test_run_all_rules_deduplicates_same_rule_but_preserves_different_rules() -> None:
    feature = FeatureInfo(
        internal_name="Sketch003",
        type_id="Sketcher::SketchObject",
        category="sketch",
        is_sketch=True,
        support_name="Pad001",
        support_kind="generated_geometry",
        support_subelement="Face3",
        support_confidence="high",
        has_confirmed_error=True,
        error_details=["State: Invalid"],
    )

    warnings = run_all_rules([feature, feature])

    assert [warning.rule_id for warning in warnings] == [
        "sketch_generated_geometry_support",
        "confirmed_recompute_error",
    ]
    assert all(warning.affected_feature_name == "Sketch003" for warning in warnings)
