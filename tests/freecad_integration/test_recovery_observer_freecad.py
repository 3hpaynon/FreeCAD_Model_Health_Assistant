"""Native observer lifecycle test; run with FreeCAD-provided Python modules."""

import tempfile
from pathlib import Path

import pytest

from model_health_assistant.recovery_observer import SketchRecoveryObserver
from model_health_assistant.recovery_vault import RecoveryVault

App = pytest.importorskip("FreeCAD")
Part = pytest.importorskip("Part")


def test_native_observer_retains_last_recomputed_state_on_deletion() -> None:
    temporary_directory = tempfile.TemporaryDirectory()
    observer = SketchRecoveryObserver(
        App,
        RecoveryVault(Path(temporary_directory.name) / "vault"),
        session_id="native-observer-test",
    )
    App.addDocumentObserver(observer)
    document = None
    try:
        document = App.newDocument("MHARecoveryObserverTest")
        sketch = document.addObject("Sketcher::SketchObject", "Sketch003")
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
            False,
        )
        sketch.Label = "Last native state – 草图"
        document.recompute()

        document.removeObject(sketch.Name)

        pending = observer.pending_deletions()
        assert len(pending) == 1
        assert pending[0].object_name == "Sketch003"
        assert pending[0].snapshot["object"]["label"] == "Last native state – 草图"
        index = observer.vault.load_index(pending[0].document_id)
        assert index["entries"][0]["latest_snapshot_id"] == pending[0].snapshot_id
    finally:
        App.removeDocumentObserver(observer)
        observer.shutdown()
        if document is not None:
            App.closeDocument(document.Name)
        temporary_directory.cleanup()
