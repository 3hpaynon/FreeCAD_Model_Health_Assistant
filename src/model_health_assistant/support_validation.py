"""Deterministic, non-AI validation for deleted-sketch attachment supports."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SUPPORT_RESTORABLE = "support_restorable"
SUPPORT_MISSING = "support_missing"
SUPPORT_CHANGED = "support_changed"
SUPPORT_UNVERIFIED = "support_unverified"
SUPPORT_NOT_APPLICABLE = "support_not_applicable"

RESTORE_WITH_ORIGINAL_SUPPORT = "original_support"
RESTORE_DETACHED = "detached"

_ORIGIN_PLANE_NAMES = frozenset({"XY_Plane", "XZ_Plane", "YZ_Plane"})
_STABLE_DATUM_PLANE_TYPES = frozenset({"PartDesign::Plane"})


@dataclass(frozen=True, slots=True)
class SupportAssessment:
    """Conservative attachment result used by both service and UI."""

    category: str
    message: str
    reference_count: int
    can_restore_original: bool
    recommended_mode: str


def assess_snapshot_support(
    document: Any,
    snapshot: Mapping[str, Any],
) -> SupportAssessment:
    """Classify support without treating a surviving Face name as proof."""

    object_record = snapshot.get("object")
    if not isinstance(object_record, Mapping):
        return _assessment(SUPPORT_UNVERIFIED, 0)
    references = _support_references(object_record)
    if references is None:
        return _assessment(SUPPORT_UNVERIFIED, 0)
    if not references:
        return _assessment(SUPPORT_NOT_APPLICABLE, 0)

    categories = tuple(
        _reference_category(document, reference) for reference in references
    )
    if all(category == SUPPORT_RESTORABLE for category in categories):
        category = SUPPORT_RESTORABLE
    elif SUPPORT_MISSING in categories:
        category = SUPPORT_MISSING
    elif SUPPORT_CHANGED in categories:
        category = SUPPORT_CHANGED
    else:
        category = SUPPORT_UNVERIFIED
    return _assessment(category, len(references))


def support_reference_is_restorable(
    document: Any,
    reference: Mapping[str, Any],
) -> bool:
    """Return true only for deterministic stable-plane reference identities."""

    return _reference_category(document, reference) == SUPPORT_RESTORABLE


def _support_references(
    object_record: Mapping[str, Any],
) -> list[Mapping[str, Any]] | None:
    support = object_record.get("support", [])
    attachment_support = object_record.get("attachment_support", [])
    if not isinstance(support, list) or not isinstance(attachment_support, list):
        return None
    combined: list[Mapping[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for reference in (*support, *attachment_support):
        if not isinstance(reference, Mapping):
            return None
        descriptor = reference.get("object")
        subelements = reference.get("subelements")
        if not isinstance(descriptor, Mapping) or not isinstance(subelements, list):
            return None
        name = descriptor.get("name")
        if not isinstance(name, str) or not name.strip() or not all(
            isinstance(item, str) and item for item in subelements
        ):
            return None
        key = (name, tuple(subelements))
        if key not in seen:
            seen.add(key)
            combined.append(reference)
    return combined


def _reference_category(document: Any, reference: Mapping[str, Any]) -> str:
    descriptor = reference.get("object")
    subelements = reference.get("subelements")
    if not isinstance(descriptor, Mapping) or not isinstance(subelements, list):
        return SUPPORT_UNVERIFIED
    name = descriptor.get("name")
    if not isinstance(name, str) or not name.strip():
        return SUPPORT_UNVERIFIED

    recorded_document = descriptor.get("document_name")
    current_document = _safe_getattr(document, "Name")
    if (
        isinstance(recorded_document, str)
        and recorded_document
        and recorded_document != current_document
    ):
        return SUPPORT_UNVERIFIED

    live_object = _get_document_object(document, name)
    if live_object is None:
        return SUPPORT_MISSING
    recorded_type = descriptor.get("type_id")
    live_type = _safe_getattr(live_object, "TypeId")
    if isinstance(recorded_type, str) and recorded_type and live_type != recorded_type:
        return SUPPORT_CHANGED

    if subelements:
        return SUPPORT_UNVERIFIED
    if name in _ORIGIN_PLANE_NAMES:
        return SUPPORT_RESTORABLE
    if live_type in _STABLE_DATUM_PLANE_TYPES:
        recorded_placement = descriptor.get("placement")
        live_placement = _placement_signature(live_object)
        if not isinstance(recorded_placement, Mapping) or live_placement is None:
            return SUPPORT_UNVERIFIED
        return (
            SUPPORT_RESTORABLE
            if _values_close(recorded_placement, live_placement)
            else SUPPORT_CHANGED
        )
    return SUPPORT_UNVERIFIED


def _get_document_object(document: Any, name: str) -> Any | None:
    getter = _safe_getattr(document, "getObject")
    if callable(getter):
        try:
            return getter(name)
        except Exception:  # noqa: BLE001 - FreeCAD wrappers vary by runtime.
            return None
    objects = _safe_getattr(document, "Objects", ())
    if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes)):
        for item in objects:
            if _safe_getattr(item, "Name") == name:
                return item
    return None


def _placement_signature(value: Any) -> dict[str, Any] | None:
    placement = _safe_getattr(value, "Placement")
    base = _safe_getattr(placement, "Base")
    rotation = _safe_getattr(placement, "Rotation")
    quaternion = _safe_getattr(rotation, "Q")
    if not isinstance(quaternion, Sequence) or isinstance(quaternion, (str, bytes)):
        return None
    if len(quaternion) != 4:
        return None
    coordinates = tuple(_safe_getattr(base, axis) for axis in ("x", "y", "z"))
    if not all(_finite_number(item) for item in (*coordinates, *quaternion)):
        return None
    return {
        "base": dict(zip(("x", "y", "z"), coordinates)),
        "rotation": {"quaternion": list(quaternion)},
    }


def _values_close(left: object, right: object, tolerance: float = 1e-9) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=tolerance,
            abs_tol=tolerance,
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _values_close(left[key], right[key], tolerance) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _values_close(first, second, tolerance)
            for first, second in zip(left, right)
        )
    return left == right


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _safe_getattr(value: object, name: str, default: Any = None) -> Any:
    try:
        return getattr(value, name, default)
    except Exception:  # noqa: BLE001 - deleted FreeCAD wrappers can raise here.
        return default


def _assessment(category: str, reference_count: int) -> SupportAssessment:
    messages = {
        SUPPORT_RESTORABLE: "Original support was deterministically validated.",
        SUPPORT_MISSING: "The original support object is missing.",
        SUPPORT_CHANGED: "The original support identity or placement has changed.",
        SUPPORT_UNVERIFIED: (
            "The sketch geometry and constraints can be recovered, but the "
            "original support cannot be safely verified."
        ),
        SUPPORT_NOT_APPLICABLE: "The sketch did not require an original support.",
    }
    can_restore_original = category == SUPPORT_RESTORABLE
    return SupportAssessment(
        category=category,
        message=messages[category],
        reference_count=reference_count,
        can_restore_original=can_restore_original,
        recommended_mode=(
            RESTORE_WITH_ORIGINAL_SUPPORT
            if can_restore_original
            else RESTORE_DETACHED
        ),
    )


__all__ = [
    "RESTORE_DETACHED",
    "RESTORE_WITH_ORIGINAL_SUPPORT",
    "SUPPORT_CHANGED",
    "SUPPORT_MISSING",
    "SUPPORT_NOT_APPLICABLE",
    "SUPPORT_RESTORABLE",
    "SUPPORT_UNVERIFIED",
    "SupportAssessment",
    "assess_snapshot_support",
    "support_reference_is_restorable",
]
