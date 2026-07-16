# Model Health Assistant

Model Health Assistant is a local-first FreeCAD workbench/plugin foundation for
analyzing parametric CAD models. Its purpose is to inspect a model's feature
tree, identify fragile modeling patterns, and generate a local model-health
report that helps users build more robust parametric models.

Parametric CAD models can become fragile when later features depend on generated
faces, edges, or unstable references from earlier operations. Small edits can
rename topology, invalidate downstream sketches, or cause recompute failures.
This project focuses on making those dependency risks visible before they become
hard-to-debug modeling problems.

## Current Scope

Version 0.2 provides a local FreeCAD workbench and native results interface:

- Extract feature and dependency information from a FreeCAD document when run
  inside FreeCAD.
- Represent model facts with pure Python dataclasses.
- Run a local, rule-based risk engine.
- Generate a human-readable JSON report on disk.
- Load as a FreeCAD workbench through `Init.py` and `InitGui.py`.
- Register an `Analyze Model Health` menu and toolbar command.
- Display a reusable, non-modal results dialog with summary values, warning
  rows, selected-warning details, and report status.
- Select a warning's affected object by its internal FreeCAD name.
- Open the local report folder, copy the report path, and manually refresh the
  active document's analysis.

## Using the Workbench

1. Make the repository available in the user's FreeCAD `Mod` directory using
   the existing addon-directory or linked-directory setup.
2. Fully restart FreeCAD.
3. Choose **Model Health Assistant** in the workbench selector.
4. Open or activate the document to inspect.
5. Click **Analyze Model Health** in the workbench toolbar or menu.

The command reads the active document, runs the deterministic local rules,
prints a summary to FreeCAD's Report View, writes a JSON report, and opens or
updates one **Model Health Assistant Results** dialog. Repeated activation
updates that dialog rather than intentionally accumulating result windows.

### Results Window

The results window displays:

- document name and analysis timestamp
- health score and document/modeling-feature/sketch counts
- total, high-, medium-, and low-severity warning counts
- the local report path or a clear unavailable state
- a read-only warning table with Severity, Feature, Rule, and Warning columns
- structured details for the selected warning, including internal feature name,
  evidence, and the raw score penalty when present in the report

Single-click a warning to inspect its details. Click **Select Feature**, or
double-click the row, to select the referenced object in the analyzed document.
Selection uses the structured internal object name, not the visible label, and
does not alter geometry, placement, visibility, or appearance.

Use **Open Report Folder** to open only the directory containing the saved JSON
report. **Copy Report Path** copies the complete local path, not the report
contents. Both actions are disabled when no valid report exists.

Use **Refresh Analysis** to analyze the document that is active at that moment.
Refresh runs once, writes a new report using the normal behavior, and replaces
the summary, warnings, details, report path, and analyzed-document identity in
the existing dialog. It is manual: there are no timers, observers, or
continuous analysis. If no document is active, previous results are preserved
and a short status is shown.

### Warning Severity and Health Score

Severity is a deterministic priority category assigned by the local rule
engine. High warnings deserve the most immediate review, medium warnings
identify material fragility risks, and low warnings identify lower-priority or
informational conditions. Severity does not prove that a feature will fail.

The health score is a rule-based engineering aid. It summarizes only the facts
and rules currently implemented and does not guarantee that the model is
error-free. Inspect warning details and engineering context rather than using
the score as a pass/fail certification.

## What Version 0.2 Does Not Include

Version 0.2 does not include:

- AI API calls
- API-key storage
- Custom machine learning
- Cloud storage
- Server backend
- Automatic uploading of CAD files
- Automatic model repair or constraint changes
- Continuous/background model monitoring

CAD files stay local. Reports are written locally. No network service is needed
for the current MVP.

The current milestone contains no AI or LLM integration. It does not send model
facts or reports to OpenAI, Anthropic, or any other external service.

## Current Limitations

- Analysis is a manual snapshot and sees only facts exposed by the current
  FreeCAD document and extractor.
- The deterministic rules cannot detect every modeling or topology problem.
- Unknown or unavailable FreeCAD metadata is reported conservatively and is not
  invented by the UI.
- Feature selection requires the analyzed document and referenced internal
  object to remain available.
- If JSON writing fails, valid analysis results remain visible, but report-path
  actions are unavailable for that run.
- The workbench does not repair models, generate geometry, change constraints,
  run automated stress tests, or provide AI explanations.

## Current Architecture

The intended local flow is:

```text
FreeCAD document
-> local extractor
-> feature/dependency facts
-> local rule engine
-> JSON report
-> FreeCAD UI summary
```

## Model Health Score

The score is a transparent deterministic heuristic, not a statistical
prediction. It starts at 100, subtracts reviewed rule-specific penalties, adds a
bounded 0-10 prevalence penalty, and is clamped to the range 0-100.

Confirmed FreeCAD errors subtract 25 points each without a cap. Generated-face
sketch dependencies subtract 8 points each (capped at 32), early fillet/chamfer
dependencies subtract 5 (capped at 20), underdefined sketches subtract 4
(capped at 20), and high downstream dependency counts subtract 2 or 4 based on
severity (capped at 16). Unknown sketch support, placeholder dimension warnings,
and unreviewed future rules are informational and subtract zero points.

The prevalence adjustment uses only sketches and meaningful modeling features;
containers and reference geometry do not inflate model size. Every JSON report
records the complete score calculation, including raw penalties, caps, affected
feature coverage, total penalty, and final score.

## JSON Report Schema

Local reports use schema version `0.3`. They identify the document and generation
time, distinguish all extracted document objects from meaningful modeling
features, summarize feature categories and warning severities, and retain full
feature, support, dependency, warning, and score-breakdown details. The legacy
`document_name`, `timestamp`, and `total_feature_count` keys remain available for
existing report consumers. Reports contain model facts rather than raw geometry
and are never uploaded.

## Development

Use the existing project virtual environment. On Windows, run:

```powershell
.\.venv\Scripts\python.exe -m compileall Init.py InitGui.py src tests
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.\.pytest_temp
```

The automated tests use headless fakes and pure Python data. They do not require
FreeCAD or PySide to be installed.

## FreeCAD Addon Loading

For local FreeCAD testing, place this project directory where FreeCAD can load
Python addons, such as the user's FreeCAD `Mod` directory. FreeCAD will load:

- `Init.py` for startup path setup.
- `InitGui.py` for workbench registration.

The addon exposes the package under `src` without requiring a network service or
uploading CAD data.

## Future Roadmap

- More detailed feature-tree extraction
- Dimension-change stress testing
- Additional deterministic local risk rules
- Optional AI explanations only in a separately designed future milestone
