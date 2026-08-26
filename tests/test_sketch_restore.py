import copy
from types import SimpleNamespace
from uuid import uuid4

from model_health_assistant.sketch_restore import (
    compare_sketch_snapshots,
    restore_sketch_as_copy,
)
from model_health_assistant.sketch_snapshot import serialize_sketch


class Vector:
    def __init__(self, x, y, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class Rotation:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0) -> None:
        self.Q = (x, y, z, w)


class Placement:
    def __init__(self, base=None, rotation=None) -> None:
        self.Base = base or Vector(0, 0, 0)
        self.Rotation = rotation or Rotation()


class LineSegment:
    TypeId = "Part::GeomLineSegment"

    def __init__(self, start, end) -> None:
        self.StartPoint = start
        self.EndPoint = end


class Circle:
    TypeId = "Part::GeomCircle"

    def __init__(self, center, axis, radius) -> None:
        self.Center = center
        self.Axis = axis
        self.Radius = radius


class ArcOfCircle:
    TypeId = "Part::GeomArcOfCircle"

    def __init__(self, circle, first_parameter, last_parameter) -> None:
        self.Center = circle.Center
        self.Axis = circle.Axis
        self.Radius = circle.Radius
        self.FirstParameter = first_parameter
        self.LastParameter = last_parameter


class Point:
    TypeId = "Part::GeomPoint"

    def __init__(self, vector) -> None:
        self.X = vector.x
        self.Y = vector.y
        self.Z = vector.z


class Constraint:
    def __init__(self, constraint_type, *arguments) -> None:
        self.Type = constraint_type
        self.First = -2000
        self.FirstPos = 0
        self.Second = -2000
        self.SecondPos = 0
        self.Third = -2000
        self.ThirdPos = 0
        self.Value = 0.0
        self.Name = ""
        self.Driving = True
        self.InVirtualSpace = False
        self.IsActive = True
        self._read_arguments(arguments)

    def _read_arguments(self, arguments) -> None:
        if self.Type in {"Horizontal", "Vertical"}:
            if len(arguments) == 4:
                self.First, self.FirstPos, self.Second, self.SecondPos = arguments
            else:
                (self.First,) = arguments
        elif self.Type == "Coincident":
            self.First, self.FirstPos, self.Second, self.SecondPos = arguments
        elif self.Type in {"Equal", "Parallel", "Perpendicular"}:
            self.First, self.Second = arguments
        elif self.Type == "PointOnObject":
            self.First, self.FirstPos, self.Second = arguments
        elif self.Type == "Symmetric":
            if len(arguments) == 6:
                (
                    self.First,
                    self.FirstPos,
                    self.Second,
                    self.SecondPos,
                    self.Third,
                    self.ThirdPos,
                ) = arguments
            else:
                self.First, self.Second, self.Third = arguments
        elif self.Type == "Tangent":
            if len(arguments) == 4:
                self.First, self.FirstPos, self.Second, self.SecondPos = arguments
            else:
                self.First, self.Second = arguments
        elif self.Type in {"Radius", "Diameter"}:
            self.First, self.Value = arguments
        elif len(arguments) == 2:
            self.First, self.Value = arguments
        elif len(arguments) == 3:
            self.First, self.Second, self.Value = arguments
        elif len(arguments) == 5:
            (
                self.First,
                self.FirstPos,
                self.Second,
                self.SecondPos,
                self.Value,
            ) = arguments
        else:
            raise ValueError("unsupported fake constraint constructor")


class Sketch:
    TypeId = "Sketcher::SketchObject"

    def __init__(self, name="Sketch003", label="Recovery source") -> None:
        self._name = name
        self._deleted = False
        self.Label = label
        self.Geometry = []
        self.Constraints = []
        self._construction = []
        self.Placement = Placement()
        self.AttachmentOffset = Placement()
        self.Support = None
        self.AttachmentSupport = None
        self.MapMode = "Deactivated"
        self.ExpressionEngine = []
        self.ExternalGeometry = []
        self.ViewObject = SimpleNamespace(Visibility=True)

    @property
    def Name(self):
        if self._deleted:
            raise ReferenceError("Cannot access Name on deleted fake object")
        return self._name

    def addGeometry(self, geometry, construction=False):
        self.Geometry.append(geometry)
        self._construction.append(bool(construction))
        return len(self.Geometry) - 1

    def getConstruction(self, index):
        return self._construction[index]

    def setConstruction(self, index, state):
        self._construction[index] = state

    def toggleConstruction(self, index):
        self._construction[index] = not self._construction[index]

    def addConstraint(self, constraint):
        self.Constraints.append(constraint)
        return len(self.Constraints) - 1

    def renameConstraint(self, index, name):
        self.Constraints[index].Name = name

    def setDriving(self, index, state):
        self.Constraints[index].Driving = state

    def setVirtualSpace(self, index, state):
        self.Constraints[index].InVirtualSpace = state

    def setActive(self, index, state):
        self.Constraints[index].IsActive = state

    def setExpression(self, property_path, expression):
        self.ExpressionEngine.append((property_path, expression))

    def addExternal(self, object_name, subelement):
        self.ExternalGeometry.append((object_name, (subelement,)))
        return -len(self.ExternalGeometry) - 2


class Feature:
    def __init__(self, name, label=None, type_id="PartDesign::Feature") -> None:
        self.Name = name
        self.Label = label or name
        self.TypeId = type_id
        self.Document = None


class Document:
    def __init__(self) -> None:
        self.Name = "TargetDocument"
        self.Objects = []
        self.transactions = []
        self.recompute_result = True
        self.fail_recompute = False
        self._transaction_start = None

    def openTransaction(self, name):
        self.transactions.append(("open", name))
        self._transaction_start = list(self.Objects)

    def commitTransaction(self):
        self.transactions.append(("commit", None))
        self._transaction_start = None

    def abortTransaction(self):
        self.transactions.append(("abort", None))
        self.Objects = list(self._transaction_start or [])
        self._transaction_start = None

    def addObject(self, type_id, name):
        if type_id != "Sketcher::SketchObject":
            raise ValueError("unexpected type")
        sketch = Sketch(name=name, label=name)
        self.Objects.append(sketch)
        return sketch

    def removeObject(self, name):
        self.Objects = [item for item in self.Objects if item.Name != name]

    def getObject(self, name):
        return next((item for item in self.Objects if item.Name == name), None)

    def recompute(self):
        if self.fail_recompute:
            raise RuntimeError("fake recompute failure")
        return self.recompute_result


class InvalidatingAbortDocument(Document):
    def __init__(self) -> None:
        super().__init__()
        self.created_during_transaction = None

    def addObject(self, type_id, name):
        created = super().addObject(type_id, name)
        self.created_during_transaction = created
        return created

    def abortTransaction(self):
        created = self.created_during_transaction
        super().abortTransaction()
        if created is not None:
            created._deleted = True


APP = SimpleNamespace(Vector=Vector, Rotation=Rotation, Placement=Placement)
PART = SimpleNamespace(
    LineSegment=LineSegment,
    Circle=Circle,
    ArcOfCircle=ArcOfCircle,
    Point=Point,
)
SKETCHER = SimpleNamespace(Constraint=Constraint)


def _ids():
    return {
        "document_id": str(uuid4()),
        "object_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "captured_at": "2026-08-25T12:00:00+00:00",
    }


def _source_sketch() -> Sketch:
    sketch = Sketch(label="Mounting Sketch – 草图")
    sketch.addGeometry(LineSegment(Vector(0, 0), Vector(10, 0)), False)
    sketch.addGeometry(Circle(Vector(5, 5), Vector(0, 0, 1), 2), True)
    sketch.addGeometry(
        ArcOfCircle(Circle(Vector(0, 0), Vector(0, 0, 1), 3), 0.25, 1.5),
        False,
    )
    sketch.addGeometry(Point(Vector(4, 7)), False)
    sketch.addConstraint(Constraint("Horizontal", 0))
    sketch.addConstraint(Constraint("Vertical", 0, 1, 1, 1))
    coincident = Constraint("Coincident", 0, 2, 1, 3)
    sketch.addConstraint(coincident)
    radius = Constraint("Radius", 1, 2.0)
    radius.Name = "CircleRadius"
    radius.Driving = False
    radius.InVirtualSpace = True
    radius.IsActive = False
    sketch.addConstraint(radius)
    sketch.Placement = Placement(Vector(1, 2, 3), Rotation(0.1, 0.2, 0.3, 0.9))
    sketch.ExpressionEngine = [("Constraints[3]", "Spreadsheet.Radius")]
    return sketch


def _snapshot(sketch=None):
    return serialize_sketch(sketch or _source_sketch(), **_ids())


def _restore(document, snapshot, **kwargs):
    return restore_sketch_as_copy(
        document,
        snapshot,
        app_module=APP,
        part_module=PART,
        sketcher_module=SKETCHER,
        **kwargs,
    )


def test_restore_as_copy_reconstructs_state_in_one_transaction() -> None:
    document = Document()
    snapshot = _snapshot()

    result = _restore(document, snapshot)

    assert result.success is True
    assert result.status_code == "restored_as_copy"
    assert result.restored_object_name == "Sketch003_Recovered"
    assert result.restored_object_label == "Mounting Sketch – 草图 (Recovered)"
    assert [event[0] for event in document.transactions] == ["open", "commit"]
    restored = result.restored_object
    assert len(restored.Geometry) == 4
    assert restored._construction == [False, True, False, False]
    assert [item.Type for item in restored.Constraints] == [
        "Horizontal",
        "Vertical",
        "Coincident",
        "Radius",
    ]
    assert restored.Constraints[1].FirstPos == 1
    assert restored.Constraints[1].Second == 1
    assert restored.Constraints[3].Name == "CircleRadius"
    assert restored.Constraints[3].Driving is False
    assert restored.Constraints[3].InVirtualSpace is True
    assert restored.Constraints[3].IsActive is False
    assert restored.Placement.Base.x == 1.0


def test_restore_never_replaces_existing_names_or_labels() -> None:
    document = Document()
    first = Sketch("Sketch003_Recovered", "Mounting Sketch – 草图 (Recovered)")
    document.Objects.append(first)

    result = _restore(document, _snapshot())

    assert result.success is True
    assert result.restored_object_name == "Sketch003_Recovered_2"
    assert result.restored_object_label == "Mounting Sketch – 草图 (Recovered) 2"
    assert document.Objects[0] is first


def test_round_trip_compares_reconstruction_state_with_tolerance() -> None:
    original = _snapshot()
    result = _restore(Document(), original)
    assert result.success
    restored = serialize_sketch(result.restored_object, **_ids())

    validation = compare_sketch_snapshots(original, restored, tolerance=1e-8)

    assert validation.success is True
    assert validation.status_code == "round_trip_valid"
    assert validation.differences == ()


def test_round_trip_reports_geometry_and_recompute_differences() -> None:
    original = _snapshot()
    changed = copy.deepcopy(original)
    changed["object"]["geometry"][0]["data"]["end"]["x"] = 11.0

    validation = compare_sketch_snapshots(
        original,
        changed,
        recompute_succeeded=False,
    )

    assert validation.success is False
    assert "geometry 0 numeric data differs" in validation.differences
    assert "document recompute did not succeed" in validation.differences


def test_failed_recompute_aborts_and_removes_partial_copy() -> None:
    document = Document()
    existing = Feature("Existing")
    document.Objects.append(existing)
    document.fail_recompute = True
    snapshot = _snapshot()
    unchanged_snapshot = copy.deepcopy(snapshot)

    result = _restore(document, snapshot)

    assert result.success is False
    assert result.status_code == "restore_failed"
    assert result.error_stage == "recompute"
    assert [event[0] for event in document.transactions] == ["open", "abort"]
    assert document.Objects == [existing]
    assert snapshot == unchanged_snapshot


def test_ineligible_snapshot_is_rejected_before_transaction() -> None:
    document = Document()
    snapshot = _snapshot()
    snapshot["recovery"]["restore_as_copy_eligibility"] = "unsupported"

    result = _restore(document, snapshot)

    assert result.success is False
    assert result.status_code == "restore_preflight_failed"
    assert result.error_stage == "preflight"
    assert document.transactions == []
    assert document.Objects == []


def test_unsafe_expression_is_skipped_without_failing_restore() -> None:
    source = _source_sketch()
    source.ExpressionEngine.append(("Placement.Base.x", "Spreadsheet.X"))

    result = _restore(Document(), _snapshot(source))

    assert result.success is True
    assert result.restored_object.ExpressionEngine == [
        ("Constraints[3]", "Spreadsheet.Radius")
    ]
    assert "An unsafe or unsupported expression was skipped." in result.warnings


def test_attachment_is_not_restored_without_explicit_validation() -> None:
    source = _source_sketch()
    support = Feature("Pad")
    support.Document = SimpleNamespace(Name="TargetDocument")
    source.Support = (support, ("Face4",))
    source.MapMode = "FlatFace"
    snapshot = _snapshot(source)

    result = _restore(Document(), snapshot)

    assert result.success is True
    assert result.restored_object.Support is None
    assert "Recorded attachment was not restored without validation." in result.warnings


def test_attachment_restores_only_after_reference_validator_accepts_it() -> None:
    source = _source_sketch()
    support = Feature("Pad")
    support.Document = SimpleNamespace(Name="TargetDocument")
    source.Support = (support, ("Face4",))
    source.MapMode = "FlatFace"
    snapshot = _snapshot(source)
    document = Document()
    live_support = Feature("Pad")
    document.Objects.append(live_support)

    result = _restore(
        document,
        snapshot,
        restore_attachment=True,
        reference_validator=lambda _document, reference: (
            reference["subelements"] == ["Face4"]
        ),
    )

    assert result.success is True
    assert result.restored_object.Support == (live_support, ("Face4",))
    assert result.restored_object.MapMode == "FlatFace"


def test_external_constraint_requires_validated_external_geometry() -> None:
    snapshot = _snapshot()
    snapshot["object"]["constraints"][0]["arguments"]["first"] = -3
    document = Document()

    result = _restore(document, snapshot)

    assert result.success is False
    assert result.status_code == "restore_preflight_failed"
    assert document.transactions == []


def test_root_axes_are_not_mistaken_for_external_geometry() -> None:
    source = _source_sketch()
    source.Constraints = [Constraint("PointOnObject", 0, 1, -2)]

    result = _restore(Document(), _snapshot(source))

    assert result.success is True
    assert result.restored_object.Constraints[0].Second == -2


def test_abort_does_not_dereference_freecad_invalidated_wrapper() -> None:
    document = InvalidatingAbortDocument()
    snapshot = _snapshot()
    snapshot["object"]["geometry"][1]["data"]["radius"] = "invalid"

    result = _restore(document, snapshot)

    assert result.success is False
    assert result.status_code == "restore_failed"
    assert result.error_stage == "geometry"
    assert result.error_detail == "circle radius must be numeric"
    assert document.Objects == []
