from pathlib import Path

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


def test_report_to_dict_includes_summary_counts() -> None:
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-06-26T00:00:00+00:00",
        health_score=80,
        features=[FeatureInfo(internal_name="Sketch001", is_sketch=True)],
        warnings=[],
    )

    data = report_to_dict(report)

    assert data["document_name"] == "Demo"
    assert data["total_feature_count"] == 1
    assert data["total_sketch_count"] == 1
    assert data["total_warning_count"] == 0


def test_save_and_load_report_json(tmp_path) -> None:
    warning = RiskWarning(
        warning_id="TEST-001",
        severity="medium",
        affected_feature_name="Sketch001",
        message="Example warning",
        technical_reason="Example reason",
        suggested_practice="Example practice",
    )
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-06-26T00:00:00+00:00",
        health_score=90,
        features=[FeatureInfo(internal_name="Sketch001", is_sketch=True)],
        warnings=[warning],
        notes=["Local-only analysis."],
    )

    output_path = save_report_json(report, tmp_path / "report.json")
    loaded = load_report_json(output_path)

    assert output_path.exists()
    assert loaded["document_name"] == "Demo"
    assert loaded["total_warning_count"] == 1
    assert loaded["features"][0]["internal_name"] == "Sketch001"
    assert loaded["warnings"][0]["severity"] == "medium"
