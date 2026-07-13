from types import SimpleNamespace

import pytest

from model_health_assistant.extractor import (
    classify_feature_type,
    extract_document_features,
    extract_sketch_support,
)


@pytest.mark.parametrize(
    ("type_id", "expected_category"),
    [
        ("Sketcher::SketchObject", "sketch"),
        ("PartDesign::Pad", "additive_feature"),
        ("PartDesign::AdditiveCylinder", "additive_feature"),
        ("PartDesign::Pocket", "subtractive_feature"),
        ("PartDesign::SubtractiveBox", "subtractive_feature"),
        ("PartDesign::Fillet", "finishing_feature"),
        ("PartDesign::Chamfer", "finishing_feature"),
        ("PartDesign::LinearPattern", "pattern_feature"),
        ("PartDesign::MultiTransform", "pattern_feature"),
        ("PartDesign::DatumPlane", "reference_geometry"),
        ("PartDesign::Plane", "reference_geometry"),
        ("PartDesign::ShapeBinder", "reference_geometry"),
        ("PartDesign::Body", "container"),
        ("App::Part", "container"),
        ("App::Origin", "container"),
        ("Part::Feature", "other"),
        ("Spreadsheet::Sheet", "other"),
        ("", "other"),
    ],
)
def test_classify_feature_type(type_id: str, expected_category: str) -> None:
    assert classify_feature_type(type_id) == expected_category


def test_extract_document_features_classifies_every_document_object() -> None:
    objects = [
        SimpleNamespace(Name="Body", TypeId="PartDesign::Body"),
        SimpleNamespace(Name="Origin", TypeId="App::Origin"),
        SimpleNamespace(Name="XY_Plane", TypeId="App::Plane"),
        SimpleNamespace(Name="Sketch", TypeId="Sketcher::SketchObject"),
        SimpleNamespace(Name="Pad", TypeId="PartDesign::Pad"),
        SimpleNamespace(Name="Sketch001", TypeId="Sketcher::SketchObject"),
        SimpleNamespace(Name="Pocket", TypeId="PartDesign::Pocket"),
        SimpleNamespace(Name="Fillet", TypeId="PartDesign::Fillet"),
        SimpleNamespace(Name="PolarPattern", TypeId="PartDesign::PolarPattern"),
        SimpleNamespace(Name="Spreadsheet", TypeId="Spreadsheet::Sheet"),
    ]

    features = extract_document_features(SimpleNamespace(Objects=objects))

    assert len(features) == len(objects)
    assert {feature.internal_name: feature.category for feature in features} == {
        "Body": "container",
        "Origin": "container",
        "XY_Plane": "reference_geometry",
        "Sketch": "sketch",
        "Pad": "additive_feature",
        "Sketch001": "sketch",
        "Pocket": "subtractive_feature",
        "Fillet": "finishing_feature",
        "PolarPattern": "pattern_feature",
        "Spreadsheet": "other",
    }
    assert [feature.internal_name for feature in features if feature.is_sketch] == [
        "Sketch",
        "Sketch001",
    ]


def test_extract_feature_records_unique_direct_dependency_relationships() -> None:
    sketch = SimpleNamespace(Name="Sketch")
    pad = SimpleNamespace(Name="Pad")
    pocket = SimpleNamespace(Name="Pocket")
    fillet = SimpleNamespace(Name="Fillet")
    feature_object = SimpleNamespace(
        Name="Pad001",
        TypeId="PartDesign::Pad",
        OutList=[sketch, pad, sketch],
        InList=[pocket, fillet, pocket],
    )

    feature = extract_document_features(SimpleNamespace(Objects=[feature_object]))[0]

    assert feature.dependency_names == ["Sketch", "Pad"]
    assert feature.downstream_dependent_names == ["Pocket", "Fillet"]
    assert feature.direct_upstream_count == 2
    assert feature.direct_downstream_count == 2
    assert feature.to_dict()["direct_upstream_count"] == 2
    assert feature.to_dict()["direct_downstream_count"] == 2


def test_extract_feature_uses_direct_dependency_method_fallbacks() -> None:
    upstream = SimpleNamespace(Name="Sketch")
    downstream = SimpleNamespace(Name="Pocket")

    class MethodBasedFeature:
        Name = "Pad"
        TypeId = "PartDesign::Pad"

        def getOutList(self):  # noqa: N802 - mirrors the FreeCAD API.
            return [upstream]

        def getInList(self):  # noqa: N802 - mirrors the FreeCAD API.
            return [downstream]

    feature = extract_document_features(
        SimpleNamespace(Objects=[MethodBasedFeature()])
    )[0]

    assert feature.dependency_names == ["Sketch"]
    assert feature.downstream_dependent_names == ["Pocket"]


def test_extract_origin_plane_support() -> None:
    support = SimpleNamespace(Name="XY_Plane", TypeId="App::Plane")

    result = extract_sketch_support(SimpleNamespace(Support=(support, [""])))

    assert result == {
        "support_name": "XY_Plane",
        "support_kind": "origin_plane",
        "support_subelement": None,
        "attached_to_generated_geometry": False,
        "support_is_stable_reference": True,
        "support_confidence": "high",
        "notes": [],
    }


def test_extract_generated_face_support_preserves_subelement() -> None:
    support = SimpleNamespace(Name="Pad001", TypeId="PartDesign::Pad")

    result = extract_sketch_support(SimpleNamespace(Support=(support, ["Face3"])))

    assert result["support_name"] == "Pad001"
    assert result["support_kind"] == "generated_geometry"
    assert result["support_subelement"] == "Face3"
    assert result["attached_to_generated_geometry"] is True
    assert result["support_is_stable_reference"] is False
    assert result["support_confidence"] == "high"


def test_extracted_feature_persists_structured_support() -> None:
    support = SimpleNamespace(Name="Pad001", TypeId="PartDesign::Pad")
    sketch = SimpleNamespace(
        Name="Sketch002",
        TypeId="Sketcher::SketchObject",
        Support=(support, ["Face12"]),
    )

    feature = extract_document_features(SimpleNamespace(Objects=[sketch]))[0]

    assert feature.support_name == "Pad001"
    assert feature.support_kind == "generated_geometry"
    assert feature.support_subelement == "Face12"
    assert feature.attached_to_generated_geometry is True
    assert feature.support_is_stable_reference is False
    assert feature.support_confidence == "high"


def test_extract_datum_plane_support() -> None:
    support = SimpleNamespace(Name="DatumPlane", TypeId="PartDesign::Plane")

    result = extract_sketch_support(SimpleNamespace(Support=(support, [])))

    assert result["support_kind"] == "datum_plane"
    assert result["support_is_stable_reference"] is True
    assert result["attached_to_generated_geometry"] is False
    assert result["support_confidence"] == "high"


def test_extract_origin_plane_from_origin_subelement() -> None:
    support = SimpleNamespace(Name="Origin", TypeId="App::Origin")

    result = extract_sketch_support(SimpleNamespace(Support=(support, ["XZ_Plane"])))

    assert result["support_kind"] == "origin_plane"
    assert result["support_subelement"] == "XZ_Plane"
    assert result["support_is_stable_reference"] is True
    assert result["support_confidence"] == "high"


def test_extract_explicitly_empty_support() -> None:
    result = extract_sketch_support(SimpleNamespace(Support=None))

    assert result["support_kind"] == "no_support"
    assert result["support_confidence"] == "high"
    assert result["attached_to_generated_geometry"] is False


def test_extract_missing_support_property_is_unknown() -> None:
    result = extract_sketch_support(SimpleNamespace())

    assert result["support_kind"] == "unknown"
    assert result["support_confidence"] == "low"
    assert result["attached_to_generated_geometry"] is False


def test_extract_unreadable_support_property_is_unknown() -> None:
    class UnreadableSupport:
        @property
        def Support(self):  # noqa: N802 - mirrors the FreeCAD property name.
            raise RuntimeError("support is unavailable")

    result = extract_sketch_support(UnreadableSupport())

    assert result["support_kind"] == "unknown"
    assert result["support_confidence"] == "low"
    assert result["notes"] == [
        "Sketch support could not be read: RuntimeError: support is unavailable"
    ]
