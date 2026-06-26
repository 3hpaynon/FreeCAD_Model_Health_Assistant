from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning
from model_health_assistant.report_writer import load_report_json, report_to_dict, save_report_json


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
