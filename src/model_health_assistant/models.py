"""Pure Python data models for model-health analysis.

These dataclasses intentionally do not import FreeCAD. Keeping the model layer
plain makes the risk engine and report writer easy to test in any Python
environment.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["low", "medium", "high"]
SupportConfidence = Literal["low", "medium", "high"]
FeatureCategory = Literal[
    "sketch",
    "additive_feature",
    "subtractive_feature",
    "finishing_feature",
    "pattern_feature",
    "reference_geometry",
    "container",
    "other",
]


@dataclass(slots=True)
class FeatureInfo:
    """A simplified view of one object in a FreeCAD document."""

    internal_name: str
    label: str = ""
    type_id: str = ""
    dependency_names: list[str] = field(default_factory=list)
    downstream_dependent_names: list[str] = field(default_factory=list)
    is_sketch: bool = False
    attached_to_generated_geometry: bool = False
    support_name: str | None = None
    support_kind: str | None = None
    recompute_status: str | None = None
    error_status: str | None = None
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    category: FeatureCategory = "other"
    support_subelement: str | None = None
    support_is_stable_reference: bool = False
    support_confidence: SupportConfidence = "low"

    @property
    def direct_upstream_count(self) -> int:
        """Return the number of direct objects this feature depends on."""

        return len(self.dependency_names)

    @property
    def direct_downstream_count(self) -> int:
        """Return the number of direct objects that depend on this feature."""

        return len(self.downstream_dependent_names)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary."""

        data = asdict(self)
        data["direct_upstream_count"] = self.direct_upstream_count
        data["direct_downstream_count"] = self.direct_downstream_count
        return data


@dataclass(slots=True)
class SketchInfo:
    """Sketch-specific details that may be attached to a feature later."""

    internal_name: str
    support_name: str | None = None
    support_kind: str | None = None
    attached_to_generated_geometry: bool = False
    constraint_count: int | None = None
    fully_constrained: bool | None = None
    external_geometry_count: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary."""

        return asdict(self)


@dataclass(slots=True)
class DependencyInfo:
    """A directed relationship between two document objects."""

    source_name: str
    target_name: str
    relationship: str = "depends_on"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary."""

        return asdict(self)


@dataclass(slots=True)
class RiskWarning:
    """A rule-based warning about a potentially fragile modeling pattern."""

    warning_id: str
    severity: Severity
    affected_feature_name: str
    message: str
    technical_reason: str
    suggested_practice: str
    rule_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary."""

        return asdict(self)


@dataclass(slots=True)
class ModelHealthReport:
    """The complete local model-health result for one document."""

    document_name: str
    generated_at: str
    health_score: int
    features: list[FeatureInfo] = field(default_factory=list)
    warnings: list[RiskWarning] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_feature_count(self) -> int:
        """Return the number of extracted features."""

        return len(self.features)

    @property
    def total_sketch_count(self) -> int:
        """Return the number of extracted sketches."""

        return sum(1 for feature in self.features if feature.is_sketch)

    @property
    def total_warning_count(self) -> int:
        """Return the number of generated warnings."""

        return len(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        """Return the report in the JSON shape used by the local writer."""

        return {
            "document_name": self.document_name,
            "timestamp": self.generated_at,
            "total_feature_count": self.total_feature_count,
            "total_sketch_count": self.total_sketch_count,
            "total_warning_count": self.total_warning_count,
            "health_score": self.health_score,
            "features": [feature.to_dict() for feature in self.features],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
        }
