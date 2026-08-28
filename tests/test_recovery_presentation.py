from datetime import datetime, timedelta, timezone
from uuid import uuid4

from model_health_assistant.recovery_lifecycle import DeletedSketchRecord
from model_health_assistant.recovery_presentation import (
    build_deleted_object_presentations,
)


_CHICAGO_SUMMER = timezone(timedelta(hours=-5), "CDT")
_NOW = datetime(2026, 8, 27, 11, 0, tzinfo=_CHICAGO_SUMMER)


def _record(
    *,
    deleted_at: str = "2026-08-27T15:42:00+00:00",
    eligibility: str = "available",
    support_status: str = "not_applicable",
    unsupported: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> DeletedSketchRecord:
    return DeletedSketchRecord(
        schema_version="1.0",
        document_id=str(uuid4()),
        object_id=str(uuid4()),
        object_name="Sketch003",
        object_label="Rotor Mounting Sketch",
        type_id="Sketcher::SketchObject",
        status="deleted",
        deleted_at=deleted_at,
        latest_snapshot_id=str(uuid4()),
        recovery_eligibility=eligibility,
        support_status=support_status,
        unsupported_elements=unsupported,
        warnings=warnings,
        geometry_count=4,
        constraint_count=7,
    )


def test_deleted_object_presentation_is_compact_and_complete() -> None:
    item = build_deleted_object_presentations((_record(),), now=_NOW)[0]

    assert item.primary_text == "Rotor Mounting Sketch\nSketch003"
    assert item.deleted_summary == "10:42 AM"
    assert item.type_text == "Sketch"
    assert item.recovery_text == "Available"
    assert item.support_text == "No original support required"
    assert item.table_values == (
        "Rotor Mounting Sketch\nSketch003",
        "10:42 AM",
        "Sketch",
        "Available",
        "No original support required",
    )
    assert "Original name: Sketch003" in item.details_text
    assert "Geometry: 4" in item.details_text
    assert "Constraints: 7" in item.details_text
    assert "Unsupported elements: None" in item.details_text
    assert "Warnings: None" in item.details_text
    assert "snapshot_id" not in item.details_text
    assert "{" not in item.details_text


def test_yesterday_warning_and_unsupported_information_are_human_readable() -> None:
    record = _record(
        deleted_at="2026-08-26T16:10:00+00:00",
        eligibility="available_with_warning",
        support_status="recorded_unresolved",
        unsupported=("geometry:Part.GeomBSplineCurve",),
        warnings=("Original attachment support requires validation before restore.",),
    )

    item = build_deleted_object_presentations((record,), now=_NOW)[0]

    assert item.deleted_summary == "Yesterday"
    assert item.recovery_text == "Available with warning"
    assert item.support_text == "Original support unresolved"
    assert item.unsupported_text == "geometry:Part.GeomBSplineCurve"
    assert "Original attachment support" in item.warnings_text


def test_partial_unavailable_and_invalid_time_have_conservative_text() -> None:
    partial, unavailable = build_deleted_object_presentations(
        (
            _record(eligibility="partial"),
            _record(deleted_at="invalid", eligibility="unavailable"),
        ),
        now=_NOW,
    )

    assert partial.recovery_text == "Partially supported"
    assert unavailable.recovery_text == "Not safely recoverable"
    assert unavailable.deleted_summary == "Unknown"
    assert "Deleted: Unknown" in unavailable.details_text
