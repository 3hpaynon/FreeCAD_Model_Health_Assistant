"""JSON report utilities for local model-health reports."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from model_health_assistant.models import ModelHealthReport


def build_report_path(
    document_name: str,
    document_file_name: str = "",
    generated_at: str | None = None,
) -> Path:
    """Build a timestamped local report path for a saved or unsaved document."""

    safe_name = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in document_name
    ).strip("_") or "untitled"
    timestamp = (
        datetime.fromisoformat(generated_at)
        if generated_at
        else datetime.now().astimezone()
    ).strftime("%Y-%m-%d_%H%M%S")

    if document_file_name:
        base_dir = Path(document_file_name).expanduser().resolve().parent
    else:
        base_dir = Path.home() / ".model_health_assistant"

    return (
        base_dir
        / "model_health_reports"
        / f"{safe_name}_model_health_{timestamp}.json"
    )


def report_to_dict(report: ModelHealthReport) -> dict[str, object]:
    """Convert only the typed local report into its allowlisted JSON shape."""

    if isinstance(report, ModelHealthReport):
        return report.to_dict()
    raise TypeError("report must be a ModelHealthReport")


def save_report_json(report: ModelHealthReport, output_path: str | Path) -> Path:
    """Save one typed local report and return the final path."""

    report_dict = report_to_dict(report)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_dict, indent=2) + "\n", encoding="utf-8")
    return path


def load_report_json(path: str | Path) -> dict[str, Any]:
    """Load a report JSON file as a dictionary."""

    return json.loads(Path(path).read_text(encoding="utf-8"))
