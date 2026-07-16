import json
from dataclasses import fields

from model_health_assistant.ai_models import (
    AI_CONTEXT_SCHEMA_VERSION,
    AI_REQUEST_SCHEMA_VERSION,
    AI_RESPONSE_SCHEMA_VERSION,
    AIContextMetadata,
    AIExplanationRequest,
    AIExplanationResponse,
    AIRequestContext,
    AIWarningContext,
    AIWarningExplanation,
)


def _empty_context() -> AIRequestContext:
    return AIRequestContext(
        report_schema_version="0.3",
        health_score=100,
        total_document_object_count=0,
        meaningful_modeling_feature_count=0,
        total_sketch_count=0,
        category_counts={},
        total_warning_count=0,
        warning_counts_by_severity={"low": 0, "medium": 0, "high": 0},
    )


def test_construct_privacy_safe_context_and_warning() -> None:
    warning = AIWarningContext(
        warning_id="MHA-FEATURE-ERROR-001",
        rule_id="confirmed_recompute_error",
        severity="high",
        affected_feature_internal_name="Pocket001",
        affected_feature_label="Drive pocket",
        affected_feature_category="subtractive_feature",
        affected_feature_type="PartDesign::Pocket",
        message="Pocket001 reports a confirmed FreeCAD error.",
        technical_reason="FreeCAD exposed an explicit error state.",
        suggested_practice="Review the earliest failing feature manually.",
        evidence={"has_confirmed_error": True},
        raw_score_penalty=25,
    )
    context = AIRequestContext(
        report_schema_version="0.3",
        health_score=70,
        total_document_object_count=12,
        meaningful_modeling_feature_count=7,
        total_sketch_count=2,
        category_counts={"subtractive_feature": 2, "sketch": 2},
        total_warning_count=1,
        warning_counts_by_severity={"low": 0, "medium": 0, "high": 1},
        warnings=[warning],
        health_score_formula_version="1.0",
        starting_health_score=100,
        total_score_penalty=30,
    )

    assert context.warnings == [warning]
    assert context.warnings[0].affected_feature_internal_name == "Pocket001"
    assert context.document_display_name == "Active FreeCAD document"


def test_empty_context_defaults_disclose_no_name_and_no_truncation() -> None:
    context = _empty_context()

    assert context.warnings == []
    assert context.analysis_limitations == []
    assert context.metadata == AIContextMetadata()
    assert context.metadata.document_name_included is False
    assert context.metadata.was_truncated is False
    assert context.metadata.original_warning_count == 0
    assert context.metadata.included_warning_count == 0
    assert context.metadata.omitted_warning_count == 0


def test_models_convert_to_json_compatible_independent_data() -> None:
    context = _empty_context()
    request = AIExplanationRequest(
        context=context,
        system_instruction="Explain only the supplied findings.",
        user_prompt='{"warnings": []}',
    )

    data = request.to_dict()
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)

    assert '"schema_version": "1.0"' in encoded
    assert data["context"]["warnings"] == []
    data["context"]["category_counts"]["other"] = 10
    assert context.category_counts == {}


def test_response_defaults_support_missing_optional_fields() -> None:
    explanation = AIWarningExplanation(
        warning_id="WARN-001",
        rule_id="example_rule",
        affected_feature_internal_name="Feature001",
        severity="medium",
        explanation="Review the existing warning.",
        why_it_matters="The feature may affect downstream operations.",
    )
    response = AIExplanationResponse(
        provider_identifier="local-test",
        provider_display_name="Local Test Provider",
        overall_summary="One existing warning was explained.",
        warning_explanations=[explanation],
    )

    data = response.to_dict()

    assert explanation.confidence is None
    assert explanation.manual_review_steps == []
    assert response.request_identifier is None
    assert data["request_identifier"] is None
    assert json.loads(json.dumps(data)) == data


def test_schema_versions_are_explicit_and_stable() -> None:
    context = _empty_context()
    request = AIExplanationRequest(
        context=context,
        system_instruction="System instruction",
        user_prompt="Structured user prompt",
    )
    response = AIExplanationResponse(
        provider_identifier="local-test",
        provider_display_name="Local Test Provider",
        overall_summary="No warnings to explain.",
    )

    assert AI_CONTEXT_SCHEMA_VERSION == "1.0"
    assert AI_REQUEST_SCHEMA_VERSION == "1.0"
    assert AI_RESPONSE_SCHEMA_VERSION == "1.0"
    assert context.schema_version == AI_CONTEXT_SCHEMA_VERSION
    assert request.schema_version == AI_REQUEST_SCHEMA_VERSION
    assert response.schema_version == AI_RESPONSE_SCHEMA_VERSION
    assert request.metadata.context_schema_version == AI_CONTEXT_SCHEMA_VERSION


def test_ai_models_have_no_path_key_or_raw_object_fields() -> None:
    model_types = (
        AIContextMetadata,
        AIWarningContext,
        AIRequestContext,
        AIExplanationRequest,
        AIWarningExplanation,
        AIExplanationResponse,
    )
    field_names = {item.name for model_type in model_types for item in fields(model_type)}

    assert not {"path", "file_name", "api_key", "document", "freecad_object"} & field_names
