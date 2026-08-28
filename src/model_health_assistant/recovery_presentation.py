"""User-facing presentation for durable deleted-sketch recovery records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .recovery_lifecycle import DeletedSketchRecord


@dataclass(frozen=True, slots=True)
class DeletedObjectPresentation:
    """Compact list and details text for one deleted sketch."""

    document_id: str
    object_id: str
    snapshot_id: str
    object_name: str
    object_label: str
    lifecycle_status: str
    recovery_eligibility: str
    support_status: str
    primary_text: str
    deleted_summary: str
    deleted_details: str
    type_text: str
    recovery_text: str
    support_text: str
    geometry_count_text: str
    constraint_count_text: str
    unsupported_text: str
    warnings_text: str
    restored_copies_text: str

    @property
    def can_attempt_restore(self) -> bool:
        return self.recovery_eligibility in {
            "available",
            "available_with_warning",
        }

    @property
    def table_values(self) -> tuple[str, str, str, str, str]:
        return (
            self.primary_text,
            self.deleted_summary,
            self.type_text,
            self.recovery_text,
            self.support_text,
        )

    @property
    def details_text(self) -> str:
        return "\n".join(
            (
                f"Label: {self.object_label or '(none)'}",
                f"Original name: {self.object_name}",
                f"Deleted: {self.deleted_details}",
                f"Type: {self.type_text}",
                f"Geometry: {self.geometry_count_text}",
                f"Constraints: {self.constraint_count_text}",
                f"Support: {self.support_text}",
                f"Recovery: {self.recovery_text}",
                f"Unsupported elements: {self.unsupported_text}",
                f"Warnings: {self.warnings_text}",
                f"Restored copies: {self.restored_copies_text}",
            )
        )


def build_deleted_object_presentations(
    records: Iterable[DeletedSketchRecord],
    *,
    now: datetime | None = None,
) -> tuple[DeletedObjectPresentation, ...]:
    """Build deterministic, non-technical UI records without raw snapshot JSON."""

    effective_now = now or datetime.now().astimezone()
    if effective_now.tzinfo is None:
        effective_now = effective_now.astimezone()
    return tuple(
        _build_deleted_object_presentation(record, now=effective_now)
        for record in records
    )


def _build_deleted_object_presentation(
    record: DeletedSketchRecord,
    *,
    now: datetime,
) -> DeletedObjectPresentation:
    deleted_at = _local_datetime(record.deleted_at, now)
    label = record.object_label.strip()
    primary_text = (
        f"{label}\n{record.object_name}"
        if label and label != record.object_name
        else record.object_name
    )
    return DeletedObjectPresentation(
        document_id=record.document_id,
        object_id=record.object_id,
        snapshot_id=record.latest_snapshot_id,
        object_name=record.object_name,
        object_label=record.object_label,
        lifecycle_status=record.status,
        recovery_eligibility=record.recovery_eligibility,
        support_status=record.support_status,
        primary_text=primary_text,
        deleted_summary=_deleted_summary(deleted_at, now),
        deleted_details=_deleted_details(deleted_at),
        type_text=_type_text(record.type_id),
        recovery_text=_recovery_text(record.recovery_eligibility),
        support_text=_support_text(record.support_status),
        geometry_count_text=_count_text(record.geometry_count),
        constraint_count_text=_count_text(record.constraint_count),
        unsupported_text=_sequence_text(record.unsupported_elements),
        warnings_text=_sequence_text(record.warnings),
        restored_copies_text=_sequence_text(record.restored_object_names),
    )


def _local_datetime(value: str, now: datetime) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(now.tzinfo)


def _deleted_summary(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "Unknown"
    if value.date() == now.date():
        return _clock_text(value)
    if value.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    return value.strftime("%b %d, %Y").replace(" 0", " ")


def _deleted_details(value: datetime | None) -> str:
    if value is None:
        return "Unknown"
    date_text = value.strftime("%b %d, %Y").replace(" 0", " ")
    zone_text = value.tzname()
    suffix = f" {zone_text}" if zone_text else ""
    return f"{date_text} at {_clock_text(value)}{suffix}"


def _clock_text(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _type_text(type_id: str) -> str:
    return "Sketch" if type_id == "Sketcher::SketchObject" else type_id


def _recovery_text(value: str) -> str:
    return {
        "available": "Available",
        "available_with_warning": "Available with warning",
        "partial": "Partially supported",
        "unavailable": "Not safely recoverable",
        "unassessed": "Not assessed",
    }.get(value, "Unknown")


def _support_text(value: str) -> str:
    return {
        "not_applicable": "No original support required",
        "recorded_unresolved": "Original support unresolved",
        "external_references_unresolved": "External references unresolved",
        "unknown": "Unknown",
    }.get(value, "Unknown")


def _count_text(value: int | None) -> str:
    return str(value) if isinstance(value, int) and value >= 0 else "Not available"


def _sequence_text(values: tuple[str, ...]) -> str:
    return "; ".join(values) if values else "None"


__all__ = [
    "DeletedObjectPresentation",
    "build_deleted_object_presentations",
]
