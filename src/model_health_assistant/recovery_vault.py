"""Versioned, atomic persistence for selective-recovery snapshots.

The vault is deliberately independent of FreeCAD and Qt.  FreeCAD-facing code
supplies document locators and JSON-compatible snapshot payloads; this module
owns UUID identities, external document aliases, index records, and safe files.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

RECOVERY_SCHEMA_VERSION = "1.0"
RECOVERY_DIRECTORY_PARTS = ("ModelHealthAssistant", "recovery")
RECOVERY_DOCUMENT_REGISTRY_FILENAME = "documents.json"
RECOVERY_INDEX_FILENAME = "index.json"
RECOVERY_SNAPSHOT_DIRECTORY_NAME = "snapshots"
RECOVERY_RECORD_STATUSES = frozenset(
    {"active", "deleted", "restored", "unsupported", "partial"}
)

Clock = Callable[[], datetime]
IdentifierFactory = Callable[[], object]


class RecoveryVaultError(RuntimeError):
    """Base class for controlled recovery-vault failures."""


class RecoveryVaultCorruptionError(RecoveryVaultError):
    """Raised when versioned recovery data cannot be read safely."""


class RecoveryVaultStorageError(RecoveryVaultError):
    """Raised when a recovery record cannot be persisted atomically."""


class RecoveryIdentityConflictError(RecoveryVaultError):
    """Raised when external aliases point to incompatible documents."""


@dataclass(frozen=True, slots=True)
class RecoveryDocumentLocator:
    """External facts used to resolve one live FreeCAD document identity.

    Saved documents use a canonical full path and, where the platform exposes
    it, a filesystem device/inode identity.  Unsaved documents require a
    caller-owned session and instance identifier so duplicate names do not
    collide.  A later Save As can retain identity because its live session key
    remains stable while the new path alias is added.
    """

    internal_name: str
    label: str
    file_path: str | os.PathLike[str] | None = None
    session_id: str | None = None
    instance_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.internal_name, str) or not self.internal_name.strip():
            raise ValueError("document internal_name must be non-empty text")
        if not isinstance(self.label, str):
            raise TypeError("document label must be text")
        if (self.session_id is None) != (self.instance_id is None):
            raise ValueError(
                "session_id and instance_id must be supplied together"
            )
        for name, value in (
            ("session_id", self.session_id),
            ("instance_id", self.instance_id),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip() or "\x00" in value
            ):
                raise ValueError(f"{name} must be non-empty safe text")
        has_saved_path = self.file_path is not None and bool(
            os.fspath(self.file_path).strip()
        )
        if not has_saved_path and self.session_id is None:
            raise ValueError(
                "unsaved documents require session_id and instance_id"
            )


@dataclass(frozen=True, slots=True)
class RecoveryIndexEntry:
    """Latest recovery state and snapshot history for one object incarnation."""

    document_id: str
    object_id: str
    object_name: str
    object_label: str
    type_id: str
    captured_at: str
    status: str
    latest_snapshot_id: str
    snapshot_ids: tuple[str, ...]
    deleted_at: str | None = None
    schema_version: str = RECOVERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_uuid(self.document_id, "document_id")
        _require_uuid(self.object_id, "object_id")
        _require_uuid(self.latest_snapshot_id, "latest_snapshot_id")
        if not self.snapshot_ids:
            raise ValueError("snapshot_ids must not be empty")
        for snapshot_id in self.snapshot_ids:
            _require_uuid(snapshot_id, "snapshot_id")
        if self.latest_snapshot_id != self.snapshot_ids[-1]:
            raise ValueError("latest_snapshot_id must be the final snapshot ID")
        for name, value in (
            ("object_name", self.object_name),
            ("type_id", self.type_id),
            ("captured_at", self.captured_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if not isinstance(self.object_label, str):
            raise TypeError("object_label must be text")
        if self.status not in RECOVERY_RECORD_STATUSES:
            raise ValueError("unsupported recovery status")
        if self.deleted_at is not None and (
            not isinstance(self.deleted_at, str) or not self.deleted_at.strip()
        ):
            raise ValueError("deleted_at must be non-empty text or None")

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["snapshot_ids"] = list(self.snapshot_ids)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> RecoveryIndexEntry:
        try:
            snapshot_ids = payload["snapshot_ids"]
            if not isinstance(snapshot_ids, list):
                raise TypeError
            return cls(
                schema_version=str(payload["schema_version"]),
                document_id=str(payload["document_id"]),
                object_id=str(payload["object_id"]),
                object_name=str(payload["object_name"]),
                object_label=str(payload["object_label"]),
                type_id=str(payload["type_id"]),
                captured_at=str(payload["captured_at"]),
                status=str(payload["status"]),
                latest_snapshot_id=str(payload["latest_snapshot_id"]),
                snapshot_ids=tuple(str(value) for value in snapshot_ids),
                deleted_at=(
                    None
                    if payload.get("deleted_at") is None
                    else str(payload["deleted_at"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RecoveryVaultCorruptionError(
                "The recovery index contains an invalid entry."
            ) from exc

    def with_snapshot(
        self,
        snapshot_id: str,
        *,
        captured_at: str,
        status: str | None = None,
        deleted_at: str | None = None,
        object_label: str | None = None,
    ) -> RecoveryIndexEntry:
        """Return an updated immutable entry for a new snapshot."""

        _require_uuid(snapshot_id, "snapshot_id")
        return replace(
            self,
            object_label=(
                self.object_label if object_label is None else object_label
            ),
            captured_at=captured_at,
            status=self.status if status is None else status,
            deleted_at=deleted_at,
            latest_snapshot_id=snapshot_id,
            snapshot_ids=(*self.snapshot_ids, snapshot_id),
        )


def recovery_root_for_app(app_module: Any) -> Path:
    """Return the cross-platform vault path reported by FreeCAD itself."""

    getter = getattr(app_module, "getUserAppDataDir", None)
    if not callable(getter):
        raise TypeError("app_module must provide getUserAppDataDir")
    try:
        user_directory = getter()
    except Exception as exc:
        raise RecoveryVaultStorageError(
            "FreeCAD's user-data directory could not be read."
        ) from exc
    if not isinstance(user_directory, (str, os.PathLike)) or not os.fspath(
        user_directory
    ).strip():
        raise RecoveryVaultStorageError(
            "FreeCAD returned an invalid user-data directory."
        )
    return Path(user_directory).expanduser().joinpath(*RECOVERY_DIRECTORY_PARTS)


def new_recovery_identifier() -> str:
    """Return one collision-resistant canonical recovery UUID."""

    return str(uuid4())


def utc_timestamp() -> str:
    """Return an explicit UTC timestamp suitable for persisted records."""

    return datetime.now(timezone.utc).isoformat()


class RecoveryVault:
    """Persist document identities, indexes, and immutable snapshot files."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: Clock | None = None,
        identifier_factory: IdentifierFactory | None = None,
    ) -> None:
        self.root = Path(root).expanduser()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._identifier_factory = identifier_factory or uuid4

    @classmethod
    def from_freecad(cls, app_module: Any) -> RecoveryVault:
        return cls(recovery_root_for_app(app_module))

    @property
    def document_registry_path(self) -> Path:
        return self.root / RECOVERY_DOCUMENT_REGISTRY_FILENAME

    def initialize(self) -> None:
        """Create the vault and its empty versioned document registry."""

        self.root.mkdir(parents=True, exist_ok=True)
        if not self.document_registry_path.exists():
            _atomic_write_json(
                self.document_registry_path,
                _empty_document_registry(),
            )

    def new_document_id(self) -> str:
        return self._new_identifier()

    def new_object_id(self) -> str:
        return self._new_identifier()

    def new_snapshot_id(self) -> str:
        return self._new_identifier()

    def resolve_document_id(self, locator: RecoveryDocumentLocator) -> str:
        """Resolve or create a UUID using external path/session aliases.

        File identity is preferred over a path.  A stable live-session alias
        carries identity across Save As.  Unsaved identities are necessarily
        session-scoped because an unsaved document cannot be reopened later.
        """

        if not isinstance(locator, RecoveryDocumentLocator):
            raise TypeError("locator must be a RecoveryDocumentLocator")
        self.initialize()
        registry = self._load_document_registry()
        previous_registry = deepcopy(registry)
        path_alias = _canonical_path_alias(locator.file_path)
        file_identity = _filesystem_identity(locator.file_path)
        session_key = _session_key(locator.session_id, locator.instance_id)

        matched = _matching_document_records(
            registry["documents"],
            path_alias=path_alias,
            file_identity=file_identity,
            session_key=session_key,
        )
        if len(matched) > 1:
            raise RecoveryIdentityConflictError(
                "Recovery document aliases resolve to multiple document IDs."
            )

        now = self._timestamp()
        if matched:
            record = matched[0]
            document_id = str(record["document_id"])
            updated = _updated_document_record(
                record,
                locator,
                path_alias=path_alias,
                file_identity=file_identity,
                session_key=session_key,
                updated_at=now,
            )
            registry["documents"] = [
                updated if item is record else item
                for item in registry["documents"]
            ]
        else:
            document_id = self.new_document_id()
            registry["documents"].append(
                {
                    "schema_version": RECOVERY_SCHEMA_VERSION,
                    "document_id": document_id,
                    "created_at": now,
                    "updated_at": now,
                    "internal_names": [locator.internal_name],
                    "labels": [locator.label],
                    "path_aliases": [path_alias] if path_alias else [],
                    "file_identities": (
                        [file_identity] if file_identity else []
                    ),
                    "session_keys": [session_key] if session_key else [],
                }
            )

        _atomic_write_json(
            self.document_registry_path,
            registry,
            backup_payload=previous_registry,
        )
        self.ensure_document_layout(document_id)
        return document_id

    def ensure_document_layout(self, document_id: str) -> Path:
        """Create one document directory, snapshots directory, and empty index."""

        _require_uuid(document_id, "document_id")
        document_directory = self.document_directory(document_id)
        self.snapshot_directory(document_id).mkdir(parents=True, exist_ok=True)
        index_path = self.index_path(document_id)
        if not index_path.exists():
            _atomic_write_json(index_path, _empty_index(document_id))
        return document_directory

    def document_directory(self, document_id: str) -> Path:
        _require_uuid(document_id, "document_id")
        return self.root / document_id

    def snapshot_directory(self, document_id: str) -> Path:
        return self.document_directory(document_id) / RECOVERY_SNAPSHOT_DIRECTORY_NAME

    def index_path(self, document_id: str) -> Path:
        return self.document_directory(document_id) / RECOVERY_INDEX_FILENAME

    def snapshot_path(self, document_id: str, snapshot_id: str) -> Path:
        _require_uuid(snapshot_id, "snapshot_id")
        return self.snapshot_directory(document_id) / f"{snapshot_id}.json"

    def new_index_entry(
        self,
        *,
        document_id: str,
        object_id: str,
        snapshot_id: str,
        object_name: str,
        object_label: str,
        type_id: str,
        status: str = "active",
        captured_at: str | None = None,
    ) -> RecoveryIndexEntry:
        timestamp = captured_at or self._timestamp()
        return RecoveryIndexEntry(
            document_id=document_id,
            object_id=object_id,
            object_name=object_name,
            object_label=object_label,
            type_id=type_id,
            captured_at=timestamp,
            status=status,
            latest_snapshot_id=snapshot_id,
            snapshot_ids=(snapshot_id,),
        )

    def store_snapshot(
        self,
        entry: RecoveryIndexEntry,
        snapshot_payload: Mapping[str, object],
    ) -> Path:
        """Write an immutable snapshot, then atomically publish its index entry."""

        if not isinstance(entry, RecoveryIndexEntry):
            raise TypeError("entry must be a RecoveryIndexEntry")
        payload = _validated_snapshot_payload(snapshot_payload, entry)
        self.ensure_document_layout(entry.document_id)
        snapshot_path = self.snapshot_path(
            entry.document_id,
            entry.latest_snapshot_id,
        )
        if snapshot_path.exists():
            raise RecoveryVaultStorageError(
                "A snapshot with this immutable ID already exists."
            )
        _atomic_write_json(snapshot_path, payload)

        index = self.load_index(entry.document_id)
        previous_index = deepcopy(index)
        existing_entries = [
            item
            for item in index["entries"]
            if item.get("object_id") != entry.object_id
        ]
        existing_entries.append(entry.to_dict())
        index["entries"] = existing_entries
        _atomic_write_json(
            self.index_path(entry.document_id),
            index,
            backup_payload=previous_index,
        )
        return snapshot_path

    def load_index(self, document_id: str) -> dict[str, Any]:
        """Load a validated index, falling back to its last valid backup."""

        _require_uuid(document_id, "document_id")
        index_path = self.index_path(document_id)
        return _read_validated_json_with_backup(
            index_path,
            lambda payload: _validated_index(payload, document_id),
        )

    def load_snapshot(
        self,
        document_id: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        """Read one independently stored, versioned snapshot."""

        payload = _read_json(self.snapshot_path(document_id, snapshot_id))
        if not isinstance(payload, dict):
            raise RecoveryVaultCorruptionError(
                "The recovery snapshot must be a JSON object."
            )
        _require_schema_version(payload.get("schema_version"))
        if payload.get("document_id") != document_id:
            raise RecoveryVaultCorruptionError(
                "The recovery snapshot document ID is invalid."
            )
        if payload.get("snapshot_id") != snapshot_id:
            raise RecoveryVaultCorruptionError(
                "The recovery snapshot ID is invalid."
            )
        return payload

    def _load_document_registry(self) -> dict[str, Any]:
        return _read_validated_json_with_backup(
            self.document_registry_path,
            _validated_document_registry,
        )

    def _new_identifier(self) -> str:
        try:
            value = str(self._identifier_factory())
            return str(UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise RecoveryVaultStorageError(
                "The recovery UUID factory returned an invalid identifier."
            ) from exc

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise RecoveryVaultStorageError(
                "The recovery clock returned an invalid timestamp."
            )
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


def _validated_index(payload: object, document_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecoveryVaultCorruptionError(
            "The recovery index must be a JSON object."
        )
    _require_schema_version(payload.get("schema_version"))
    if payload.get("document_id") != document_id:
        raise RecoveryVaultCorruptionError(
            "The recovery index document ID does not match its directory."
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise RecoveryVaultCorruptionError(
            "The recovery index entries must be a list."
        )
    validated = [RecoveryIndexEntry.from_mapping(item) for item in entries]
    payload["entries"] = [entry.to_dict() for entry in validated]
    return payload


def _validated_document_registry(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecoveryVaultCorruptionError(
            "The recovery document registry must be a JSON object."
        )
    _require_schema_version(payload.get("schema_version"))
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise RecoveryVaultCorruptionError(
            "The recovery document registry must contain a document list."
        )
    for record in documents:
        _validate_document_record(record)
    return payload


def _empty_document_registry() -> dict[str, object]:
    return {"schema_version": RECOVERY_SCHEMA_VERSION, "documents": []}


def _empty_index(document_id: str) -> dict[str, object]:
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "document_id": document_id,
        "entries": [],
    }


def _canonical_path_alias(
    file_path: str | os.PathLike[str] | None,
) -> str | None:
    if file_path is None or not os.fspath(file_path).strip():
        return None
    try:
        resolved = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecoveryVaultStorageError(
            "The document path could not be normalized."
        ) from exc
    return os.path.normcase(os.path.normpath(str(resolved)))


def _filesystem_identity(
    file_path: str | os.PathLike[str] | None,
) -> str | None:
    if file_path is None or not os.fspath(file_path).strip():
        return None
    try:
        stat_result = Path(file_path).expanduser().stat()
    except OSError:
        return None
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    device = int(getattr(stat_result, "st_dev", 0) or 0)
    return f"{device}:{inode}" if inode > 0 else None


def _session_key(
    session_id: str | None,
    instance_id: str | None,
) -> str | None:
    if session_id is None or instance_id is None:
        return None
    return f"{session_id}:{instance_id}"


def _matching_document_records(
    records: list[dict[str, Any]],
    *,
    path_alias: str | None,
    file_identity: str | None,
    session_key: str | None,
) -> list[dict[str, Any]]:
    strong_matches = [
        record
        for record in records
        if (
            file_identity is not None
            and file_identity in record["file_identities"]
        )
        or (
            session_key is not None
            and session_key in record["session_keys"]
        )
    ]
    if strong_matches:
        return strong_matches
    if path_alias is None:
        return []
    path_matches = [
        record for record in records if path_alias in record["path_aliases"]
    ]
    if file_identity is None:
        return path_matches
    return [
        record
        for record in path_matches
        if not record["file_identities"]
    ]


def _updated_document_record(
    record: dict[str, Any],
    locator: RecoveryDocumentLocator,
    *,
    path_alias: str | None,
    file_identity: str | None,
    session_key: str | None,
    updated_at: str,
) -> dict[str, Any]:
    updated = dict(record)
    updated["updated_at"] = updated_at
    for key, value in (
        ("internal_names", locator.internal_name),
        ("labels", locator.label),
        ("path_aliases", path_alias),
        ("file_identities", file_identity),
        ("session_keys", session_key),
    ):
        values = list(updated[key])
        if value is not None and value not in values:
            values.append(value)
        updated[key] = values
    return updated


def _validate_document_record(record: object) -> None:
    if not isinstance(record, dict):
        raise RecoveryVaultCorruptionError(
            "The recovery document registry contains an invalid record."
        )
    try:
        _require_schema_version(record["schema_version"])
        _require_uuid(str(record["document_id"]), "document_id")
        for key in (
            "internal_names",
            "labels",
            "path_aliases",
            "file_identities",
            "session_keys",
        ):
            if not isinstance(record[key], list) or not all(
                isinstance(value, str) for value in record[key]
            ):
                raise TypeError
        for key in ("created_at", "updated_at"):
            if not isinstance(record[key], str) or not record[key]:
                raise TypeError
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryVaultCorruptionError(
            "The recovery document registry contains an invalid record."
        ) from exc


def _validated_snapshot_payload(
    snapshot_payload: Mapping[str, object],
    entry: RecoveryIndexEntry,
) -> dict[str, object]:
    if not isinstance(snapshot_payload, Mapping):
        raise TypeError("snapshot_payload must be a mapping")
    payload = dict(snapshot_payload)
    _require_schema_version(payload.get("schema_version"))
    expected = {
        "document_id": entry.document_id,
        "object_id": entry.object_id,
        "snapshot_id": entry.latest_snapshot_id,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"snapshot {key} does not match its index entry")
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("snapshot_payload must be JSON-compatible") from exc
    return payload


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, object],
    *,
    backup_payload: Mapping[str, object] | None = None,
) -> None:
    """Write UTF-8 JSON through a closed same-directory temporary file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        serialized = json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise RecoveryVaultStorageError(
            "Recovery data is not JSON-compatible."
        ) from exc

    if backup_payload is not None:
        _atomic_write_json(_backup_path(path), backup_payload)

    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise RecoveryVaultStorageError(
            f"Recovery data could not be stored at '{path}'."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_validated_json_with_backup(
    path: Path,
    validator: Callable[[object], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return validator(_read_json(path))
    except RecoveryVaultCorruptionError as primary_error:
        backup = _backup_path(path)
        if not backup.exists():
            raise primary_error
        return validator(_read_json(backup))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryVaultCorruptionError(
            f"Recovery data at '{path}' could not be read safely."
        ) from exc


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _require_schema_version(value: object) -> None:
    if value != RECOVERY_SCHEMA_VERSION:
        raise RecoveryVaultCorruptionError(
            "The recovery record schema version is unsupported."
        )


def _require_uuid(value: object, name: str) -> None:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    if str(parsed) != str(value):
        raise ValueError(f"{name} must use canonical UUID text")


__all__ = [
    "RECOVERY_DIRECTORY_PARTS",
    "RECOVERY_DOCUMENT_REGISTRY_FILENAME",
    "RECOVERY_INDEX_FILENAME",
    "RECOVERY_RECORD_STATUSES",
    "RECOVERY_SCHEMA_VERSION",
    "RECOVERY_SNAPSHOT_DIRECTORY_NAME",
    "RecoveryDocumentLocator",
    "RecoveryIdentityConflictError",
    "RecoveryIndexEntry",
    "RecoveryVault",
    "RecoveryVaultCorruptionError",
    "RecoveryVaultError",
    "RecoveryVaultStorageError",
    "new_recovery_identifier",
    "recovery_root_for_app",
    "utc_timestamp",
]
