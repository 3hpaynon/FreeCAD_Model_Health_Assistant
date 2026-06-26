"""JSON report utilities for local model-health reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_health_assistant.models import ModelHealthReport


def report_to_dict(report: ModelHealthReport | dict[str, Any]) -> dict[str, Any]:
    """Convert a report object into the Version 0.1 JSON shape."""

    if isinstance(report, ModelHealthReport):
        return report.to_dict()
    if isinstance(report, dict):
        return dict(report)
    raise TypeError("report must be a ModelHealthReport or dictionary")


def save_report_json(report: ModelHealthReport | dict[str, Any], output_path: str | Path) -> Path:
    """Save a local JSON report and return the final path."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report_dict = report_to_dict(report)
    path.write_text(json.dumps(report_dict, indent=2) + "\n", encoding="utf-8")
    return path


def load_report_json(path: str | Path) -> dict[str, Any]:
    """Load a report JSON file as a dictionary."""

    return json.loads(Path(path).read_text(encoding="utf-8"))
