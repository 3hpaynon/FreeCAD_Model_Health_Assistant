"""Best-effort FreeCAD document extraction.

This module is safe to import outside FreeCAD. When FreeCAD is unavailable,
``get_active_document`` returns ``None`` and the other functions can still be
used with simple fake objects in tests or examples.
"""

from __future__ import annotations

from typing import Any

from model_health_assistant.models import FeatureCategory, FeatureInfo


_ADDITIVE_FEATURE_TYPES = {
    "PartDesign::Pad",
    "PartDesign::Revolution",
    "PartDesign::AdditiveLoft",
    "PartDesign::AdditivePipe",
    "PartDesign::AdditiveHelix",
}
_SUBTRACTIVE_FEATURE_TYPES = {
    "PartDesign::Pocket",
    "PartDesign::Groove",
    "PartDesign::SubtractiveLoft",
    "PartDesign::SubtractivePipe",
    "PartDesign::SubtractiveHelix",
}
_FINISHING_FEATURE_TYPES = {
    "PartDesign::Fillet",
    "PartDesign::Chamfer",
    "PartDesign::Draft",
    "PartDesign::Thickness",
}
_PATTERN_FEATURE_TYPES = {
    "PartDesign::LinearPattern",
    "PartDesign::PolarPattern",
    "PartDesign::Mirrored",
    "PartDesign::MultiTransform",
    "PartDesign::Scaled",
}
_REFERENCE_GEOMETRY_TYPES = {
    "App::OriginFeature",
    "App::Plane",
    "App::Line",
    "App::Point",
    "PartDesign::Plane",
    "PartDesign::Line",
    "PartDesign::Point",
    "PartDesign::DatumPlane",
    "PartDesign::DatumLine",
    "PartDesign::DatumPoint",
    "PartDesign::CoordinateSystem",
    "PartDesign::ShapeBinder",
    "PartDesign::SubShapeBinder",
}
_CONTAINER_TYPES = {
    "App::DocumentObjectGroup",
    "App::DocumentObjectGroupPython",
    "App::LinkGroup",
    "App::Origin",
    "App::Part",
    "PartDesign::Body",
}
_MISSING = object()

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None


def get_active_document() -> Any | None:
    """Return the active FreeCAD document when FreeCAD is available."""

    if App is None:
        return None
    return getattr(App, "ActiveDocument", None)


def extract_document_features(doc: Any | None) -> list[FeatureInfo]:
    """Extract basic feature information from a FreeCAD-like document."""

    if doc is None:
        return []

    objects = getattr(doc, "Objects", None) or []
    return [extract_feature_info(obj) for obj in objects]


def extract_feature_info(obj: Any) -> FeatureInfo:
    """Extract a defensive, JSON-friendly summary from a FreeCAD object."""

    name = _object_name(obj)
    label = str(getattr(obj, "Label", "") or name)
    type_id = str(getattr(obj, "TypeId", "") or "")
    is_sketch = is_sketch_object(obj)
    support = extract_sketch_support(obj) if is_sketch else {}

    metadata: dict[str, Any] = {}
    if is_sketch:
        _add_count_if_available(metadata, "geometry_count", getattr(obj, "Geometry", None))
        _add_count_if_available(metadata, "constraint_count", getattr(obj, "Constraints", None))
        constraint_status = getattr(obj, "ConstraintStatus", None)
        if constraint_status is not None:
            metadata["constraint_status"] = str(constraint_status)

    return FeatureInfo(
        internal_name=name,
        label=label,
        type_id=type_id,
        category=classify_feature_type(type_id),
        dependency_names=extract_dependencies(obj),
        downstream_dependent_names=extract_downstream_dependents(obj),
        is_sketch=is_sketch,
        attached_to_generated_geometry=bool(support.get("attached_to_generated_geometry", False)),
        support_name=support.get("support_name"),
        support_kind=support.get("support_kind"),
        recompute_status=_extract_recompute_status(obj),
        error_status=_extract_error_status(obj),
        notes=list(support.get("notes", [])),
        metadata=metadata,
        support_subelement=support.get("support_subelement"),
        support_is_stable_reference=bool(
            support.get("support_is_stable_reference", False)
        ),
        support_confidence=support.get("support_confidence", "low"),
    )


def classify_feature_type(type_id: str) -> FeatureCategory:
    """Classify a FreeCAD TypeId without relying on user-controlled object names."""

    normalized_type = str(type_id or "").strip()
    lower_type = normalized_type.lower()

    if "sketcher::sketchobject" in lower_type:
        return "sketch"
    if normalized_type in _CONTAINER_TYPES:
        return "container"
    if normalized_type in _REFERENCE_GEOMETRY_TYPES:
        return "reference_geometry"
    if normalized_type in _ADDITIVE_FEATURE_TYPES:
        return "additive_feature"
    if normalized_type in _SUBTRACTIVE_FEATURE_TYPES:
        return "subtractive_feature"
    if normalized_type in _FINISHING_FEATURE_TYPES:
        return "finishing_feature"
    if normalized_type in _PATTERN_FEATURE_TYPES:
        return "pattern_feature"

    type_name = normalized_type.rsplit("::", 1)[-1].lower()
    if type_name.startswith("additive"):
        return "additive_feature"
    if type_name.startswith("subtractive"):
        return "subtractive_feature"
    return "other"


def extract_dependencies(obj: Any) -> list[str]:
    """Return names of objects this object directly depends on.

    FreeCAD exposes direct upstream relationships through ``OutList``. The
    helper also supports simple fake objects used in tests.
    """

    out_list = getattr(obj, "OutList", None)
    if out_list is None and hasattr(obj, "getOutList"):
        try:
            out_list = obj.getOutList()
        except Exception:
            out_list = []
    return _names_from_objects(out_list or [])


def extract_downstream_dependents(obj: Any) -> list[str]:
    """Return names of objects that directly depend on this object."""

    in_list = getattr(obj, "InList", None)
    if in_list is None and hasattr(obj, "getInList"):
        try:
            in_list = obj.getInList()
        except Exception:
            in_list = []
    return _names_from_objects(in_list or [])


def is_sketch_object(obj: Any) -> bool:
    """Return ``True`` when an object appears to be a FreeCAD sketch."""

    type_id = str(getattr(obj, "TypeId", "") or "").lower()
    class_name = obj.__class__.__name__.lower()
    return "sketcher::sketchobject" in type_id or class_name.endswith("sketchobject")


def extract_sketch_support(obj: Any) -> dict[str, Any]:
    """Extract support information for a sketch-like object.

    FreeCAD sketch support data can vary by version and modeling context. This
    function intentionally classifies support conservatively.
    """

    try:
        raw_support = getattr(obj, "Support", _MISSING)
    except Exception as exc:
        return _unknown_support(
            f"Sketch support could not be read: {type(exc).__name__}: {exc}"
        )

    if raw_support is _MISSING:
        return _unknown_support("Sketch support property was not available.")

    if raw_support is None or (
        isinstance(raw_support, (list, tuple)) and not raw_support
    ):
        return {
            "support_name": None,
            "support_kind": "no_support",
            "support_subelement": None,
            "attached_to_generated_geometry": False,
            "support_is_stable_reference": False,
            "support_confidence": "high",
            "notes": ["Sketch has no support object."],
        }

    support_obj, sub_elements = _normalize_support(raw_support)

    if support_obj is None:
        return _unknown_support("Sketch support format was not recognized.")

    support_name = _object_name(support_obj)
    support_kind, is_stable_reference, confidence = _classify_support(
        support_obj, sub_elements
    )
    support_subelement = sub_elements[0] if sub_elements else None
    attached_to_generated_geometry = support_kind == "generated_geometry"

    return {
        "support_name": support_name,
        "support_kind": support_kind,
        "support_subelement": support_subelement,
        "attached_to_generated_geometry": attached_to_generated_geometry,
        "support_is_stable_reference": is_stable_reference,
        "support_confidence": confidence,
        "notes": [],
    }


def build_model_snapshot(doc: Any | None) -> list[FeatureInfo]:
    """Build the model snapshot used by the risk engine."""

    return extract_document_features(doc)


def _object_name(obj: Any) -> str:
    name = getattr(obj, "Name", None) or getattr(obj, "Label", None) or ""
    return str(name)


def _names_from_objects(objects: Any) -> list[str]:
    if hasattr(objects, "Name") or hasattr(objects, "Label"):
        objects = [objects]

    names: list[str] = []
    seen: set[str] = set()
    for item in objects:
        name = _object_name(item)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _normalize_support(raw_support: Any) -> tuple[Any | None, list[str]]:
    if raw_support is None:
        return None, []

    if hasattr(raw_support, "Name") or hasattr(raw_support, "Label"):
        return raw_support, []

    if not isinstance(raw_support, (list, tuple)) or not raw_support:
        return None, []

    first = raw_support[0]
    if hasattr(first, "Name") or hasattr(first, "Label"):
        sub_elements = _support_sub_elements(raw_support[1]) if len(raw_support) > 1 else []
        return first, sub_elements

    if isinstance(first, (list, tuple)) and first:
        support_obj = first[0]
        sub_elements = _support_sub_elements(first[1]) if len(first) > 1 else []
        return support_obj, sub_elements

    return None, []


def _classify_support(
    support_obj: Any,
    sub_elements: list[str],
) -> tuple[str, bool, str]:
    type_id = str(getattr(support_obj, "TypeId", "") or "")
    name = _object_name(support_obj)
    lower_type = type_id.lower()
    lower_name = name.lower()

    if any(_is_generated_sub_element(item) for item in sub_elements):
        return "generated_geometry", False, "high"

    origin_plane_names = {"xy_plane", "xz_plane", "yz_plane"}
    if lower_name in origin_plane_names or any(
        item.lower() in origin_plane_names for item in sub_elements
    ):
        return "origin_plane", True, "high"

    if lower_type in {"partdesign::plane", "partdesign::datumplane"}:
        return "datum_plane", True, "high"

    stable_reference_types = {
        "app::originfeature",
        "app::plane",
        "app::line",
        "app::point",
        "partdesign::line",
        "partdesign::point",
        "partdesign::datumline",
        "partdesign::datumpoint",
        "partdesign::coordinatesystem",
    }
    if lower_name == "origin" or lower_type in stable_reference_types:
        return "stable_reference", True, "high"

    if "sketcher::sketchobject" in lower_type:
        return "sketch", False, "medium"

    if "partdesign::" in lower_type or "part::" in lower_type:
        return "feature", False, "low"

    return "object", False, "low"


def _is_generated_sub_element(value: str) -> bool:
    lower_value = value.strip().lower()
    return lower_value.startswith(("face", "edge", "vertex"))


def _support_sub_elements(value: Any) -> list[str]:
    return [item.strip() for item in _coerce_string_list(value) if item.strip()]


def _unknown_support(note: str) -> dict[str, Any]:
    return {
        "support_name": None,
        "support_kind": "unknown",
        "support_subelement": None,
        "attached_to_generated_geometry": False,
        "support_is_stable_reference": False,
        "support_confidence": "low",
        "notes": [note],
    }


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _add_count_if_available(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    try:
        metadata[key] = len(value)
    except TypeError:
        return


def _extract_recompute_status(obj: Any) -> str | None:
    state = getattr(obj, "State", None)
    if state is None:
        return None

    values = _coerce_string_list(state)
    lower_values = [value.lower() for value in values]
    if any("error" in value or "invalid" in value or "failed" in value for value in lower_values):
        return "error"
    if any("touched" in value for value in lower_values):
        return "touched"
    return "ok"


def _extract_error_status(obj: Any) -> str | None:
    for attribute in ("Error", "ErrorString", "Status"):
        value = getattr(obj, attribute, None)
        if value:
            return str(value)
    return None
