from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_health_assistant.recovery_lifecycle import (
    ELIGIBILITY_AVAILABLE,
    ELIGIBILITY_AVAILABLE_WITH_WARNING,
    ELIGIBILITY_PARTIAL,
    ELIGIBILITY_UNAVAILABLE,
    SUPPORT_EXTERNAL_UNRESOLVED,
    SUPPORT_NOT_APPLICABLE,
    SUPPORT_RECORDED_UNRESOLVED,
    RecoveryLifecycleService,
    assess_sketch_snapshot,
)
from model_health_assistant.recovery_vault import (
    RecoveryDocumentLocator,
    RecoveryVault,
)
from model_health_assistant.sketch_snapshot import serialize_sketch


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class Placement:
    def __init__(self) -> None:
        self.Base = Vector()
        self.Rotation = SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0))


class Sketch:
    TypeId = "Sketcher::SketchObject"

    def __init__(self) -> None:
        self.Name = "Sketch003"
        self.Label = "Recoverable sketch"
        self.Geometry = []
        self.Constraints = []
        self.Placement = Placement()
        self.AttachmentOffset = Placement()
        self.Support = None
        self.AttachmentSupport = None
        self.MapMode = "Deactivated"
        self.ExpressionEngine = []
        self.ExternalGeometry = []
        self.ViewObject = SimpleNamespace(Visibility=True)


def _seed_snapshot(vault: RecoveryVault):
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name="RecoveryDocument",
            label="Recovery document",
            session_id="lifecycle-test-session",
            instance_id="lifecycle-test-document",
        )
    )
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    snapshot = serialize_sketch(
        Sketch(),
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        captured_at="2026-08-25T11:00:00+00:00",
    )
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name="Sketch003",
        object_label="Recoverable sketch",
        type_id="Sketcher::SketchObject",
        captured_at="2026-08-25T11:00:00+00:00",
    )
    return entry, snapshot


def _store_seed(vault: RecoveryVault, *, mutate=None):
    entry, snapshot = _seed_snapshot(vault)
    if mutate is not None:
        mutate(snapshot)
    vault.store_snapshot(entry, snapshot)
    return entry, snapshot


def _candidate(entry, *, observed_at="2026-08-25T12:00:00+00:00"):
    return SimpleNamespace(
        document_id=entry.document_id,
        object_id=entry.object_id,
        object_name=entry.object_name,
        object_label=entry.object_label,
        type_id=entry.type_id,
        snapshot_id=entry.latest_snapshot_id,
        observed_at=observed_at,
        snapshot={},
    )


def _reference(name="Pad", *, document_name="RecoveryDocument"):
    return {
        "object": {
            "name": name,
            "label": name,
            "type_id": "PartDesign::Feature",
            "document_name": document_name,
        },
        "subelements": ["Face1"],
    }


def _add_supported_content(snapshot) -> None:
    snapshot["object"]["geometry"] = [
        {
            "index": 0,
            "type": "LineSegment",
            "source_type": "Part.LineSegment",
            "construction": False,
            "supported": True,
            "data": {
                "start": {"x": 0.0, "y": 0.0, "z": 0.0},
                "end": {"x": 10.0, "y": 0.0, "z": 0.0},
            },
        }
    ]
    snapshot["object"]["geometry_count"] = 1
    snapshot["object"]["constraints"] = [
        {
            "index": 0,
            "type": "Horizontal",
            "supported": True,
            "arguments": {
                "first": 0,
                "first_pos": 0,
                "second": -2000,
                "second_pos": 0,
                "third": -2000,
                "third_pos": 0,
            },
            "value": 0.0,
            "name": None,
            "driving": True,
            "in_virtual_space": False,
            "active": True,
        }
    ]
    snapshot["object"]["constraint_count"] = 1


def test_fully_recoverable_snapshot_becomes_deleted_record(tmp_path: Path) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    entry, _snapshot = _store_seed(vault, mutate=_add_supported_content)

    result = RecoveryLifecycleService(vault).record_deletion(_candidate(entry))

    assert result.created is True
    assert result.record.status == "deleted"
    assert result.record.deleted_at == "2026-08-25T12:00:00+00:00"
    assert result.record.recovery_eligibility == ELIGIBILITY_AVAILABLE
    assert result.record.support_status == SUPPORT_NOT_APPLICABLE
    assert result.record.unsupported_elements == ()
    assert result.record.geometry_count == 1
    assert result.record.constraint_count == 1
    persisted = vault.load_index(entry.document_id)["entries"][0]
    assert persisted["object_name"] == "Sketch003"
    assert persisted["object_label"] == "Recoverable sketch"
    assert persisted["type_id"] == "Sketcher::SketchObject"
    assert persisted["schema_version"] == "1.0"
    assert persisted["latest_snapshot_id"] == entry.latest_snapshot_id
    assert vault.snapshot_path(entry.document_id, entry.latest_snapshot_id).is_file()


@pytest.mark.parametrize(
    ("field_name", "expected_support_status"),
    [
        ("support", SUPPORT_RECORDED_UNRESOLVED),
        ("external_geometry", SUPPORT_EXTERNAL_UNRESOLVED),
    ],
)
def test_references_are_recoverable_with_explicit_warning(
    tmp_path: Path,
    field_name: str,
    expected_support_status: str,
) -> None:
    vault = RecoveryVault(tmp_path / field_name)

    def add_reference(snapshot):
        snapshot["object"][field_name] = [_reference()]

    entry, _snapshot = _store_seed(vault, mutate=add_reference)

    result = RecoveryLifecycleService(vault).record_deletion(_candidate(entry))

    assert result.record.status == "deleted"
    assert (
        result.record.recovery_eligibility
        == ELIGIBILITY_AVAILABLE_WITH_WARNING
    )
    assert result.record.support_status == expected_support_status
    assert result.record.warnings


def test_explicit_unsupported_elements_become_partial_record(tmp_path: Path) -> None:
    vault = RecoveryVault(tmp_path / "vault")

    def add_unsupported_elements(snapshot):
        snapshot["object"]["geometry"] = [
            {
                "index": 0,
                "type": "BSplineCurve",
                "source_type": "Part.GeomBSplineCurve",
                "construction": False,
                "supported": False,
                "data": None,
            }
        ]
        snapshot["object"]["geometry_count"] = 1
        snapshot["object"]["constraints"] = [
            {
                "index": 0,
                "type": "Block",
                "supported": False,
                "arguments": {},
                "value": None,
                "name": None,
                "driving": None,
                "in_virtual_space": None,
                "active": None,
            }
        ]
        snapshot["object"]["constraint_count"] = 1
        snapshot["recovery"].update(
            {
                "serialization_status": "partial",
                "restore_as_copy_eligibility": "unsupported",
                "unsupported_geometry_types": ["Part.GeomBSplineCurve"],
                "unsupported_constraint_types": ["Block"],
            }
        )

    entry, snapshot = _store_seed(vault, mutate=add_unsupported_elements)

    assessment = assess_sketch_snapshot(snapshot)
    result = RecoveryLifecycleService(vault).record_deletion(_candidate(entry))

    assert assessment.eligibility == ELIGIBILITY_PARTIAL
    assert result.record.status == "partial"
    assert result.record.recovery_eligibility == ELIGIBILITY_PARTIAL
    assert result.record.unsupported_elements == (
        "constraint:Block",
        "geometry:Part.GeomBSplineCurve",
    )


def test_json_with_invalid_reconstruction_data_is_not_marked_safe(
    tmp_path: Path,
) -> None:
    vault = RecoveryVault(tmp_path / "vault")

    def add_invalid_circle(snapshot):
        snapshot["object"]["geometry"] = [
            {
                "index": 0,
                "type": "Circle",
                "source_type": "Part.Circle",
                "construction": False,
                "supported": True,
                "data": {
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                    "radius": "not numeric",
                },
            }
        ]
        snapshot["object"]["geometry_count"] = 1

    entry, snapshot = _store_seed(vault, mutate=add_invalid_circle)

    assessment = assess_sketch_snapshot(snapshot)
    result = RecoveryLifecycleService(vault).record_deletion(_candidate(entry))

    assert assessment.eligibility == ELIGIBILITY_UNAVAILABLE
    assert assessment.critical_issues
    assert result.record.status == "unsupported"
    assert result.record.recovery_eligibility == ELIGIBILITY_UNAVAILABLE


def test_duplicate_deletion_is_idempotent_and_snapshot_history_survives_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    vault = RecoveryVault(root)
    first_entry, first_snapshot = _store_seed(vault)
    second_snapshot_id = vault.new_snapshot_id()
    second_snapshot = deepcopy(first_snapshot)
    second_snapshot["snapshot_id"] = second_snapshot_id
    second_snapshot["captured_at"] = "2026-08-25T11:30:00+00:00"
    second_snapshot["object"]["label"] = "Latest recoverable sketch"
    second_entry = first_entry.with_snapshot(
        second_snapshot_id,
        captured_at="2026-08-25T11:30:00+00:00",
        object_label="Latest recoverable sketch",
    )
    vault.store_snapshot(second_entry, second_snapshot)
    service = RecoveryLifecycleService(vault)

    first_result = service.record_deletion(_candidate(second_entry))
    duplicate_result = service.record_deletion(
        _candidate(second_entry, observed_at="2026-08-25T13:00:00+00:00")
    )

    assert first_result.created is True
    assert duplicate_result.created is False
    assert duplicate_result.record.deleted_at == "2026-08-25T12:00:00+00:00"
    persisted = vault.load_index(second_entry.document_id)["entries"][0]
    assert persisted["snapshot_ids"] == [
        first_entry.latest_snapshot_id,
        second_snapshot_id,
    ]
    assert vault.snapshot_path(
        second_entry.document_id,
        first_entry.latest_snapshot_id,
    ).is_file()
    assert vault.snapshot_path(second_entry.document_id, second_snapshot_id).is_file()

    reopened_records = RecoveryLifecycleService(
        RecoveryVault(root)
    ).list_deleted_records()
    assert len(reopened_records) == 1
    assert reopened_records[0].object_id == second_entry.object_id
    assert reopened_records[0].object_label == "Latest recoverable sketch"
    assert reopened_records[0].latest_snapshot_id == second_snapshot_id


def test_one_corrupt_snapshot_does_not_hide_other_deleted_records(
    tmp_path: Path,
) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    corrupt_entry, _snapshot = _store_seed(vault)
    healthy_entry, _snapshot = _store_seed(vault, mutate=_add_supported_content)
    service = RecoveryLifecycleService(vault)
    service.record_deletion(_candidate(corrupt_entry))
    service.record_deletion(_candidate(healthy_entry))
    vault.snapshot_path(
        corrupt_entry.document_id,
        corrupt_entry.latest_snapshot_id,
    ).write_text("{malformed", encoding="utf-8")

    records = service.list_deleted_records(corrupt_entry.document_id)

    by_id = {record.object_id: record for record in records}
    assert set(by_id) == {corrupt_entry.object_id, healthy_entry.object_id}
    corrupt = by_id[corrupt_entry.object_id]
    healthy = by_id[healthy_entry.object_id]
    assert corrupt.recovery_eligibility == ELIGIBILITY_UNAVAILABLE
    assert corrupt.geometry_count is None
    assert corrupt.constraint_count is None
    assert "snapshot could not be read safely" in " ".join(corrupt.warnings)
    assert healthy.recovery_eligibility == ELIGIBILITY_AVAILABLE
    assert healthy.geometry_count == 1
    assert len(vault.load_index(corrupt_entry.document_id)["entries"]) == 2
