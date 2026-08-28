from types import SimpleNamespace

from model_health_assistant.support_validation import (
    RESTORE_DETACHED,
    RESTORE_WITH_ORIGINAL_SUPPORT,
    SUPPORT_CHANGED,
    SUPPORT_MISSING,
    SUPPORT_NOT_APPLICABLE,
    SUPPORT_RESTORABLE,
    SUPPORT_UNVERIFIED,
    assess_snapshot_support,
    support_reference_is_restorable,
)


class Feature:
    def __init__(self, name: str, type_id: str, *, placement=None) -> None:
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Placement = placement


class Document:
    def __init__(self, name="RecoveryDocument", objects=()) -> None:
        self.Name = name
        self.Objects = list(objects)

    def getObject(self, name):  # noqa: N802
        return next((item for item in self.Objects if item.Name == name), None)


def _reference(
    name: str,
    type_id: str,
    *,
    subelements=(),
    document_name="RecoveryDocument",
    placement=None,
):
    descriptor = {
        "name": name,
        "label": name,
        "type_id": type_id,
        "document_name": document_name,
    }
    if placement is not None:
        descriptor["placement"] = placement
    return {
        "object": {
            **descriptor,
        },
        "subelements": list(subelements),
    }


def _snapshot(*references):
    return {
        "object": {
            "support": list(references),
            "attachment_support": [],
        }
    }


def test_no_support_is_not_applicable_and_detached_is_safe_default() -> None:
    assessment = assess_snapshot_support(Document(), _snapshot())

    assert assessment.category == SUPPORT_NOT_APPLICABLE
    assert assessment.can_restore_original is False
    assert assessment.recommended_mode == RESTORE_DETACHED


def test_origin_and_datum_planes_are_deterministically_restorable() -> None:
    placement = {
        "base": {"x": 0.0, "y": 0.0, "z": 2.0},
        "rotation": {"quaternion": [0.0, 0.0, 0.0, 1.0]},
    }
    live_placement = SimpleNamespace(
        Base=SimpleNamespace(x=0.0, y=0.0, z=2.0),
        Rotation=SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0)),
    )
    origin = Feature("XY_Plane", "PartDesign::Feature")
    datum = Feature("DatumPlane", "PartDesign::Plane", placement=live_placement)
    document = Document(objects=(origin, datum))

    origin_assessment = assess_snapshot_support(
        document,
        _snapshot(_reference("XY_Plane", "PartDesign::Feature")),
    )
    datum_assessment = assess_snapshot_support(
        document,
        _snapshot(
            _reference("DatumPlane", "PartDesign::Plane", placement=placement)
        ),
    )

    assert origin_assessment.category == SUPPORT_RESTORABLE
    assert datum_assessment.category == SUPPORT_RESTORABLE
    assert origin_assessment.can_restore_original is True
    assert origin_assessment.recommended_mode == RESTORE_WITH_ORIGINAL_SUPPORT


def test_modified_datum_plane_is_support_changed() -> None:
    recorded = {
        "base": {"x": 0.0, "y": 0.0, "z": 2.0},
        "rotation": {"quaternion": [0.0, 0.0, 0.0, 1.0]},
    }
    changed_placement = SimpleNamespace(
        Base=SimpleNamespace(x=0.0, y=0.0, z=5.0),
        Rotation=SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0)),
    )
    datum = Feature(
        "DatumPlane",
        "PartDesign::Plane",
        placement=changed_placement,
    )

    assessment = assess_snapshot_support(
        Document(objects=(datum,)),
        _snapshot(
            _reference("DatumPlane", "PartDesign::Plane", placement=recorded)
        ),
    )

    assert assessment.category == SUPPORT_CHANGED
    assert assessment.recommended_mode == RESTORE_DETACHED


def test_missing_and_changed_support_are_distinct_categories() -> None:
    document = Document(objects=(Feature("DatumPlane", "PartDesign::Feature"),))

    missing = assess_snapshot_support(
        document,
        _snapshot(_reference("MissingPlane", "PartDesign::Plane")),
    )
    changed = assess_snapshot_support(
        document,
        _snapshot(_reference("DatumPlane", "PartDesign::Plane")),
    )

    assert missing.category == SUPPORT_MISSING
    assert changed.category == SUPPORT_CHANGED
    assert missing.recommended_mode == RESTORE_DETACHED
    assert changed.recommended_mode == RESTORE_DETACHED


def test_generated_face_name_is_never_treated_as_topological_proof() -> None:
    pad = Feature("Pad", "PartDesign::Feature")
    document = Document(objects=(pad,))
    reference = _reference(
        "Pad",
        "PartDesign::Feature",
        subelements=("Face4",),
    )

    assessment = assess_snapshot_support(document, _snapshot(reference))

    assert assessment.category == SUPPORT_UNVERIFIED
    assert assessment.can_restore_original is False
    assert assessment.recommended_mode == RESTORE_DETACHED
    assert "cannot be safely verified" in assessment.message
    assert support_reference_is_restorable(document, reference) is False


def test_external_document_support_is_unverified_even_when_name_matches() -> None:
    plane = Feature("XY_Plane", "PartDesign::Feature")
    document = Document(objects=(plane,))

    assessment = assess_snapshot_support(
        document,
        _snapshot(
            _reference(
                "XY_Plane",
                "PartDesign::Feature",
                document_name="ExternalDocument",
            )
        ),
    )

    assert assessment.category == SUPPORT_UNVERIFIED
    assert assessment.recommended_mode == RESTORE_DETACHED


def test_invalid_support_shape_fails_closed_as_unverified() -> None:
    assessment = assess_snapshot_support(
        Document(),
        {"object": {"support": SimpleNamespace(), "attachment_support": []}},
    )

    assert assessment.category == SUPPORT_UNVERIFIED
    assert assessment.can_restore_original is False
