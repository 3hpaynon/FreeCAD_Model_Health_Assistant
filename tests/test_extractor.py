from types import SimpleNamespace

import pytest

from model_health_assistant.extractor import classify_feature_type, extract_document_features


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
