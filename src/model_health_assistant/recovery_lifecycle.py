"""Durable deleted-sketch lifecycle and deterministic recovery assessment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from .recovery_vault import (
    RECOVERY_SCHEMA_VERSION,
    RecoveryIndexEntry,
    RecoveryVault,
    RecoveryVaultCorruptionError,
)
from .sketch_snapshot import (
    DIMENSIONAL_CONSTRAINT_TYPES,
    SKETCH_SNAPSHOT_RECORD_TYPE,
    SKETCH_TYPE_ID,
    SUPPORTED_CONSTRAINT_TYPES,
    SUPPORTED_GEOMETRY_TYPES,
)

_CONSTRAINT_ARGUMENT_FIELDS = (
    "first",
    "first_pos",
    "second",
    "second_pos",
    "third",
    "third_pos",
)
_SAFE_EXPRESSION_PATH = re.compile(
    r"^Constraints(?:\[(?:0|[1-9][0-9]*)\]|\.[A-Za-z_][A-Za-z0-9_]*)$"
)

ELIGIBILITY_AVAILABLE = "available"
ELIGIBILITY_AVAILABLE_WITH_WARNING = "available_with_warning"
ELIGIBILITY_PARTIAL = "partial"
ELIGIBILITY_UNAVAILABLE = "unavailable"

SUPPORT_NOT_APPLICABLE = "not_applicable"
SUPPORT_RECORDED_UNRESOLVED = "recorded_unresolved"
SUPPORT_EXTERNAL_UNRESOLVED = "external_references_unresolved"


class RecoveryLifecycleError(RuntimeError):
    """Raised when deletion metadata cannot safely transition the vault."""


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Deterministic assessment derived from structured snapshot fields."""

    eligibility: str
    support_status: str
    unsupported_elements: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    critical_issues: tuple[str, ...] = ()

    @property
    def lifecycle_status(self) -> str:
        if self.eligibility == ELIGIBILITY_PARTIAL:
            return "partial"
        if self.eligibility == ELIGIBILITY_UNAVAILABLE:
            return "unsupported"
        return "deleted"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("unsupported_elements", "warnings", "critical_issues"):
            payload[key] = list(payload[key])
        payload["lifecycle_status"] = self.lifecycle_status
        return payload


@dataclass(frozen=True, slots=True)
class DeletedSketchRecord:
    """UI-ready durable metadata for one deleted sketch incarnation."""

    schema_version: str
    document_id: str
    object_id: str
    object_name: str
    object_label: str
    type_id: str
    status: str
    deleted_at: str
    latest_snapshot_id: str
    recovery_eligibility: str
    support_status: str
    unsupported_elements: tuple[str, ...]
    warnings: tuple[str, ...]
    geometry_count: int | None
    constraint_count: int | None
    last_restored_at: str | None = None
    restored_object_names: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["unsupported_elements"] = list(self.unsupported_elements)
        payload["warnings"] = list(self.warnings)
        payload["restored_object_names"] = list(self.restored_object_names)
        return payload


@dataclass(frozen=True, slots=True)
class DeletionLifecycleResult:
    """Idempotent deletion transition result."""

    created: bool
    record: DeletedSketchRecord

    def to_dict(self) -> dict[str, object]:
        return {"created": self.created, "record": self.record.to_dict()}


def assess_sketch_snapshot(snapshot: object) -> RecoveryAssessment:
    """Assess recovery based on content, never merely on JSON existence."""

    critical: list[str] = []
    warnings: list[str] = []
    unsupported: set[str] = set()
    if not isinstance(snapshot, Mapping):
        return _unavailable_assessment("Snapshot is not a structured object.")

    if snapshot.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        critical.append("Snapshot schema version is unsupported.")
    if snapshot.get("record_type") != SKETCH_SNAPSHOT_RECORD_TYPE:
        critical.append("Snapshot record type is unsupported.")
    for field_name in ("document_id", "object_id", "snapshot_id"):
        if not _is_canonical_uuid(snapshot.get(field_name)):
            critical.append(f"Snapshot {field_name} is invalid.")
    if not _is_timestamp(snapshot.get("captured_at")):
        critical.append("Snapshot capture time is invalid.")

    object_record = snapshot.get("object")
    if not isinstance(object_record, Mapping):
        critical.append("Snapshot object data is missing.")
        return _assessment_from_findings(
            critical=critical,
            warnings=warnings,
            unsupported=unsupported,
            support_status="unknown",
            serialization_status=None,
            restore_eligibility=None,
        )

    for field_name in ("name", "type_id"):
        value = object_record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            critical.append(f"Object {field_name} is invalid.")
    if not isinstance(object_record.get("label"), str):
        critical.append("Object label is invalid.")
    if object_record.get("type_id") != SKETCH_TYPE_ID:
        critical.append("Object type is not a supported sketch.")

    geometry = object_record.get("geometry")
    constraints = object_record.get("constraints")
    _assess_geometry(geometry, object_record.get("geometry_count"), critical, unsupported)
    _assess_constraints(
        constraints,
        object_record.get("constraint_count"),
        critical,
        unsupported,
    )
    if not _valid_placement(object_record.get("placement")):
        critical.append("Sketch placement is missing or invalid.")
    _assess_optional_object_state(object_record, critical)

    support_status = _assess_references(object_record, critical, warnings)
    recovery = snapshot.get("recovery")
    serialization_status = None
    restore_eligibility = None
    if not isinstance(recovery, Mapping):
        critical.append("Recovery assessment metadata is missing.")
    else:
        serialization_status = recovery.get("serialization_status")
        restore_eligibility = recovery.get("restore_as_copy_eligibility")
        if serialization_status not in {"complete", "partial"}:
            critical.append("Serialization status is invalid.")
        if restore_eligibility not in {"eligible", "unsupported"}:
            critical.append("Restore eligibility metadata is invalid.")
        observed_unsupported = set(unsupported)
        declared_unsupported = _declared_unsupported(recovery, critical)
        unsupported.update(declared_unsupported)
        if declared_unsupported != observed_unsupported:
            critical.append("Declared unsupported elements are inconsistent.")
        declared_warnings = recovery.get("warnings")
        if not isinstance(declared_warnings, list) or not all(
            isinstance(item, str) for item in declared_warnings
        ):
            critical.append("Recovery warning metadata is invalid.")
        elif any(not item.strip() for item in declared_warnings):
            critical.append("Recovery warning metadata contains empty text.")
        else:
            warnings.extend(declared_warnings)
        if observed_unsupported and (
            serialization_status != "partial"
            or restore_eligibility != "unsupported"
        ):
            critical.append("Unsupported elements conflict with recovery metadata.")

    return _assessment_from_findings(
        critical=critical,
        warnings=warnings,
        unsupported=unsupported,
        support_status=support_status,
        serialization_status=serialization_status,
        restore_eligibility=restore_eligibility,
    )


class RecoveryLifecycleService:
    """Transition cached active snapshots into durable deleted records."""

    def __init__(self, vault: RecoveryVault) -> None:
        if not isinstance(vault, RecoveryVault):
            raise TypeError("vault must be a RecoveryVault")
        self.vault = vault

    def record_deletion(self, candidate: Any) -> DeletionLifecycleResult:
        """Persist one deletion without adding or removing snapshot files."""

        document_id = _candidate_text(candidate, "document_id")
        object_id = _candidate_text(candidate, "object_id")
        snapshot_id = _candidate_text(candidate, "snapshot_id")
        observed_at = _candidate_text(candidate, "observed_at")
        for field_name, value in (
            ("document_id", document_id),
            ("object_id", object_id),
            ("snapshot_id", snapshot_id),
        ):
            if not _is_canonical_uuid(value):
                raise RecoveryLifecycleError(f"{field_name} is invalid")
        if not _is_timestamp(observed_at):
            raise RecoveryLifecycleError("deletion timestamp is invalid")

        entry = self._load_entry(document_id, object_id)
        if entry.latest_snapshot_id != snapshot_id:
            raise RecoveryLifecycleError(
                "deletion candidate does not reference the latest snapshot"
            )
        if entry.deleted_at is not None:
            return DeletionLifecycleResult(
                created=False,
                record=self._record_from_entry(entry),
            )

        snapshot = self.vault.load_snapshot(document_id, snapshot_id)
        _validate_snapshot_identity(snapshot, entry)
        assessment = assess_sketch_snapshot(snapshot)
        updated = entry.with_lifecycle(
            status=assessment.lifecycle_status,
            deleted_at=observed_at,
            recovery_eligibility=assessment.eligibility,
            support_status=assessment.support_status,
            unsupported_elements=assessment.unsupported_elements,
            recovery_warnings=tuple(
                dict.fromkeys((*assessment.warnings, *assessment.critical_issues))
            ),
        )
        changed = self.vault.update_index_entry(updated)
        if not changed:
            raise RecoveryLifecycleError("deletion lifecycle did not change the index")
        return DeletionLifecycleResult(
            created=True,
            record=self._record_from_entry(updated, snapshot=snapshot),
        )

    def list_deleted_records(
        self,
        document_id: str | None = None,
    ) -> tuple[DeletedSketchRecord, ...]:
        """Load persistent deleted history independently of observer memory."""

        document_ids = (
            (document_id,)
            if document_id is not None
            else self.vault.list_document_ids()
        )
        records: list[DeletedSketchRecord] = []
        for current_document_id in document_ids:
            index = self.vault.load_index(current_document_id)
            for payload in index["entries"]:
                entry = RecoveryIndexEntry.from_mapping(payload)
                if entry.deleted_at is None:
                    continue
                records.append(self._record_from_entry(entry))
        return tuple(
            sorted(
                records,
                key=lambda item: (item.deleted_at, item.object_id),
                reverse=True,
            )
        )

    def _load_entry(self, document_id: str, object_id: str) -> RecoveryIndexEntry:
        index = self.vault.load_index(document_id)
        matches = [
            RecoveryIndexEntry.from_mapping(payload)
            for payload in index["entries"]
            if payload.get("object_id") == object_id
        ]
        if len(matches) != 1:
            raise RecoveryLifecycleError(
                "deletion candidate does not identify one indexed object"
            )
        return matches[0]

    def _record_from_entry(
        self,
        entry: RecoveryIndexEntry,
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> DeletedSketchRecord:
        if entry.deleted_at is None:
            raise RecoveryLifecycleError("indexed object is not deleted")
        snapshot_warning: tuple[str, ...] = ()
        recovery_eligibility = entry.recovery_eligibility
        if snapshot is not None:
            active_snapshot = snapshot
        else:
            try:
                active_snapshot = self.vault.load_snapshot(
                    entry.document_id,
                    entry.latest_snapshot_id,
                )
            except RecoveryVaultCorruptionError:
                # One damaged immutable snapshot must not hide the rest of a
                # valid document index. Keep the entry visible but disable
                # restoration until the underlying storage is repaired.
                active_snapshot = {}
                recovery_eligibility = ELIGIBILITY_UNAVAILABLE
                snapshot_warning = (
                    "The latest recovery snapshot could not be read safely.",
                )
        object_record = active_snapshot.get("object")
        geometry_count = None
        constraint_count = None
        if isinstance(object_record, Mapping):
            if isinstance(object_record.get("geometry_count"), int):
                geometry_count = int(object_record["geometry_count"])
            if isinstance(object_record.get("constraint_count"), int):
                constraint_count = int(object_record["constraint_count"])
        return DeletedSketchRecord(
            schema_version=entry.schema_version,
            document_id=entry.document_id,
            object_id=entry.object_id,
            object_name=entry.object_name,
            object_label=entry.object_label,
            type_id=entry.type_id,
            status=entry.status,
            deleted_at=entry.deleted_at,
            latest_snapshot_id=entry.latest_snapshot_id,
            recovery_eligibility=recovery_eligibility,
            support_status=entry.support_status,
            unsupported_elements=entry.unsupported_elements,
            warnings=tuple(
                dict.fromkeys((*entry.recovery_warnings, *snapshot_warning))
            ),
            geometry_count=geometry_count,
            constraint_count=constraint_count,
            last_restored_at=entry.last_restored_at,
            restored_object_names=entry.restored_object_names,
        )


def _assessment_from_findings(
    *,
    critical: list[str],
    warnings: list[str],
    unsupported: set[str],
    support_status: str,
    serialization_status: object,
    restore_eligibility: object,
) -> RecoveryAssessment:
    unique_warnings = tuple(dict.fromkeys(warnings))
    critical_issues = tuple(dict.fromkeys(critical))
    unsupported_elements = tuple(sorted(unsupported))
    if critical_issues:
        eligibility = ELIGIBILITY_UNAVAILABLE
    elif unsupported_elements:
        eligibility = ELIGIBILITY_PARTIAL
    elif serialization_status != "complete" or restore_eligibility != "eligible":
        eligibility = ELIGIBILITY_UNAVAILABLE
        critical_issues = (
            "Snapshot does not contain complete eligible reconstruction data.",
        )
    elif support_status != SUPPORT_NOT_APPLICABLE or unique_warnings:
        eligibility = ELIGIBILITY_AVAILABLE_WITH_WARNING
    else:
        eligibility = ELIGIBILITY_AVAILABLE
    return RecoveryAssessment(
        eligibility=eligibility,
        support_status=support_status,
        unsupported_elements=unsupported_elements,
        warnings=unique_warnings,
        critical_issues=critical_issues,
    )


def _unavailable_assessment(issue: str) -> RecoveryAssessment:
    return RecoveryAssessment(
        eligibility=ELIGIBILITY_UNAVAILABLE,
        support_status="unknown",
        critical_issues=(issue,),
    )


def _assess_geometry(
    geometry: object,
    declared_count: object,
    critical: list[str],
    unsupported: set[str],
) -> None:
    if not isinstance(geometry, list):
        critical.append("Sketch geometry is missing or invalid.")
        return
    if not isinstance(declared_count, int) or declared_count != len(geometry):
        critical.append("Sketch geometry count is inconsistent.")
    for index, record in enumerate(geometry):
        if not isinstance(record, Mapping) or record.get("index") != index:
            critical.append(f"Geometry {index} record is invalid.")
            continue
        geometry_type = record.get("type")
        source_type = record.get("source_type")
        supported = record.get("supported")
        if (
            not isinstance(geometry_type, str)
            or not geometry_type.strip()
            or not isinstance(source_type, str)
            or not source_type.strip()
        ):
            critical.append(f"Geometry {index} type information is invalid.")
            continue
        if not isinstance(record.get("construction"), bool):
            critical.append(f"Geometry {index} construction state is invalid.")
        if supported is False:
            unsupported.add(f"geometry:{source_type}")
        elif supported is not True:
            critical.append(f"Geometry {index} support state is invalid.")
        elif geometry_type not in SUPPORTED_GEOMETRY_TYPES:
            critical.append(f"Geometry {index} reconstruction data is invalid.")
        elif not _valid_geometry_data(geometry_type, record.get("data")):
            critical.append(f"Geometry {index} reconstruction data is invalid.")


def _assess_constraints(
    constraints: object,
    declared_count: object,
    critical: list[str],
    unsupported: set[str],
) -> None:
    if not isinstance(constraints, list):
        critical.append("Sketch constraints are missing or invalid.")
        return
    if not isinstance(declared_count, int) or declared_count != len(constraints):
        critical.append("Sketch constraint count is inconsistent.")
    for index, record in enumerate(constraints):
        if not isinstance(record, Mapping) or record.get("index") != index:
            critical.append(f"Constraint {index} record is invalid.")
            continue
        constraint_type = record.get("type")
        supported = record.get("supported")
        if not isinstance(constraint_type, str) or not constraint_type.strip():
            critical.append(f"Constraint {index} type is invalid.")
            continue
        if supported is False:
            unsupported.add(f"constraint:{constraint_type}")
        elif supported is not True:
            critical.append(f"Constraint {index} support state is invalid.")
        elif constraint_type not in SUPPORTED_CONSTRAINT_TYPES:
            critical.append(f"Constraint {index} reconstruction data is invalid.")
        elif not _valid_constraint_record(record):
            critical.append(f"Constraint {index} reconstruction data is invalid.")


def _assess_references(
    object_record: Mapping[str, Any],
    critical: list[str],
    warnings: list[str],
) -> str:
    values: dict[str, list[Any]] = {}
    for field_name in ("support", "attachment_support", "external_geometry"):
        value = object_record.get(field_name)
        if not isinstance(value, list):
            critical.append(f"Sketch {field_name} data is missing or invalid.")
            values[field_name] = []
            continue
        values[field_name] = value
        for reference in value:
            if not _valid_reference(reference):
                critical.append(f"Sketch {field_name} contains an invalid reference.")
    if values["support"] or values["attachment_support"]:
        warnings.append("Original attachment support requires validation before restore.")
        if values["external_geometry"]:
            warnings.append("External geometry references require validation before restore.")
        return SUPPORT_RECORDED_UNRESOLVED
    if values["external_geometry"]:
        warnings.append("External geometry references require validation before restore.")
        return SUPPORT_EXTERNAL_UNRESOLVED
    return SUPPORT_NOT_APPLICABLE


def _declared_unsupported(
    recovery: Mapping[str, Any],
    critical: list[str],
) -> set[str]:
    declared: set[str] = set()
    for field_name, prefix in (
        ("unsupported_geometry_types", "geometry"),
        ("unsupported_constraint_types", "constraint"),
    ):
        values = recovery.get(field_name)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item for item in values
        ):
            critical.append(f"{field_name} metadata is invalid.")
            continue
        declared.update(f"{prefix}:{item}" for item in values)
    return declared


def _assess_optional_object_state(
    object_record: Mapping[str, Any],
    critical: list[str],
) -> None:
    expressions = object_record.get("expressions")
    if not isinstance(expressions, list) or not all(
        _valid_expression(record) for record in expressions
    ):
        critical.append("Sketch expressions are missing or invalid.")
    map_mode = object_record.get("map_mode")
    if map_mode is not None and not isinstance(map_mode, str):
        critical.append("Sketch map mode is invalid.")
    attachment_offset = object_record.get("attachment_offset")
    if attachment_offset is not None and not _valid_placement(attachment_offset):
        critical.append("Sketch attachment offset is invalid.")
    visibility = object_record.get("visibility")
    if visibility is not None and not isinstance(visibility, bool):
        critical.append("Sketch visibility is invalid.")


def _valid_geometry_data(geometry_type: str, data: object) -> bool:
    if not isinstance(data, Mapping):
        return False
    if geometry_type == "LineSegment":
        return (
            _valid_vector(data.get("start"))
            and _valid_vector(data.get("end"))
            and data.get("start") != data.get("end")
        )
    if geometry_type == "Circle":
        return (
            _valid_vector(data.get("center"))
            and _valid_direction(data.get("axis"))
            and _positive_number(data.get("radius"))
        )
    if geometry_type == "ArcOfCircle":
        return (
            _valid_vector(data.get("center"))
            and _valid_direction(data.get("axis"))
            and _positive_number(data.get("radius"))
            and _finite_number(data.get("first_parameter"))
            and _finite_number(data.get("last_parameter"))
        )
    if geometry_type == "Point":
        return _valid_vector(data.get("point"))
    return False


def _valid_constraint_record(record: Mapping[str, Any]) -> bool:
    arguments = record.get("arguments")
    if not isinstance(arguments, Mapping) or not all(
        _integer(arguments.get(field_name))
        for field_name in _CONSTRAINT_ARGUMENT_FIELDS
    ):
        return False
    if not _finite_number(record.get("value")):
        return False
    name = record.get("name")
    if name is not None and not isinstance(name, str):
        return False
    for field_name in ("driving", "in_virtual_space", "active"):
        value = record.get(field_name)
        if value is not None and not isinstance(value, bool):
            return False
    return not (
        record.get("type") in DIMENSIONAL_CONSTRAINT_TYPES
        and not isinstance(record.get("driving"), bool)
    )


def _valid_expression(record: object) -> bool:
    if not isinstance(record, Mapping):
        return False
    property_path = record.get("property")
    expression = record.get("expression")
    return (
        isinstance(property_path, str)
        and _SAFE_EXPRESSION_PATH.fullmatch(property_path) is not None
        and isinstance(expression, str)
        and bool(expression.strip())
    )


def _valid_reference(reference: object) -> bool:
    if not isinstance(reference, Mapping):
        return False
    descriptor = reference.get("object")
    subelements = reference.get("subelements")
    optional_descriptor_values = (
        descriptor.get("label"),
        descriptor.get("type_id"),
        descriptor.get("document_name"),
    ) if isinstance(descriptor, Mapping) else ()
    return (
        isinstance(descriptor, Mapping)
        and isinstance(descriptor.get("name"), str)
        and bool(descriptor["name"])
        and all(value is None or isinstance(value, str) for value in optional_descriptor_values)
        and isinstance(subelements, list)
        and all(isinstance(item, str) and item for item in subelements)
    )


def _valid_vector(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        _finite_number(value.get(axis)) for axis in ("x", "y", "z")
    )


def _valid_direction(value: object) -> bool:
    return _valid_vector(value) and any(value.get(axis) != 0 for axis in ("x", "y", "z"))


def _valid_placement(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    base = value.get("base")
    rotation = value.get("rotation")
    if not isinstance(base, Mapping) or not isinstance(rotation, Mapping):
        return False
    quaternion = rotation.get("quaternion")
    return all(_finite_number(base.get(axis)) for axis in ("x", "y", "z")) and (
        isinstance(quaternion, list)
        and len(quaternion) == 4
        and all(_finite_number(item) for item in quaternion)
    )


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _positive_number(value: object) -> bool:
    return _finite_number(value) and value > 0


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _candidate_text(candidate: Any, field_name: str) -> str:
    try:
        value = getattr(candidate, field_name)
    except Exception as exc:  # noqa: BLE001 - candidates may be wrapper records.
        raise RecoveryLifecycleError(f"deletion {field_name} is unavailable") from exc
    if not isinstance(value, str) or not value.strip():
        raise RecoveryLifecycleError(f"deletion {field_name} is invalid")
    return value


def _validate_snapshot_identity(
    snapshot: Mapping[str, Any],
    entry: RecoveryIndexEntry,
) -> None:
    expected = {
        "document_id": entry.document_id,
        "object_id": entry.object_id,
        "snapshot_id": entry.latest_snapshot_id,
    }
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise RecoveryVaultCorruptionError(
            "The deleted snapshot identity does not match its index."
        )
    object_record = snapshot.get("object")
    if not isinstance(object_record, Mapping) or any(
        object_record.get(key) != value
        for key, value in {
            "name": entry.object_name,
            "type_id": entry.type_id,
        }.items()
    ):
        raise RecoveryVaultCorruptionError(
            "The deleted snapshot object identity does not match its index."
        )


def _is_canonical_uuid(value: object) -> bool:
    try:
        return str(UUID(str(value))) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


__all__ = [
    "DeletedSketchRecord",
    "DeletionLifecycleResult",
    "RecoveryAssessment",
    "RecoveryLifecycleError",
    "RecoveryLifecycleService",
    "assess_sketch_snapshot",
]
