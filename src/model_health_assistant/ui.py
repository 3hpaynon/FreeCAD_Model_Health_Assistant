"""FreeCAD UI command placeholder for Model Health Assistant.

This module is safe to import outside FreeCAD. When FreeCAD GUI modules are not
available, command registration simply does nothing.
"""

from __future__ import annotations

from pathlib import Path

from model_health_assistant.extractor import extract_document_features, get_active_document
from model_health_assistant.report_writer import save_report_json
from model_health_assistant.risk_engine import analyze_model_health

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCADGui as Gui  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    Gui = None

try:  # pragma: no cover - depends on FreeCAD/PySide installation.
    from PySide2 import QtWidgets  # type: ignore
except Exception:  # pragma: no cover
    try:
        from PySide import QtGui as QtWidgets  # type: ignore
    except Exception:
        QtWidgets = None


COMMAND_NAME = "MHA_AnalyzeFeatureTree"


class AnalyzeFeatureTreeCommand:
    """FreeCAD command that runs the local model-health analysis."""

    def GetResources(self) -> dict[str, str]:  # noqa: N802 - FreeCAD API name.
        """Return command metadata for FreeCAD."""

        return {
            "MenuText": "Analyze Feature Tree",
            "ToolTip": "Run a local model-health analysis and save a JSON report.",
            "Pixmap": "",
        }

    def IsActive(self) -> bool:  # noqa: N802 - FreeCAD API name.
        """Enable the command only when a document is active."""

        return get_active_document() is not None

    def Activated(self) -> None:  # noqa: N802 - FreeCAD API name.
        """Run extraction, risk analysis, report writing, and summary display."""

        doc = get_active_document()
        if doc is None:
            _show_message("Model Health Assistant", "No active FreeCAD document was found.")
            return

        features = extract_document_features(doc)
        report = analyze_model_health(features, document_name=_document_name(doc))
        output_path = _default_report_path(doc)
        save_report_json(report, output_path)

        summary = (
            f"Health score: {report.health_score}/100\n"
            f"Warnings: {report.total_warning_count}\n"
            f"Report saved to:\n{output_path}"
        )
        _show_message("Model Health Assistant", summary)


def register_command() -> bool:
    """Register the command with FreeCADGui when available."""

    if Gui is None:
        return False
    Gui.addCommand(COMMAND_NAME, AnalyzeFeatureTreeCommand())
    return True


def _document_name(doc: object) -> str:
    return str(getattr(doc, "Name", None) or getattr(doc, "Label", None) or "Untitled")


def _default_report_path(doc: object) -> Path:
    safe_name = _safe_filename(_document_name(doc))
    file_name = str(getattr(doc, "FileName", "") or "")
    base_dir = Path(file_name).resolve().parent if file_name else Path.cwd()
    return base_dir / "model_health_reports" / f"{safe_name}_model_health_report.json"


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return cleaned.strip("_") or "untitled"


def _show_message(title: str, message: str) -> None:
    if QtWidgets is not None:
        QtWidgets.QMessageBox.information(None, title, message)
        return

    if App is not None and hasattr(App, "Console"):
        App.Console.PrintMessage(f"{title}: {message}\n")
        return

    print(f"{title}: {message}")
