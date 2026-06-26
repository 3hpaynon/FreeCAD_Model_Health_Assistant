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

## MVP Scope

Version 0.1 builds the local foundation only:

- Extract feature and dependency information from a FreeCAD document when run
  inside FreeCAD.
- Represent model facts with pure Python dataclasses.
- Run a local, rule-based risk engine.
- Generate a human-readable JSON report on disk.
- Provide a safe placeholder FreeCAD UI command.

## What Version 0.1 Does Not Include

Version 0.1 does not include:

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

The code is organized so a future AI explanation layer can be added after the
local extractor and risk engine produce structured facts. The AI layer should
explain known local findings, not discover facts directly from raw CAD files.

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

## Future Roadmap

- Local feature-tree extraction
- Local risk engine
- JSON reports
- FreeCAD UI panel
- Optional GPT/Claude explanation layer using user-provided API keys
- Secure API-key storage with OS keychain
- Dimension-change stress testing
- Recompute failure detection
- Opt-in anonymized training data
- Future custom ML risk model
