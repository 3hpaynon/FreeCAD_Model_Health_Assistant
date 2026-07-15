from model_health_assistant.models import FeatureInfo, RiskWarning
from model_health_assistant.risk_engine import (
    calculate_health_score,
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
    features = [FeatureInfo(internal_name="Sketch001", is_sketch=True)]
    warnings = [
        RiskWarning(
            warning_id="TEST-001",
            severity="high",
            affected_feature_name="Sketch001",
            message="Test warning",
            technical_reason="Testing high severity penalty.",
            suggested_practice="Fix the modeled issue.",
        )
    ]

    assert calculate_health_score(features, []) == 100
    assert calculate_health_score(features, warnings) < 100


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
