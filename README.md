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

Version 0.2 builds the local FreeCAD workbench/addon foundation:

- Extract feature and dependency information from a FreeCAD document when run
  inside FreeCAD.
- Represent model facts with pure Python dataclasses.
- Run a local, rule-based risk engine.
- Generate a human-readable JSON report on disk.
- Load as a FreeCAD workbench through `Init.py` and `InitGui.py`.
- Register a FreeCAD command, workbench menu, and toolbar entry.

## What Version 0.2 Does Not Include

Version 0.2 does not include:

- AI API calls
- API-key storage
- Custom machine learning
- Cloud storage
- Server backend
- Automatic uploading of CAD files

CAD files stay local. Reports are written locally. No network service is needed
for the current MVP.

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

Install the project in editable mode with test dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
python -m pytest
```

The tests only cover pure Python logic and do not require FreeCAD to be
installed.

## FreeCAD Addon Loading

For local FreeCAD testing, place this project directory where FreeCAD can load
Python addons, such as the user's FreeCAD `Mod` directory. FreeCAD will load:

- `Init.py` for startup path setup.
- `InitGui.py` for workbench registration.

The addon exposes the package under `src` without requiring a network service or
uploading CAD data.

## Future Roadmap

- Broader FreeCAD workbench UI panel
- More detailed feature-tree extraction
- Dimension-change stress testing
- Recompute failure detection
- Additional deterministic local risk rules
