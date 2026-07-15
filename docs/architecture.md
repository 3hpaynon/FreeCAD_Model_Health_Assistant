# Model Health Assistant Architecture

Model Health Assistant is designed as a local-first FreeCAD workbench. Version
0.2 creates a loadable FreeCAD addon foundation with feature-tree extraction,
deterministic risk analysis, local JSON reporting, and a registered workbench
command.

## Local System Flow

```text
FreeCAD document
-> local extractor
-> feature/dependency graph
-> local risk engine
-> JSON report
-> UI summary
```

## Version 0.2 Components

### FreeCAD Entry Points

FreeCAD loads the addon through root-level `Init.py` and `InitGui.py` files.
These files add the local `src` directory to `sys.path` so the package can be
used directly from FreeCAD's addon directory without an installation step.

`InitGui.py` defines `ModelHealthAssistantWorkbench`, registers it with
FreeCADGui when available, and adds the model-health command to a workbench menu
and toolbar.

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
and future UI or AI layers.

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

### UI Command

`model_health_assistant.ui` defines `AnalyzeFeatureTreeCommand`, a FreeCAD
command. When run inside FreeCAD, the command:

1. Read the active document.
2. Extract feature information.
3. Run local rules.
4. Save a JSON report.
5. Show a simple summary dialog.

The UI module is safe to import outside FreeCAD and does not require FreeCAD GUI
modules during tests. Command registration is owned by the workbench lifecycle,
not by import side effects.

## Privacy Design

The privacy posture is explicit:

- CAD files stay local.
- JSON reports stay local by default.
- No AI calls are made.
- No API keys are stored.
- No server backend is used.
- No CAD files or reports are uploaded.

Version 0.2 implements only the local foundation and contains no API-key,
server, cloud, upload, AI, or machine-learning code.
