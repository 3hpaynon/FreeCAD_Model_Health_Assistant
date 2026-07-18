"""Pure provider-neutral data models for future explanation workflows.

The models in this module intentionally contain no FreeCAD, Qt, network, or
provider-SDK types.  They form a JSON-compatible boundary between a completed
local model-health report and an optional future explanation provider.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TypeAlias

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

AI_CONTEXT_SCHEMA_VERSION = "1.0"
AI_REQUEST_SCHEMA_VERSION = "1.0"
AI_RESPONSE_SCHEMA_VERSION = "1.0"


@dataclass(slots=True)
class AIContextMetadata:
    """Disclose privacy omissions and any future deterministic truncation.

    ``omitted_fields`` and ``omitted_evidence_fields`` contain stable field
    identifiers, not omitted values.  The truncation counters remain zero until
    context-size controls are explicitly applied.
    """

    document_name_included: bool = False
    omitted_fields: list[str] = field(default_factory=list)
    omitted_evidence_fields: list[str] = field(default_factory=list)
    was_truncated: bool = False
    original_warning_count: int = 0
    included_warning_count: int = 0
    omitted_warning_count: int = 0
    truncated_field_count: int = 0
    limit_policy_version: str | None = None
    maximum_warning_count: int | None = None
    maximum_feature_summary_count: int | None = None
    maximum_text_characters: int | None = None
    maximum_evidence_list_items: int | None = None
    maximum_serialized_context_characters: int | None = None
    serialized_context_characters: int | None = None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AIWarningContext:
    """Privacy-reviewed facts for one existing model-health warning.

    Feature labels and all warning text originate from the CAD analysis and
    must be treated as untrusted data by prompt construction and providers.
    Optional feature fields are ``None`` when no exactly matched feature fact
    exists in the completed report.
    """

    warning_id: str
    rule_id: str
    severity: str
    affected_feature_internal_name: str
    message: str
    technical_reason: str
    suggested_practice: str
    affected_feature_label: str | None = None
    affected_feature_category: str | None = None
    affected_feature_type: str | None = None
    evidence: dict[str, JSONValue] = field(default_factory=dict)
    raw_score_penalty: int | None = None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AIRequestContext:
    """Allowlisted model-health facts that may be explained in the future."""

    report_schema_version: str
    health_score: int
    total_document_object_count: int
    meaningful_modeling_feature_count: int
    total_sketch_count: int
    category_counts: dict[str, int]
    total_warning_count: int
    warning_counts_by_severity: dict[str, int]
    warnings: list[AIWarningContext] = field(default_factory=list)
    analysis_limitations: list[str] = field(default_factory=list)
    health_score_formula_version: str | None = None
    starting_health_score: int | None = None
    total_score_penalty: int | None = None
    document_display_name: str = "Active FreeCAD document"
    metadata: AIContextMetadata = field(default_factory=AIContextMetadata)
    schema_version: str = AI_CONTEXT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(slots=True)
class AIRequestMetadata:
    """Provider-neutral metadata describing an explanation request.

    ``prompt_template_version`` is absent until the later prompt-building step
    constructs prompt content from an ``AIRequestContext``.
    """

    purpose: str = "model_health_explanation"
    context_schema_version: str = AI_CONTEXT_SCHEMA_VERSION
    prompt_template_version: str | None = None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(slots=True, kw_only=True)
class AIExplanationRequest:
    """Complete provider-neutral request produced by the prompt builder.

    ``user_question`` is intentionally separate from ``user_prompt``.  The
    latter contains only the delimited, privacy-safe CAD-derived context, while
    the former is an optional user-authored grounded question.  Providers must
    not merge the two sources before applying their trust boundaries.
    """

    context: AIRequestContext
    system_instruction: str
    user_prompt: str
    user_question: str | None = None
    metadata: AIRequestMetadata = field(default_factory=AIRequestMetadata)
    schema_version: str = AI_REQUEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(slots=True, kw_only=True)
class AIWarningExplanation:
    """Structured advisory explanation corresponding to one source warning."""

    warning_id: str
    rule_id: str
    affected_feature_internal_name: str
    severity: str
    explanation: str
    why_it_matters: str
    manual_review_steps: list[str] = field(default_factory=list)
    confidence: str | None = None
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class AIUsageMetadata:
    """Provider-neutral token usage returned by an explanation request."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return JSON-compatible non-secret usage counters."""

        return asdict(self)


@dataclass(slots=True, kw_only=True)
class AIExplanationResponse:
    """Validated-shape response contract shared by all future providers.

    ``request_identifier`` is optional because a local provider or a future
    provider may not issue one.  Suggestions remain advisory text and this
    contract intentionally has no model-modification fields.
    """

    provider_identifier: str
    provider_display_name: str
    overall_summary: str
    warning_explanations: list[AIWarningExplanation] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    request_identifier: str | None = None
    actual_model_identifier: str | None = None
    usage: AIUsageMetadata | None = None
    schema_version: str = AI_RESPONSE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JSONValue]:
        """Return an independent JSON-compatible dictionary."""

        return asdict(self)


__all__ = [
    "AI_CONTEXT_SCHEMA_VERSION",
    "AI_REQUEST_SCHEMA_VERSION",
    "AI_RESPONSE_SCHEMA_VERSION",
    "AIContextMetadata",
    "AIExplanationRequest",
    "AIExplanationResponse",
    "AIRequestContext",
    "AIRequestMetadata",
    "AIWarningContext",
    "AIWarningExplanation",
    "AIUsageMetadata",
    "JSONScalar",
    "JSONValue",
]
