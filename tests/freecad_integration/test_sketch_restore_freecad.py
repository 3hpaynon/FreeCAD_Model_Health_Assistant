"""Native reconstruction test; run under a Python environment provided by FreeCAD."""

from uuid import uuid4

import pytest

from model_health_assistant.sketch_restore import (
    compare_sketch_snapshots,
    restore_sketch_as_copy,
)
from model_health_assistant.sketch_snapshot import serialize_sketch
from model_health_assistant.support_validation import (
    support_reference_is_restorable,
)

App = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")
Sketcher = pytest.importorskip("Sketcher")


def _ids():
    return {
        "document_id": str(uuid4()),
        "object_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "captured_at": "2026-08-25T12:00:00+00:00",
    }


def test_native_restore_as_copy_round_trip() -> None:
    document = App.newDocument("MHARecoveryRoundTripTest")
    try:
        source = document.addObject("Sketcher::SketchObject", "Sketch003")
        source.Label = "Round trip – 草图"
        source.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
            False,
        )
        source.addGeometry(
            Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2),
            True,
        )
        source.addGeometry(
            Part.ArcOfCircle(
                Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3),
                0.25,
                1.5,
            ),
            False,
        )
        source.addConstraint(Sketcher.Constraint("Horizontal", 0))
        radius = source.addConstraint(Sketcher.Constraint("Radius", 1, 2.0))
        source.renameConstraint(radius, "CircleRadius")
        document.recompute()
        original = serialize_sketch(source, **_ids())

        result = restore_sketch_as_copy(document, original)

        assert result.success, result.to_dict()
        assert result.restored_object is not source
        assert document.getObject(source.Name) is source
        restored = serialize_sketch(result.restored_object, **_ids())
        validation = compare_sketch_snapshots(original, restored, tolerance=1e-7)
        assert validation.success, validation.to_dict()
    finally:
        App.closeDocument(document.Name)


def test_native_origin_plane_attachment_uses_attachment_support() -> None:
    document = App.newDocument("MHARecoveryAttachmentTest")
    try:
        body = document.addObject("PartDesign::Body", "Body")
        plane = document.getObject("XY_Plane")
        source = body.newObject("Sketcher::SketchObject", "AttachedSketch")
        source.AttachmentSupport = (plane, [""])
        source.MapMode = "FlatFace"
        source.addGeometry(
            Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2),
            False,
        )
        source.addConstraint(Sketcher.Constraint("Radius", 0, 2.0))
        document.recompute()
        snapshot = serialize_sketch(source, **_ids())

        result = restore_sketch_as_copy(
            document,
            snapshot,
            restore_attachment=True,
            reference_validator=support_reference_is_restorable,
        )

        assert result.success, result.to_dict()
        restored_support = result.restored_object.AttachmentSupport
        assert restored_support[0][0].Name == plane.Name
        assert restored_support[0][1] == ("",)
        assert result.restored_object.MapMode == "FlatFace"
        assert document.recompute() is not False
    finally:
        App.closeDocument(document.Name)


def test_native_detached_restore_omits_unverifiable_external_dependency() -> None:
    document = App.newDocument("MHARecoveryDetachedTest")
    try:
        source = document.addObject("Sketcher::SketchObject", "Sketch003")
        source.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
            False,
        )
        source.addGeometry(
            Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2),
            False,
        )
        source.addConstraint(Sketcher.Constraint("Horizontal", 0))
        source.addConstraint(Sketcher.Constraint("Radius", 1, 2.0))
        document.recompute()
        snapshot = serialize_sketch(source, **_ids())
        snapshot["object"]["constraints"][0]["arguments"]["first"] = -3
        snapshot["object"]["support"] = [
            {
                "object": {
                    "name": "FormerPad",
                    "label": "Former Pad",
                    "type_id": "PartDesign::Feature",
                    "document_name": document.Name,
                },
                "subelements": ["Face4"],
            }
        ]
        snapshot["object"]["external_geometry"] = [
            {
                "object": {
                    "name": "FormerPad",
                    "label": "Former Pad",
                    "type_id": "PartDesign::Feature",
                    "document_name": document.Name,
                },
                "subelements": ["Edge7"],
            }
        ]

        result = restore_sketch_as_copy(
            document,
            snapshot,
            detach_unsafe_references=True,
        )

        assert result.success, result.to_dict()
        assert not getattr(result.restored_object, "Support", None)
        assert not getattr(result.restored_object, "AttachmentSupport", None)
        assert len(result.restored_object.Constraints) == 1
        assert result.restored_object.Constraints[0].Type == "Radius"
        assert "1 external-reference constraint(s) were omitted." in result.warnings
        assert document.recompute() is not False
    finally:
        App.closeDocument(document.Name)
