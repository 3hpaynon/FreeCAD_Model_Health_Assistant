"""Read-only presentation of validated provider explanation responses."""

from __future__ import annotations

from model_health_assistant.ai_consent import AIRequestReview
from model_health_assistant.ai_models import AIExplanationResponse
from model_health_assistant.ai_usage_presentation import (
    build_ai_usage_presentation,
)
from model_health_assistant.presentation import WarningDisplayItem

AI_ADVISORY_DISCLAIMER = (
    "AI explanations are advisory text based only on the displayed structured "
    "context. They do not verify geometry, prove model correctness, or replace "
    "engineering judgment. No model changes or actions were performed."
)


def format_selected_warning_explanation(
    response: AIExplanationResponse,
    source_warning: WarningDisplayItem,
    review: AIRequestReview,
) -> str:
    """Keep the original warning distinct from its validated AI explanation."""

    matching = [
        item
        for item in response.warning_explanations
        if item.warning_id == source_warning.warning_id
        and item.rule_id == source_warning.rule_id
        and item.affected_feature_internal_name
        == source_warning.feature_internal_name
    ]
    lines = [
        "ORIGINAL MODEL-HEALTH WARNING (RULE-BASED)",
        "=" * 48,
        f"Warning ID: {source_warning.warning_id}",
        f"Rule: {source_warning.rule_id}",
        f"Feature: {source_warning.feature_internal_name or '(document-level)'}",
        f"Severity: {source_warning.severity}",
        "Warning:",
        source_warning.message,
        "Technical reason:",
        source_warning.technical_reason,
        "Suggested practice:",
        source_warning.suggested_practice,
        "",
        "AI EXPLANATION (ADVISORY)",
        "=" * 48,
        *_provider_metadata_lines(response, review),
        "Overall summary:",
        response.overall_summary,
    ]
    if matching:
        explanation = matching[0]
        lines.extend(
            (
                "Explanation:",
                explanation.explanation,
                "Why it matters:",
                explanation.why_it_matters,
                "Manual review steps:",
            )
        )
        if explanation.manual_review_steps:
            lines.extend(
                f"{index}. {step}"
                for index, step in enumerate(
                    explanation.manual_review_steps,
                    start=1,
                )
            )
        else:
            lines.append("None supplied.")
        lines.extend(
            (
                "Confidence:",
                explanation.confidence or "Not reported",
                "Warning-specific limitations:",
            )
        )
        lines.extend(
            f"- {item}" for item in explanation.limitations
        )
        if not explanation.limitations:
            lines.append("- None supplied.")
    else:
        lines.extend(
            (
                "Per-warning explanation:",
                "The provider returned no explanation for the selected source warning.",
            )
        )

    lines.extend(("Overall limitations:",))
    lines.extend(f"- {item}" for item in response.limitations)
    lines.extend(("", "DISCLAIMER", AI_ADVISORY_DISCLAIMER))
    return "\n".join(lines)


def format_full_analysis_explanation(
    response: AIExplanationResponse,
    review: AIRequestReview,
) -> str:
    """Format one full response in the reviewed request's warning order."""

    context = review.request.context
    source_warnings = list(context.warnings)
    source_identities = {
        (warning.warning_id, warning.rule_id, warning.affected_feature_internal_name)
        for warning in source_warnings
    }
    explanations = {}
    for explanation in response.warning_explanations:
        identity = (
            explanation.warning_id,
            explanation.rule_id,
            explanation.affected_feature_internal_name,
        )
        if identity not in source_identities:
            raise ValueError(
                "Provider response contains an unknown warning explanation."
            )
        if identity in explanations:
            raise ValueError(
                "Provider response contains a duplicate warning explanation."
            )
        explanations[identity] = explanation

    metadata = context.metadata
    lines = [
        "FULL ANALYSIS AI EXPLANATION (READ-ONLY ADVISORY)",
        "=" * 56,
    ]
    if metadata.was_truncated or metadata.omitted_warning_count:
        lines.extend(
            (
                "IMPORTANT: THE REVIEWED REQUEST WAS LIMITED OR TRUNCATED.",
                "The provider did not receive omitted warnings or truncated material.",
            )
        )
    lines.extend(
        [
        *_provider_metadata_lines(response, review),
        "",
        "REVIEWED CONTEXT COVERAGE",
        f"Warnings in completed analysis: {metadata.original_warning_count}",
        f"Warnings included in request: {metadata.included_warning_count}",
        f"Warnings omitted by limits/scope: {metadata.omitted_warning_count}",
        f"Context truncated: {'Yes' if metadata.was_truncated else 'No'}",
        f"Truncated fields: {metadata.truncated_field_count}",
        ]
    )
    if metadata.was_truncated or metadata.omitted_warning_count:
        lines.append(
            "Limit acknowledgment: This explanation covers only the reviewed "
            "privacy-safe context included in the request; omitted or truncated "
            "material was not available to the provider."
        )

    lines.extend(
        (
            "",
            "SUMMARY",
            response.overall_summary,
            "",
            "KEY CONCERNS (SUGGESTED MANUAL REVIEW ORDER)",
        )
    )
    if not source_warnings:
        lines.append("No source warnings were included in the reviewed request.")
    for index, warning in enumerate(source_warnings, start=1):
        identity = (
            warning.warning_id,
            warning.rule_id,
            warning.affected_feature_internal_name,
        )
        explanation = explanations.get(identity)
        concern = (
            explanation.why_it_matters
            if explanation is not None
            else "The provider returned no explanation for this included warning."
        )
        lines.append(
            f"{index}. [{warning.severity}] {warning.rule_id} - "
            f"{warning.affected_feature_internal_name}: {concern}"
        )

    lines.extend(("", "PER-WARNING EXPLANATIONS (REQUEST SOURCE ORDER)"))
    if not source_warnings:
        lines.append("None.")
    for index, warning in enumerate(source_warnings, start=1):
        identity = (
            warning.warning_id,
            warning.rule_id,
            warning.affected_feature_internal_name,
        )
        explanation = explanations.get(identity)
        lines.extend(
            (
                "",
                f"Warning {index}",
                f"Warning ID: {warning.warning_id}",
                f"Rule: {warning.rule_id}",
                f"Feature: {warning.affected_feature_internal_name}",
                f"Severity: {warning.severity}",
                "Source warning:",
                warning.message,
            )
        )
        if explanation is None:
            lines.extend(
                (
                    "AI explanation:",
                    "Not supplied. The provider response was partial for this warning.",
                )
            )
            continue
        lines.extend(
            (
                "AI explanation:",
                explanation.explanation,
                "Why it matters:",
                explanation.why_it_matters,
                "Manual review steps:",
            )
        )
        if explanation.manual_review_steps:
            lines.extend(
                f"{step_index}. {step}"
                for step_index, step in enumerate(
                    explanation.manual_review_steps,
                    start=1,
                )
            )
        else:
            lines.append("None supplied.")
        lines.extend(
            (
                "Confidence:",
                explanation.confidence or "Not reported",
                "Warning-specific limitations:",
            )
        )
        if explanation.limitations:
            lines.extend(f"- {item}" for item in explanation.limitations)
        else:
            lines.append("- None supplied.")

    missing_count = len(source_warnings) - len(explanations)
    lines.extend(
        (
            "",
            "RESPONSE COMPLETENESS",
            f"Included warnings explained: {len(explanations)} of {len(source_warnings)}",
            f"Included warnings without an explanation: {missing_count}",
            "",
            "OVERALL LIMITATIONS",
        )
    )
    lines.extend(f"- {item}" for item in response.limitations)
    lines.extend(("", "DISCLAIMER", AI_ADVISORY_DISCLAIMER))
    return "\n".join(lines)


def format_grounded_question_response(
    response: AIExplanationResponse,
    review: AIRequestReview,
) -> str:
    """Format one non-persistent answer grounded in the reviewed analysis."""

    question = review.user_question
    if (
        review.request_type != "custom-question"
        or not isinstance(question, str)
        or not question.strip()
        or review.request.user_question != question
    ):
        raise ValueError("A reviewed grounded question is required.")

    context = review.request.context
    lines = [
        "ASK ABOUT THIS ANALYSIS (ONE-SHOT, READ-ONLY ADVISORY)",
        "=" * 60,
        *_provider_metadata_lines(response, review),
        "",
        "USER QUESTION",
        question,
        "",
        "GROUNDED AI ANSWER",
        response.overall_summary,
        "",
        "REVIEWED CONTEXT COVERAGE",
        f"Warnings included: {context.metadata.included_warning_count}",
        f"Warnings omitted: {context.metadata.omitted_warning_count}",
        f"Context truncated: {'Yes' if context.metadata.was_truncated else 'No'}",
        "",
        "RELATED SOURCE-WARNING EXPLANATIONS",
    ]
    if not response.warning_explanations:
        lines.append("None supplied.")
    for index, explanation in enumerate(response.warning_explanations, start=1):
        lines.extend(
            (
                f"{index}. Rule: {explanation.rule_id}",
                f"   Feature: {explanation.affected_feature_internal_name}",
                f"   Explanation: {explanation.explanation}",
                f"   Why it matters: {explanation.why_it_matters}",
            )
        )
    lines.extend(("", "OVERALL LIMITATIONS"))
    lines.extend(f"- {item}" for item in response.limitations)
    lines.extend(
        (
            "",
            "NO CHAT HISTORY",
            (
                "This answer used only the displayed one-shot question and reviewed "
                "privacy-safe context. It was not chained to a previous response."
            ),
            "",
            "DISCLAIMER",
            AI_ADVISORY_DISCLAIMER,
        )
    )
    return "\n".join(lines)


def _provider_metadata_lines(
    response: AIExplanationResponse,
    review: AIRequestReview,
) -> tuple[str, ...]:
    return build_ai_usage_presentation(
        response,
        configured_model_identifier=review.model_identifier,
    ).lines()


__all__ = [
    "AI_ADVISORY_DISCLAIMER",
    "format_full_analysis_explanation",
    "format_grounded_question_response",
    "format_selected_warning_explanation",
]
