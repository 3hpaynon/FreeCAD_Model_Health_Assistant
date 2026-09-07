import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from model_health_assistant import recovery_vault
from model_health_assistant.recovery_vault import (
    RECOVERY_SCHEMA_VERSION,
    RecoveryDocumentLocator,
    RecoveryVault,
    RecoveryVaultCorruptionError,
    RecoveryVaultStorageError,
    recovery_root_for_app,
)


_NOW = datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)


def _vault(tmp_path: Path) -> RecoveryVault:
    return RecoveryVault(
        tmp_path / "User Data With Spaces" / "ModelHealthAssistant" / "recovery",
        clock=lambda: _NOW,
    )


def _saved_locator(path: Path, *, label: str = "Rotor") -> RecoveryDocumentLocator:
    return RecoveryDocumentLocator(
        internal_name="RecoveryDocument",
        label=label,
        file_path=path,
        session_id="freecad-session-1",
        instance_id="document-instance-1",
    )


def _snapshot_payload(
    document_id: str,
    object_id: str,
    snapshot_id: str,
    *,
    label: str = "Mounting Sketch ⅛",
) -> dict[str, object]:
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "document_id": document_id,
        "object_id": object_id,
        "snapshot_id": snapshot_id,
        "object": {
            "name": "Sketch003",
            "label": label,
            "type_id": "Sketcher::SketchObject",
        },
    }


def _store_one(
    vault: RecoveryVault,
    document_id: str,
    *,
    label: str = "Mounting Sketch ⅛",
):
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name="Sketch003",
        object_label=label,
        type_id="Sketcher::SketchObject",
    )
    path = vault.store_snapshot(
        entry,
        _snapshot_payload(document_id, object_id, snapshot_id, label=label),
    )
    return entry, path


def test_recovery_root_uses_freecad_user_directory_without_creating_it(
    tmp_path: Path,
) -> None:
    freecad_user_directory = tmp_path / "FreeCAD User 数据"
    app = SimpleNamespace(getUserAppDataDir=lambda: str(freecad_user_directory))

    root = recovery_root_for_app(app)

    assert root == (
        freecad_user_directory / "ModelHealthAssistant" / "recovery"
    )
    assert not root.exists()


def test_initialize_creates_only_versioned_vault_registry(tmp_path: Path) -> None:
    vault = _vault(tmp_path)

    vault.initialize()

    assert vault.root.is_dir()
    registry = json.loads(
        vault.document_registry_path.read_text(encoding="utf-8")
    )
    assert registry == {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "documents": [],
    }


def test_unusable_storage_directory_reports_controlled_error(tmp_path: Path) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("blocked", encoding="utf-8")
    vault = RecoveryVault(blocking_file / "recovery")

    with pytest.raises(RecoveryVaultStorageError, match="is not writable"):
        vault.initialize()


def test_saved_document_identity_survives_label_and_internal_name_changes(
    tmp_path: Path,
) -> None:
    model = tmp_path / "Models With Spaces" / "rotor.FCStd"
    model.parent.mkdir()
    model.write_bytes(b"FreeCAD test placeholder")
    vault = _vault(tmp_path)

    first_id = vault.resolve_document_id(_saved_locator(model))
    renamed_locator = RecoveryDocumentLocator(
        internal_name="RenamedDocument",
        label="Rotor Mount – renamed",
        file_path=model,
        session_id="freecad-session-1",
        instance_id="document-instance-1",
    )
    second_id = vault.resolve_document_id(renamed_locator)

    assert second_id == first_id
    UUID(first_id)


def test_saved_document_identity_survives_filesystem_rename(tmp_path: Path) -> None:
    original = tmp_path / "original.FCStd"
    renamed = tmp_path / "renamed model.FCStd"
    original.write_bytes(b"FreeCAD test placeholder")
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(_saved_locator(original))

    original.rename(renamed)
    resolved_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name="RecoveryDocument",
            label="Rotor",
            file_path=renamed,
        )
    )

    assert resolved_id == document_id


def test_live_session_alias_carries_identity_across_save_as(tmp_path: Path) -> None:
    original = tmp_path / "original.FCStd"
    saved_as = tmp_path / "new folder" / "saved as.FCStd"
    original.write_bytes(b"first file")
    saved_as.parent.mkdir()
    saved_as.write_bytes(b"second file with a different inode")
    vault = _vault(tmp_path)
    first_id = vault.resolve_document_id(_saved_locator(original))

    second_id = vault.resolve_document_id(_saved_locator(saved_as))

    assert second_id == first_id


def test_unsaved_duplicate_document_names_use_distinct_runtime_instances(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    first = RecoveryDocumentLocator(
        internal_name="Untitled",
        label="Untitled",
        session_id="session-a",
        instance_id="instance-a",
    )
    second = RecoveryDocumentLocator(
        internal_name="Untitled",
        label="Untitled",
        session_id="session-a",
        instance_id="instance-b",
    )

    first_id = vault.resolve_document_id(first)
    repeated_first_id = vault.resolve_document_id(first)
    second_id = vault.resolve_document_id(second)

    assert repeated_first_id == first_id
    assert second_id != first_id


def test_blank_saved_path_is_treated_as_unsaved() -> None:
    with pytest.raises(ValueError, match="unsaved documents"):
        RecoveryDocumentLocator("Untitled", "Untitled", file_path="   ")


def test_identical_filenames_in_different_directories_do_not_collide(
    tmp_path: Path,
) -> None:
    first = tmp_path / "Project A" / "model.FCStd"
    second = tmp_path / "Project B" / "model.FCStd"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    vault = _vault(tmp_path)

    first_id = vault.resolve_document_id(
        RecoveryDocumentLocator("DocA", "Same label", first)
    )
    second_id = vault.resolve_document_id(
        RecoveryDocumentLocator("DocB", "Same label", second)
    )

    assert first_id != second_id


def test_document_object_and_snapshot_ids_are_distinct_uuids(tmp_path: Path) -> None:
    vault = _vault(tmp_path)

    values = {
        vault.new_document_id(),
        vault.new_object_id(),
        vault.new_snapshot_id(),
    }

    assert len(values) == 3
    assert all(str(UUID(value)) == value for value in values)


def test_store_snapshot_writes_utf8_independent_snapshot_and_index(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name="UnicodeDoc",
            label="装配文档",
            session_id="会话",
            instance_id="实例",
        )
    )

    entry, snapshot_path = _store_one(vault, document_id)

    assert snapshot_path.parent.name == "snapshots"
    assert "Mounting Sketch ⅛" in snapshot_path.read_text(encoding="utf-8")
    assert vault.load_snapshot(document_id, entry.latest_snapshot_id)["object"] == {
        "label": "Mounting Sketch ⅛",
        "name": "Sketch003",
        "type_id": "Sketcher::SketchObject",
    }
    index = vault.load_index(document_id)
    assert index["schema_version"] == "1.0"
    assert index["document_id"] == document_id
    assert index["entries"] == [entry.to_dict()]


def test_lifecycle_update_cannot_replace_snapshot_identity_or_history(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            internal_name="LifecycleDocument",
            label="Lifecycle document",
            session_id="lifecycle-session",
            instance_id="lifecycle-instance",
        )
    )
    entry, _snapshot_path = _store_one(vault, document_id)
    altered_identity = replace(
        entry,
        object_label="Unexpected replacement label",
    )

    with pytest.raises(RecoveryVaultStorageError, match="snapshot identity"):
        vault.update_index_entry(altered_identity)

    assert vault.load_index(document_id)["entries"] == [entry.to_dict()]


def test_duplicate_labels_are_separate_recovery_objects(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="doc",
        )
    )
    first, _ = _store_one(vault, document_id, label="Duplicate")
    second, _ = _store_one(vault, document_id, label="Duplicate")

    entries = vault.load_index(document_id)["entries"]

    assert {entry["object_id"] for entry in entries} == {
        first.object_id,
        second.object_id,
    }


def test_multiple_documents_keep_separate_indexes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    first_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "First",
            "First",
            session_id="session",
            instance_id="first",
        )
    )
    second_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Second",
            "Second",
            session_id="session",
            instance_id="second",
        )
    )

    first_entry, _ = _store_one(vault, first_id)
    second_entry, _ = _store_one(vault, second_id)

    assert vault.load_index(first_id)["entries"] == [first_entry.to_dict()]
    assert vault.load_index(second_id)["entries"] == [second_entry.to_dict()]


def test_failed_index_replace_preserves_last_valid_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    first_entry, _ = _store_one(vault, document_id)
    second_snapshot_id = vault.new_snapshot_id()
    second_entry = first_entry.with_snapshot(
        second_snapshot_id,
        captured_at="2026-08-24T13:00:00+00:00",
    )
    real_replace = recovery_vault.os.replace

    def fail_final_index_replace(source, destination) -> None:
        if Path(destination) == vault.index_path(document_id):
            raise OSError("simulated interruption")
        real_replace(source, destination)

    monkeypatch.setattr(recovery_vault.os, "replace", fail_final_index_replace)

    with pytest.raises(RecoveryVaultStorageError):
        vault.store_snapshot(
            second_entry,
            _snapshot_payload(
                document_id,
                first_entry.object_id,
                second_snapshot_id,
            ),
        )

    assert vault.load_index(document_id)["entries"] == [first_entry.to_dict()]


def test_malformed_index_falls_back_to_last_valid_backup(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    first_entry, _ = _store_one(vault, document_id)
    second_snapshot_id = vault.new_snapshot_id()
    second_entry = first_entry.with_snapshot(
        second_snapshot_id,
        captured_at="2026-08-24T13:00:00+00:00",
    )
    vault.store_snapshot(
        second_entry,
        _snapshot_payload(
            document_id,
            first_entry.object_id,
            second_snapshot_id,
        ),
    )
    vault.index_path(document_id).write_text("{broken", encoding="utf-8")

    recovered = vault.load_index(document_id)

    assert recovered["entries"] == [first_entry.to_dict()]
    assert vault.load_snapshot(document_id, second_snapshot_id)["snapshot_id"] == (
        second_snapshot_id
    )


def test_semantically_invalid_index_falls_back_to_valid_backup(
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    first_entry, _ = _store_one(vault, document_id)
    second_snapshot_id = vault.new_snapshot_id()
    second_entry = first_entry.with_snapshot(
        second_snapshot_id,
        captured_at="2026-08-24T13:00:00+00:00",
    )
    vault.store_snapshot(
        second_entry,
        _snapshot_payload(
            document_id,
            first_entry.object_id,
            second_snapshot_id,
        ),
    )
    vault.index_path(document_id).write_text(
        json.dumps(
            {
                "schema_version": "99.0",
                "document_id": document_id,
                "entries": [],
            }
        ),
        encoding="utf-8",
    )

    recovered = vault.load_index(document_id)

    assert recovered["entries"] == [first_entry.to_dict()]


def test_snapshot_ids_are_immutable(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    entry, _ = _store_one(vault, document_id)

    with pytest.raises(RecoveryVaultStorageError):
        vault.store_snapshot(
            entry,
            _snapshot_payload(
                document_id,
                entry.object_id,
                entry.latest_snapshot_id,
            ),
        )


def test_snapshot_schema_and_identity_must_match_index(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name="Sketch",
        object_label="Sketch",
        type_id="Sketcher::SketchObject",
    )
    payload = _snapshot_payload(document_id, object_id, snapshot_id)
    payload["schema_version"] = "99.0"

    with pytest.raises(RecoveryVaultCorruptionError):
        vault.store_snapshot(entry, payload)


def test_missing_index_reports_controlled_corruption_error(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    vault.index_path(document_id).unlink()

    with pytest.raises(
        RecoveryVaultCorruptionError,
        match="could not be read safely",
    ):
        vault.load_index(document_id)


def test_read_only_storage_failure_preserves_valid_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    vault = _vault(tmp_path)
    document_id = vault.resolve_document_id(
        RecoveryDocumentLocator(
            "Doc",
            "Doc",
            session_id="session",
            instance_id="instance",
        )
    )
    original_index = vault.load_index(document_id)
    object_id = vault.new_object_id()
    snapshot_id = vault.new_snapshot_id()
    entry = vault.new_index_entry(
        document_id=document_id,
        object_id=object_id,
        snapshot_id=snapshot_id,
        object_name="Sketch003",
        object_label="Read-only test",
        type_id="Sketcher::SketchObject",
    )

    def deny_temporary_file(*_args, **_kwargs):
        raise PermissionError("simulated read-only recovery directory")

    monkeypatch.setattr(recovery_vault.tempfile, "mkstemp", deny_temporary_file)

    with pytest.raises(RecoveryVaultStorageError, match="could not be stored"):
        vault.store_snapshot(
            entry,
            _snapshot_payload(document_id, object_id, snapshot_id),
        )

    assert vault.load_index(document_id) == original_index
    assert not vault.snapshot_path(document_id, snapshot_id).exists()
