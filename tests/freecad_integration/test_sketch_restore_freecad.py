"""Native reconstruction test; run under a Python environment provided by FreeCAD."""

from uuid import uuid4

import pytest

from model_health_assistant.sketch_restore import (
    compare_sketch_snapshots,
    restore_sketch_as_copy,
)
from model_health_assistant.sketch_snapshot import serialize_sketch

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
