# Selective Sketch Recovery: Architecture and Validation

This document is the final review and manual validation plan for the first
Model Health Assistant Recovery Vault milestone. It covers only
`Sketcher::SketchObject` recovery as a new copy. It does not describe ordinary
Undo, document rollback, or PartDesign feature recovery.

## Architecture

```text
FreeCAD Sketch
    ↓
SketchRecoveryObserver
    ↓ meaningful change / stable recompute
serialize_sketch()
    ↓ versioned UTF-8 JSON
RecoveryVault
    ↓ object deletion retains last valid snapshot
RecoveryLifecycleService
    ↓ durable Deleted Objects record
Recovery tab
    ↓ explicit assessment and restore choice
RecoveryRestoreService
    ↓ one FreeCAD transaction
Sketch003_Recovered
```

The observer tracks supported sketches only. It captures an immediate baseline,
then debounces repeated edits and fingerprints serialized state to avoid
duplicate writes. Deletion uses the cached last-known state rather than reading
an invalidated FreeCAD object wrapper.

The UI reads durable deleted records through service and presentation layers.
It never reconstructs a sketch directly. At restore time the service reloads
and reassesses the snapshot and validates live attachment support again.

Restoration always creates a uniquely named copy. It never invokes Undo,
replaces the deleted object, removes later work, or overwrites an earlier
recovered copy.

## Runtime Storage and Persisted Schema

FreeCAD supplies the user-data root. The same implementation is used on macOS
and Windows:

```python
Path(App.getUserAppDataDir()) / "ModelHealthAssistant" / "recovery"
```

Conceptual layout:

```text
recovery/
├── documents.json
└── <document-uuid>/
    ├── index.json
    ├── index.json.bak
    └── snapshots/
        └── <snapshot-uuid>.json
```

`documents.json` maps external document facts to durable UUIDs:

```json
{
  "schema_version": "1.0",
  "documents": [
    {
      "schema_version": "1.0",
      "document_id": "<uuid>",
      "created_at": "<ISO-8601 UTC timestamp>",
      "updated_at": "<ISO-8601 UTC timestamp>",
      "internal_names": ["RecoveryTest"],
      "labels": ["Recovery Test"],
      "path_aliases": ["<normalized saved path>"],
      "file_identities": ["<device-and-inode identity where available>"],
      "session_keys": ["<session-id>:<document-instance-id>"]
    }
  ]
}
```

Each document index contains metadata and snapshot history, not the only copy
of reconstructable state:

```json
{
  "schema_version": "1.0",
  "document_id": "<uuid>",
  "entries": [
    {
      "schema_version": "1.0",
      "document_id": "<uuid>",
      "object_id": "<uuid>",
      "object_name": "Sketch003",
      "object_label": "Mounting Sketch ⅛",
      "type_id": "Sketcher::SketchObject",
      "captured_at": "<ISO-8601 UTC timestamp>",
      "status": "deleted",
      "latest_snapshot_id": "<uuid>",
      "snapshot_ids": ["<uuid>"],
      "deleted_at": "<ISO-8601 UTC timestamp>",
      "recovery_eligibility": "available_with_warning",
      "support_status": "recorded_unresolved",
      "unsupported_elements": [],
      "recovery_warnings": [],
      "last_restored_at": null,
      "restored_object_names": []
    }
  ]
}
```

Each immutable snapshot contains structured reconstruction data:

```json
{
  "schema_version": "1.0",
  "record_type": "sketch_snapshot",
  "snapshot_id": "<uuid>",
  "document_id": "<uuid>",
  "object_id": "<uuid>",
  "captured_at": "<ISO-8601 UTC timestamp>",
  "object": {
    "name": "Sketch003",
    "label": "Mounting Sketch ⅛",
    "type_id": "Sketcher::SketchObject",
    "placement": {
      "base": {"x": 0.0, "y": 0.0, "z": 0.0},
      "rotation": {"quaternion": [0.0, 0.0, 0.0, 1.0]}
    },
    "geometry": [],
    "geometry_count": 0,
    "constraints": [],
    "constraint_count": 0,
    "support": [],
    "attachment_support": [],
    "map_mode": "Deactivated",
    "attachment_offset": {
      "base": {"x": 0.0, "y": 0.0, "z": 0.0},
      "rotation": {"quaternion": [0.0, 0.0, 0.0, 1.0]}
    },
    "expressions": [],
    "external_geometry": [],
    "visibility": true
  },
  "recovery": {
    "serialization_status": "complete",
    "restore_as_copy_eligibility": "eligible",
    "unsupported_geometry_types": [],
    "unsupported_constraint_types": [],
    "warnings": []
  }
}
```

Geometry, constraints, placements, and references use explicit structured
fields. Raw Python representations are not used as the recovery format. JSON
reads and writes explicitly use UTF-8, and writes use a closed same-directory
temporary file followed by atomic replacement.

## Files Changed

- `InitGui.py` — registers one process-wide recovery observer during plugin
  initialization and fails closed if recovery cannot start.
- `src/model_health_assistant/ui.py` — appends the Recovery tab without
  reordering Overview, Issues, or AI; loads deleted records and coordinates
  explicit restore confirmation and result messages.
- `tests/test_freecad_entrypoints.py` — verifies observer initialization and
  existing workbench entry-point behavior.
- `tests/test_ui.py` — verifies Recovery-tab integration, repeated results
  dialog opening, and preservation of existing UI behavior.

## New Files

- `src/model_health_assistant/recovery_vault.py` — cross-platform document
  identity, UUIDs, versioned indexes, immutable snapshots, backups, atomic
  UTF-8 storage, and controlled corruption/storage errors.
- `src/model_health_assistant/sketch_snapshot.py` — deterministic structured
  sketch serialization and conservative unsupported-element reporting.
- `src/model_health_assistant/sketch_restore.py` — reconstruction, detached
  filtering, unique names, transactions, rollback, and round-trip comparison.
- `src/model_health_assistant/recovery_observer.py` — lifecycle observation,
  meaningful-state capture, debounce, deletion cache, shutdown flushing, and
  duplicate-registration protection.
- `src/model_health_assistant/recovery_lifecycle.py` — deletion transitions,
  eligibility assessment, durable records, and per-snapshot corruption
  isolation.
- `src/model_health_assistant/recovery_presentation.py` — compact safe list and
  details text for deleted records.
- `src/model_health_assistant/recovery_ui.py` — Qt-neutral Recovery controller
  and the Deleted Objects tab factory.
- `src/model_health_assistant/support_validation.py` — deterministic
  restorable/missing/changed/unverified/not-applicable support classification.
- `src/model_health_assistant/recovery_restore.py` — request-time reassessment,
  safe mode selection, observer isolation, restore history, and controlled
  service outcomes.
- `scripts/test_steps_8_9_freecad.py` — native FreeCAD 1.1 verification for
  stable-support, repeated-copy, and detached generated-face recovery.
- `tests/test_recovery_vault.py` — identity, Unicode/path, atomicity,
  corruption, missing-index, and storage-failure tests.
- `tests/test_sketch_snapshot.py` — pure-Python serializer coverage.
- `tests/test_sketch_restore.py` — reconstruction, naming, transaction,
  rollback, attachment compatibility, empty-sketch, and detached tests.
- `tests/test_recovery_observer.py` — observer lifecycle, performance,
  multi-document, Save As, shutdown, and registration tests.
- `tests/test_recovery_lifecycle.py` — deletion, eligibility, restart, and
  isolated-corruption tests.
- `tests/test_recovery_presentation.py` — user-facing deleted-record text.
- `tests/test_recovery_ui.py` — selection, actions, empty state, details, and
  safe confirmation tests.
- `tests/test_recovery_restore.py` — service assessment, repeated restores,
  history, revalidation, malformed data, and failure-containment tests.
- `tests/test_support_validation.py` — origin/datum, missing, changed,
  generated-face, and external-document support tests.
- `tests/freecad_integration/test_sketch_snapshot_freecad.py` — native FreeCAD
  serialization coverage.
- `tests/freecad_integration/test_sketch_restore_freecad.py` — native FreeCAD
  round-trip, `AttachmentSupport`, and detached reconstruction coverage.
- `tests/freecad_integration/test_recovery_observer_freecad.py` — native
  last-known-state deletion coverage.

## Recommended Manual Test Model

Create `RecoveryTest.FCStd` without committing it to the repository:

```text
RecoveryTest.FCStd
└── Body
    ├── Sketch001
    ├── Pad
    ├── Sketch002
    ├── Pocket
    ├── DatumPlane
    ├── Sketch003
    └── Fillet
```

Use simple geometry so failures are visually obvious. Give `Sketch003` a line,
circle, geometric constraint, and dimensional constraint. Add at least one
later object whose existence and properties can be checked after recovery.

## Manual macOS Checklist

Before testing:

1. Fully restart FreeCAD so plugin modules are not cached.
2. Select the Model Health Assistant workbench.
3. Open **View → Panels → Report view** and **Python console**.
4. Open or create and save `RecoveryTest.FCStd`.
5. Confirm the recovery root without assuming a macOS path:

   ```python
   from pathlib import Path
   print(Path(App.getUserAppDataDir()) / "ModelHealthAssistant" / "recovery")
   ```

### Scenario A — Central selective recovery

1. Create `Sketch003` with supported geometry and constraints.
2. Close sketch editing, recompute, and wait at least one debounce interval.
3. Run **Analyze Model Health**.
4. Delete `Sketch003` and recompute.
5. Create or modify an unrelated object after the deletion and record its
   name/property value.
6. Open **Model Health Assistant Results → Recovery**.
7. Confirm `Sketch003` appears under Deleted Objects.
8. Select it, click **Restore as Copy**, review the assessment, and choose an
   offered safe mode.

Expected:

- `Sketch003_Recovered` exists.
- The unrelated later work and its recorded property remain unchanged.
- The original deleted object is not recreated in place.
- No Undo command or document rewind occurs.
- The document recomputes cleanly.

### Scenario B — Deleted or changed support

1. Create a sketch on a generated Pad face and recompute it.
2. Run analysis, then delete the sketch.
3. Modify or delete the original Pad/support.
4. Open Recovery and assess the deleted sketch.

Expected:

- The original support is reported missing, changed, or unverified.
- Generated `FaceN`/`EdgeN` names are not accepted as identity proof.
- Only **Restore Detached** is offered when support is unsafe.
- Internal geometry and supported constraints recover.
- Unsafe external geometry and dependent constraints are omitted with a
  warning.

### Scenario C — Multiple restores

1. Restore the same deleted record twice.

Expected:

- Copies have unique names such as `Sketch003_Recovered` and
  `Sketch003_Recovered_2`.
- The first recovered copy is not overwritten.
- The original immutable snapshot remains in the vault.

### Scenario D — Restart persistence

1. Save `RecoveryTest.FCStd`.
2. Delete a captured sketch and confirm it appears in Recovery.
3. Save the document in its deleted state and close FreeCAD.
4. Reopen FreeCAD and the same saved document.
5. Run analysis and open Recovery.

Expected:

- The deleted record remains available after restart.
- Its deletion timestamp, label, and eligibility remain intact.
- Restoration still creates a copy without changing later work.

### Scenario E — Unicode

1. Name a sketch `Mounting Sketch ⅛` or use another non-ASCII label.
2. Save the document in a directory containing spaces and Unicode text.
3. Capture, delete, display, and restore the sketch.

Expected:

- The label is readable in the Recovery UI and JSON.
- No encoding or path error occurs.
- The restored label retains the Unicode text plus its recovery suffix.

### Native Steps 8–9 smoke test

Run from FreeCAD's Python console, adapting the repository path if needed:

```python
import runpy
_ = runpy.run_path(
    "/path/to/FreeCAD_Model_Health_Assistant/"
    "scripts/test_steps_8_9_freecad.py",
    run_name="__main__",
)
```

Expected final line:

```text
ALL STEP 8 AND STEP 9 TESTS PASSED
```

The script uses temporary vaults and leaves its uniquely named test documents
open for inspection. Close them without saving afterward.

## Windows Regression Checklist

Use the same source checkout and do not install FreeCAD, FreeCADGui, Sketcher,
Part, or PySide through pip.

1. Install/link the repository under the FreeCAD user `Mod` directory and
   restart FreeCAD.
2. Confirm Model Health Assistant and its existing analysis/provider commands
   load normally.
3. In FreeCAD's Python console, print `App.getUserAppDataDir()` and confirm the
   recovery directory is derived from it. Do not assume `APPDATA`, a drive
   letter, or a username.
4. Repeat scenarios A through E using a saved Windows `.FCStd` document.
5. Repeat the test with spaces and Unicode in the document path and label.
6. Keep two documents with identical labels open and delete one sketch from
   each; confirm their Recovery lists remain isolated.
7. Save an initially unsaved document with **Save As** and confirm its current
   Recovery entries retain identity.
8. Close FreeCAD with a recently edited sketch before the debounce timer fires,
   reopen the saved document, and continue the deletion/recovery workflow.
9. Confirm opening the results dialog repeatedly does not duplicate observers,
   tabs, or recovery records.
10. Confirm a restored sketch recomputes and later work remains unchanged.

Record the FreeCAD version, Python version, Windows version, vault path, and
pass/fail result for each scenario.

## Automated Checks

Pure-Python checks from the repository root:

```bash
.venv/bin/python -m compileall -q Init.py InitGui.py src tests scripts
.venv/bin/python -m pytest -o addopts='' -q -rs
```

The three modules under `tests/freecad_integration/` intentionally use
FreeCAD-provided modules and skip under an ordinary Python environment. Run
those through a FreeCAD-provided Python environment or use the native smoke
script in the FreeCAD GUI.

The full pure-Python suite also covers the existing analysis, risk, report,
provider, credential, and AI paths. Recovery did not change the health-score
rules, risk rules, health-report schema, AI context schema, prompts, or provider
logic. Recovery-specific UI additions are isolated to the results dialog and
service adapters.

## Known Limitations

- Recovery supports only `Sketcher::SketchObject`.
- V1 geometry support is limited to line segments, circles, circle arcs, and
  points. Unsupported geometry makes the record partial and disables unsafe
  restoration.
- Only the explicitly supported common Sketcher constraints are reconstructed.
- Generated-face and generated-edge topology is never automatically remapped.
- Detached mode omits external geometry and constraints/expressions that depend
  on it.
- The original Body or container membership is not restored automatically; a
  recovered sketch is a new document object that the user may move or reattach.
- Recovering a sketch does not automatically relink downstream features that
  referenced the deleted original.
- Only allowlisted constraint expression paths are restored.
- Unsaved-document identity is session-scoped. Save the document before relying
  on recovery across a FreeCAD restart.
- There is no historical-version browser, checkpoint UI, preview, retention
  policy, whole-document rollback, or PartDesign feature recovery.
- Stable origin and datum-plane supports can be validated deterministically;
  generated geometry defaults to detached recovery.
- Native macOS Steps 8–9 validation has passed. Restart persistence and the
  complete scenarios above still require manual macOS validation.
- Windows runtime validation remains required on a Windows FreeCAD installation.

## Final Git Procedure

Review only; do not commit or push automatically:

```bash
git status --short
git diff --stat
```

Inspect the diff, run the automated checks, complete the platform checklists,
and then decide whether to commit through the normal project workflow.
