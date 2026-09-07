"""FreeCAD observation, debounced snapshots, and durable deletion transitions."""

from __future__ import annotations

import atexit
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .recovery_lifecycle import RecoveryLifecycleService
from .recovery_vault import (
    RecoveryDocumentLocator,
    RecoveryIndexEntry,
    RecoveryVault,
    utc_timestamp,
)
from .sketch_snapshot import SKETCH_TYPE_ID, serialize_sketch

SnapshotSerializer = Callable[..., dict[str, Any]]
TimestampFactory = Callable[[], str]

DEFAULT_DEBOUNCE_MILLISECONDS = 750
_APP_OBSERVER_ATTRIBUTE = "_model_health_assistant_recovery_observer"


@dataclass(slots=True)
class TrackedSketchState:
    """Mutable internal state for one live sketch incarnation."""

    document_runtime_key: int
    document_id: str
    object_runtime_key: int
    object_id: str
    object_name: str
    object_label: str
    latest_entry: RecoveryIndexEntry | None = None
    latest_snapshot: dict[str, Any] | None = None
    fingerprint: str | None = None
    dirty: bool = False
    deleted: bool = False
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class PendingSketchDeletion:
    """Last known valid state retained when a tracked object disappears."""

    document_id: str
    object_id: str
    object_name: str
    object_label: str
    type_id: str
    snapshot_id: str
    observed_at: str
    snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "object_id": self.object_id,
            "object_name": self.object_name,
            "object_label": self.object_label,
            "type_id": self.type_id,
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at,
            "snapshot": deepcopy(self.snapshot),
        }


class QtDebounceScheduler:
    """Small retained single-shot QTimer adapter used inside FreeCAD GUI."""

    def __init__(self, qt_core: Any) -> None:
        self._callback: Callable[[], None] | None = None
        self._timer = qt_core.QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    def restart(self, callback: Callable[[], None], delay_ms: int) -> None:
        self._callback = callback
        self._timer.start(delay_ms)

    def cancel(self) -> None:
        self._timer.stop()
        self._callback = None

    def _on_timeout(self) -> None:
        callback = self._callback
        self._callback = None
        if callback is not None:
            callback()


class SketchRecoveryObserver:
    """Track supported sketches and persist only meaningful stable states."""

    def __init__(
        self,
        app_module: Any,
        vault: RecoveryVault,
        *,
        serializer: SnapshotSerializer = serialize_sketch,
        scheduler: Any | None = None,
        debounce_milliseconds: int = DEFAULT_DEBOUNCE_MILLISECONDS,
        session_id: str | None = None,
        timestamp_factory: TimestampFactory = utc_timestamp,
        lifecycle_service: RecoveryLifecycleService | None = None,
    ) -> None:
        if debounce_milliseconds < 0:
            raise ValueError("debounce_milliseconds must not be negative")
        self.app_module = app_module
        self.vault = vault
        self._serializer = serializer
        self._scheduler = scheduler
        self._debounce_milliseconds = debounce_milliseconds
        self._session_id = session_id or str(uuid4())
        self._timestamp_factory = timestamp_factory
        self._lifecycle_service = lifecycle_service or RecoveryLifecycleService(
            vault
        )
        self._document_instance_ids: dict[int, str] = {}
        self._document_ids: dict[int, str] = {}
        self._document_objects: dict[int, Any] = {}
        self._states_by_runtime: dict[int, TrackedSketchState] = {}
        self._state_keys_by_document: dict[int, set[int]] = {}
        self._pending_deletions: dict[tuple[str, str], PendingSketchDeletion] = {}
        self._deletion_persistence_errors: dict[tuple[str, str], str] = {}
        self._tracking_suspension_depth = 0
        self._shutdown = False
        self._registered = False

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def tracked_sketch_count(self) -> int:
        return len(self._states_by_runtime)

    def document_id_for(self, document: Any) -> str | None:
        """Return the durable identity already assigned to one live document."""

        return self._document_ids.get(id(document))

    @contextmanager
    def suspend_object_tracking(self):
        """Ignore transient object callbacks during one controlled transaction."""

        self._tracking_suspension_depth += 1
        try:
            yield
        finally:
            self._tracking_suspension_depth = max(
                0,
                self._tracking_suspension_depth - 1,
            )

    def capture_restored_sketch(self, sketch: Any) -> bool:
        """Capture one fully reconstructed sketch after callback suspension ends."""

        if self._shutdown or self._tracking_suspension_depth:
            return False
        state = self._track_object(sketch, capture_immediately=True)
        return bool(state is not None and state.latest_snapshot is not None)

    def start(self) -> None:
        """Scan documents already open when the observer is registered."""

        if self._shutdown:
            return
        for document in _open_documents(self.app_module):
            self._track_document(document)

    def shutdown(self) -> None:
        """Release live wrapper references and cancel a pending timer."""

        if self._shutdown:
            return
        # Capture meaningful edits that have not yet reached the debounce
        # timer or a document recompute. Failures remain contained by
        # _capture_state so application shutdown is never interrupted.
        self.flush_pending()
        self.persist_pending_deletions()
        self._shutdown = True
        self._cancel_scheduler()
        self._states_by_runtime.clear()
        self._state_keys_by_document.clear()
        self._document_objects.clear()
        self._document_ids.clear()
        self._document_instance_ids.clear()
        self._pending_deletions.clear()
        self._deletion_persistence_errors.clear()

    def pending_deletions(self) -> tuple[PendingSketchDeletion, ...]:
        """Return defensive copies of deletion events retained this session."""

        return tuple(
            PendingSketchDeletion(
                document_id=item.document_id,
                object_id=item.object_id,
                object_name=item.object_name,
                object_label=item.object_label,
                type_id=item.type_id,
                snapshot_id=item.snapshot_id,
                observed_at=item.observed_at,
                snapshot=deepcopy(item.snapshot),
            )
            for item in self._pending_deletions.values()
        )

    def deletion_persistence_errors(self) -> dict[tuple[str, str], str]:
        """Return non-sensitive error types for deletion transitions awaiting retry."""

        return dict(self._deletion_persistence_errors)

    def persist_pending_deletions(self) -> int:
        """Idempotently persist all cached deletion events into the vault index."""

        created = 0
        for key, pending in tuple(self._pending_deletions.items()):
            try:
                result = self._lifecycle_service.record_deletion(pending)
            except Exception as exc:  # noqa: BLE001 - FreeCAD callbacks must survive.
                self._deletion_persistence_errors[key] = type(exc).__name__
                continue
            self._deletion_persistence_errors.pop(key, None)
            if result.created:
                created += 1
        return created

    def flush_pending(self) -> int:
        """Capture every dirty live sketch and return the number persisted."""

        if self._shutdown:
            return 0
        persisted = 0
        for runtime_key, state in list(self._states_by_runtime.items()):
            if state.dirty and not state.deleted:
                sketch = _find_object_for_state(
                    self._document_objects.get(state.document_runtime_key),
                    state,
                    runtime_key,
                )
                if sketch is not None and self._capture_state(state, sketch):
                    persisted += 1
        if not self._has_dirty_states():
            self._cancel_scheduler()
        return persisted

    def slotCreatedDocument(self, document: Any) -> None:  # noqa: N802
        self._safe_track_document(document)

    def slotDeletedDocument(self, document: Any) -> None:  # noqa: N802
        self._safe_forget_document(document)

    def slotActivateDocument(self, document: Any) -> None:  # noqa: N802
        self._safe_track_document(document)

    def slotChangedDocument(self, document: Any, property_name: str) -> None:  # noqa: N802
        if property_name in {"FileName", "Label", "Name"}:
            self._safe_refresh_document_identity(document)

    def slotCreatedObject(self, obj: Any) -> None:  # noqa: N802
        if self._tracking_suspension_depth:
            return
        self._safe_track_object(obj, capture_immediately=True)

    def slotBeforeChangeObject(self, obj: Any, _property: str) -> None:  # noqa: N802
        """Guarantee at least one baseline before the first observed mutation."""

        if self._tracking_suspension_depth:
            return
        try:
            state = self._state_for_object(obj, create=True)
            if state is not None and state.latest_snapshot is None:
                self._capture_state(state, obj)
        except Exception:  # noqa: BLE001 - observer callbacks must not escape.
            return

    def slotChangedObject(self, obj: Any, _property: str) -> None:  # noqa: N802
        if self._tracking_suspension_depth:
            return
        self._safe_mark_dirty(obj)

    def slotRecomputedObject(self, obj: Any) -> None:  # noqa: N802
        if self._tracking_suspension_depth:
            return
        self._safe_flush_object(obj)

    def slotRecomputedDocument(self, document: Any) -> None:  # noqa: N802
        if self._tracking_suspension_depth:
            return
        self._safe_flush_document(document)

    def slotDeletedObject(self, obj: Any) -> None:  # noqa: N802
        """Retain cached state without reading destroyed sketch geometry."""

        if self._tracking_suspension_depth:
            return
        try:
            runtime_key = id(obj)
            state = self._states_by_runtime.get(runtime_key)
            if state is None:
                return
            state.deleted = True
            state.dirty = False
            if state.latest_snapshot is not None and state.latest_entry is not None:
                pending = PendingSketchDeletion(
                    document_id=state.document_id,
                    object_id=state.object_id,
                    object_name=state.object_name,
                    object_label=state.object_label,
                    type_id=SKETCH_TYPE_ID,
                    snapshot_id=state.latest_entry.latest_snapshot_id,
                    observed_at=self._timestamp_factory(),
                    snapshot=deepcopy(state.latest_snapshot),
                )
                self._pending_deletions[(state.document_id, state.object_id)] = pending
                self.persist_pending_deletions()
            self._remove_live_state(runtime_key, state)
            if not self._has_dirty_states():
                self._cancel_scheduler()
        except Exception:  # noqa: BLE001 - deletion must never disrupt FreeCAD.
            return

    def _safe_track_document(self, document: Any) -> None:
        try:
            self._track_document(document)
        except Exception:  # noqa: BLE001 - observer callbacks must not escape.
            return

    def _safe_forget_document(self, document: Any) -> None:
        try:
            self._forget_document(document)
        except Exception:  # noqa: BLE001 - observer callbacks must not escape.
            return

    def _safe_refresh_document_identity(self, document: Any) -> None:
        try:
            self._ensure_document_identity(document)
        except Exception:  # noqa: BLE001 - Save As can expose transient paths.
            return

    def _safe_track_object(self, obj: Any, *, capture_immediately: bool) -> None:
        try:
            self._track_object(obj, capture_immediately=capture_immediately)
        except Exception:  # noqa: BLE001 - object creation must remain unaffected.
            return

    def _safe_mark_dirty(self, obj: Any) -> None:
        try:
            state = self._state_for_object(obj, create=True)
            if state is None or state.deleted:
                return
            state.dirty = True
            self._restart_scheduler()
        except Exception:  # noqa: BLE001 - object editing must remain unaffected.
            return

    def _safe_flush_object(self, obj: Any) -> None:
        try:
            state = self._state_for_object(obj, create=False)
            if state is not None and state.dirty and not state.deleted:
                self._capture_state(state, obj)
            if not self._has_dirty_states():
                self._cancel_scheduler()
        except Exception:  # noqa: BLE001 - recompute must remain unaffected.
            return

    def _safe_flush_document(self, document: Any) -> None:
        try:
            document_key = id(document)
            for runtime_key in list(
                self._state_keys_by_document.get(document_key, set())
            ):
                state = self._states_by_runtime.get(runtime_key)
                if state is None or not state.dirty or state.deleted:
                    continue
                sketch = _find_object_for_state(document, state, runtime_key)
                if sketch is not None:
                    self._capture_state(state, sketch)
            if not self._has_dirty_states():
                self._cancel_scheduler()
        except Exception:  # noqa: BLE001 - recompute must remain unaffected.
            return

    def _track_document(self, document: Any) -> None:
        if self._shutdown or document is None:
            return
        document_key = id(document)
        self._document_objects[document_key] = document
        self._ensure_document_identity(document)
        objects = getattr(document, "Objects", ())
        if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes)):
            for obj in objects:
                self._track_object(obj, capture_immediately=True)

    def _forget_document(self, document: Any) -> None:
        self._safe_flush_document(document)
        self.persist_pending_deletions()
        document_key = id(document)
        for runtime_key in list(
            self._state_keys_by_document.pop(document_key, set())
        ):
            self._states_by_runtime.pop(runtime_key, None)
        document_id = self._document_ids.pop(document_key, None)
        self._document_objects.pop(document_key, None)
        self._document_instance_ids.pop(document_key, None)
        if document_id is not None:
            self._pending_deletions = {
                key: item
                for key, item in self._pending_deletions.items()
                if item.document_id != document_id
            }
            self._deletion_persistence_errors = {
                key: value
                for key, value in self._deletion_persistence_errors.items()
                if key[0] != document_id
            }
        if not self._has_dirty_states():
            self._cancel_scheduler()

    def _track_object(
        self,
        obj: Any,
        *,
        capture_immediately: bool,
    ) -> TrackedSketchState | None:
        if self._shutdown or not _is_supported_sketch(obj):
            return None
        runtime_key = id(obj)
        existing = self._states_by_runtime.get(runtime_key)
        if existing is not None:
            return existing
        document = getattr(obj, "Document", None)
        if document is None:
            return None
        document_key = id(document)
        self._document_objects[document_key] = document
        document_id = self._ensure_document_identity(document)
        state = TrackedSketchState(
            document_runtime_key=document_key,
            document_id=document_id,
            object_runtime_key=runtime_key,
            object_id=self.vault.new_object_id(),
            object_name=str(obj.Name),
            object_label=str(getattr(obj, "Label", obj.Name)),
            dirty=True,
        )
        self._states_by_runtime[runtime_key] = state
        self._state_keys_by_document.setdefault(document_key, set()).add(runtime_key)
        if capture_immediately:
            self._capture_state(state, obj)
        else:
            self._restart_scheduler()
        return state

    def _state_for_object(
        self,
        obj: Any,
        *,
        create: bool,
    ) -> TrackedSketchState | None:
        state = self._states_by_runtime.get(id(obj))
        if state is not None or not create:
            return state
        return self._track_object(obj, capture_immediately=False)

    def _ensure_document_identity(self, document: Any) -> str:
        document_key = id(document)
        instance_id = self._document_instance_ids.setdefault(
            document_key,
            str(uuid4()),
        )
        locator = RecoveryDocumentLocator(
            internal_name=str(document.Name),
            label=str(getattr(document, "Label", document.Name)),
            file_path=_document_file_path(document),
            session_id=self._session_id,
            instance_id=instance_id,
        )
        document_id = self.vault.resolve_document_id(locator)
        existing = self._document_ids.get(document_key)
        if existing is not None and existing != document_id:
            raise RuntimeError("a live document changed recovery identity")
        self._document_ids[document_key] = document_id
        return document_id

    def _capture_state(self, state: TrackedSketchState, sketch: Any) -> bool:
        snapshot_id = self.vault.new_snapshot_id()
        captured_at = self._timestamp_factory()
        try:
            snapshot = self._serializer(
                sketch,
                document_id=state.document_id,
                object_id=state.object_id,
                snapshot_id=snapshot_id,
                captured_at=captured_at,
            )
            fingerprint = _snapshot_fingerprint(snapshot)
            if fingerprint == state.fingerprint:
                state.dirty = False
                state.last_error = None
                return False
            status = _active_snapshot_status(snapshot)
            if state.latest_entry is None:
                entry = self.vault.new_index_entry(
                    document_id=state.document_id,
                    object_id=state.object_id,
                    snapshot_id=snapshot_id,
                    object_name=state.object_name,
                    object_label=str(sketch.Label),
                    type_id=SKETCH_TYPE_ID,
                    status=status,
                    captured_at=captured_at,
                )
            else:
                entry = state.latest_entry.with_snapshot(
                    snapshot_id,
                    captured_at=captured_at,
                    status=status,
                    object_label=str(sketch.Label),
                )
            self.vault.store_snapshot(entry, snapshot)
        except Exception as exc:  # noqa: BLE001 - retain prior valid state.
            state.last_error = type(exc).__name__
            return False

        state.latest_entry = entry
        state.latest_snapshot = snapshot
        state.fingerprint = fingerprint
        state.object_label = str(sketch.Label)
        state.dirty = False
        state.last_error = None
        return True

    def _restart_scheduler(self) -> None:
        if self._scheduler is None:
            return
        restart = getattr(self._scheduler, "restart", None)
        if callable(restart):
            restart(self.flush_pending, self._debounce_milliseconds)

    def _cancel_scheduler(self) -> None:
        if self._scheduler is None:
            return
        cancel = getattr(self._scheduler, "cancel", None)
        if callable(cancel):
            cancel()

    def _has_dirty_states(self) -> bool:
        return any(
            state.dirty and not state.deleted
            for state in self._states_by_runtime.values()
        )

    def _remove_live_state(
        self,
        runtime_key: int,
        state: TrackedSketchState,
    ) -> None:
        self._states_by_runtime.pop(runtime_key, None)
        keys = self._state_keys_by_document.get(state.document_runtime_key)
        if keys is not None:
            keys.discard(runtime_key)
            if not keys:
                self._state_keys_by_document.pop(state.document_runtime_key, None)


def ensure_recovery_observer_registered(
    app_module: Any,
    *,
    vault: RecoveryVault | None = None,
    scheduler: Any | None = None,
) -> SketchRecoveryObserver | None:
    """Register one process-wide observer, even across workbench reloads."""

    global _REGISTERED_APP, _REGISTERED_OBSERVER
    add_observer = getattr(app_module, "addDocumentObserver", None)
    if not callable(add_observer):
        return None
    app_observer = getattr(app_module, _APP_OBSERVER_ATTRIBUTE, None)
    if app_observer is not None and bool(
        getattr(app_observer, "is_registered", False)
    ):
        _REGISTERED_APP = app_module
        _REGISTERED_OBSERVER = app_observer
        return app_observer
    if (
        _REGISTERED_OBSERVER is not None
        and _REGISTERED_APP is app_module
        and _REGISTERED_OBSERVER.is_registered
    ):
        return _REGISTERED_OBSERVER
    if _REGISTERED_OBSERVER is not None:
        shutdown_recovery_observer()

    active_vault = vault or RecoveryVault.from_freecad(app_module)
    active_scheduler = scheduler
    if active_scheduler is None:
        active_scheduler = _load_qt_scheduler()
    observer = SketchRecoveryObserver(
        app_module,
        active_vault,
        scheduler=active_scheduler,
    )
    try:
        add_observer(observer)
        observer._registered = True
        observer.start()
    except Exception:
        remove_observer = getattr(app_module, "removeDocumentObserver", None)
        if callable(remove_observer) and observer.is_registered:
            try:
                remove_observer(observer)
            except Exception:  # noqa: BLE001 - preserve the registration failure.
                pass
        observer._registered = False
        observer.shutdown()
        raise
    _REGISTERED_APP = app_module
    _REGISTERED_OBSERVER = observer
    try:
        setattr(app_module, _APP_OBSERVER_ATTRIBUTE, observer)
    except Exception:  # noqa: BLE001 - module anchoring is an extra reload guard.
        pass
    return observer


def shutdown_recovery_observer() -> bool:
    """Remove and release the process-wide observer exactly once."""

    global _REGISTERED_APP, _REGISTERED_OBSERVER
    observer = _REGISTERED_OBSERVER
    app_module = _REGISTERED_APP
    if observer is None:
        return False
    remove_observer = getattr(app_module, "removeDocumentObserver", None)
    if callable(remove_observer) and observer.is_registered:
        try:
            remove_observer(observer)
        except Exception:  # noqa: BLE001 - FreeCAD may already be shutting down.
            pass
    observer._registered = False
    observer.shutdown()
    try:
        if getattr(app_module, _APP_OBSERVER_ATTRIBUTE, None) is observer:
            delattr(app_module, _APP_OBSERVER_ATTRIBUTE)
    except Exception:  # noqa: BLE001 - FreeCAD may already be shutting down.
        pass
    _REGISTERED_OBSERVER = None
    _REGISTERED_APP = None
    return True


def registered_recovery_observer(app_module: Any) -> SketchRecoveryObserver | None:
    """Return the active process-wide observer without registering a new one."""

    observer = getattr(app_module, _APP_OBSERVER_ATTRIBUTE, None)
    if observer is None and _REGISTERED_APP is app_module:
        observer = _REGISTERED_OBSERVER
    if observer is None or not bool(getattr(observer, "is_registered", False)):
        return None
    return observer


def _open_documents(app_module: Any) -> list[Any]:
    list_documents = getattr(app_module, "listDocuments", None)
    if callable(list_documents):
        documents = list_documents()
        if isinstance(documents, Mapping):
            return list(documents.values())
    active = getattr(app_module, "ActiveDocument", None)
    return [active] if active is not None else []


def _document_file_path(document: Any) -> str | None:
    value = getattr(document, "FileName", None)
    return value if isinstance(value, str) and value.strip() else None


def _is_supported_sketch(obj: Any) -> bool:
    try:
        return getattr(obj, "TypeId", None) == SKETCH_TYPE_ID
    except Exception:  # noqa: BLE001 - deleted wrappers can reject property access.
        return False


def _find_object_for_state(
    document: Any,
    state: TrackedSketchState,
    runtime_key: int,
) -> Any | None:
    if document is None:
        return None
    objects = getattr(document, "Objects", ())
    if not isinstance(objects, Sequence) or isinstance(objects, (str, bytes)):
        return None
    for obj in objects:
        if id(obj) == runtime_key:
            return obj
    for obj in objects:
        if getattr(obj, "Name", None) == state.object_name:
            return obj
    return None


def _snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    meaningful = {
        "object": snapshot.get("object"),
        "recovery": snapshot.get("recovery"),
    }
    serialized = json.dumps(
        meaningful,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _active_snapshot_status(snapshot: Mapping[str, Any]) -> str:
    recovery = snapshot.get("recovery")
    if not isinstance(recovery, Mapping):
        return "unsupported"
    if recovery.get("restore_as_copy_eligibility") == "eligible":
        return "active"
    if recovery.get("serialization_status") == "partial":
        return "partial"
    return "unsupported"


def _load_qt_scheduler() -> QtDebounceScheduler | None:
    qt_core = None
    try:  # pragma: no cover - depends on the FreeCAD Python runtime.
        from PySide6 import QtCore as qt_core  # type: ignore
    except ImportError:  # pragma: no cover - older FreeCAD releases.
        try:
            from PySide2 import QtCore as qt_core  # type: ignore
        except ImportError:
            try:  # pragma: no cover - legacy FreeCAD releases.
                from PySide import QtCore as qt_core  # type: ignore
            except ImportError:
                return None
    try:
        return QtDebounceScheduler(qt_core)
    except Exception:  # noqa: BLE001 - recompute flushing remains available.
        return None


_REGISTERED_APP: Any | None = None
_REGISTERED_OBSERVER: SketchRecoveryObserver | None = None

atexit.register(shutdown_recovery_observer)


__all__ = [
    "DEFAULT_DEBOUNCE_MILLISECONDS",
    "PendingSketchDeletion",
    "QtDebounceScheduler",
    "SketchRecoveryObserver",
    "TrackedSketchState",
    "ensure_recovery_observer_registered",
    "registered_recovery_observer",
    "shutdown_recovery_observer",
]
