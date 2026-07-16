import json
import unicodedata

from model_health_assistant.ai_context import (
    AI_CONTEXT_LIMIT_POLICY_VERSION,
    DEFAULT_AI_CONTEXT_LIMITS,
    TRUNCATION_MARKER,
    AIContextLimits,
    build_ai_request_context,
    serialize_ai_request_context,
)
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _warning(
    index: int,
    severity: str = "low",
    *,
    message: str = "Short warning message.",
    technical_reason: str = "Short technical reason.",
    suggested_practice: str = "Review the feature manually.",
) -> RiskWarning:
    return RiskWarning(
        warning_id=f"WARN-{index:03d}",
        severity=severity,  # type: ignore[arg-type]
        affected_feature_name=f"Feature{index:03d}",
        message=message,
        technical_reason=technical_reason,
        suggested_practice=suggested_practice,
        rule_name="test_rule",
        rule_id=f"test_rule_{index:03d}",
    )


def _report(
    warnings: list[RiskWarning],
    *,
    features: list[FeatureInfo] | None = None,
) -> ModelHealthReport:
    return ModelHealthReport(
        document_name="Private model",
        generated_at="2026-07-15T12:30:00+00:00",
        health_score=85,
        features=[] if features is None else features,
        warnings=warnings,
    )


def test_short_context_records_limits_without_truncation() -> None:
    context = build_ai_request_context(_report([_warning(1, "medium")]))
    serialized = serialize_ai_request_context(context)

    assert context.metadata.was_truncated is False
    assert context.metadata.truncated_field_count == 0
    assert context.metadata.original_warning_count == 1
    assert context.metadata.included_warning_count == 1
    assert context.metadata.omitted_warning_count == 0
    assert context.metadata.limit_policy_version == AI_CONTEXT_LIMIT_POLICY_VERSION
    assert context.metadata.maximum_warning_count == 50
    assert context.metadata.maximum_feature_summary_count == 50
    assert context.metadata.maximum_text_characters == 2_000
    assert context.metadata.maximum_evidence_list_items == 10
    assert context.metadata.maximum_serialized_context_characters == 64_000
    assert context.metadata.serialized_context_characters == len(serialized)


def test_warning_limit_prioritizes_severity_and_preserves_stable_order() -> None:
    warnings = (
        [_warning(index, "low") for index in range(49)]
        + [_warning(100, "high"), _warning(101, "high")]
        + [_warning(200, "medium"), _warning(201, "medium")]
    )

    context = build_ai_request_context(_report(warnings))
    warning_ids = [warning.warning_id for warning in context.warnings]

    assert len(warning_ids) == 50
    assert warning_ids[:4] == ["WARN-100", "WARN-101", "WARN-200", "WARN-201"]
    assert warning_ids[4:] == [f"WARN-{index:03d}" for index in range(46)]
    assert context.metadata.original_warning_count == 53
    assert context.metadata.included_warning_count == 50
    assert context.metadata.omitted_warning_count == 3
    assert context.metadata.was_truncated is True


def test_long_text_is_marked_and_unicode_boundary_is_safe() -> None:
    retained_before_marker = (
        DEFAULT_AI_CONTEXT_LIMITS.maximum_text_characters - len(TRUNCATION_MARKER)
    )
    message = "A" * (retained_before_marker - 2) + "e\u0301" + "B" * 100
    context = build_ai_request_context(_report([_warning(1, message=message)]))
    truncated_message = context.warnings[0].message
    prefix = truncated_message[: -len(TRUNCATION_MARKER)]

    assert truncated_message.endswith(TRUNCATION_MARKER)
    assert len(truncated_message) <= 2_000
    assert prefix
    assert unicodedata.combining(prefix[-1]) == 0
    assert context.metadata.truncated_field_count == 1
    assert context.metadata.was_truncated is True


def test_evidence_list_limit_is_disclosed() -> None:
    feature = FeatureInfo(
        internal_name="Feature001",
        category="subtractive_feature",
        error_details=[f"Error detail {index}" for index in range(15)],
    )
    context = build_ai_request_context(
        _report([_warning(1)], features=[feature])
    )

    assert context.warnings[0].evidence["error_details"] == [
        f"Error detail {index}" for index in range(10)
    ]
    assert (
        "affected_feature.error_details.item_limit"
        in context.metadata.omitted_evidence_fields
    )
    assert context.metadata.truncated_field_count == 1
    assert context.metadata.was_truncated is True


def test_feature_summary_limit_matches_inline_warning_contexts() -> None:
    limits = AIContextLimits(
        maximum_warning_count=5,
        maximum_feature_summary_count=2,
    )
    context = build_ai_request_context(
        _report([_warning(index, "high") for index in range(3)]),
        limits=limits,
    )

    assert [warning.warning_id for warning in context.warnings] == [
        "WARN-000",
        "WARN-001",
    ]
    assert context.metadata.maximum_warning_count == 5
    assert context.metadata.maximum_feature_summary_count == 2
    assert context.metadata.original_warning_count == 3
    assert context.metadata.included_warning_count == 2
    assert context.metadata.omitted_warning_count == 1


def test_total_size_limit_removes_lowest_priority_warnings_and_keeps_valid_json() -> None:
    long_text = "Engineering context " * 500
    warnings = [
        _warning(
            index,
            "high" if index < 2 else "low",
            message=long_text,
            technical_reason=long_text,
            suggested_practice=long_text,
        )
        for index in range(50)
    ]

    context = build_ai_request_context(_report(warnings))
    serialized = serialize_ai_request_context(context)
    decoded = json.loads(serialized)

    assert context.metadata.included_warning_count < 50
    assert context.metadata.omitted_warning_count == (
        50 - context.metadata.included_warning_count
    )
    assert [warning.warning_id for warning in context.warnings[:2]] == [
        "WARN-000",
        "WARN-001",
    ]
    assert len(serialized) <= 64_000
    assert context.metadata.serialized_context_characters == len(serialized)
    assert decoded["metadata"]["serialized_context_characters"] == len(serialized)
    assert decoded["metadata"]["was_truncated"] is True
    assert all(
        warning["message"].endswith(TRUNCATION_MARKER)
        for warning in decoded["warnings"]
    )


def test_truncated_context_is_deterministic() -> None:
    long_text = "X" * 5_000
    report = _report(
        [
            _warning(index, severity, message=long_text)
            for index, severity in enumerate(
                ("low", "high", "medium", "high", "low")
            )
        ]
    )

    first = build_ai_request_context(report)
    second = build_ai_request_context(report)

    assert first.to_dict() == second.to_dict()
    assert serialize_ai_request_context(first) == serialize_ai_request_context(second)
    assert [warning.warning_id for warning in first.warnings] == [
        "WARN-001",
        "WARN-003",
        "WARN-002",
        "WARN-000",
        "WARN-004",
    ]
