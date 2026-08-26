"""Native integration checks; run with a Python environment provided by FreeCAD."""

from uuid import uuid4

import pytest

from model_health_assistant.sketch_snapshot import serialize_sketch

App = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")
Sketcher = pytest.importorskip("Sketcher")


def test_native_line_circle_arc_and_constraints_serialize() -> None:
    document = App.newDocument("MHARecoverySerializerTest")
    try:
        sketch = document.addObject("Sketcher::SketchObject", "Sketch003")
        sketch.Label = "Native recovery – 草图"
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
            False,
        )
        sketch.addGeometry(
            Part.Circle(App.Vector(5, 5, 0), App.Vector(0, 0, 1), 2),
            True,
        )
        sketch.addGeometry(
            Part.ArcOfCircle(
                Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 3),
                0.0,
                1.5,
            ),
            False,
        )
        sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
        radius_index = sketch.addConstraint(Sketcher.Constraint("Radius", 1, 2.0))
        sketch.renameConstraint(radius_index, "NativeRadius")

        snapshot = serialize_sketch(
            sketch,
            document_id=str(uuid4()),
            object_id=str(uuid4()),
            snapshot_id=str(uuid4()),
            captured_at="2026-08-24T12:30:00+00:00",
        )

        assert [item["type"] for item in snapshot["object"]["geometry"]] == [
            "LineSegment",
            "Circle",
            "ArcOfCircle",
        ]
        assert snapshot["object"]["geometry"][1]["construction"] is True
        assert snapshot["object"]["constraints"][1]["name"] == "NativeRadius"
        assert snapshot["recovery"]["serialization_status"] == "complete"
    finally:
        App.closeDocument(document.Name)
