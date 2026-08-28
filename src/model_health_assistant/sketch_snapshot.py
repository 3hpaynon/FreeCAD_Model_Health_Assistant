"""Deterministic, JSON-safe snapshots for ``Sketcher::SketchObject``.

This module intentionally imports neither FreeCAD nor Qt.  It reads the
document objects supplied by FreeCAD through their public Python attributes,
which also keeps the serialization contract testable outside FreeCAD.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .recovery_vault import RECOVERY_SCHEMA_VERSION

SKETCH_SNAPSHOT_RECORD_TYPE = "sketch_snapshot"
SKETCH_TYPE_ID = "Sketcher::SketchObject"

SUPPORTED_GEOMETRY_TYPES = frozenset(
    {"LineSegment", "Circle", "ArcOfCircle", "Point"}
)
SUPPORTED_CONSTRAINT_TYPES = frozenset(
    {
        "Coincident",
        "Horizontal",
        "Vertical",
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
        "Equal",
        "Parallel",
        "Perpendicular",
        "Tangent",
        "Symmetric",
        "PointOnObject",
    }
)
DIMENSIONAL_CONSTRAINT_TYPES = frozenset(
    {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}
)

_MISSING = object()
_CONSTRAINT_INTEGER_FIELDS = (
    ("first", "First"),
    ("first_pos", "FirstPos"),
    ("second", "Second"),
    ("second_pos", "SecondPos"),
    ("third", "Third"),
    ("third_pos", "ThirdPos"),
)


class SketchSerializationError(ValueError):
    """Raised when the object cannot produce a safe sketch snapshot."""


def serialize_sketch(
    sketch: Any,
    *,
    document_id: str,
    object_id: str,
    snapshot_id: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Serialize one live sketch without changing the FreeCAD document.

    Unsupported geometry or constraints remain visible in the snapshot, but
    make its restore eligibility conservative.  This prevents a later restore
    implementation from silently constructing an incomplete sketch.
    """

    for field_name, value in (
        ("document_id", document_id),
        ("object_id", object_id),
        ("snapshot_id", snapshot_id),
    ):
        _require_uuid(value, field_name)

    type_id = _required_text_attribute(sketch, "TypeId")
    if type_id != SKETCH_TYPE_ID:
        raise SketchSerializationError(
            f"Only {SKETCH_TYPE_ID} objects can be serialized."
        )

    object_name = _required_text_attribute(sketch, "Name")
    label = _text_attribute(sketch, "Label", default=object_name)
    timestamp = _timestamp(captured_at)
    warnings: list[str] = []

    geometry, unsupported_geometry = _serialize_geometry_list(sketch, warnings)
    constraints, unsupported_constraints = _serialize_constraint_list(
        sketch, warnings
    )
    placement = _optional_placement(sketch, "Placement", warnings)
    attachment_offset = _optional_placement(
        sketch, "AttachmentOffset", warnings
    )
    support = _serialize_link_sub_property(sketch, "Support", warnings)
    attachment_support = _serialize_link_sub_property(
        sketch, "AttachmentSupport", warnings
    )
    external_geometry = _serialize_link_sub_property(
        sketch, "ExternalGeometry", warnings
    )
    expressions = _serialize_expressions(sketch, warnings)
    visibility = _serialize_visibility(sketch, warnings)
    map_mode = _optional_text_attribute(sketch, "MapMode")

    is_complete = not (
        unsupported_geometry or unsupported_constraints or warnings
    )
    serialization_status = "complete" if is_complete else "partial"
    restore_eligibility = "eligible" if is_complete else "unsupported"

    snapshot: dict[str, Any] = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "record_type": SKETCH_SNAPSHOT_RECORD_TYPE,
        "snapshot_id": snapshot_id,
        "document_id": document_id,
        "object_id": object_id,
        "captured_at": timestamp,
        "object": {
            "name": object_name,
            "label": label,
            "type_id": type_id,
            "placement": placement,
            "geometry": geometry,
            "geometry_count": len(geometry),
            "constraints": constraints,
            "constraint_count": len(constraints),
            "support": support,
            "attachment_support": attachment_support,
            "map_mode": map_mode,
            "attachment_offset": attachment_offset,
            "expressions": expressions,
            "external_geometry": external_geometry,
            "visibility": visibility,
        },
        "recovery": {
            "serialization_status": serialization_status,
            "restore_as_copy_eligibility": restore_eligibility,
            "unsupported_geometry_types": sorted(unsupported_geometry),
            "unsupported_constraint_types": sorted(unsupported_constraints),
            "warnings": warnings,
        },
    }
    _assert_json_safe(snapshot)
    return snapshot


def _serialize_geometry_list(
    sketch: Any,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_geometry = _sequence_attribute(sketch, "Geometry")
    serialized: list[dict[str, Any]] = []
    unsupported: set[str] = set()
    construction_getter = _safe_attribute(sketch, "getConstruction")

    for index, geometry in enumerate(raw_geometry):
        source_type, geometry_type = _geometry_type(geometry)
        construction = _construction_state(
            construction_getter,
            geometry,
            index,
            warnings,
        )
        item: dict[str, Any] = {
            "index": index,
            "type": geometry_type,
            "source_type": source_type,
            "construction": construction,
        }
        if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
            item.update({"supported": False, "data": None})
            unsupported.add(source_type)
            serialized.append(item)
            continue

        try:
            item.update(
                {
                    "supported": True,
                    "data": _serialize_geometry_data(
                        geometry,
                        geometry_type,
                    ),
                }
            )
        except (SketchSerializationError, TypeError, ValueError):
            item.update({"supported": False, "data": None})
            unsupported.add(source_type)
            warnings.append(
                f"Geometry {index} ({source_type}) could not be serialized safely."
            )
        serialized.append(item)
    return serialized, unsupported


def _serialize_geometry_data(
    geometry: Any,
    geometry_type: str,
) -> dict[str, Any]:
    if geometry_type == "LineSegment":
        return {
            "start": _vector(_required_attribute(geometry, "StartPoint")),
            "end": _vector(_required_attribute(geometry, "EndPoint")),
        }
    if geometry_type == "Circle":
        return {
            "center": _vector(_required_attribute(geometry, "Center")),
            "axis": _vector(_required_attribute(geometry, "Axis")),
            "radius": _finite_float(
                _required_attribute(geometry, "Radius"),
                "circle radius",
            ),
        }
    if geometry_type == "ArcOfCircle":
        return {
            "center": _vector(_required_attribute(geometry, "Center")),
            "axis": _vector(_required_attribute(geometry, "Axis")),
            "radius": _finite_float(
                _required_attribute(geometry, "Radius"),
                "arc radius",
            ),
            "first_parameter": _finite_float(
                _required_attribute(geometry, "FirstParameter"),
                "arc first parameter",
            ),
            "last_parameter": _finite_float(
                _required_attribute(geometry, "LastParameter"),
                "arc last parameter",
            ),
        }
    if geometry_type == "Point":
        return {
            "point": {
                axis: _finite_float(
                    _required_attribute(geometry, axis.upper()),
                    f"point {axis}",
                )
                for axis in ("x", "y", "z")
            }
        }
    raise SketchSerializationError("Unsupported geometry type")


def _serialize_constraint_list(
    sketch: Any,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_constraints = _sequence_attribute(sketch, "Constraints")
    serialized: list[dict[str, Any]] = []
    unsupported: set[str] = set()

    for index, constraint in enumerate(raw_constraints):
        constraint_type = _text_attribute(constraint, "Type", default="Unknown")
        arguments = {
            output_name: _optional_integer_attribute(constraint, source_name)
            for output_name, source_name in _CONSTRAINT_INTEGER_FIELDS
        }
        value = _optional_float_attribute(constraint, "Value")
        driving = _constraint_state(
            constraint,
            sketch,
            index,
            "Driving",
            "getDriving",
        )
        supported = constraint_type in SUPPORTED_CONSTRAINT_TYPES
        if supported and (
            any(argument is None for argument in arguments.values())
            or value is None
            or (
                constraint_type in DIMENSIONAL_CONSTRAINT_TYPES
                and driving is None
            )
        ):
            supported = False
            warnings.append(
                f"Constraint {index} ({constraint_type}) is missing reconstruction data."
            )
        item: dict[str, Any] = {
            "index": index,
            "type": constraint_type,
            "supported": supported,
            "arguments": arguments,
            "value": value,
            "name": _optional_text_attribute(constraint, "Name"),
            "driving": driving,
            "in_virtual_space": _constraint_state(
                constraint,
                sketch,
                index,
                "InVirtualSpace",
                "getVirtualSpace",
            ),
            "active": _constraint_state(
                constraint,
                sketch,
                index,
                "IsActive",
                "getActive",
            ),
        }
        if not supported:
            unsupported.add(constraint_type)
        serialized.append(item)
    return serialized, unsupported


def _constraint_state(
    constraint: Any,
    sketch: Any,
    index: int,
    attribute_name: str,
    method_name: str,
) -> bool | None:
    value = _safe_attribute(constraint, attribute_name)
    if isinstance(value, bool):
        return value
    method = _safe_attribute(sketch, method_name)
    if not callable(method):
        return None
    try:
        result = method(index)
    except Exception:  # noqa: BLE001 - FreeCAD raises several wrapper errors.
        return None
    return result if isinstance(result, bool) else None


def _construction_state(
    construction_getter: Any,
    geometry: Any,
    index: int,
    warnings: list[str],
) -> bool:
    if callable(construction_getter):
        try:
            value = construction_getter(index)
        except Exception:  # noqa: BLE001 - FreeCAD wrapper errors vary by build.
            value = _MISSING
        if isinstance(value, bool):
            return value
    fallback = _safe_attribute(geometry, "Construction")
    if isinstance(fallback, bool):
        return fallback
    warnings.append(f"Construction state for geometry {index} is unavailable.")
    return False


def _geometry_type(geometry: Any) -> tuple[str, str]:
    type_id = _safe_attribute(geometry, "TypeId")
    if isinstance(type_id, str) and type_id.strip():
        source_type = type_id.strip()
    else:
        geometry_class = type(geometry)
        module_name = geometry_class.__module__
        class_name = geometry_class.__name__
        source_type = (
            f"{module_name}.{class_name}"
            if module_name and module_name != "builtins"
            else class_name
        )
    normalized = source_type.casefold()
    if "arcofcircle" in normalized:
        return source_type, "ArcOfCircle"
    if "linesegment" in normalized:
        return source_type, "LineSegment"
    if normalized.endswith("geomcircle") or normalized.endswith(".circle"):
        return source_type, "Circle"
    if normalized.endswith("geompoint") or normalized.endswith(".point"):
        return source_type, "Point"
    return source_type, source_type.rsplit(".", maxsplit=1)[-1]


def _optional_placement(
    owner: Any,
    attribute_name: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    placement = _safe_attribute(owner, attribute_name)
    if placement is _MISSING or placement is None:
        return None
    try:
        return _placement(placement)
    except (SketchSerializationError, TypeError, ValueError):
        warnings.append(f"{attribute_name} could not be serialized safely.")
        return None


def _placement(placement: Any) -> dict[str, Any]:
    rotation = _required_attribute(placement, "Rotation")
    quaternion = _safe_attribute(rotation, "Q")
    if not isinstance(quaternion, Sequence) or isinstance(
        quaternion, (str, bytes)
    ) or len(quaternion) != 4:
        raise SketchSerializationError("rotation quaternion is unavailable")
    return {
        "base": _vector(_required_attribute(placement, "Base")),
        "rotation": {
            "quaternion": [
                _finite_float(value, "rotation quaternion")
                for value in quaternion
            ]
        },
    }


def _vector(value: Any) -> dict[str, float]:
    return {
        axis: _finite_float(
            _required_attribute(value, axis),
            f"vector {axis}",
        )
        for axis in ("x", "y", "z")
    }


def _serialize_link_sub_property(
    owner: Any,
    attribute_name: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    value = _safe_attribute(owner, attribute_name)
    if value is _MISSING or value is None:
        return []
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 0
    ):
        return []
    pairs = _link_sub_pairs(value)
    if pairs is None:
        warnings.append(f"{attribute_name} uses an unsupported reference shape.")
        return []

    references: list[dict[str, Any]] = []
    for linked_object, subelements in pairs:
        descriptor = _linked_object_descriptor(linked_object)
        normalized_subelements = _subelement_names(subelements)
        if descriptor is None or normalized_subelements is None:
            warnings.append(
                f"{attribute_name} contains a reference that could not be structured."
            )
            continue
        references.append(
            {
                "object": descriptor,
                "subelements": normalized_subelements,
            }
        )
    return references


def _link_sub_pairs(value: Any) -> list[tuple[Any, Any]] | None:
    if _looks_like_link_sub_pair(value):
        return [(value[0], value[1])]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if all(_looks_like_link_sub_pair(item) for item in value):
            return [(item[0], item[1]) for item in value]
        if not value:
            return []
    if _linked_object_descriptor(value) is not None:
        return [(value, ())]
    return None


def _looks_like_link_sub_pair(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 2
        and _linked_object_descriptor(value[0]) is not None
    )


def _linked_object_descriptor(value: Any) -> dict[str, Any] | None:
    name = _safe_attribute(value, "Name")
    if not isinstance(name, str) or not name.strip():
        return None
    document = _safe_attribute(value, "Document")
    document_name = _safe_attribute(document, "Name")
    descriptor: dict[str, Any] = {
        "name": name,
        "label": _optional_text_attribute(value, "Label"),
        "type_id": _optional_text_attribute(value, "TypeId"),
        "document_name": (
            document_name
            if isinstance(document_name, str) and document_name.strip()
            else None
        ),
    }
    placement = _safe_attribute(value, "Placement")
    if placement is not _MISSING and placement is not None:
        try:
            descriptor["placement"] = _placement(placement)
        except (SketchSerializationError, TypeError, ValueError):
            pass
    return descriptor


def _subelement_names(value: Any) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        if all(isinstance(item, str) for item in value):
            return [item for item in value if item]
    return None


def _serialize_expressions(
    sketch: Any,
    warnings: list[str],
) -> list[dict[str, str]]:
    value = _safe_attribute(sketch, "ExpressionEngine")
    if value is _MISSING or value is None:
        return []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        warnings.append("ExpressionEngine is not iterable.")
        return []
    expressions: list[dict[str, str]] = []
    try:
        items = list(value)
    except Exception:  # noqa: BLE001 - property wrappers can fail while iterating.
        warnings.append("ExpressionEngine could not be read safely.")
        return []
    for item in items:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (str, bytes))
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            warnings.append("An expression could not be structured safely.")
            continue
        expressions.append({"property": item[0], "expression": item[1]})
    return sorted(
        expressions,
        key=lambda item: (item["property"], item["expression"]),
    )


def _serialize_visibility(sketch: Any, warnings: list[str]) -> bool | None:
    view_object = _safe_attribute(sketch, "ViewObject")
    if view_object is _MISSING or view_object is None:
        return None
    visibility = _safe_attribute(view_object, "Visibility")
    if visibility is _MISSING or visibility is None:
        return None
    if not isinstance(visibility, bool):
        warnings.append("Visibility is not a Boolean value.")
        return None
    return visibility


def _sequence_attribute(owner: Any, attribute_name: str) -> list[Any]:
    value = _required_attribute(owner, attribute_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SketchSerializationError(f"{attribute_name} must be a sequence")
    return list(value)


def _required_attribute(owner: Any, attribute_name: str) -> Any:
    value = _safe_attribute(owner, attribute_name)
    if value is _MISSING:
        raise SketchSerializationError(
            f"Required sketch attribute {attribute_name} is unavailable."
        )
    return value


def _required_text_attribute(owner: Any, attribute_name: str) -> str:
    value = _required_attribute(owner, attribute_name)
    if not isinstance(value, str) or not value.strip():
        raise SketchSerializationError(
            f"Required sketch attribute {attribute_name} must be text."
        )
    return value


def _text_attribute(owner: Any, attribute_name: str, *, default: str) -> str:
    value = _safe_attribute(owner, attribute_name)
    return value if isinstance(value, str) else default


def _optional_text_attribute(owner: Any, attribute_name: str) -> str | None:
    value = _safe_attribute(owner, attribute_name)
    if value is _MISSING or value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _optional_integer_attribute(owner: Any, attribute_name: str) -> int | None:
    value = _safe_attribute(owner, attribute_name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float_attribute(owner: Any, attribute_name: str) -> float | None:
    value = _safe_attribute(owner, attribute_name)
    if value is _MISSING or value is None:
        return None
    try:
        return _finite_float(value, attribute_name)
    except (TypeError, ValueError):
        return None


def _safe_attribute(owner: Any, attribute_name: str) -> Any:
    if owner is None or owner is _MISSING:
        return _MISSING
    try:
        return getattr(owner, attribute_name)
    except Exception:  # noqa: BLE001 - FreeCAD property wrappers vary by build.
        return _MISSING


def _finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(value, str) or not value.strip():
        raise SketchSerializationError("captured_at must be non-empty text")
    return value


def _require_uuid(value: object, field_name: str) -> None:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SketchSerializationError(f"{field_name} must be a UUID") from exc
    if str(parsed) != str(value):
        raise SketchSerializationError(
            f"{field_name} must use canonical UUID text"
        )


def _assert_json_safe(snapshot: Mapping[str, Any]) -> None:
    try:
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SketchSerializationError(
            "The sketch snapshot is not JSON-safe."
        ) from exc
