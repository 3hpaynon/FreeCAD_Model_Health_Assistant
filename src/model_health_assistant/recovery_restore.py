"""Service-layer orchestration for selective restore-as-copy workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from .recovery_lifecycle import assess_sketch_snapshot
from .recovery_vault import (
    RecoveryIndexEntry,
    RecoveryVault,
    utc_timestamp,
)
from .sketch_restore import SketchRestoreResult, restore_sketch_as_copy
from .support_validation import (
    RESTORE_DETACHED,
    RESTORE_WITH_ORIGINAL_SUPPORT,
    SUPPORT_NOT_APPLICABLE,
    SupportAssessment,
    assess_snapshot_support,
    support_reference_is_restorable,
)

RestoreEngine = Callable[..., SketchRestoreResult]
TimestampFactory = Callable[[], str]


@dataclass(frozen=True, slots=True)
class RestoreAssessment:
    """Request-time facts displayed before a selective restore."""

    document_id: str
    object_id: str
    snapshot_id: str
    object_name: str
    object_label: str
    geometry_count: int | None
    constraint_count: int | None
    geometry_recoverable: bool
    constraints_recoverable: bool
    recovery_eligibility: str
    support: SupportAssessment
    allowed_modes: tuple[str, ...]
    recommended_mode: str | None
    warnings: tuple[str, ...] = ()

    @property
    def can_restore(self) -> bool:
        return bool(self.allowed_modes)

    @property
    def confirmation_text(self) -> str:
        geometry = "✓ Recoverable" if self.geometry_recoverable else "✗ Not recoverable"
        constraints = (
            "✓ Recoverable" if self.constraints_recoverable else "✗ Not recoverable"
        )
        if self.support.can_restore_original:
            support = "✓ Available"
        elif self.support.category == SUPPORT_NOT_APPLICABLE:
            support = "Not required"
        else:
            support = "⚠ Could not be safely verified"
        lines = [
            f"Restore {self.object_name}",
            "",
            "Geometry:",
            geometry,
            "",
            "Constraints:",
            constraints,
            "",
            "Original support:",
            support,
            "",
            self.support.message,
        ]
        if self.warnings:
            lines.extend(("", "Warnings:", *(f"• {item}" for item in self.warnings)))
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RestoreExecutionResult:
    """One service-level restore outcome suitable for UI presentation."""

    success: bool
    status_code: str
    message: str
    mode: str
    restored_object_name: str | None = None
    restored_object_label: str | None = None
    warnings: tuple[str, ...] = ()
    error_stage: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    restored_object: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        # dataclasses.asdict() deep-copies every field before we could remove
        # restored_object. Native FreeCAD wrappers are deliberately not
        # copyable, so construct the public diagnostic payload explicitly.
        return {
            "success": self.success,
            "status_code": self.status_code,
            "message": self.message,
            "mode": self.mode,
            "restored_object_name": self.restored_object_name,
            "restored_object_label": self.restored_object_label,
            "warnings": list(self.warnings),
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
        }


class RecoveryRestoreService:
    """Load, reassess, restore, and mark one durable deleted sketch record."""

    def __init__(
        self,
        vault: RecoveryVault,
        *,
        restore_engine: RestoreEngine = restore_sketch_as_copy,
        timestamp_factory: TimestampFactory = utc_timestamp,
        app_module: Any | None = None,
        part_module: Any | None = None,
        sketcher_module: Any | None = None,
        observer: Any | None = None,
    ) -> None:
        if not isinstance(vault, RecoveryVault):
            raise TypeError("vault must be a RecoveryVault")
        self.vault = vault
        self.restore_engine = restore_engine
        self.timestamp_factory = timestamp_factory
        self.app_module = app_module
        self.part_module = part_module
        self.sketcher_module = sketcher_module
        self.observer = observer

    def assess(
        self,
        document: Any,
        document_id: str,
        object_id: str,
    ) -> RestoreAssessment:
        """Revalidate snapshot and live support before showing confirmation."""

        entry = self._load_entry(document_id, object_id)
        if entry.deleted_at is None:
            raise ValueError("The recovery entry is not a deleted sketch.")
        snapshot = self.vault.load_snapshot(document_id, entry.latest_snapshot_id)
        snapshot_assessment = assess_sketch_snapshot(snapshot)
        support = assess_snapshot_support(document, snapshot)
        object_record = snapshot.get("object")
        if not isinstance(object_record, Mapping):
            raise ValueError("The recovery snapshot object record is invalid.")
        geometry_count = _optional_count(object_record.get("geometry_count"))
        constraint_count = _optional_count(object_record.get("constraint_count"))
        recoverable = snapshot_assessment.eligibility in {
            "available",
            "available_with_warning",
        }
        has_unsafe_external_references = _uses_external_geometry(object_record)
        allowed_modes: tuple[str, ...] = ()
        if recoverable:
            if support.can_restore_original and not has_unsafe_external_references:
                allowed_modes = (
                    RESTORE_WITH_ORIGINAL_SUPPORT,
                    RESTORE_DETACHED,
                )
            else:
                allowed_modes = (RESTORE_DETACHED,)
        if has_unsafe_external_references:
            external_reference_warnings = (
                "External geometry references cannot be safely reattached "
                "automatically; restore detached instead.",
            )
        else:
            external_reference_warnings = ()
        warnings = tuple(
            dict.fromkeys(
                (
                    *snapshot_assessment.warnings,
                    *snapshot_assessment.critical_issues,
                    *external_reference_warnings,
                )
            )
        )
        return RestoreAssessment(
            document_id=document_id,
            object_id=object_id,
            snapshot_id=entry.latest_snapshot_id,
            object_name=entry.object_name,
            object_label=entry.object_label,
            geometry_count=geometry_count,
            constraint_count=constraint_count,
            geometry_recoverable=(recoverable and not any(
                item.startswith("geometry:")
                for item in snapshot_assessment.unsupported_elements
            )),
            constraints_recoverable=(recoverable and not any(
                item.startswith("constraint:")
                for item in snapshot_assessment.unsupported_elements
            )),
            recovery_eligibility=snapshot_assessment.eligibility,
            support=support,
            allowed_modes=allowed_modes,
            recommended_mode=(
                RESTORE_DETACHED
                if recoverable and has_unsafe_external_references
                else support.recommended_mode
                if recoverable
                else None
            ),
            warnings=warnings,
        )

    def restore(
        self,
        document: Any,
        document_id: str,
        object_id: str,
        *,
        mode: str,
    ) -> RestoreExecutionResult:
        """Restore after request-time revalidation and retain the source snapshot."""

        try:
            assessment = self.assess(document, document_id, object_id)
        except Exception as exc:  # noqa: BLE001 - return a controlled service result.
            return _service_failure(
                mode,
                "restore_assessment_failed",
                "The deleted sketch could not be safely reassessed.",
                exc,
                stage="assessment",
            )
        if mode not in assessment.allowed_modes:
            return RestoreExecutionResult(
                success=False,
                status_code="restore_mode_not_allowed",
                message="The selected restore mode is not safe for this sketch.",
                mode=mode,
                error_stage="assessment",
            )

        try:
            snapshot = self.vault.load_snapshot(
                document_id,
                assessment.snapshot_id,
            )
        except Exception as exc:  # noqa: BLE001 - return a controlled service result.
            return _service_failure(
                mode,
                "restore_snapshot_unavailable",
                "The deleted sketch snapshot could not be read safely.",
                exc,
                stage="snapshot",
            )

        restore_attachment = mode == RESTORE_WITH_ORIGINAL_SUPPORT
        validator = support_reference_is_restorable if restore_attachment else None
        try:
            suspension = getattr(self.observer, "suspend_object_tracking", None)
            tracking_context = (
                suspension() if callable(suspension) else nullcontext()
            )
            with tracking_context:
                engine_result = self.restore_engine(
                    document,
                    snapshot,
                    app_module=self.app_module,
                    part_module=self.part_module,
                    sketcher_module=self.sketcher_module,
                    restore_attachment=restore_attachment,
                    restore_external_geometry=False,
                    reference_validator=validator,
                    detach_unsafe_references=mode == RESTORE_DETACHED,
                )
        except Exception as exc:  # noqa: BLE001 - contain native wrapper failures.
            return _service_failure(
                mode,
                "restore_engine_failed",
                "The sketch could not be restored safely.",
                exc,
                stage="reconstruction",
            )
        if not isinstance(engine_result, SketchRestoreResult):
            return _service_failure(
                mode,
                "restore_engine_result_invalid",
                "The sketch restoration engine returned an invalid result.",
                TypeError("restore engine result must be a SketchRestoreResult"),
                stage="reconstruction",
            )
        if not engine_result.success:
            return RestoreExecutionResult(
                success=False,
                status_code=engine_result.status_code,
                message=engine_result.message,
                mode=mode,
                warnings=engine_result.warnings,
                error_stage=engine_result.error_stage,
                error_type=engine_result.error_type,
                error_detail=engine_result.error_detail,
            )

        restored_name = engine_result.restored_object_name
        if not isinstance(restored_name, str) or not restored_name:
            return RestoreExecutionResult(
                success=False,
                status_code="restore_result_invalid",
                message="The restore completed without a valid new object identity.",
                mode=mode,
                restored_object=engine_result.restored_object,
            )

        capture_restored = getattr(self.observer, "capture_restored_sketch", None)
        observer_warning: tuple[str, ...] = ()
        if callable(capture_restored):
            try:
                captured = bool(capture_restored(engine_result.restored_object))
            except Exception:  # noqa: BLE001 - the restored copy already exists.
                captured = False
            if not captured:
                observer_warning = (
                    "The restored copy could not be added to live recovery tracking yet.",
                )

        history_warning: tuple[str, ...] = ()
        status_code = "restored_as_copy"
        try:
            entry = self._load_entry(document_id, object_id)
            updated = entry.with_restore(
                restored_at=self.timestamp_factory(),
                restored_object_name=restored_name,
            )
            self.vault.update_index_entry(updated)
        except Exception:  # noqa: BLE001 - the document copy already exists.
            status_code = "restored_with_history_warning"
            history_warning = (
                "The sketch was restored, but its vault restore history could not be updated.",
            )

        return RestoreExecutionResult(
            success=True,
            status_code=status_code,
            message=f"Recovered {assessment.object_name} as {restored_name}.",
            mode=mode,
            restored_object_name=restored_name,
            restored_object_label=engine_result.restored_object_label,
            warnings=tuple(
                (*engine_result.warnings, *observer_warning, *history_warning)
            ),
            restored_object=engine_result.restored_object,
        )

    def _load_entry(self, document_id: str, object_id: str) -> RecoveryIndexEntry:
        index = self.vault.load_index(document_id)
        matches = [
            RecoveryIndexEntry.from_mapping(payload)
            for payload in index["entries"]
            if payload.get("object_id") == object_id
        ]
        if len(matches) != 1:
            raise ValueError("The deleted sketch entry could not be uniquely resolved.")
        return matches[0]


def _optional_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _uses_external_geometry(object_record: Mapping[str, Any]) -> bool:
    if object_record.get("external_geometry"):
        return True
    constraints = object_record.get("constraints")
    if not isinstance(constraints, list):
        return True
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            return True
        arguments = constraint.get("arguments")
        if not isinstance(arguments, Mapping):
            return True
        for key in ("first", "second", "third"):
            value = arguments.get(key)
            if isinstance(value, int) and -2000 < value <= -3:
                return True
    return False


def _service_failure(
    mode: str,
    status_code: str,
    message: str,
    error: Exception,
    *,
    stage: str,
) -> RestoreExecutionResult:
    return RestoreExecutionResult(
        success=False,
        status_code=status_code,
        message=message,
        mode=mode,
        error_stage=stage,
        error_type=type(error).__name__,
        error_detail=str(error),
    )


__all__ = [
    "RecoveryRestoreService",
    "RestoreAssessment",
    "RestoreExecutionResult",
]
