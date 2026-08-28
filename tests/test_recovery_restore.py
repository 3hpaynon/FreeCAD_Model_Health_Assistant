from pathlib import Path
from types import SimpleNamespace

from model_health_assistant.recovery_lifecycle import (
    RecoveryLifecycleService,
)
from model_health_assistant.recovery_restore import (
    RecoveryRestoreService,
    RestoreExecutionResult,
)
from model_health_assistant.recovery_vault import (
    RecoveryDocumentLocator,
    RecoveryVault,
)
from model_health_assistant.sketch_restore import SketchRestoreResult
from model_health_assistant.sketch_snapshot import serialize_sketch
from model_health_assistant.support_validation import (
    RESTORE_DETACHED,
    RESTORE_WITH_ORIGINAL_SUPPORT,
    SUPPORT_RESTORABLE,
    SUPPORT_UNVERIFIED,
)


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class Placement:
    def __init__(self) -> None:
        self.Base = Vector()
        self.Rotation = SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0))


class Feature:
    def __init__(self, name: str, type_id: str, document) -> None:
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Document = document
        self.Placement = Placement()


class Sketch:
    TypeId = "Sketcher::SketchObject"

    def __init__(self, support=None) -> None:
        self.Name = "Sketch003"
        self.Label = "Rotor Mounting Sketch"
        self.Geometry = []
        self.Constraints = []
        self.Placement = Placement()
        self.AttachmentOffset = Placement()
        self.Support = support
        self.AttachmentSupport = None
        self.MapMode = "Deactivated"
        self.ExpressionEngine = []
        self.ExternalGeometry = []
        self.ViewObject = SimpleNamespace(Visibility=True)


class Document:
    def __init__(self, name="RecoveryDocument", objects=()) -> None:
        self.Name = name
        self.Objects = list(objects)

    def getObject(self, name):  # noqa: N802
        return next((item for item in self.Objects if item.Name == name), None)


def _seed_deleted(
    vault: RecoveryVault,
    *,
    support_kind: str | None = None,
    with_external_geometry: bool = False,
):
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name="RecoveryDocument",
            label="Recovery document",
            session_id="restore-session",
            instance_id="restore-document",
        )
    )
    live_document = Document()
    support = None
    if support_kind == "face":
        support_object = Feature("Pad", "PartDesign::Feature", live_document)
        support = (support_object, ("Face4",))
        live_document.Objects.append(support_object)
    elif support_kind == "datum":
        support_object = Feature("DatumPlane", "PartDesign::Plane", live_document)
        support = (support_object, ("",))
        live_document.Objects.append(support_object)
    sketch = Sketch(support=support)
    if with_external_geometry:
        external_object = Feature(
            "MasterSketch",
            "Sketcher::SketchObject",
            live_document,
        )
        live_document.Objects.append(external_object)
        sketch.ExternalGeometry = [(external_object, ("Edge7",))]
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    snapshot = serialize_sketch(
        sketch,
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        captured_at="2026-08-27T12:00:00+00:00",
    )
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name="Sketch003",
        object_label="Rotor Mounting Sketch",
        type_id="Sketcher::SketchObject",
        captured_at="2026-08-27T12:00:00+00:00",
    )
    vault.store_snapshot(entry, snapshot)
    candidate = SimpleNamespace(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        observed_at="2026-08-27T12:30:00+00:00",
    )
    RecoveryLifecycleService(vault).record_deletion(candidate)
    return live_document, entry, snapshot


class SuccessfulEngine:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, document, snapshot, **kwargs):
        self.calls.append((document, snapshot, kwargs))
        suffix = "" if len(self.calls) == 1 else f"_{len(self.calls)}"
        name = "Sketch003_Recovered" + suffix
        restored = SimpleNamespace(Name=name, Label="Recovered sketch")
        return SketchRestoreResult(
            success=True,
            status_code="restored_as_copy",
            message="The sketch was restored as a new copy.",
            restored_object_name=name,
            restored_object_label="Recovered sketch",
            restored_object=restored,
        )


class NativeWrapperThatCannotBeCopied:
    def __deepcopy__(self, _memo):
        raise TypeError("native wrapper cannot be copied")


def test_execution_result_dictionary_never_copies_native_wrapper() -> None:
    result = RestoreExecutionResult(
        success=True,
        status_code="restored_as_copy",
        message="Recovered Sketch as Sketch_Recovered.",
        mode=RESTORE_DETACHED,
        restored_object_name="Sketch_Recovered",
        restored_object=NativeWrapperThatCannotBeCopied(),
    )

    assert result.to_dict() == {
        "success": True,
        "status_code": "restored_as_copy",
        "message": "Recovered Sketch as Sketch_Recovered.",
        "mode": RESTORE_DETACHED,
        "restored_object_name": "Sketch_Recovered",
        "restored_object_label": None,
        "warnings": [],
        "error_stage": None,
        "error_type": None,
        "error_detail": None,
    }


def test_assessment_offers_detached_mode_for_unattached_sketch(tmp_path: Path) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    document, entry, _snapshot = _seed_deleted(vault)
    service = RecoveryRestoreService(vault, restore_engine=SuccessfulEngine())

    assessment = service.assess(document, entry.document_id, entry.object_id)

    assert assessment.can_restore is True
    assert assessment.geometry_recoverable is True
    assert assessment.constraints_recoverable is True
    assert assessment.allowed_modes == (RESTORE_DETACHED,)
    assert "Restore Sketch003" in assessment.confirmation_text
    assert "✓ Recoverable" in assessment.confirmation_text
    assert "Original support:" in assessment.confirmation_text


def test_face_support_defaults_to_detached_but_stable_datum_offers_both(
    tmp_path: Path,
) -> None:
    face_vault = RecoveryVault(tmp_path / "face")
    face_document, face_entry, _snapshot = _seed_deleted(
        face_vault,
        support_kind="face",
    )
    datum_vault = RecoveryVault(tmp_path / "datum")
    datum_document, datum_entry, _snapshot = _seed_deleted(
        datum_vault,
        support_kind="datum",
    )

    face = RecoveryRestoreService(face_vault).assess(
        face_document,
        face_entry.document_id,
        face_entry.object_id,
    )
    datum = RecoveryRestoreService(datum_vault).assess(
        datum_document,
        datum_entry.document_id,
        datum_entry.object_id,
    )

    assert face.support.category == SUPPORT_UNVERIFIED
    assert face.allowed_modes == (RESTORE_DETACHED,)
    assert "⚠ Could not be safely verified" in face.confirmation_text
    assert (
        "geometry and constraints can be recovered" in face.confirmation_text
    )
    assert datum.support.category == SUPPORT_RESTORABLE
    assert datum.allowed_modes == (
        RESTORE_WITH_ORIGINAL_SUPPORT,
        RESTORE_DETACHED,
    )


def test_external_geometry_forces_detached_even_with_stable_support(
    tmp_path: Path,
) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    document, entry, _snapshot = _seed_deleted(
        vault,
        support_kind="datum",
        with_external_geometry=True,
    )

    assessment = RecoveryRestoreService(vault).assess(
        document,
        entry.document_id,
        entry.object_id,
    )

    assert assessment.support.category == SUPPORT_RESTORABLE
    assert assessment.allowed_modes == (RESTORE_DETACHED,)
    assert assessment.recommended_mode == RESTORE_DETACHED
    assert "External geometry references" in assessment.confirmation_text


def test_success_marks_vault_but_keeps_snapshot_and_allows_repeat_restore(
    tmp_path: Path,
) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    document, entry, _snapshot = _seed_deleted(vault)
    engine = SuccessfulEngine()
    service = RecoveryRestoreService(
        vault,
        restore_engine=engine,
        timestamp_factory=lambda: "2026-08-27T13:00:00+00:00",
    )

    first = service.restore(
        document,
        entry.document_id,
        entry.object_id,
        mode=RESTORE_DETACHED,
    )
    second = service.restore(
        document,
        entry.document_id,
        entry.object_id,
        mode=RESTORE_DETACHED,
    )

    assert first.success is True
    assert first.message == "Recovered Sketch003 as Sketch003_Recovered."
    assert second.success is True
    assert second.restored_object_name == "Sketch003_Recovered_2"
    persisted = vault.load_index(entry.document_id)["entries"][0]
    assert persisted["status"] == "restored"
    assert persisted["last_restored_at"] == "2026-08-27T13:00:00+00:00"
    assert persisted["restored_object_names"] == [
        "Sketch003_Recovered",
        "Sketch003_Recovered_2",
    ]
    assert vault.snapshot_path(entry.document_id, entry.latest_snapshot_id).is_file()
    records = RecoveryLifecycleService(vault).list_deleted_records(entry.document_id)
    assert len(records) == 1
    assert records[0].status == "restored"
    assert records[0].restored_object_names == (
        "Sketch003_Recovered",
        "Sketch003_Recovered_2",
    )
    assert all(
        call[2]["detach_unsafe_references"] is True for call in engine.calls
    )


def test_restore_failure_does_not_mark_vault_as_restored(tmp_path: Path) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    document, entry, _snapshot = _seed_deleted(vault)

    def failing_engine(_document, _snapshot, **_kwargs):
        return SketchRestoreResult(
            success=False,
            status_code="restore_failed",
            message="The sketch restore failed and was rolled back.",
            error_stage="recompute",
            error_type="RuntimeError",
            error_detail="fake failure",
        )

    result = RecoveryRestoreService(
        vault,
        restore_engine=failing_engine,
    ).restore(
        document,
        entry.document_id,
        entry.object_id,
        mode=RESTORE_DETACHED,
    )

    assert result.success is False
    assert result.error_stage == "recompute"
    persisted = vault.load_index(entry.document_id)["entries"][0]
    assert persisted["status"] == "deleted"
    assert persisted["restored_object_names"] == []


def test_original_support_mode_is_revalidated_before_engine_runs(
    tmp_path: Path,
) -> None:
    vault = RecoveryVault(tmp_path / "vault")
    document, entry, _snapshot = _seed_deleted(vault, support_kind="datum")
    engine = SuccessfulEngine()
    service = RecoveryRestoreService(vault, restore_engine=engine)
    assert service.assess(
        document,
        entry.document_id,
        entry.object_id,
    ).support.category == SUPPORT_RESTORABLE
    document.Objects.clear()

    result = service.restore(
        document,
        entry.document_id,
        entry.object_id,
        mode=RESTORE_WITH_ORIGINAL_SUPPORT,
    )

    assert result.success is False
    assert result.status_code == "restore_mode_not_allowed"
    assert engine.calls == []
