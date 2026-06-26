# Model Health Assistant Architecture

Model Health Assistant is designed as a local-first FreeCAD workbench. Version
0.1 creates the foundation for feature-tree extraction, deterministic risk
analysis, local JSON reporting, and a basic UI command.

## Local System Flow

```text
FreeCAD document
-> local extractor
-> feature/dependency graph
-> local risk engine
-> JSON report
-> UI summary
-> optional future AI explanation layer
```

## Version 0.1 Components

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

`model_health_assistant.risk_engine` runs deterministic local rules. Version 0.1
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

### UI Placeholder

`model_health_assistant.ui` defines `AnalyzeFeatureTreeCommand`, a basic FreeCAD
command placeholder. When run inside FreeCAD, the command is intended to:

1. Read the active document.
2. Extract feature information.
3. Run local rules.
4. Save a JSON report.
5. Show a simple summary dialog.

The UI module is safe to import outside FreeCAD and does not require FreeCAD GUI
modules during tests.

## Future AI Layer

The future AI layer should not discover facts directly from raw CAD files.
Instead:

1. The local extractor should produce structured model facts.
2. The local risk engine should identify deterministic warnings.
3. A future AI model may explain those already-known facts in clearer language.

This separation keeps CAD-specific truth grounded in local analysis and limits
what would need to be shared with any cloud model.

## Privacy Design

The long-term privacy posture should be explicit:

- CAD files stay local.
- JSON reports stay local by default.
- AI calls are user-triggered only.
- Users must consent before sending any summary to a cloud model.
- API keys should be stored with secure OS key storage, not plaintext files.
- Any future anonymized training data collection must be opt-in.

Version 0.1 implements only the local foundation and contains no API-key,
server, cloud, upload, AI, or machine-learning code.
