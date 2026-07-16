"""FreeCAD analysis workflow and native results interface.

The module remains safe to import outside FreeCAD.  FreeCAD and PySide are
optional at import time, and Qt is loaded only when a parented results dialog is
actually requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from model_health_assistant.models import ModelHealthReport
from model_health_assistant.presentation import (
    AnalysisPresentation,
    build_analysis_presentation,
)

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCADGui as Gui  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    Gui = None


COMMAND_NAME = "MHA_AnalyzeFeatureTree"


class AnalyzeFeatureTreeCommand:
    """Compatibility command that uses the shared local analysis workflow."""

    def GetResources(self) -> dict[str, str]:  # noqa: N802 - FreeCAD API name.
        """Return command metadata for FreeCAD."""

        return {
            "MenuText": "Analyze Feature Tree",
            "ToolTip": "Run a local model-health analysis and save a JSON report.",
            "Pixmap": "",
        }

    def IsActive(self) -> bool:  # noqa: N802 - FreeCAD API name.
        """Enable the command only when a document is active."""

        return (
            getattr(App, "ActiveDocument", None) is not None
            if App is not None
            else False
        )

    def Activated(self) -> None:  # noqa: N802 - FreeCAD API name.
        """Run the shared analysis workflow."""

        run_analysis_command(app_module=App, gui_module=Gui)


class _ResultsDialogController:
    """Keep Qt details behind a small Python interface that is easy to reuse."""

    def __init__(self, dialog: Any, value_labels: dict[str, Any]) -> None:
        self.dialog = dialog
        self.value_labels = value_labels

    def update_presentation(self, presentation: AnalysisPresentation) -> None:
        """Replace every displayed summary value."""

        self.value_labels["document"].setText(presentation.document_name)
        self.value_labels["score"].setText(f"{presentation.health_score}/100")
        self.value_labels["meaningful_features"].setText(
            str(presentation.meaningful_modeling_feature_count)
        )
        self.value_labels["sketches"].setText(str(presentation.total_sketch_count))
        self.value_labels["warnings"].setText(str(presentation.total_warning_count))
        report_text = (
            str(presentation.report_path)
            if presentation.report_path is not None
            else "Not available"
        )
        self.value_labels["report"].setText(report_text)

    def show(self) -> None:
        """Show and foreground the existing non-modal dialog."""

        self.dialog.show()
        raise_dialog = getattr(self.dialog, "raise_", None)
        if callable(raise_dialog):
            raise_dialog()
        activate_window = getattr(self.dialog, "activateWindow", None)
        if callable(activate_window):
            activate_window()


_results_dialog: _ResultsDialogController | None = None


def register_command() -> bool:
    """Register the compatibility command with FreeCADGui when available."""

    if Gui is None or not hasattr(Gui, "addCommand"):
        return False
    Gui.addCommand(COMMAND_NAME, AnalyzeFeatureTreeCommand())
    return True


def run_analysis_command(
    *,
    app_module: Any | None = None,
    gui_module: Any | None = None,
) -> AnalysisPresentation | None:
    """Analyze the active document, write JSON, print, and show summary results."""

    active_app = App if app_module is None else app_module
    active_gui = Gui if gui_module is None else gui_module
    document = (
        getattr(active_app, "ActiveDocument", None) if active_app is not None else None
    )
    if document is None:
        _print_message(
            "Model Health Assistant: No active FreeCAD document is open.\n",
            active_app,
        )
        return None

    document_name = str(
        getattr(document, "Label", None)
        or getattr(document, "Name", None)
        or "Untitled"
    )

    try:
        from model_health_assistant import extractor

        features = extractor.extract_document_features(document)
    except ImportError as exc:
        _print_message(
            _failure_message(
                "Could not load the local extractor",
                exc,
            ),
            active_app,
        )
        return None
    except Exception as exc:
        _print_message(_failure_message("Extractor failed", exc), active_app)
        return None

    try:
        from model_health_assistant import risk_engine

        report = risk_engine.analyze_model_health(
            features,
            document_name=document_name,
        )
    except ImportError as exc:
        _print_message(
            _failure_message(
                "Could not load the local risk engine",
                exc,
            ),
            active_app,
        )
        return None
    except Exception as exc:
        _print_message(_failure_message("Risk engine failed", exc), active_app)
        return None

    try:
        from model_health_assistant import report_writer

        output_path = report_writer.build_report_path(
            str(getattr(document, "Name", None) or document_name),
            str(getattr(document, "FileName", "") or ""),
            report.generated_at,
        )
        saved_path = report_writer.save_report_json(report, output_path)
        message = _format_report_view_summary(report, saved_path)
    except ImportError as exc:
        _print_message(
            _failure_message(
                "Could not load the local report writer",
                exc,
            ),
            active_app,
        )
        return None
    except Exception as exc:
        _print_message(_failure_message("Report writing failed", exc), active_app)
        return None

    _print_message(message, active_app)
    presentation = None
    try:
        presentation = build_analysis_presentation(report, saved_path)
        show_results_dialog(presentation, gui_module=active_gui)
    except Exception as exc:  # pragma: no cover - defensive FreeCAD runtime boundary.
        _print_error(_failure_message("Results dialog failed", exc), active_app)
    return presentation


def show_results_dialog(
    presentation: AnalysisPresentation,
    *,
    gui_module: Any | None = None,
) -> bool:
    """Create or update the single parented, non-modal results dialog."""

    active_gui = Gui if gui_module is None else gui_module
    parent = _main_window(active_gui)
    if parent is None:
        return False

    qt_widgets = _load_qt_widgets()
    if qt_widgets is None:
        return False

    global _results_dialog
    if _results_dialog is None:
        _results_dialog = _create_results_dialog(qt_widgets, parent)

    _results_dialog.update_presentation(presentation)
    _results_dialog.show()
    return True


def _format_report_view_summary(
    report: ModelHealthReport,
    saved_path: str | Path,
) -> str:
    severity_counts = report.warning_counts_by_severity
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    top_warnings = sorted(
        report.warnings,
        key=lambda warning: severity_rank.get(warning.severity, 3),
    )[:3]
    top_issues = "\n".join(
        str(index) + ". " + warning.message
        for index, warning in enumerate(top_warnings, start=1)
    )
    if not top_issues:
        top_issues = "None."

    return (
        "------------------------------------------------------------------------------------\n"
        + "Model Health Assistant Analysis\n\n"
        + "Document: "
        + report.document_name
        + "\n"
        + "Document objects: "
        + str(report.total_document_object_count)
        + "\n"
        + "Meaningful modeling features: "
        + str(report.meaningful_modeling_feature_count)
        + "\n"
        + "Sketches: "
        + str(report.total_sketch_count)
        + "\n\n"
        + "Warnings:\n"
        + "High: "
        + str(severity_counts["high"])
        + "\n"
        + "Medium: "
        + str(severity_counts["medium"])
        + "\n"
        + "Low: "
        + str(severity_counts["low"])
        + "\n"
        + "Total: "
        + str(report.total_warning_count)
        + "\n\n"
        + "Health score: "
        + str(report.health_score)
        + "/100\n\n"
        + "Top issues:\n\n"
        + top_issues
        + "\n\n"
        + "Report:\n"
        + str(saved_path)
        + "\n"
        + "------------------------------------------------------------------------------------\n"
    )


def _create_results_dialog(qt_widgets: Any, parent: Any) -> _ResultsDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle("Model Health Assistant Results")
    dialog.setModal(False)

    layout = qt_widgets.QVBoxLayout(dialog)
    form_layout = qt_widgets.QFormLayout()
    layout.addLayout(form_layout)

    value_labels: dict[str, Any] = {}
    rows = (
        ("document", "Document:"),
        ("score", "Health score:"),
        ("meaningful_features", "Meaningful modeling features:"),
        ("sketches", "Sketches:"),
        ("warnings", "Total warnings:"),
        ("report", "Report:"),
    )
    for key, caption in rows:
        value_label = qt_widgets.QLabel("", dialog)
        if key == "report" and hasattr(value_label, "setWordWrap"):
            value_label.setWordWrap(True)
        value_labels[key] = value_label
        form_layout.addRow(caption, value_label)

    button_layout = qt_widgets.QHBoxLayout()
    button_layout.addStretch()
    close_button = qt_widgets.QPushButton("Close", dialog)
    close_button.clicked.connect(dialog.close)
    button_layout.addWidget(close_button)
    layout.addLayout(button_layout)

    controller = _ResultsDialogController(dialog, value_labels)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_results_dialog(
                held_controller
            )
        )
    return controller


def _clear_results_dialog(controller: _ResultsDialogController) -> None:
    global _results_dialog
    if _results_dialog is controller:
        _results_dialog = None


def _main_window(gui_module: Any | None) -> Any | None:
    if gui_module is None:
        return None
    get_main_window = getattr(gui_module, "getMainWindow", None)
    if not callable(get_main_window):
        return None
    try:
        return get_main_window()
    except Exception:
        return None


def _load_qt_widgets() -> Any | None:
    try:  # pragma: no cover - depends on FreeCAD's bundled Qt runtime.
        from PySide2 import QtWidgets  # type: ignore

        return QtWidgets
    except Exception:  # pragma: no cover
        try:
            from PySide import QtGui as QtWidgets  # type: ignore

            return QtWidgets
        except Exception:
            return None


def _failure_message(context: str, exc: BaseException) -> str:
    return (
        "Model Health Assistant: "
        + context
        + ": "
        + type(exc).__name__
        + ": "
        + str(exc)
        + "\n"
    )


def _print_message(message: str, app_module: Any | None) -> None:
    try:
        if app_module is not None and hasattr(app_module, "Console"):
            app_module.Console.PrintMessage(message)
            return
    except Exception:
        pass
    print(message, end="")


def _print_error(message: str, app_module: Any | None) -> None:
    try:
        if app_module is not None and hasattr(app_module, "Console"):
            print_error = getattr(app_module.Console, "PrintError", None)
            if callable(print_error):
                print_error(message)
                return
    except Exception:
        pass
    print(message, end="")


__all__ = [
    "AnalyzeFeatureTreeCommand",
    "COMMAND_NAME",
    "register_command",
    "run_analysis_command",
    "show_results_dialog",
]
