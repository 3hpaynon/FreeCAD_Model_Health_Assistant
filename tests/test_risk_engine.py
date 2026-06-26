from model_health_assistant.models import FeatureInfo, RiskWarning
from model_health_assistant.risk_engine import (
    calculate_health_score,
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
        )
    ]

    warnings = rule_sketch_attached_to_generated_geometry(features)

    assert len(warnings) == 1
    assert warnings[0].severity == "high"
    assert warnings[0].affected_feature_name == "Sketch003"
    assert "datum plane" in warnings[0].suggested_practice


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
