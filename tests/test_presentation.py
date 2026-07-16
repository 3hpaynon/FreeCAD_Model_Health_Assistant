from pathlib import Path

import pytest

from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning
from model_health_assistant.presentation import build_analysis_presentation


def _warning(
    warning_id: str,
    severity: str,
    feature_name: str,
    rule_id: str,
) -> RiskWarning:
    return RiskWarning(
        warning_id=warning_id,
        severity=severity,  # type: ignore[arg-type]
        affected_feature_name=feature_name,
        message=f"Message for {warning_id}",
        technical_reason=f"Reason for {warning_id}",
        suggested_practice=f"Practice for {warning_id}",
        rule_id=rule_id,
    )


def test_build_presentation_exposes_summary_counts_and_existing_warning_data() -> None:
    warning = _warning("WARN-001", "high", "Pocket001", "confirmed_error")
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=72,
        features=[
            FeatureInfo(
                internal_name="Sketch001",
                label="Profile",
                category="sketch",
                is_sketch=True,
            ),
            FeatureInfo(
                internal_name="Pocket001",
                label="Drive pocket",
                category="subtractive_feature",
                has_confirmed_error=True,
                error_details=["State: Invalid"],
                metadata={"shape_valid": False},
            ),
            FeatureInfo(internal_name="Body", category="container"),
        ],
        warnings=[warning],
        health_score_breakdown={
            "warning_penalties": [
                {
                    "warning_id": "WARN-001",
                    "rule_id": "confirmed_error",
                    "affected_feature_name": "Pocket001",
                    "raw_penalty": 25,
                }
            ]
        },
    )

    presentation = build_analysis_presentation(report)

    assert presentation.document_name == "Demo"
    assert presentation.analysis_timestamp == "2026-07-15T12:30:00+00:00"
    assert presentation.total_document_object_count == 3
    assert presentation.meaningful_modeling_feature_count == 1
    assert presentation.total_sketch_count == 1
    assert presentation.total_warning_count == 1
    assert presentation.high_warning_count == 1
    assert presentation.medium_warning_count == 0
    assert presentation.low_warning_count == 0
    assert presentation.health_score == 72

    item = presentation.warning_items[0]
    assert item.warning_id == "WARN-001"
    assert item.rule_id == "confirmed_error"
    assert item.feature_internal_name == "Pocket001"
    assert item.feature_label == "Drive pocket"
    assert item.message == "Message for WARN-001"
    assert item.technical_reason == "Reason for WARN-001"
    assert item.suggested_practice == "Practice for WARN-001"
    assert item.evidence == {
        "has_confirmed_error": True,
        "error_details": ["State: Invalid"],
        "metadata": {"shape_valid": False},
    }
    assert item.raw_score_penalty == 25


def test_warning_order_is_severity_first_and_stable_within_each_severity() -> None:
    warnings = [
        _warning("LOW-1", "low", "Feature1", "low_rule"),
        _warning("HIGH-1", "high", "Feature2", "high_rule"),
        _warning("MEDIUM-1", "medium", "Feature3", "medium_rule"),
        _warning("HIGH-2", "high", "Feature4", "high_rule"),
        _warning("LOW-2", "low", "Feature5", "low_rule"),
    ]
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=80,
        warnings=warnings,
    )

    presentation = build_analysis_presentation(report)

    assert presentation.total_warning_count == 5
    assert presentation.high_warning_count == 2
    assert presentation.medium_warning_count == 1
    assert presentation.low_warning_count == 2
    assert [item.warning_id for item in presentation.warning_items] == [
        "HIGH-1",
        "HIGH-2",
        "MEDIUM-1",
        "LOW-1",
        "LOW-2",
    ]
    assert report.warnings == warnings


def test_empty_warning_report_produces_zero_counts_and_no_items() -> None:
    report = ModelHealthReport(
        document_name="Empty",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=100,
    )

    presentation = build_analysis_presentation(report)

    assert presentation.total_warning_count == 0
    assert presentation.high_warning_count == 0
    assert presentation.medium_warning_count == 0
    assert presentation.low_warning_count == 0
    assert presentation.warning_items == ()


def test_missing_optional_warning_data_remains_unavailable() -> None:
    warning = _warning("WARN-001", "medium", "MissingFeature", "example_rule")
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="",
        health_score=90,
        warnings=[warning],
        health_score_breakdown={
            "warning_penalties": [
                {
                    "warning_id": "OTHER-WARNING",
                    "rule_id": "example_rule",
                    "affected_feature_name": "MissingFeature",
                    "raw_penalty": 5,
                }
            ]
        },
    )

    presentation = build_analysis_presentation(report, "")
    item = presentation.warning_items[0]

    assert presentation.analysis_timestamp is None
    assert presentation.report_path is None
    assert item.feature_label is None
    assert item.evidence is None
    assert item.raw_score_penalty is None


@pytest.mark.parametrize(
    ("supplied_path", "expected_path"),
    [
        (Path("reports/report.json"), Path("reports/report.json")),
        ("reports/report.json", Path("reports/report.json")),
        (None, None),
    ],
)
def test_report_path_is_normalized_without_filesystem_access(
    supplied_path: str | Path | None,
    expected_path: Path | None,
) -> None:
    report = ModelHealthReport(
        document_name="Demo",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=100,
    )

    presentation = build_analysis_presentation(report, supplied_path)

    assert presentation.report_path == expected_path
