# Model Health Assistant Architecture

Model Health Assistant is designed as a local-first FreeCAD workbench. Version
0.2 provides feature-tree extraction, deterministic risk analysis, local JSON
reporting, and a small native FreeCAD results interface.

## Local System Flow

```text
FreeCAD document
-> local extractor
-> feature/dependency graph
-> local risk engine
-> completed report object
   -> local JSON report writer
   -> UI-neutral presentation data
      -> Report View and native results dialog
```

## Version 0.2 Components

### FreeCAD Entry Points

FreeCAD loads the addon through root-level `Init.py` and `InitGui.py` files.
`Init.py` exposes the local `src` directory on `sys.path` so the package can be
used directly from FreeCAD's addon directory without an installation step.

`InitGui.py` remains deliberately minimal. It defines
`ModelHealthAssistantWorkbench`, registers the stable internal command ID
`MHA_TestCommand`, and adds it to the workbench menu and toolbar. The local
command wrapper imports and delegates activation to
`model_health_assistant.ui.run_analysis_command`; it contains no extraction,
risk, report, or dialog-construction logic.

### Extractor

`model_health_assistant.extractor` is responsible for reading the active FreeCAD
document when FreeCAD is available. It collects a defensive snapshot of document
objects, including names, labels, type IDs, dependency relationships, sketch
support, and best-effort recompute/error status.

The extractor is intentionally cautious. It uses `getattr()` heavily so the
module can be imported and tested outside FreeCAD.

### Data Models

`model_health_assistant.models` contains pure Python dataclasses:

- `FeatureInfo`
- `SketchInfo`
- `DependencyInfo`
- `RiskWarning`
- `ModelHealthReport`

These classes are the shared contract between extraction, analysis, reporting,
and presentation layers.

### Presentation Layer

`model_health_assistant.presentation` contains FreeCAD- and Qt-independent
dataclasses `AnalysisPresentation` and `WarningDisplayItem`. It converts an
already completed `ModelHealthReport` into stable display data without rerunning
rules, recalculating the health score, parsing warning messages, or mutating the
report. Warning ordering and counts come from structured report fields. Feature
evidence and raw penalty values are copied only when exact structured matches
exist.

### Risk Engine

`model_health_assistant.risk_engine` runs deterministic local rules. Version 0.2
includes rules for:

- Sketches attached to generated geometry
- Underdefined sketch metadata when available
- Recompute/error placeholder status
- Early fillets or chamfers with downstream dependents
- High downstream dependency counts
- Missing or unknown sketch support
- Future hardcoded/repeated dimension metadata

The risk engine does not use AI, machine learning, cloud calls, or servers.

### Health Score

The health score is a versioned deterministic heuristic. Formula version 1.0
starts at 100 and applies the following reviewed penalties after warning
deduplication:

| Rule ID | Penalty per affected feature | Rule cap |
| --- | ---: | ---: |
| `confirmed_recompute_error` | 25 | none |
| `sketch_generated_geometry_support` | 8 | 32 |
| `early_finishing_feature` | 5 | 20 |
| `underdefined_sketch` | 4 | 20 |
| `high_downstream_dependency_count` | 2 low / 4 medium | 16 |
| `unknown_sketch_support` | 0 | 0 |
| `hardcoded_repeated_dimensions` | 0 | 0 |

Unrecognized future rules receive zero points until an explicit weight is
reviewed. This prevents a new warning from silently changing score semantics.

Let `N` be the number of sketches plus additive, subtractive, finishing, and
pattern features. Let `A` be the number of unique features affected by rules
with nonzero penalties. The bounded prevalence penalty is:

```text
min(10, floor((10 * A / max(1, N)) + 0.5))
```

The final score is:

```text
clamp(100 - applied rule penalties - prevalence penalty, 0, 100)
```

Fixed rule penalties ensure confirmed errors are not diluted in large models.
The bounded prevalence term makes widespread issues matter more in small models
without allowing model size to dominate. Reports preserve the complete
calculation under `health_score_breakdown`, including per-warning raw penalties,
per-rule caps, affected-feature coverage, total penalty, and final score.

### Report Writer

`model_health_assistant.report_writer` writes indented JSON reports to local
storage. Report schema 0.3 adds explicit, machine-readable context while keeping
the earlier top-level keys for existing consumers:

- `schema_version` identifies the JSON contract independently of the plugin
  version.
- `document` holds structured document metadata currently limited to the local
  FreeCAD document name.
- `generated_at` is the explicit generation timestamp; the legacy `timestamp`
  alias remains present.
- `total_document_object_count` reports every object captured from the FreeCAD
  document, while `meaningful_modeling_feature_count` reports only additive,
  subtractive, finishing, and pattern features.
- `category_counts`, `total_sketch_count`, and
  `warning_counts_by_severity` make the report summary auditable without
  reclassifying the feature and warning arrays.
- Each feature retains its category, direct dependencies and dependents, sketch
  support details, and confirmed error details. Each warning retains its stable
  rule ID and severity.
- `health_score_breakdown` retains the complete deterministic score calculation.

For backward compatibility, `document_name`, `timestamp`, and
`total_feature_count` remain in schema 0.3. `total_feature_count` is the legacy
name for the extracted document-object count; new consumers should use
`total_document_object_count` because its meaning is unambiguous.

### UI Workflow and Results Dialog

`model_health_assistant.ui.run_analysis_command` owns the synchronous
application workflow:

1. resolve the active FreeCAD document
2. extract a defensive fact snapshot
3. run the deterministic local risk engine once
4. attempt to save the completed report as local JSON
5. build UI-neutral presentation data from the completed report
6. print Report View output and create or update the results dialog

The report object remains valid if JSON writing fails, so the UI still displays
the completed analysis with report actions disabled and a concise partial-
success status. Extraction and risk-engine failures preserve previously shown
results and publish technical diagnostics to Report View.

The non-modal `QDialog` is parented to FreeCAD's main window and retained by one
module-level controller reference. Repeated command activation and manual
refresh replace the dialog's values and rows rather than accumulating stale
results or new windows. The controller stores the exact analyzed document
identity. Feature selection revalidates that document and resolves the object by
its structured internal name before using `FreeCADGui.Selection`; it does not
modify model or view properties.

Report-folder opening uses Qt's local-file URL support and passes only the
report's parent directory. Clipboard support copies only the complete report
path. Refresh is explicit and synchronous—there are no timers, observers,
threads, retry loops, or background analysis.

FreeCAD, FreeCADGui, and PySide imports are guarded. PySide is loaded only from
FreeCAD's bundled PySide2 or legacy PySide namespaces at UI-creation time.
Importing and testing the package outside FreeCAD therefore remains safe.

### Responsibility Boundaries

- `Init.py`: expose the local package layout to FreeCAD.
- `InitGui.py`: register the workbench, stable command ID, menu, and toolbar;
  delegate activation.
- `extractor.py`: read FreeCAD objects and produce model facts without mutation.
- `models.py`: define pure structured facts, warnings, and completed reports.
- `risk_engine.py`: own all warning rules, severity, deduplication, and scoring.
- `report_writer.py`: own local path construction and JSON serialization.
- `presentation.py`: reshape completed structured results for display only.
- `ui.py`: orchestrate one analysis run, print status, manage the native dialog,
  and handle user actions.

Automated tests exercise pure logic and headless fakes; they require neither a
FreeCAD installation nor a pip-installed PySide package.

## Privacy Design

The privacy posture is explicit:

- CAD files stay local.
- JSON reports stay local by default.
- No AI calls are made.
- No API keys are stored.
- No server backend is used.
- No CAD files or reports are uploaded.

Version 0.2 contains no API-key, server, cloud, upload, AI, machine-learning,
telemetry, or automatic model-repair code. Analysis and JSON reports remain on
the local machine.
