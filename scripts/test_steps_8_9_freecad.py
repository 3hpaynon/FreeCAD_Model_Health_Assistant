"""Manual native-FreeCAD verification for recovery Steps 8 and 9.

Run this file from FreeCAD's Python console with::

    import runpy
    runpy.run_path(
        "/Users/dhruvroy/Developer/FreeCAD_Model_Health_Assistant/"
        "scripts/test_steps_8_9_freecad.py",
        run_name="__main__",
    )

The tests use temporary recovery vaults, never the user's normal recovery
storage. Successful test documents remain open for visual inspection.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

import FreeCAD as App  # type: ignore  # noqa: E402
import FreeCADGui as Gui  # type: ignore  # noqa: E402,F401
import Part  # type: ignore  # noqa: E402
import Sketcher  # type: ignore  # noqa: E402

from model_health_assistant.recovery_lifecycle import (  # noqa: E402
    RecoveryLifecycleService,
)
from model_health_assistant.recovery_restore import (  # noqa: E402
    RecoveryRestoreService,
)
from model_health_assistant.recovery_vault import (  # noqa: E402
    RecoveryDocumentLocator,
    RecoveryVault,
    utc_timestamp,
)
from model_health_assistant.sketch_snapshot import serialize_sketch  # noqa: E402
from model_health_assistant.support_validation import (  # noqa: E402
    RESTORE_DETACHED,
    RESTORE_WITH_ORIGINAL_SUPPORT,
    SUPPORT_RESTORABLE,
    SUPPORT_UNVERIFIED,
)

KEEP_TEST_DOCUMENTS_OPEN = True


def _message(text: str) -> None:
    App.Console.PrintMessage(text.rstrip() + "\n")


def _error(text: str) -> None:
    App.Console.PrintError(text.rstrip() + "\n")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _new_document(prefix: str) -> Any:
    name = f"{prefix}_{uuid4().hex[:8]}"
    document = App.newDocument(name)
    _require(document is not None, f"FreeCAD did not create {name}.")
    return document


def _new_test_vault(prefix: str) -> tuple[tempfile.TemporaryDirectory, RecoveryVault]:
    temporary_directory = tempfile.TemporaryDirectory(prefix=prefix)
    vault = RecoveryVault(Path(temporary_directory.name) / "vault")
    vault.initialize()
    return temporary_directory, vault


def _store_then_delete(
    vault: RecoveryVault,
    document: Any,
    sketch: Any,
    *,
    snapshot_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name=str(document.Name),
            label=str(document.Label),
            session_id=f"manual-test-session-{uuid4()}",
            instance_id=f"manual-test-document-{uuid4()}",
        )
    )
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    captured_at = utc_timestamp()
    snapshot = serialize_sketch(
        sketch,
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        captured_at=captured_at,
    )
    if snapshot_mutator is not None:
        snapshot_mutator(snapshot)

    object_record = snapshot["object"]
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name=str(object_record["name"]),
        object_label=str(object_record["label"]),
        type_id=str(object_record["type_id"]),
        captured_at=captured_at,
    )
    vault.store_snapshot(entry, snapshot)

    original_name = str(sketch.Name)
    document.removeObject(original_name)
    document.recompute()
    _require(
        document.getObject(original_name) is None,
        "The source sketch was not deleted during test setup.",
    )

    deletion = SimpleNamespace(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        observed_at=utc_timestamp(),
    )
    lifecycle_result = RecoveryLifecycleService(vault).record_deletion(deletion)
    _require(lifecycle_result.created, "The deleted recovery record was not created.")
    return document_id, object_id, snapshot_id, snapshot


def _service(vault: RecoveryVault) -> RecoveryRestoreService:
    return RecoveryRestoreService(
        vault,
        app_module=App,
        part_module=Part,
        sketcher_module=Sketcher,
    )


def _support_object_name(sketch: Any) -> str | None:
    for property_name in ("Support", "AttachmentSupport"):
        support = getattr(sketch, property_name, None)
        if not support:
            continue
        if isinstance(support, tuple) and support:
            linked_object = support[0]
            return str(getattr(linked_object, "Name", "")) or None
        if isinstance(support, list) and support:
            first = support[0]
            if isinstance(first, tuple) and first:
                return str(getattr(first[0], "Name", "")) or None
    return None


def test_step_8_restore_as_copy() -> str:
    """Validate supported restore, repeat naming, history, and model isolation."""

    document = _new_document("MHAStep8RestoreTest")
    temporary_directory, vault = _new_test_vault("mha-step8-")
    try:
        body = document.addObject("PartDesign::Body", "RecoveryBody")
        _require(body is not None, "Could not create the Step 8 test Body.")
        origin_plane = document.getObject("XY_Plane")
        _require(origin_plane is not None, "The Body XY origin plane is unavailable.")

        sketch = body.newObject("Sketcher::SketchObject", "Step8Sketch")
        sketch.Label = "Step 8 Recovery Source"
        sketch.AttachmentSupport = (origin_plane, [""])
        sketch.MapMode = "FlatFace"
        line_index = sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(12, 0, 0)),
            False,
        )
        circle_index = sketch.addGeometry(
            Part.Circle(App.Vector(6, 5, 0), App.Vector(0, 0, 1), 2),
            False,
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", line_index))
        sketch.addConstraint(Sketcher.Constraint("Radius", circle_index, 2.0))

        marker = document.addObject("App::FeaturePython", "UnrelatedMarker")
        marker.addProperty("App::PropertyString", "RecoveryTestValue")
        marker.RecoveryTestValue = "must remain unchanged"
        document.recompute()

        document_id, object_id, snapshot_id, _snapshot = _store_then_delete(
            vault,
            document,
            sketch,
        )
        service = _service(vault)
        assessment = service.assess(document, document_id, object_id)

        _require(
            assessment.support.category == SUPPORT_RESTORABLE,
            f"Expected restorable support, received {assessment.support.category}.",
        )
        _require(
            RESTORE_WITH_ORIGINAL_SUPPORT in assessment.allowed_modes,
            "Restore with original support was not offered.",
        )
        _require(
            RESTORE_DETACHED in assessment.allowed_modes,
            "The optional detached restore mode was not offered.",
        )

        first = service.restore(
            document,
            document_id,
            object_id,
            mode=RESTORE_WITH_ORIGINAL_SUPPORT,
        )
        _require(first.success, f"First restore failed: {first.to_dict()}")
        _require(first.restored_object is not None, "No first restored object returned.")
        _require(
            len(first.restored_object.Geometry) == 2,
            "The first copy did not preserve both geometry elements.",
        )
        _require(
            len(first.restored_object.Constraints) == 2,
            "The first copy did not preserve both constraints.",
        )
        _require(
            _support_object_name(first.restored_object) == str(origin_plane.Name),
            "The validated XY-plane support was not restored.",
        )

        second = service.restore(
            document,
            document_id,
            object_id,
            mode=RESTORE_WITH_ORIGINAL_SUPPORT,
        )
        _require(second.success, f"Repeat restore failed: {second.to_dict()}")
        _require(
            second.restored_object_name != first.restored_object_name,
            "Repeat restore reused the first recovered object's name.",
        )
        _require(
            str(second.restored_object_name).endswith("_2"),
            "Repeat restore did not receive the expected _2 suffix.",
        )

        index = vault.load_index(document_id)
        records = [
            item for item in index["entries"] if item.get("object_id") == object_id
        ]
        _require(len(records) == 1, "Restore history entry could not be resolved.")
        record = records[0]
        _require(record["status"] == "restored", "Vault status was not restored.")
        _require(
            record["restored_object_names"]
            == [first.restored_object_name, second.restored_object_name],
            "The vault did not retain both recovered-copy names.",
        )
        _require(
            vault.snapshot_path(document_id, snapshot_id).is_file(),
            "The historical source snapshot was removed after restoration.",
        )
        _require(
            marker.RecoveryTestValue == "must remain unchanged",
            "Restore changed an unrelated object.",
        )
        _require(document.recompute() is not False, "Step 8 recompute failed.")

        _message("STEP 8 TEST PASSED")
        _message(f"  First copy:  {first.restored_object_name}")
        _message(f"  Repeat copy: {second.restored_object_name}")
        _message(f"  Document left open: {document.Name}")
        return str(document.Name)
    finally:
        temporary_directory.cleanup()
        if not KEEP_TEST_DOCUMENTS_OPEN:
            App.closeDocument(document.Name)


def _add_unsafe_references(snapshot: dict[str, Any], support_object: Any) -> None:
    object_record = snapshot["object"]
    descriptor = {
        "name": str(support_object.Name),
        "label": str(support_object.Label),
        "type_id": str(support_object.TypeId),
        "document_name": str(support_object.Document.Name),
    }
    object_record["support"] = [
        {"object": dict(descriptor), "subelements": ["Face4"]}
    ]
    object_record["attachment_support"] = []
    object_record["map_mode"] = "FlatFace"
    object_record["external_geometry"] = [
        {"object": dict(descriptor), "subelements": ["Edge7"]}
    ]
    object_record["constraints"][0]["arguments"]["first"] = -3


def test_step_9_detached_support_safety() -> str:
    """Validate generated-face rejection and safe detached reconstruction."""

    document = _new_document("MHAStep9DetachedTest")
    temporary_directory, vault = _new_test_vault("mha-step9-")
    try:
        former_pad = document.addObject("Part::Feature", "FormerPad")
        former_pad.Label = "Former Pad"
        former_pad.Shape = Part.makeBox(20, 20, 5)

        sketch = document.addObject("Sketcher::SketchObject", "Step9Sketch")
        sketch.Label = "Step 9 Detached Recovery Source"
        line_index = sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
            False,
        )
        circle_index = sketch.addGeometry(
            Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2),
            False,
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", line_index))
        sketch.addConstraint(Sketcher.Constraint("Radius", circle_index, 2.0))
        document.recompute()

        document_id, object_id, _snapshot_id, _snapshot = _store_then_delete(
            vault,
            document,
            sketch,
            snapshot_mutator=lambda value: _add_unsafe_references(
                value,
                former_pad,
            ),
        )
        service = _service(vault)
        assessment = service.assess(document, document_id, object_id)

        _require(
            assessment.support.category == SUPPORT_UNVERIFIED,
            f"Expected unverified Face4 support, received {assessment.support.category}.",
        )
        _require(
            assessment.allowed_modes == (RESTORE_DETACHED,),
            f"Unsafe support offered unexpected modes: {assessment.allowed_modes}.",
        )
        _require(
            assessment.recommended_mode == RESTORE_DETACHED,
            "Detached mode was not the safe recommendation.",
        )
        _require(
            "cannot be safely verified" in assessment.support.message,
            "The support warning did not explain the conservative decision.",
        )

        rejected = service.restore(
            document,
            document_id,
            object_id,
            mode=RESTORE_WITH_ORIGINAL_SUPPORT,
        )
        _require(
            not rejected.success and rejected.status_code == "restore_mode_not_allowed",
            "The service did not reject unsafe original-support restoration.",
        )

        detached = service.restore(
            document,
            document_id,
            object_id,
            mode=RESTORE_DETACHED,
        )
        _require(detached.success, f"Detached restore failed: {detached.to_dict()}")
        restored = detached.restored_object
        _require(restored is not None, "No detached restored object returned.")
        _require(
            _support_object_name(restored) is None,
            "The detached copy retained an unsafe support reference.",
        )
        _require(
            len(restored.ExternalGeometry) == 0,
            "The detached copy retained unsafe external geometry.",
        )
        _require(
            len(restored.Geometry) == 2,
            "Detached recovery did not preserve the internal geometry.",
        )
        _require(
            len(restored.Constraints) == 1
            and restored.Constraints[0].Type == "Radius",
            "Detached recovery did not preserve only the safe Radius constraint.",
        )
        _require(
            "1 external-reference constraint(s) were omitted." in detached.warnings,
            "Detached recovery did not report the omitted unsafe constraint.",
        )
        _require(
            len(former_pad.Shape.Faces) == 6,
            "Detached recovery modified the unrelated support solid.",
        )
        _require(document.recompute() is not False, "Step 9 recompute failed.")

        _message("STEP 9 TEST PASSED")
        _message(f"  Detached copy: {detached.restored_object_name}")
        _message("  Face4 and Edge7 were not reattached.")
        _message("  One unsafe external-reference constraint was omitted.")
        _message(f"  Document left open: {document.Name}")
        return str(document.Name)
    finally:
        temporary_directory.cleanup()
        if not KEEP_TEST_DOCUMENTS_OPEN:
            App.closeDocument(document.Name)


def run_all() -> None:
    """Run both tests sequentially and print every failure before returning."""

    _message("=" * 68)
    _message("Running Model Health Assistant Steps 8 and 9 FreeCAD tests")
    _message("=" * 68)
    failures: list[str] = []
    opened_documents: list[str] = []
    for label, test in (
        ("STEP 8", test_step_8_restore_as_copy),
        ("STEP 9", test_step_9_detached_support_safety),
    ):
        try:
            opened_documents.append(test())
        except Exception:  # noqa: BLE001 - show both native test outcomes.
            failures.append(label)
            _error(f"{label} TEST FAILED")
            _error(traceback.format_exc())

    if failures:
        failure_text = ", ".join(failures)
        _error(f"FAILED: {failure_text}")
        raise AssertionError(f"FreeCAD recovery test failure: {failure_text}")

    _message("=" * 68)
    _message("ALL STEP 8 AND STEP 9 TESTS PASSED")
    if KEEP_TEST_DOCUMENTS_OPEN:
        _message("Open test documents: " + ", ".join(opened_documents))
    _message("=" * 68)


if __name__ == "__main__":
    run_all()
