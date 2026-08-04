from pathlib import Path

import pytest

from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning
from model_health_assistant.report_writer import (
    build_report_path,
    load_report_json,
    report_to_dict,
    save_report_json,
)


def test_build_report_path_for_saved_document(tmp_path) -> None:
    path = build_report_path(
        "Mechanical-EM Coupler",
        str(tmp_path / "Mechanical-EM Coupler.FCStd"),
        "2026-06-28T22:15:00+00:00",
    )

    assert path == (
        tmp_path
        / "model_health_reports"
        / "Mechanical-EM_Coupler_model_health_2026-06-28_221500.json"
    )


def test_build_report_path_for_unsaved_document() -> None:
    path = build_report_path(
        "Untitled document",
        generated_at="2026-06-28T22:15:00+00:00",
    )

    assert path == (
        Path.home()
        / ".model_health_assistant"
        / "model_health_reports"
        / "Untitled_document_model_health_2026-06-28_221500.json"
    )


def test_report_to_dict_includes_schema_and_transparent_summary_counts() -> None:
    categories = (
        "sketch",
        "additive_feature",
        "subtractive_feature",
        "finishing_feature",
        "pattern_feature",
        "reference_geometry",
        "container",
        "other",
    )
    warnings = [
        RiskWarning(
            warning_id=f"TEST-{index}",
            severity=severity,
            affected_feature_name="Sketch001",
            message="Example warning",
            technical_reason="Example reason",
            suggested_practice="Example practice",
            rule_id=f"example_{severity}_rule",
        )
        for index, severity in enumerate(("low", "medium", "high"), start=1)
    ]
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-06-26T00:00:00+00:00",
        health_score=80,
        features=[
            FeatureInfo(
                internal_name=f"Feature{index}",
                category=category,
                is_sketch=category == "sketch",
            )
            for index, category in enumerate(categories, start=1)
        ],
        warnings=warnings,
    )

    data = report_to_dict(report)

    assert data["schema_version"] == "0.3"
    assert data["document"] == {"name": "Demo"}
    assert data["generated_at"] == "2026-06-26T00:00:00+00:00"

    # Legacy keys remain available for existing report consumers.
    assert data["document_name"] == "Demo"
    assert data["timestamp"] == "2026-06-26T00:00:00+00:00"
    assert data["total_feature_count"] == 8

    assert data["total_document_object_count"] == 8
    assert data["meaningful_modeling_feature_count"] == 4
    assert data["total_sketch_count"] == 1
    assert data["category_counts"] == {
        "sketch": 1,
        "additive_feature": 1,
        "subtractive_feature": 1,
        "finishing_feature": 1,
        "pattern_feature": 1,
        "reference_geometry": 1,
        "container": 1,
        "other": 1,
    }
    assert data["total_warning_count"] == 3
    assert data["warning_counts_by_severity"] == {
        "low": 1,
        "medium": 1,
        "high": 1,
    }
    assert data["health_score_breakdown"] == {}


def test_save_and_load_report_json(tmp_path) -> None:
    warning = RiskWarning(
        warning_id="TEST-001",
        severity="medium",
        affected_feature_name="Sketch001",
        message="Example warning",
        technical_reason="Example reason",
        suggested_practice="Example practice",
        rule_id="example_rule",
    )
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-06-26T00:00:00+00:00",
        health_score=90,
        features=[
            FeatureInfo(
                internal_name="Sketch001",
                category="sketch",
                is_sketch=True,
                dependency_names=["Pad001"],
                downstream_dependent_names=["Pocket001"],
                attached_to_generated_geometry=True,
                support_name="Pad001",
                support_kind="generated_geometry",
                support_subelement="Face3",
                support_is_stable_reference=False,
                support_confidence="high",
            )
        ],
        warnings=[warning],
        notes=["Local-only analysis."],
        health_score_breakdown={"starting_score": 100, "final_score": 90},
    )

    output_path = save_report_json(report, tmp_path / "report.json")
    loaded = load_report_json(output_path)

    assert output_path.exists()
    assert loaded["schema_version"] == "0.3"
    assert loaded["document"] == {"name": "Demo"}
    assert loaded["generated_at"] == "2026-06-26T00:00:00+00:00"
    assert loaded["document_name"] == "Demo"
    assert loaded["timestamp"] == "2026-06-26T00:00:00+00:00"
    assert loaded["total_document_object_count"] == 1
    assert loaded["meaningful_modeling_feature_count"] == 0
    assert loaded["category_counts"]["sketch"] == 1
    assert loaded["total_warning_count"] == 1
    assert loaded["warning_counts_by_severity"] == {
        "low": 0,
        "medium": 1,
        "high": 0,
    }
    assert loaded["features"][0]["internal_name"] == "Sketch001"
    assert loaded["features"][0]["category"] == "sketch"
    assert loaded["features"][0]["dependency_names"] == ["Pad001"]
    assert loaded["features"][0]["downstream_dependent_names"] == ["Pocket001"]
    assert loaded["features"][0]["direct_upstream_count"] == 1
    assert loaded["features"][0]["direct_downstream_count"] == 1
    assert loaded["features"][0]["support_name"] == "Pad001"
    assert loaded["features"][0]["support_kind"] == "generated_geometry"
    assert loaded["features"][0]["support_subelement"] == "Face3"
    assert loaded["features"][0]["support_confidence"] == "high"
    assert loaded["warnings"][0]["severity"] == "medium"
    assert loaded["warnings"][0]["rule_id"] == "example_rule"
    assert loaded["health_score_breakdown"] == {
        "starting_score": 100,
        "final_score": 90,
    }


def test_report_json_preserves_non_ascii_text_as_utf8(tmp_path) -> None:
    document_name = "Mécanisme_日本語"
    report = ModelHealthReport(
        document_name=document_name,
        generated_at="2026-06-26T00:00:00+00:00",
        health_score=100,
    )

    output_path = save_report_json(report, tmp_path / "unicode-report.json")
    serialized = output_path.read_text(encoding="utf-8")

    assert document_name in serialized
    assert load_report_json(output_path)["document_name"] == document_name


def test_report_writer_rejects_arbitrary_mapping_before_creating_a_file(
    tmp_path,
) -> None:
    output_path = tmp_path / "reports" / "unsafe.json"
    untrusted_mapping = {"api_key": "UNIT_TEST_SECRET_DO_NOT_LOG"}

    with pytest.raises(TypeError, match="ModelHealthReport"):
        save_report_json(untrusted_mapping, output_path)

    assert output_path.exists() is False
