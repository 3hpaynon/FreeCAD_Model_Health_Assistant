from pathlib import Path
from types import SimpleNamespace

from model_health_assistant import recovery_observer
from model_health_assistant.recovery_lifecycle import RecoveryLifecycleService
from model_health_assistant.recovery_observer import (
    SketchRecoveryObserver,
    ensure_recovery_observer_registered,
    registered_recovery_observer,
    shutdown_recovery_observer,
)
from model_health_assistant.recovery_vault import RecoveryVault
from model_health_assistant.sketch_snapshot import serialize_sketch


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class Placement:
    def __init__(self) -> None:
        self.Base = Vector()
        self.Rotation = SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0))


class Sketch:
    TypeId = "Sketcher::SketchObject"

    def __init__(self, name="Sketch003", label="Sketch") -> None:
        self.Name = name
        self.Label = label
        self.Document = None
        self.Geometry = []
        self.Constraints = []
        self.Placement = Placement()
        self.AttachmentOffset = Placement()
        self.Support = None
        self.AttachmentSupport = None
        self.MapMode = "Deactivated"
        self.ExpressionEngine = []
        self.ExternalGeometry = []
        self.ViewObject = SimpleNamespace(Visibility=True)

    def getConstruction(self, _index):
        return False


class UnsupportedObject:
    TypeId = "PartDesign::Feature"

    def __init__(self, name="Pad") -> None:
        self.Name = name
        self.Label = name
        self.Document = None


class Document:
    def __init__(self, name, *, file_name="") -> None:
        self.Name = name
        self.Label = name
        self.FileName = file_name
        self.Objects = []

    def add(self, obj):
        obj.Document = self
        self.Objects.append(obj)
        return obj


class App:
    def __init__(self, user_directory: Path) -> None:
        self._user_directory = user_directory
        self.documents = {}
        self.observers = []
        self.add_calls = 0
        self.remove_calls = 0
        self.ActiveDocument = None

    def getUserAppDataDir(self):
        return str(self._user_directory)

    def listDocuments(self):
        return dict(self.documents)

    def addDocumentObserver(self, observer):
        self.add_calls += 1
        self.observers.append(observer)

    def removeDocumentObserver(self, observer):
        self.remove_calls += 1
        self.observers.remove(observer)


class ManualScheduler:
    def __init__(self) -> None:
        self.callback = None
        self.restart_count = 0
        self.cancel_count = 0
        self.delays = []

    def restart(self, callback, delay_ms):
        self.callback = callback
        self.restart_count += 1
        self.delays.append(delay_ms)

    def cancel(self):
        self.callback = None
        self.cancel_count += 1

    def fire(self):
        callback = self.callback
        self.callback = None
        if callback is not None:
            return callback()
        return 0


def _observer(
    tmp_path: Path,
    app: App,
    *,
    scheduler=None,
    serializer=serialize_sketch,
    lifecycle_service=None,
):
    return SketchRecoveryObserver(
        app,
        RecoveryVault(tmp_path / "vault"),
        scheduler=scheduler,
        serializer=serializer,
        session_id="test-session",
        timestamp_factory=lambda: "2026-08-25T12:00:00+00:00",
        lifecycle_service=lifecycle_service,
    )


def _index(observer: SketchRecoveryObserver, document: Document):
    document_id = observer._document_ids[id(document)]
    return observer.vault.load_index(document_id)


def test_start_tracks_existing_supported_sketches_only(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    document.add(Sketch(label="Existing sketch"))
    document.add(UnsupportedObject())
    app.documents[document.Name] = document
    observer = _observer(tmp_path, app)

    observer.start()

    assert observer.tracked_sketch_count == 1
    assert observer.document_id_for(document) == observer._document_ids[id(document)]
    entries = _index(observer, document)["entries"]
    assert len(entries) == 1
    assert entries[0]["object_label"] == "Existing sketch"
    assert entries[0]["status"] == "active"


def test_created_sketch_gets_an_immediate_baseline(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    app.documents[document.Name] = document
    observer = _observer(tmp_path, app)
    observer.start()
    sketch = document.add(Sketch())

    observer.slotCreatedObject(sketch)

    assert observer.tracked_sketch_count == 1
    assert len(_index(observer, document)["entries"]) == 1


def test_restore_transaction_callbacks_are_suspended_until_final_capture(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    app.documents[document.Name] = document
    observer = _observer(tmp_path, app)
    observer.start()
    restored = document.add(Sketch(name="RecoveredSketch", label="Recovered"))

    with observer.suspend_object_tracking():
        observer.slotCreatedObject(restored)
        observer.slotChangedObject(restored, "Geometry")
        observer.slotRecomputedObject(restored)
        assert observer.capture_restored_sketch(restored) is False

    assert observer.tracked_sketch_count == 0
    assert observer.capture_restored_sketch(restored) is True
    assert observer.tracked_sketch_count == 1
    entries = _index(observer, document)["entries"]
    assert len(entries) == 1
    assert entries[0]["object_name"] == "RecoveredSketch"


def test_repeated_changes_debounce_and_identical_states_are_not_stored(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    scheduler = ManualScheduler()
    observer = _observer(tmp_path, app, scheduler=scheduler)
    observer.start()
    first_entry = _index(observer, document)["entries"][0]
    sketch.Label = "Changed once"

    for _ in range(20):
        observer.slotChangedObject(sketch, "Geometry")

    assert scheduler.restart_count == 20
    assert scheduler.delays == [750] * 20
    assert _index(observer, document)["entries"][0] == first_entry
    assert scheduler.fire() == 1
    changed_entry = _index(observer, document)["entries"][0]
    assert len(changed_entry["snapshot_ids"]) == 2

    for _ in range(10):
        observer.slotChangedObject(sketch, "Geometry")
    assert scheduler.fire() == 0
    assert _index(observer, document)["entries"][0] == changed_entry


def test_document_recompute_flushes_dirty_state_without_timer(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    observer = _observer(tmp_path, app)
    observer.start()
    sketch.Label = "Stable after recompute"
    observer.slotChangedObject(sketch, "Label")

    observer.slotRecomputedDocument(document)

    entry = _index(observer, document)["entries"][0]
    assert len(entry["snapshot_ids"]) == 2
    assert entry["object_label"] == "Stable after recompute"


def test_deletion_uses_cached_state_without_serializing_destroyed_object(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    serializer_calls = []
    destroyed = set()

    def guarded_serializer(obj, **kwargs):
        if id(obj) in destroyed:
            raise AssertionError("deleted object was serialized")
        serializer_calls.append(str(obj.Label))
        return serialize_sketch(obj, **kwargs)

    observer = _observer(tmp_path, app, serializer=guarded_serializer)
    observer.start()
    sketch.Label = "Last stable label"
    observer.slotChangedObject(sketch, "Label")
    observer.slotRecomputedObject(sketch)
    calls_before_deletion = list(serializer_calls)
    document.Objects.remove(sketch)
    destroyed.add(id(sketch))

    observer.slotDeletedObject(sketch)

    assert serializer_calls == calls_before_deletion
    assert observer.tracked_sketch_count == 0
    pending = observer.pending_deletions()
    assert len(pending) == 1
    assert pending[0].object_label == "Last stable label"
    assert pending[0].snapshot["object"]["label"] == "Last stable label"
    index_entry = observer.vault.load_index(pending[0].document_id)["entries"][0]
    assert index_entry["status"] == "deleted"
    assert index_entry["deleted_at"] == "2026-08-25T12:00:00+00:00"
    assert index_entry["recovery_eligibility"] == "available"
    assert observer.deletion_persistence_errors() == {}


def test_document_close_releases_live_and_pending_state(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    scheduler = ManualScheduler()
    observer = _observer(tmp_path, app, scheduler=scheduler)
    observer.start()
    document.Objects.remove(sketch)
    observer.slotDeletedObject(sketch)
    assert observer.pending_deletions()
    document_id = observer.pending_deletions()[0].document_id
    object_id = observer.pending_deletions()[0].object_id

    observer.slotDeletedDocument(document)

    assert observer.tracked_sketch_count == 0
    assert observer.pending_deletions() == ()
    assert scheduler.callback is None
    reopened = RecoveryLifecycleService(
        RecoveryVault(tmp_path / "vault")
    ).list_deleted_records(document_id)
    assert len(reopened) == 1
    assert reopened[0].object_id == object_id


def test_document_close_flushes_dirty_sketch_before_releasing_it(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    scheduler = ManualScheduler()
    observer = _observer(tmp_path, app, scheduler=scheduler)
    observer.start()
    document_id = observer.document_id_for(document)
    sketch.Label = "Final unsaved label"
    observer.slotChangedObject(sketch, "Label")

    observer.slotDeletedDocument(document)

    assert document_id is not None
    entry = observer.vault.load_index(document_id)["entries"][0]
    assert entry["object_label"] == "Final unsaved label"
    assert len(entry["snapshot_ids"]) == 2
    assert observer.tracked_sketch_count == 0
    assert scheduler.callback is None


def test_observer_shutdown_flushes_dirty_sketch_once(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    scheduler = ManualScheduler()
    observer = _observer(tmp_path, app, scheduler=scheduler)
    observer.start()
    document_id = observer.document_id_for(document)
    sketch.Label = "Dirty at application shutdown"
    observer.slotChangedObject(sketch, "Label")

    observer.shutdown()
    observer.shutdown()

    assert document_id is not None
    entry = observer.vault.load_index(document_id)["entries"][0]
    assert entry["object_label"] == "Dirty at application shutdown"
    assert len(entry["snapshot_ids"]) == 2
    assert observer.tracked_sketch_count == 0
    assert scheduler.callback is None


def test_failed_deletion_persistence_is_retained_for_idempotent_retry(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Assembly")
    sketch = document.add(Sketch())
    app.documents[document.Name] = document
    vault = RecoveryVault(tmp_path / "vault")
    durable_service = RecoveryLifecycleService(vault)

    class FailOnceService:
        def __init__(self) -> None:
            self.calls = 0

        def record_deletion(self, candidate):
            self.calls += 1
            if self.calls == 1:
                raise OSError("simulated transient write failure")
            return durable_service.record_deletion(candidate)

    lifecycle_service = FailOnceService()
    observer = SketchRecoveryObserver(
        app,
        vault,
        serializer=serialize_sketch,
        session_id="test-session",
        timestamp_factory=lambda: "2026-08-25T12:00:00+00:00",
        lifecycle_service=lifecycle_service,
    )
    observer.start()
    document.Objects.remove(sketch)

    observer.slotDeletedObject(sketch)

    pending = observer.pending_deletions()
    assert len(pending) == 1
    assert observer.deletion_persistence_errors() == {
        (pending[0].document_id, pending[0].object_id): "OSError"
    }
    assert vault.load_index(pending[0].document_id)["entries"][0]["status"] == "active"

    assert observer.persist_pending_deletions() == 1
    assert observer.deletion_persistence_errors() == {}
    assert vault.load_index(pending[0].document_id)["entries"][0]["status"] == "deleted"


def test_multiple_documents_keep_separate_identity_and_state(tmp_path: Path) -> None:
    app = App(tmp_path / "user")
    first = Document("First")
    second = Document("Second")
    first.add(Sketch(name="Sketch", label="Same label"))
    second.add(Sketch(name="Sketch", label="Same label"))
    app.documents = {first.Name: first, second.Name: second}
    observer = _observer(tmp_path, app)

    observer.start()

    first_id = observer._document_ids[id(first)]
    second_id = observer._document_ids[id(second)]
    assert first_id != second_id
    assert len(observer.vault.load_index(first_id)["entries"]) == 1
    assert len(observer.vault.load_index(second_id)["entries"]) == 1


def test_observer_preserves_unsaved_document_identity_across_save_as(
    tmp_path: Path,
) -> None:
    app = App(tmp_path / "user")
    document = Document("Untitled")
    document.add(Sketch())
    app.documents[document.Name] = document
    observer = _observer(tmp_path, app)
    observer.start()
    original_id = observer.document_id_for(document)
    saved_path = tmp_path / "Models With Spaces" / "Mounting ⅛.FCStd"
    saved_path.parent.mkdir()
    saved_path.write_bytes(b"FreeCAD test placeholder")
    document.FileName = str(saved_path)

    observer.slotChangedDocument(document, "FileName")

    assert original_id is not None
    assert observer.document_id_for(document) == original_id
    assert observer.vault.list_document_ids() == (original_id,)


def test_duplicate_registration_returns_same_observer_and_removes_once(
    tmp_path: Path,
) -> None:
    shutdown_recovery_observer()
    app = App(tmp_path / "user")
    vault = RecoveryVault(tmp_path / "vault")
    scheduler = ManualScheduler()
    try:
        first = ensure_recovery_observer_registered(
            app,
            vault=vault,
            scheduler=scheduler,
        )
        recovery_observer._REGISTERED_APP = None
        recovery_observer._REGISTERED_OBSERVER = None
        second = ensure_recovery_observer_registered(
            app,
            vault=vault,
            scheduler=scheduler,
        )

        assert first is second
        assert first is not None
        assert first.is_registered is True
        assert app.add_calls == 1
        assert len(app.observers) == 1
        assert registered_recovery_observer(app) is first
    finally:
        removed = shutdown_recovery_observer()

    assert removed is True
    assert app.remove_calls == 1
    assert app.observers == []


def test_unavailable_observer_api_fails_closed(tmp_path: Path) -> None:
    shutdown_recovery_observer()
    app = SimpleNamespace(getUserAppDataDir=lambda: str(tmp_path))

    assert ensure_recovery_observer_registered(app) is None
