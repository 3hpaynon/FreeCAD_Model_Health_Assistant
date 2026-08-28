"""Conservative restore-as-copy support for serialized FreeCAD sketches."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .recovery_vault import RECOVERY_SCHEMA_VERSION
from .sketch_snapshot import (
    DIMENSIONAL_CONSTRAINT_TYPES,
    SKETCH_SNAPSHOT_RECORD_TYPE,
    SKETCH_TYPE_ID,
    SUPPORTED_CONSTRAINT_TYPES,
    SUPPORTED_GEOMETRY_TYPES,
)

ReferenceValidator = Callable[[Any, Mapping[str, Any]], bool]

_UNUSED_GEOMETRY = -2000
_SAFE_EXPRESSION_PATH = re.compile(
    r"^Constraints(?:\[(?:0|[1-9][0-9]*)\]|\.[A-Za-z_][A-Za-z0-9_]*)$"
)
_INDEXED_CONSTRAINT_EXPRESSION = re.compile(
    r"^Constraints\[(?P<index>0|[1-9][0-9]*)\]$"
)


@dataclass(frozen=True, slots=True)
class SketchRestoreResult:
    """Structured outcome that does not expose wrapper exceptions to the UI."""

    success: bool
    status_code: str
    message: str
    restored_object_name: str | None = None
    restored_object_label: str | None = None
    warnings: tuple[str, ...] = ()
    error_stage: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    restored_object: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "message": self.message,
            "restored_object_name": self.restored_object_name,
            "restored_object_label": self.restored_object_label,
            "warnings": list(self.warnings),
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
        }


@dataclass(frozen=True, slots=True)
class RoundTripValidationResult:
    """Meaningful comparison of original and reconstructed snapshots."""

    success: bool
    status_code: str
    message: str
    differences: tuple[str, ...]
    recompute_succeeded: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["differences"] = list(self.differences)
        return payload


class SketchReconstructionError(ValueError):
    """Raised internally when a snapshot cannot be reconstructed faithfully."""


def restore_sketch_as_copy(
    document: Any,
    snapshot: Mapping[str, Any],
    *,
    app_module: Any | None = None,
    part_module: Any | None = None,
    sketcher_module: Any | None = None,
    restore_attachment: bool = False,
    restore_external_geometry: bool = False,
    reference_validator: ReferenceValidator | None = None,
    detach_unsafe_references: bool = False,
) -> SketchRestoreResult:
    """Restore one eligible snapshot as a new sketch in ``document``.

    Existing objects are never replaced.  Attachment and external references
    are opt-in because a surviving ``Face4`` or ``Edge2`` name does not prove
    that the topological element still represents the original geometry.
    """

    preflight_warnings: tuple[str, ...] = ()
    try:
        object_record = _validated_snapshot_object(snapshot)
        if detach_unsafe_references:
            if restore_attachment or restore_external_geometry:
                raise SketchReconstructionError(
                    "detached restoration cannot restore recorded references"
                )
            object_record, preflight_warnings = _detached_object_record(
                object_record
            )
        app = app_module or importlib.import_module("FreeCAD")
        part = part_module or importlib.import_module("Part")
        sketcher = sketcher_module or importlib.import_module("Sketcher")
        internal_name = _unique_internal_name(document, object_record["name"])
        label = _unique_label(document, object_record["label"])
        _validate_reference_policy(
            object_record,
            restore_attachment=restore_attachment,
            restore_external_geometry=restore_external_geometry,
            reference_validator=reference_validator,
        )
    except Exception as exc:  # noqa: BLE001 - return a controlled preflight result.
        return SketchRestoreResult(
            success=False,
            status_code="restore_preflight_failed",
            message="The sketch snapshot could not be prepared for restoration.",
            error_stage="preflight",
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )

    open_transaction = getattr(document, "openTransaction", None)
    commit_transaction = getattr(document, "commitTransaction", None)
    abort_transaction = getattr(document, "abortTransaction", None)
    if not all(
        callable(method)
        for method in (open_transaction, commit_transaction, abort_transaction)
    ):
        return SketchRestoreResult(
            success=False,
            status_code="transactions_unavailable",
            message="The document does not provide safe transaction APIs.",
            error_stage="preflight",
        )

    warnings: list[str] = list(preflight_warnings)
    restored_object = None
    restored_object_name_for_rollback: str | None = None
    transaction_open = False
    stage = "open_transaction"
    try:
        open_transaction("Model Health Assistant: Restore sketch as copy")
        transaction_open = True

        stage = "create_sketch"
        restored_object = document.addObject(SKETCH_TYPE_ID, internal_name)
        restored_object_name_for_rollback = str(restored_object.Name)
        restored_object.Label = label

        stage = "external_geometry"
        if restore_external_geometry:
            _restore_external_geometry(
                document,
                restored_object,
                object_record["external_geometry"],
                reference_validator,
            )

        stage = "geometry"
        _restore_geometry(restored_object, object_record["geometry"], app, part)

        stage = "constraints"
        _restore_constraints(
            restored_object,
            object_record["constraints"],
            sketcher,
        )

        stage = "placement"
        placement = object_record.get("placement")
        if placement is not None:
            restored_object.Placement = _build_placement(placement, app)

        stage = "expressions"
        _restore_expressions(
            restored_object,
            object_record.get("expressions", []),
            warnings,
        )

        stage = "attachment"
        _restore_attachment_state(
            document,
            restored_object,
            object_record,
            app,
            restore_attachment=restore_attachment,
            reference_validator=reference_validator,
            warnings=warnings,
        )

        stage = "visibility"
        _restore_visibility(restored_object, object_record.get("visibility"), warnings)

        stage = "recompute"
        recompute_result = document.recompute()
        if recompute_result is False:
            raise SketchReconstructionError("FreeCAD recompute reported failure")

        stage = "commit_transaction"
        commit_transaction()
        transaction_open = False
    except Exception as exc:  # noqa: BLE001 - FreeCAD wrapper failures vary.
        rollback_warnings = _rollback_restore(
            document,
            restored_object,
            restored_object_name=restored_object_name_for_rollback,
            transaction_open=transaction_open,
            abort_transaction=abort_transaction,
        )
        return SketchRestoreResult(
            success=False,
            status_code="restore_failed",
            message="The sketch restore failed and was rolled back.",
            warnings=tuple((*warnings, *rollback_warnings)),
            error_stage=stage,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )

    return SketchRestoreResult(
        success=True,
        status_code="restored_as_copy",
        message="The sketch was restored as a new copy.",
        restored_object_name=str(restored_object.Name),
        restored_object_label=str(restored_object.Label),
        warnings=tuple(warnings),
        restored_object=restored_object,
    )


def compare_sketch_snapshots(
    original: Mapping[str, Any],
    restored: Mapping[str, Any],
    *,
    tolerance: float = 1e-9,
    recompute_succeeded: bool = True,
) -> RoundTripValidationResult:
    """Compare reconstruction-relevant state without requiring JSON equality."""

    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be a finite non-negative number")
    original_object = _validated_snapshot_object(original)
    restored_object = _validated_snapshot_object(restored)
    differences: list[str] = []

    _compare_count(
        differences,
        "geometry",
        original_object["geometry"],
        restored_object["geometry"],
    )
    _compare_sequence_field(
        differences,
        "geometry types",
        original_object["geometry"],
        restored_object["geometry"],
        "type",
    )
    _compare_sequence_field(
        differences,
        "construction flags",
        original_object["geometry"],
        restored_object["geometry"],
        "construction",
    )
    _compare_geometry_data(
        differences,
        original_object["geometry"],
        restored_object["geometry"],
        tolerance,
    )
    _compare_count(
        differences,
        "constraint",
        original_object["constraints"],
        restored_object["constraints"],
    )
    _compare_sequence_field(
        differences,
        "constraint types",
        original_object["constraints"],
        restored_object["constraints"],
        "type",
    )
    if not _values_close(
        original_object.get("placement"),
        restored_object.get("placement"),
        tolerance,
    ):
        differences.append("placement differs")
    if not recompute_succeeded:
        differences.append("document recompute did not succeed")

    success = not differences
    return RoundTripValidationResult(
        success=success,
        status_code="round_trip_valid" if success else "round_trip_mismatch",
        message=(
            "The reconstructed sketch matches the original snapshot."
            if success
            else "The reconstructed sketch differs from the original snapshot."
        ),
        differences=tuple(differences),
        recompute_succeeded=recompute_succeeded,
    )


def _validated_snapshot_object(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise SketchReconstructionError("snapshot must be a mapping")
    if snapshot.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        raise SketchReconstructionError("snapshot schema version is unsupported")
    if snapshot.get("record_type") != SKETCH_SNAPSHOT_RECORD_TYPE:
        raise SketchReconstructionError("snapshot record type is unsupported")
    recovery = snapshot.get("recovery")
    if not isinstance(recovery, Mapping) or recovery.get(
        "restore_as_copy_eligibility"
    ) != "eligible":
        raise SketchReconstructionError("snapshot is not eligible for restoration")
    object_record = snapshot.get("object")
    if not isinstance(object_record, Mapping):
        raise SketchReconstructionError("snapshot object record is invalid")
    required_text = {"name", "label", "type_id"}
    if not all(isinstance(object_record.get(key), str) for key in required_text):
        raise SketchReconstructionError("snapshot object identity is invalid")
    if object_record["type_id"] != SKETCH_TYPE_ID:
        raise SketchReconstructionError("snapshot is not a supported sketch")
    for key in ("geometry", "constraints"):
        value = object_record.get(key)
        if not isinstance(value, list):
            raise SketchReconstructionError(f"snapshot {key} must be a list")
    normalized = dict(object_record)
    for key in (
        "expressions",
        "support",
        "attachment_support",
        "external_geometry",
    ):
        value = normalized.get(key, [])
        if not isinstance(value, list):
            raise SketchReconstructionError(f"snapshot {key} must be a list")
        normalized[key] = value
    return normalized


def _detached_object_record(
    object_record: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove only constraints that depend on unsafe external geometry."""

    original_constraints = object_record["constraints"]
    kept_constraints: list[dict[str, Any]] = []
    index_mapping: dict[int, int] = {}
    kept_names: set[str] = set()
    for old_index, constraint in enumerate(original_constraints):
        if not isinstance(constraint, Mapping):
            kept_constraints.append(constraint)
            continue
        if _constraint_uses_external_geometry(constraint):
            continue
        new_index = len(kept_constraints)
        normalized = dict(constraint)
        normalized["index"] = new_index
        kept_constraints.append(normalized)
        index_mapping[old_index] = new_index
        name = constraint.get("name")
        if isinstance(name, str) and name:
            kept_names.add(name)

    expressions: list[dict[str, str]] = []
    omitted_expressions = 0
    for expression in object_record.get("expressions", []):
        if not isinstance(expression, Mapping):
            omitted_expressions += 1
            continue
        property_path = expression.get("property")
        expression_text = expression.get("expression")
        if not isinstance(property_path, str) or not isinstance(expression_text, str):
            omitted_expressions += 1
            continue
        indexed_match = _INDEXED_CONSTRAINT_EXPRESSION.fullmatch(property_path)
        if indexed_match is not None:
            old_index = int(indexed_match.group("index"))
            if old_index not in index_mapping:
                omitted_expressions += 1
                continue
            property_path = f"Constraints[{index_mapping[old_index]}]"
        elif property_path.startswith("Constraints."):
            if property_path.removeprefix("Constraints.") not in kept_names:
                omitted_expressions += 1
                continue
        expressions.append(
            {"property": property_path, "expression": expression_text}
        )

    omitted_constraints = len(original_constraints) - len(kept_constraints)
    warnings: list[str] = []
    if object_record.get("support") or object_record.get("attachment_support"):
        warnings.append(
            "Original support was not restored; the recovered sketch is detached."
        )
    if object_record.get("external_geometry"):
        warnings.append("Recorded external geometry was not restored in detached mode.")
    if omitted_constraints:
        warnings.append(
            f"{omitted_constraints} external-reference constraint(s) were omitted."
        )
    if omitted_expressions:
        warnings.append(
            f"{omitted_expressions} dependent expression(s) were omitted."
        )
    normalized_object = dict(object_record)
    normalized_object["constraints"] = kept_constraints
    normalized_object["constraint_count"] = len(kept_constraints)
    normalized_object["expressions"] = expressions
    return normalized_object, tuple(warnings)


def _unique_internal_name(document: Any, original_name: str) -> str:
    ascii_name = original_name.encode("ascii", errors="ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name).strip("_")
    if not cleaned:
        cleaned = "Sketch"
    if not cleaned[0].isalpha():
        cleaned = "Sketch_" + cleaned
    base = cleaned + "_Recovered"
    return _unique_name(document, base)


def _unique_name(document: Any, base: str) -> str:
    existing = {
        str(getattr(item, "Name", ""))
        for item in _document_objects(document)
    }
    candidate = base
    suffix = 2
    while candidate in existing or _get_document_object(document, candidate) is not None:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _unique_label(document: Any, original_label: str) -> str:
    label_base = (original_label or "Recovered Sketch") + " (Recovered)"
    existing = {
        str(getattr(item, "Label", ""))
        for item in _document_objects(document)
    }
    candidate = label_base
    suffix = 2
    while candidate in existing:
        candidate = f"{label_base} {suffix}"
        suffix += 1
    return candidate


def _document_objects(document: Any) -> list[Any]:
    objects = getattr(document, "Objects", ())
    if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes)):
        return list(objects)
    return []


def _get_document_object(document: Any, name: str) -> Any | None:
    getter = getattr(document, "getObject", None)
    if callable(getter):
        return getter(name)
    for item in _document_objects(document):
        if getattr(item, "Name", None) == name:
            return item
    return None


def _restore_geometry(
    sketch: Any,
    geometry_records: list[dict[str, Any]],
    app: Any,
    part: Any,
) -> None:
    for expected_index, record in enumerate(geometry_records):
        if not isinstance(record, Mapping) or record.get("supported") is not True:
            raise SketchReconstructionError("snapshot contains unsupported geometry")
        if record.get("index") != expected_index:
            raise SketchReconstructionError("geometry indexes are not contiguous")
        geometry_type = record.get("type")
        if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
            raise SketchReconstructionError("geometry type is unsupported")
        geometry = _build_geometry(geometry_type, record.get("data"), app, part)
        result = sketch.addGeometry(geometry, False)
        if isinstance(result, int) and result != expected_index:
            raise SketchReconstructionError("FreeCAD returned an unexpected geometry index")
        construction = record.get("construction")
        if not isinstance(construction, bool):
            raise SketchReconstructionError("construction state is invalid")
        if construction:
            setter = getattr(sketch, "setConstruction", None)
            if callable(setter):
                setter(expected_index, True)
            else:
                sketch.toggleConstruction(expected_index)


def _build_geometry(geometry_type: str, data: Any, app: Any, part: Any) -> Any:
    if not isinstance(data, Mapping):
        raise SketchReconstructionError("geometry data is invalid")
    if geometry_type == "LineSegment":
        return part.LineSegment(
            _build_vector(data.get("start"), app),
            _build_vector(data.get("end"), app),
        )
    if geometry_type == "Circle":
        return part.Circle(
            _build_vector(data.get("center"), app),
            _build_vector(data.get("axis"), app),
            _number(data.get("radius"), "circle radius"),
        )
    if geometry_type == "ArcOfCircle":
        circle = part.Circle(
            _build_vector(data.get("center"), app),
            _build_vector(data.get("axis"), app),
            _number(data.get("radius"), "arc radius"),
        )
        return part.ArcOfCircle(
            circle,
            _number(data.get("first_parameter"), "arc first parameter"),
            _number(data.get("last_parameter"), "arc last parameter"),
        )
    if geometry_type == "Point":
        return part.Point(_build_vector(data.get("point"), app))
    raise SketchReconstructionError("geometry type is unsupported")


def _restore_constraints(
    sketch: Any,
    constraint_records: list[dict[str, Any]],
    sketcher: Any,
) -> None:
    for expected_index, record in enumerate(constraint_records):
        if not isinstance(record, Mapping) or record.get("supported") is not True:
            raise SketchReconstructionError("snapshot contains unsupported constraints")
        if record.get("index") != expected_index:
            raise SketchReconstructionError("constraint indexes are not contiguous")
        constraint_type = record.get("type")
        if constraint_type not in SUPPORTED_CONSTRAINT_TYPES:
            raise SketchReconstructionError("constraint type is unsupported")
        arguments = _constraint_constructor_arguments(record)
        constraint = sketcher.Constraint(constraint_type, *arguments)
        result = sketch.addConstraint(constraint)
        constraint_index = result if isinstance(result, int) else expected_index
        if constraint_index != expected_index:
            raise SketchReconstructionError(
                "FreeCAD returned an unexpected constraint index"
            )

        name = record.get("name")
        if isinstance(name, str) and name:
            sketch.renameConstraint(constraint_index, name)
        if (
            constraint_type in DIMENSIONAL_CONSTRAINT_TYPES
            and record.get("driving") is False
        ):
            sketch.setDriving(constraint_index, False)
        if record.get("in_virtual_space") is True:
            sketch.setVirtualSpace(constraint_index, True)
        if record.get("active") is False:
            sketch.setActive(constraint_index, False)


def _constraint_constructor_arguments(record: Mapping[str, Any]) -> list[Any]:
    constraint_type = str(record["type"])
    arguments = record.get("arguments")
    if not isinstance(arguments, Mapping):
        raise SketchReconstructionError("constraint arguments are invalid")
    values = {
        name: _integer(arguments.get(name), f"constraint {name}")
        for name in (
            "first",
            "first_pos",
            "second",
            "second_pos",
            "third",
            "third_pos",
        )
    }
    first = values["first"]
    first_pos = values["first_pos"]
    second = values["second"]
    second_pos = values["second_pos"]
    third = values["third"]
    third_pos = values["third_pos"]

    if constraint_type in {"Horizontal", "Vertical"} and not (
        first_pos or second != _UNUSED_GEOMETRY or second_pos
    ):
        return [first]
    if constraint_type in {"Horizontal", "Vertical"}:
        return [first, first_pos, second, second_pos]
    if constraint_type == "Coincident":
        return [first, first_pos, second, second_pos]
    if constraint_type in {"Equal", "Parallel", "Perpendicular"}:
        return [first, second]
    if constraint_type == "PointOnObject":
        return [first, first_pos, second]
    if constraint_type == "Symmetric":
        if first_pos or second_pos or third_pos:
            return [first, first_pos, second, second_pos, third, third_pos]
        return [first, second, third]
    if constraint_type == "Tangent":
        if first_pos or second_pos:
            return [first, first_pos, second, second_pos]
        return [first, second]

    value = _number(record.get("value"), "constraint value")
    if constraint_type in {"Radius", "Diameter"}:
        return [first, value]
    if constraint_type in {"Distance", "DistanceX", "DistanceY", "Angle"}:
        if first_pos:
            if second != _UNUSED_GEOMETRY:
                if second_pos:
                    return [first, first_pos, second, second_pos, value]
                return [first, first_pos, second, value]
            return [first, first_pos, value]
        if second != _UNUSED_GEOMETRY:
            if second_pos:
                return [first, second, second_pos, value]
            return [first, second, value]
        return [first, value]
    raise SketchReconstructionError("constraint constructor is unsupported")


def _build_vector(data: Any, app: Any) -> Any:
    if not isinstance(data, Mapping):
        raise SketchReconstructionError("vector data is invalid")
    return app.Vector(
        _number(data.get("x"), "vector x"),
        _number(data.get("y"), "vector y"),
        _number(data.get("z"), "vector z"),
    )


def _build_placement(data: Any, app: Any) -> Any:
    if not isinstance(data, Mapping):
        raise SketchReconstructionError("placement data is invalid")
    rotation_data = data.get("rotation")
    if not isinstance(rotation_data, Mapping):
        raise SketchReconstructionError("placement rotation is invalid")
    quaternion = rotation_data.get("quaternion")
    if not isinstance(quaternion, list) or len(quaternion) != 4:
        raise SketchReconstructionError("placement quaternion is invalid")
    return app.Placement(
        _build_vector(data.get("base"), app),
        app.Rotation(*[_number(value, "quaternion") for value in quaternion]),
    )


def _restore_expressions(
    sketch: Any,
    expressions: Any,
    warnings: list[str],
) -> None:
    if not isinstance(expressions, list):
        raise SketchReconstructionError("expressions must be a list")
    setter = getattr(sketch, "setExpression", None)
    for record in expressions:
        if not isinstance(record, Mapping):
            warnings.append("An invalid expression was skipped.")
            continue
        property_path = record.get("property")
        expression = record.get("expression")
        if (
            not isinstance(property_path, str)
            or not _SAFE_EXPRESSION_PATH.fullmatch(property_path)
            or not isinstance(expression, str)
            or not expression.strip()
            or not callable(setter)
        ):
            warnings.append("An unsafe or unsupported expression was skipped.")
            continue
        try:
            setter(property_path, expression)
        except Exception:  # noqa: BLE001 - invalid external references are non-fatal.
            warnings.append(f"Expression for {property_path} could not be restored.")


def _validate_reference_policy(
    object_record: Mapping[str, Any],
    *,
    restore_attachment: bool,
    restore_external_geometry: bool,
    reference_validator: ReferenceValidator | None,
) -> None:
    support_records = object_record.get("support") or object_record.get(
        "attachment_support"
    )
    if restore_attachment and support_records and reference_validator is None:
        raise SketchReconstructionError("attachment validation is required")
    if (
        restore_external_geometry
        and object_record.get("external_geometry")
        and reference_validator is None
    ):
        raise SketchReconstructionError("external-reference validation is required")
    if _constraints_use_external_geometry(object_record["constraints"]) and not (
        restore_external_geometry and reference_validator is not None
    ):
        raise SketchReconstructionError(
            "external geometry must be validated before restoring its constraints"
        )


def _constraints_use_external_geometry(constraints: list[Any]) -> bool:
    for constraint in constraints:
        if isinstance(constraint, Mapping) and _constraint_uses_external_geometry(
            constraint
        ):
            return True
    return False


def _constraint_uses_external_geometry(constraint: Mapping[str, Any]) -> bool:
    arguments = constraint.get("arguments")
    if not isinstance(arguments, Mapping):
        return False
    return any(
        isinstance(arguments.get(key), int)
        and _UNUSED_GEOMETRY < arguments[key] <= -3
        for key in ("first", "second", "third")
    )


def _restore_external_geometry(
    document: Any,
    sketch: Any,
    references: Any,
    validator: ReferenceValidator | None,
) -> None:
    for reference in _validated_references(document, references, validator):
        linked_object, subelements = reference
        for subelement in subelements:
            sketch.addExternal(str(linked_object.Name), subelement)


def _restore_attachment_state(
    document: Any,
    sketch: Any,
    object_record: Mapping[str, Any],
    app: Any,
    *,
    restore_attachment: bool,
    reference_validator: ReferenceValidator | None,
    warnings: list[str],
) -> None:
    legacy_support_records = object_record.get("support", [])
    attachment_support_records = object_record.get("attachment_support", [])
    prefer_attachment_support = bool(
        attachment_support_records and not legacy_support_records
    )
    support_records = legacy_support_records or attachment_support_records
    map_mode = object_record.get("map_mode")
    if support_records and not restore_attachment:
        warnings.append("Recorded attachment was not restored without validation.")
    elif support_records:
        resolved = _validated_references(
            document,
            support_records,
            reference_validator,
        )
        support_value = [
            (linked_object, tuple(subelements or [""]))
            for linked_object, subelements in resolved
        ]
        _assign_attachment_support(
            sketch,
            support_value,
            prefer_attachment_support=prefer_attachment_support,
        )
        if isinstance(map_mode, str) and map_mode:
            sketch.MapMode = map_mode
        offset = object_record.get("attachment_offset")
        if offset is not None:
            sketch.AttachmentOffset = _build_placement(offset, app)
        return

    if isinstance(map_mode, str) and map_mode in {"Deactivated", "DeactivatedMode"}:
        sketch.MapMode = map_mode


def _assign_attachment_support(
    sketch: Any,
    support_value: list[tuple[Any, tuple[str, ...]]],
    *,
    prefer_attachment_support: bool,
) -> None:
    """Write the attachment property exposed by this FreeCAD generation."""

    property_names = (
        ("AttachmentSupport", "Support")
        if prefer_attachment_support
        else ("Support", "AttachmentSupport")
    )
    assigned_value: object = (
        support_value[0] if len(support_value) == 1 else support_value
    )
    for property_name in property_names:
        try:
            getattr(sketch, property_name)
        except (AttributeError, ReferenceError):
            continue
        try:
            setattr(sketch, property_name, assigned_value)
        except AttributeError:
            continue
        return
    raise SketchReconstructionError(
        "restored sketch does not expose a supported attachment property"
    )


def _restore_visibility(
    sketch: Any,
    visibility: Any,
    warnings: list[str],
) -> None:
    if visibility is None:
        return
    if not isinstance(visibility, bool):
        warnings.append("Recorded visibility was invalid and was skipped.")
        return
    view_object = getattr(sketch, "ViewObject", None)
    if view_object is None:
        warnings.append("Recorded visibility could not be restored in this environment.")
        return
    try:
        view_object.Visibility = visibility
    except Exception:  # noqa: BLE001 - GUI wrappers differ in headless mode.
        warnings.append("Recorded visibility could not be restored.")


def _validated_references(
    document: Any,
    references: Any,
    validator: ReferenceValidator | None,
) -> list[tuple[Any, list[str]]]:
    if not isinstance(references, list):
        raise SketchReconstructionError("references must be a list")
    if references and validator is None:
        raise SketchReconstructionError("reference validation is required")
    resolved: list[tuple[Any, list[str]]] = []
    for reference in references:
        if not isinstance(reference, Mapping):
            raise SketchReconstructionError("reference record is invalid")
        descriptor = reference.get("object")
        subelements = reference.get("subelements")
        if not isinstance(descriptor, Mapping) or not isinstance(subelements, list):
            raise SketchReconstructionError("reference fields are invalid")
        name = descriptor.get("name")
        if not isinstance(name, str) or not all(
            isinstance(item, str) and item for item in subelements
        ):
            raise SketchReconstructionError("reference identity is invalid")
        linked_object = _get_document_object(document, name)
        if linked_object is None:
            raise SketchReconstructionError("referenced object is unavailable")
        type_id = descriptor.get("type_id")
        if isinstance(type_id, str) and type_id and getattr(
            linked_object, "TypeId", None
        ) != type_id:
            raise SketchReconstructionError("referenced object type changed")
        try:
            valid = bool(validator(document, reference)) if validator else False
        except Exception as exc:  # noqa: BLE001 - validators may inspect wrappers.
            raise SketchReconstructionError("reference validation failed") from exc
        if not valid:
            raise SketchReconstructionError("reference was not validated")
        resolved.append((linked_object, list(subelements)))
    return resolved


def _rollback_restore(
    document: Any,
    restored_object: Any,
    *,
    restored_object_name: str | None,
    transaction_open: bool,
    abort_transaction: Any,
) -> list[str]:
    warnings: list[str] = []
    if transaction_open:
        try:
            abort_transaction()
        except Exception:  # noqa: BLE001 - continue with explicit cleanup.
            warnings.append("The restore transaction could not be aborted normally.")
    if restored_object is None or not isinstance(restored_object_name, str):
        return warnings
    try:
        current = _get_document_object(document, restored_object_name)
        if current is restored_object:
            document.removeObject(restored_object_name)
    except Exception:  # noqa: BLE001 - report rather than masking original failure.
        warnings.append("The partially created sketch could not be removed automatically.")
    return warnings


def _compare_count(
    differences: list[str],
    label: str,
    original: list[Any],
    restored: list[Any],
) -> None:
    if len(original) != len(restored):
        differences.append(
            f"{label} count differs: expected {len(original)}, got {len(restored)}"
        )


def _compare_sequence_field(
    differences: list[str],
    label: str,
    original: list[Any],
    restored: list[Any],
    field_name: str,
) -> None:
    original_values = [
        item.get(field_name) if isinstance(item, Mapping) else None
        for item in original
    ]
    restored_values = [
        item.get(field_name) if isinstance(item, Mapping) else None
        for item in restored
    ]
    if original_values != restored_values:
        differences.append(f"{label} differ")


def _compare_geometry_data(
    differences: list[str],
    original: list[Any],
    restored: list[Any],
    tolerance: float,
) -> None:
    for index, (expected, actual) in enumerate(zip(original, restored)):
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            differences.append(f"geometry {index} record differs")
            continue
        if not _values_close(expected.get("data"), actual.get("data"), tolerance):
            differences.append(f"geometry {index} numeric data differs")


def _values_close(expected: Any, actual: Any, tolerance: float) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(
            float(expected),
            float(actual),
            rel_tol=tolerance,
            abs_tol=tolerance,
        )
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        return set(expected) == set(actual) and all(
            _values_close(expected[key], actual[key], tolerance)
            for key in expected
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _values_close(left, right, tolerance)
            for left, right in zip(expected, actual)
        )
    return expected == actual


def _integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SketchReconstructionError(f"{field_name} must be an integer")
    return value


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise SketchReconstructionError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SketchReconstructionError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise SketchReconstructionError(f"{field_name} must be finite")
    return number
