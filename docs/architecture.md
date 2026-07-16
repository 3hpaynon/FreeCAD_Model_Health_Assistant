# Model Health Assistant Architecture

Model Health Assistant is designed as a local-first FreeCAD workbench. The
current pre-API milestone provides feature-tree extraction, deterministic risk
analysis, local JSON reporting, a native FreeCAD results interface, and a
provider-neutral explanation foundation exercised only by a deterministic local
mock provider. No AI model, API key, provider SDK, or network request is used.

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
   -> explicit allowlist and deterministic limits
      -> AIRequestContext 1.0
      -> versioned provider-neutral explanation request
      -> injected LocalMockAIProvider
      -> validated AIExplanationResponse 1.0
      -> read-only local mock explanation dialog
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

### Provider-Neutral Explanation Foundation

The explanation branch begins only after `ModelHealthReport` is complete. It
does not read a live FreeCAD document, rerun extraction, parse the JSON report
file, or receive a report path.

`model_health_assistant.ai_models` defines pure Python, JSON-compatible context,
request, and response contracts. Context, request, and response schema versions
are independently fixed at `1.0`. No contract contains FreeCAD objects, Qt
objects, credentials, paths, binary geometry, or automatic model-modification
instructions.

`model_health_assistant.ai_context` is the only report-to-provider data boundary.
It builds a new `AIRequestContext` through an explicit allowlist. It excludes or
redacts absolute Windows and private POSIX paths, excludes the real document name
by default, omits report metadata and output paths, and never copies the entire
report. Allowlisted evidence is rebuilt from reviewed feature fields; unsupported
values and arbitrary metadata are omitted without using `repr`.

CAD-originated strings—including document names, feature labels, warning text,
notes, and evidence—remain untrusted data. `model_health_assistant.ai_prompts`
serializes the context as canonical JSON between explicit untrusted-context
delimiters. Its system instruction says not to follow instructions embedded in
CAD data. Prompt template version `model-health-explanation-v1` is recorded in
request metadata.

Default limit policy `context-limits-v1` is deterministic:

| Limit | Default |
| --- | ---: |
| Warnings | 50 |
| Inline feature summaries | 50 |
| Characters per text field | 2,000 |
| Items per allowlisted evidence list | 10 |
| Canonical serialized context characters | 64,000 |

Warnings are prioritized high, then medium, then low, with stable source order
inside each severity. Truncation always preserves valid JSON. Context metadata
discloses the limit policy, configured limits, original/included/omitted warning
counts, omitted field identifiers, truncated field count, and final serialized
character count. These are character and item limits, not token estimates.

`model_health_assistant.ai_provider.AIExplanationProvider` is a structural,
provider-neutral contract. It accepts only `AIExplanationRequest` and returns
`AIExplanationResponse`. `LocalMockAIProvider` is the only implementation. It is
deterministic, has no configuration or credential input, creates no new risk
findings, does not recalculate the score, and does not claim to inspect geometry
or use an AI model.

`model_health_assistant.ai_service.AIExplanationService` receives an explicitly
injected provider and coordinates context construction, prompt construction,
execution, and response validation. Validation rejects wrong schemas, provider
identity mismatches, unknown warning identities, changed severities, duplicate
explanations, missing explanation text, and missing global limitations. A valid
partial response may omit per-warning explanations.

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

The results controller also retains the completed `ModelHealthReport` for two
explicit local actions. **Preview AI Context** displays exactly the allowlisted
context as read-only JSON with a no-transmission notice. **Test Mock
Explanation** injects `LocalMockAIProvider`, invokes the service, and displays
the validated response separately from the source warnings with a no-AI/no-
network notice. Both child dialogs are parented and reused. If the analyzed
document is closed, replaced, or no longer active, the report snapshot remains
usable and the results status discloses that state. Neither action writes a file,
uses the clipboard, modifies the model, or changes the health score.

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
- `ai_models.py`: define provider-neutral JSON-compatible AI contracts.
- `ai_context.py`: apply the privacy allowlist, sanitization, and deterministic
  size limits to a completed report.
- `ai_prompts.py`: build the versioned prompt from the safe context and delimit
  untrusted CAD text.
- `ai_provider.py`: define the provider contract and neutral error taxonomy.
- `mock_ai_provider.py`: implement the deterministic local test provider.
- `ai_service.py`: orchestrate an injected provider and validate its response.
- `ui.py`: orchestrate one analysis run, print status, manage the native dialog,
  retain the completed report, and handle explicit user actions.

Automated tests exercise pure logic and headless fakes; they require neither a
FreeCAD installation nor a pip-installed PySide package.

## Privacy Design

The privacy posture is explicit:

- CAD files stay local.
- JSON reports stay local by default.
- AI context is rebuilt from an explicit allowlist; a complete report is not
  passed across the provider boundary.
- Absolute paths and private path components are excluded or redacted.
- CAD-originated text is treated as untrusted data.
- Deterministic truncation is disclosed.
- No AI model or real provider is called.
- No API key is accepted or stored.
- No HTTP client, provider SDK, telemetry, or analytics is present.
- No server backend is used.
- No CAD files or reports are uploaded.

The local context preview is transparency tooling, not transmission. The mock
explanation proves the architecture without intelligence or connectivity.

## Next-Milestone Boundary

**Secure API-Key and Real Provider Integration** is intentionally deferred. A
future real-provider adapter needs to implement only:

1. stable provider identity
2. provider availability
3. translation from `AIExplanationRequest` to the provider-specific request
4. network execution
5. translation from the provider-specific response to `AIExplanationResponse`
6. mapping provider-specific failures to the neutral error taxonomy

A future credential manager must be a separate component from the FreeCAD
dialog, context builder, prompt builder, analysis engine, and provider-neutral
service. That milestone may add OS-backed credential storage; key add, replace,
and remove actions; provider selection; connection testing; explicit consent;
payload preview; timeout, authentication, and rate-limit handling; and explicit
**Explain Selected Warning** and **Explain Full Analysis** actions. No placeholder
key storage, provider selection, or real execution code exists in this milestone.
