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

### Report Writer

`model_health_assistant.report_writer` writes indented JSON reports to local
storage. Reports include document name, timestamp, feature counts, sketch counts,
warning counts, health score, extracted features, warnings, and local-only
analysis notes.

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
