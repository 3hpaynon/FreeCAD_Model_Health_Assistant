import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from model_health_assistant.sketch_snapshot import (
    SKETCH_SNAPSHOT_RECORD_TYPE,
    SketchSerializationError,
    serialize_sketch,
)


class Vector:
    def __init__(self, x, y, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class Placement:
    def __init__(
        self,
        base=(0.0, 0.0, 0.0),
        quaternion=(0.0, 0.0, 0.0, 1.0),
    ) -> None:
        self.Base = Vector(*base)
        self.Rotation = SimpleNamespace(Q=quaternion)


class LineSegment:
    TypeId = "Part::GeomLineSegment"

    def __init__(self, start, end) -> None:
        self.StartPoint = Vector(*start)
        self.EndPoint = Vector(*end)


class Circle:
    TypeId = "Part::GeomCircle"

    def __init__(self, center, radius, axis=(0.0, 0.0, 1.0)) -> None:
        self.Center = Vector(*center)
        self.Axis = Vector(*axis)
        self.Radius = radius


class ArcOfCircle(Circle):
    TypeId = "Part::GeomArcOfCircle"

    def __init__(
        self,
        center,
        radius,
        first_parameter,
        last_parameter,
    ) -> None:
        super().__init__(center, radius)
        self.FirstParameter = first_parameter
        self.LastParameter = last_parameter


class Point:
    TypeId = "Part::GeomPoint"

    def __init__(self, x, y, z=0.0) -> None:
        self.X = x
        self.Y = y
        self.Z = z


class Ellipse:
    TypeId = "Part::GeomEllipse"


class Constraint:
    def __init__(
        self,
        constraint_type,
        *,
        first=-2000,
        first_pos=0,
        second=-2000,
        second_pos=0,
        third=-2000,
        third_pos=0,
        value=0.0,
        name="",
        driving=True,
        in_virtual_space=False,
        active=True,
    ) -> None:
        self.Type = constraint_type
        self.First = first
        self.FirstPos = first_pos
        self.Second = second
        self.SecondPos = second_pos
        self.Third = third
        self.ThirdPos = third_pos
        self.Value = value
        self.Name = name
        self.Driving = driving
        self.InVirtualSpace = in_virtual_space
        self.IsActive = active


class Sketch:
    TypeId = "Sketcher::SketchObject"

    def __init__(
        self,
        geometry=(),
        constraints=(),
        construction=(),
        *,
        label="Sketch",
    ) -> None:
        self.Name = "Sketch003"
        self.Label = label
        self.Geometry = list(geometry)
        self.Constraints = list(constraints)
        self._construction = list(construction) or [False] * len(self.Geometry)
        self.Placement = Placement()
        self.AttachmentOffset = Placement()
        self.Support = None
        self.AttachmentSupport = None
        self.MapMode = "Deactivated"
        self.ExpressionEngine = []
        self.ExternalGeometry = []
        self.ViewObject = SimpleNamespace(Visibility=True)

    def getConstruction(self, index):
        return self._construction[index]


def _ids():
    return {
        "document_id": str(uuid4()),
        "object_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "captured_at": "2026-08-24T12:30:00+00:00",
    }


def _serialize(sketch):
    return serialize_sketch(sketch, **_ids())


def test_empty_sketch_is_complete_and_json_safe() -> None:
    arguments = _ids()

    snapshot = serialize_sketch(Sketch(), **arguments)

    assert snapshot["record_type"] == SKETCH_SNAPSHOT_RECORD_TYPE
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["object"]["geometry"] == []
    assert snapshot["object"]["constraints"] == []
    assert snapshot["recovery"] == {
        "serialization_status": "complete",
        "restore_as_copy_eligibility": "eligible",
        "unsupported_geometry_types": [],
        "unsupported_constraint_types": [],
        "warnings": [],
    }
    assert json.loads(json.dumps(snapshot, ensure_ascii=False)) == snapshot
    for field in ("document_id", "object_id", "snapshot_id"):
        assert str(UUID(snapshot[field])) == arguments[field]


def test_simple_line_is_structured_without_raw_representation() -> None:
    sketch = Sketch([LineSegment((1, 2, 0), (3, 4, 0))])

    geometry = _serialize(sketch)["object"]["geometry"][0]

    assert geometry == {
        "index": 0,
        "type": "LineSegment",
        "source_type": "Part::GeomLineSegment",
        "construction": False,
        "supported": True,
        "data": {
            "start": {"x": 1.0, "y": 2.0, "z": 0.0},
            "end": {"x": 3.0, "y": 4.0, "z": 0.0},
        },
    }


def test_rectangle_and_geometric_constraints_preserve_indices() -> None:
    lines = [
        LineSegment((0, 0), (10, 0)),
        LineSegment((10, 0), (10, 5)),
        LineSegment((10, 5), (0, 5)),
        LineSegment((0, 5), (0, 0)),
    ]
    constraints = [
        Constraint("Coincident", first=0, first_pos=2, second=1, second_pos=1),
        Constraint("Horizontal", first=0),
        Constraint("Vertical", first=1),
        Constraint("Equal", first=0, second=2),
        Constraint("Parallel", first=0, second=2),
        Constraint("Perpendicular", first=0, second=1),
        Constraint("Tangent", first=0, first_pos=2, second=1, second_pos=1),
        Constraint(
            "Symmetric",
            first=0,
            first_pos=1,
            second=2,
            second_pos=2,
            third=1,
            third_pos=3,
        ),
        Constraint("PointOnObject", first=0, first_pos=1, second=-1),
    ]

    snapshot = _serialize(Sketch(lines, constraints))

    assert snapshot["object"]["geometry_count"] == 4
    assert snapshot["object"]["constraint_count"] == 9
    coincident = snapshot["object"]["constraints"][0]
    assert coincident["arguments"] == {
        "first": 0,
        "first_pos": 2,
        "second": 1,
        "second_pos": 1,
        "third": -2000,
        "third_pos": 0,
    }
    assert all(item["supported"] for item in snapshot["object"]["constraints"])


def test_circle_arc_point_and_mixed_geometry() -> None:
    sketch = Sketch(
        [
            Circle((2, 3, 0), 4),
            ArcOfCircle((5, 6, 0), 7, 0.25, 2.5),
            Point(8, 9, 0),
            LineSegment((0, 0), (1, 1)),
        ]
    )

    geometry = _serialize(sketch)["object"]["geometry"]

    assert [item["type"] for item in geometry] == [
        "Circle",
        "ArcOfCircle",
        "Point",
        "LineSegment",
    ]
    assert geometry[0]["data"]["radius"] == 4.0
    assert geometry[1]["data"]["first_parameter"] == 0.25
    assert geometry[1]["data"]["last_parameter"] == 2.5
    assert geometry[2]["data"]["point"] == {"x": 8.0, "y": 9.0, "z": 0.0}


def test_construction_geometry_state_is_separate() -> None:
    sketch = Sketch(
        [LineSegment((0, 0), (1, 0)), Circle((0, 0), 2)],
        construction=[True, False],
    )

    geometry = _serialize(sketch)["object"]["geometry"]

    assert [item["construction"] for item in geometry] == [True, False]


@pytest.mark.parametrize(
    "constraint_type",
    [
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
    ],
)
def test_dimensional_constraints_preserve_values_and_state(
    constraint_type,
) -> None:
    constraint = Constraint(
        constraint_type,
        first=0,
        first_pos=2,
        second=1,
        second_pos=1,
        value=12.75,
        name="尺寸",
        driving=False,
        in_virtual_space=True,
        active=False,
    )

    serialized = _serialize(Sketch(constraints=[constraint]))["object"][
        "constraints"
    ][0]

    assert serialized["value"] == 12.75
    assert serialized["name"] == "尺寸"
    assert serialized["driving"] is False
    assert serialized["in_virtual_space"] is True
    assert serialized["active"] is False


def test_placement_unicode_label_expressions_and_visibility() -> None:
    sketch = Sketch(label="支架草图 – ⅛")
    sketch.Placement = Placement((1, 2, 3), (0.1, 0.2, 0.3, 0.9))
    sketch.AttachmentOffset = Placement((4, 5, 6), (0, 0, 0, 1))
    sketch.ExpressionEngine = [
        ("Constraints[1]", "Spreadsheet.Width"),
        ("Constraints[0]", "Spreadsheet.Height"),
    ]
    sketch.ViewObject.Visibility = False

    serialized = _serialize(sketch)["object"]

    assert serialized["label"] == "支架草图 – ⅛"
    assert serialized["placement"]["base"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert serialized["placement"]["rotation"]["quaternion"] == [
        0.1,
        0.2,
        0.3,
        0.9,
    ]
    assert serialized["attachment_offset"]["base"]["x"] == 4.0
    assert serialized["visibility"] is False
    assert [item["property"] for item in serialized["expressions"]] == [
        "Constraints[0]",
        "Constraints[1]",
    ]


def test_support_and_external_geometry_are_structured_conservatively() -> None:
    document = SimpleNamespace(Name="Assembly")
    support = SimpleNamespace(
        Name="Pad",
        Label="Pad – mount",
        TypeId="PartDesign::Feature",
        Document=document,
    )
    external = SimpleNamespace(
        Name="MasterSketch",
        Label="Master Sketch",
        TypeId="Sketcher::SketchObject",
        Document=document,
    )
    sketch = Sketch()
    sketch.Support = (support, ("Face4",))
    sketch.AttachmentSupport = [(support, ("Face4", "Edge2"))]
    sketch.MapMode = "FlatFace"
    sketch.ExternalGeometry = [(external, ("Edge1", "Edge3"))]

    serialized = _serialize(sketch)["object"]

    assert serialized["support"][0] == {
        "object": {
            "name": "Pad",
            "label": "Pad – mount",
            "type_id": "PartDesign::Feature",
            "document_name": "Assembly",
        },
        "subelements": ["Face4"],
    }
    assert serialized["attachment_support"][0]["subelements"] == [
        "Face4",
        "Edge2",
    ]
    assert serialized["external_geometry"][0]["subelements"] == [
        "Edge1",
        "Edge3",
    ]
    assert serialized["map_mode"] == "FlatFace"


def test_datum_support_records_placement_and_normalizes_whole_object_link() -> None:
    document = SimpleNamespace(Name="Assembly")
    support = SimpleNamespace(
        Name="DatumPlane",
        Label="Datum Plane",
        TypeId="PartDesign::Plane",
        Document=document,
        Placement=Placement((1, 2, 3), (0, 0, 0, 1)),
    )
    sketch = Sketch()
    sketch.Support = (support, ("",))

    reference = _serialize(sketch)["object"]["support"][0]

    assert reference["subelements"] == []
    assert reference["object"]["placement"] == {
        "base": {"x": 1.0, "y": 2.0, "z": 3.0},
        "rotation": {"quaternion": [0.0, 0.0, 0.0, 1.0]},
    }


def test_unsupported_geometry_is_recorded_and_restore_is_conservative() -> None:
    snapshot = _serialize(Sketch([Ellipse()]))

    geometry = snapshot["object"]["geometry"][0]
    assert geometry == {
        "index": 0,
        "type": "Part::GeomEllipse",
        "source_type": "Part::GeomEllipse",
        "construction": False,
        "supported": False,
        "data": None,
    }
    assert snapshot["recovery"]["serialization_status"] == "partial"
    assert snapshot["recovery"]["restore_as_copy_eligibility"] == "unsupported"
    assert snapshot["recovery"]["unsupported_geometry_types"] == [
        "Part::GeomEllipse"
    ]


def test_unsupported_constraint_is_recorded_without_crashing() -> None:
    snapshot = _serialize(Sketch(constraints=[Constraint("Block", first=0)]))

    constraint = snapshot["object"]["constraints"][0]
    assert constraint["type"] == "Block"
    assert constraint["supported"] is False
    assert snapshot["recovery"]["unsupported_constraint_types"] == ["Block"]
    assert snapshot["recovery"]["restore_as_copy_eligibility"] == "unsupported"


def test_same_inputs_produce_same_snapshot() -> None:
    sketch = Sketch([LineSegment((0, 0), (1, 1))])
    arguments = _ids()

    first = serialize_sketch(sketch, **arguments)
    second = serialize_sketch(sketch, **arguments)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_non_sketch_objects_are_rejected() -> None:
    object_to_serialize = Sketch()
    object_to_serialize.TypeId = "PartDesign::Feature"

    with pytest.raises(SketchSerializationError, match="Sketcher::SketchObject"):
        _serialize(object_to_serialize)


def test_invalid_geometry_numbers_become_partial_instead_of_nan() -> None:
    snapshot = _serialize(Sketch([Circle((0, 0), float("nan"))]))

    assert snapshot["object"]["geometry"][0]["supported"] is False
    assert snapshot["recovery"]["serialization_status"] == "partial"
    assert "NaN" not in json.dumps(snapshot, allow_nan=False)
