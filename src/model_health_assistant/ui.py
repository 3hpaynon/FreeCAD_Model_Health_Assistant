"""FreeCAD analysis workflow and native results interface.

The module remains safe to import outside FreeCAD.  FreeCAD and PySide are
optional at import time, and Qt is loaded only when a parented results dialog is
actually requested.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model_health_assistant.models import ModelHealthReport
from model_health_assistant.presentation import (
    AnalysisPresentation,
    WarningDisplayItem,
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

_WARNING_TABLE_HEADERS = ("Severity", "Feature", "Rule", "Warning")
_NO_WARNINGS_MESSAGE = "No model-health warnings were generated."


@dataclass(frozen=True, slots=True)
class _AnalyzedDocumentContext:
    """UI-only identity and API context for the document that was analyzed."""

    document: Any
    document_name: str
    app_module: Any
    gui_module: Any


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

    def __init__(
        self,
        dialog: Any,
        value_labels: dict[str, Any],
        *,
        warning_table: Any | None = None,
        empty_warning_label: Any | None = None,
        table_item_factory: Any | None = None,
        feature_name_data_role: Any | None = None,
        warning_details: Any | None = None,
        select_feature_button: Any | None = None,
        selection_error_handler: Any | None = None,
    ) -> None:
        self.dialog = dialog
        self.value_labels = value_labels
        self.warning_table = warning_table
        self.empty_warning_label = empty_warning_label
        self.table_item_factory = table_item_factory
        self.feature_name_data_role = feature_name_data_role
        self.warning_details = warning_details
        self.select_feature_button = select_feature_button
        self.selection_error_handler = selection_error_handler
        self.warning_items: tuple[WarningDisplayItem, ...] = ()
        self.document_context: _AnalyzedDocumentContext | None = None

    def update_presentation(
        self,
        presentation: AnalysisPresentation,
        *,
        document_context: _AnalyzedDocumentContext | None = None,
    ) -> None:
        """Replace every displayed summary value."""

        self.document_context = document_context
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
        self._update_warning_table(presentation)

    def _update_warning_table(self, presentation: AnalysisPresentation) -> None:
        self.warning_items = tuple(presentation.warning_items)
        warning_items = self.warning_items
        self._set_warning_details("")
        self._set_select_feature_enabled(False)
        if self.empty_warning_label is not None:
            set_text = getattr(self.empty_warning_label, "setText", None)
            if callable(set_text):
                set_text(_NO_WARNINGS_MESSAGE)
            set_visible = getattr(self.empty_warning_label, "setVisible", None)
            if callable(set_visible):
                set_visible(not warning_items)

        if self.warning_table is None or self.table_item_factory is None:
            return

        clear_selection = getattr(self.warning_table, "clearSelection", None)
        if callable(clear_selection):
            clear_selection()
        self.warning_table.setRowCount(0)
        self.warning_table.setRowCount(len(warning_items))
        for row_index, warning in enumerate(warning_items):
            values = _warning_table_values(warning)
            for column_index, value in enumerate(values):
                table_item = self.table_item_factory(value)
                if (
                    column_index == 1
                    and warning.feature_internal_name
                    and self.feature_name_data_role is not None
                ):
                    set_data = getattr(table_item, "setData", None)
                    if callable(set_data):
                        set_data(
                            self.feature_name_data_role,
                            warning.feature_internal_name,
                        )
                self.warning_table.setItem(row_index, column_index, table_item)

    def update_selected_warning_details(self) -> None:
        """Show selected details and enable selection only for a valid target."""

        self._set_warning_details("")
        self._set_select_feature_enabled(False)
        warning = self._selected_warning()
        if warning is None:
            return

        self._set_warning_details(_format_warning_details(warning))
        validation_error = _select_feature_by_internal_name(
            self.document_context,
            warning.feature_internal_name,
            perform_selection=False,
        )
        self._set_select_feature_enabled(validation_error is None)

    def select_selected_feature(self, *_args: Any) -> bool:
        """Revalidate and select the selected warning's internal feature name."""

        warning = self._selected_warning()
        if warning is None:
            self._set_select_feature_enabled(False)
            return False

        error = _select_feature_by_internal_name(
            self.document_context,
            warning.feature_internal_name,
            perform_selection=True,
        )
        if error is None:
            return True

        self._set_select_feature_enabled(False)
        if callable(self.selection_error_handler):
            self.selection_error_handler(error)
        app_module = (
            self.document_context.app_module
            if self.document_context is not None
            else App
        )
        _print_message(f"Model Health Assistant: {error}\n", app_module)
        return False

    def _selected_warning(self) -> WarningDisplayItem | None:
        if self.warning_table is None:
            return None
        selected_items_method = getattr(self.warning_table, "selectedItems", None)
        current_row_method = getattr(self.warning_table, "currentRow", None)
        if not callable(selected_items_method) or not callable(current_row_method):
            return None
        selected_items = selected_items_method()
        if not selected_items:
            return None
        row_index = current_row_method()
        if not isinstance(row_index, int) or not 0 <= row_index < len(
            self.warning_items
        ):
            return None
        return self.warning_items[row_index]

    def _set_warning_details(self, text: str) -> None:
        if self.warning_details is None:
            return
        set_plain_text = getattr(self.warning_details, "setPlainText", None)
        if callable(set_plain_text):
            set_plain_text(text)
            return
        set_text = getattr(self.warning_details, "setText", None)
        if callable(set_text):
            set_text(text)

    def _set_select_feature_enabled(self, enabled: bool) -> None:
        if self.select_feature_button is None:
            return
        set_enabled = getattr(self.select_feature_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(enabled)

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
        document_context = _AnalyzedDocumentContext(
            document=document,
            document_name=str(getattr(document, "Name", None) or ""),
            app_module=active_app,
            gui_module=active_gui,
        )
        show_results_dialog(
            presentation,
            gui_module=active_gui,
            document_context=document_context,
        )
    except Exception as exc:  # pragma: no cover - defensive FreeCAD runtime boundary.
        _print_error(_failure_message("Results dialog failed", exc), active_app)
    return presentation


def show_results_dialog(
    presentation: AnalysisPresentation,
    *,
    gui_module: Any | None = None,
    document_context: _AnalyzedDocumentContext | None = None,
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

    _results_dialog.update_presentation(
        presentation,
        document_context=document_context,
    )
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

    warning_table = qt_widgets.QTableWidget(dialog)
    _configure_warning_table(qt_widgets, warning_table)
    layout.addWidget(warning_table)

    empty_warning_label = qt_widgets.QLabel(_NO_WARNINGS_MESSAGE, dialog)
    empty_warning_label.setVisible(False)
    layout.addWidget(empty_warning_label)

    details_label = qt_widgets.QLabel("Warning details:", dialog)
    layout.addWidget(details_label)
    warning_details = qt_widgets.QPlainTextEdit(dialog)
    warning_details.setReadOnly(True)
    layout.addWidget(warning_details)

    button_layout = qt_widgets.QHBoxLayout()
    select_feature_button = qt_widgets.QPushButton("Select Feature", dialog)
    select_feature_button.setEnabled(False)
    button_layout.addWidget(select_feature_button)
    button_layout.addStretch()
    close_button = qt_widgets.QPushButton("Close", dialog)
    close_button.clicked.connect(dialog.close)
    button_layout.addWidget(close_button)
    layout.addLayout(button_layout)

    controller = _ResultsDialogController(
        dialog,
        value_labels,
        warning_table=warning_table,
        empty_warning_label=empty_warning_label,
        table_item_factory=qt_widgets.QTableWidgetItem,
        feature_name_data_role=_load_qt_user_role(),
        warning_details=warning_details,
        select_feature_button=select_feature_button,
        selection_error_handler=lambda message: _show_selection_error(
            qt_widgets,
            dialog,
            message,
        ),
    )
    warning_table.itemSelectionChanged.connect(
        controller.update_selected_warning_details
    )
    select_feature_button.clicked.connect(controller.select_selected_feature)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_results_dialog(
                held_controller
            )
        )
    return controller


def _configure_warning_table(qt_widgets: Any, warning_table: Any) -> None:
    """Configure the warning table as a read-only, row-selecting view."""

    warning_table.setColumnCount(len(_WARNING_TABLE_HEADERS))
    warning_table.setHorizontalHeaderLabels(list(_WARNING_TABLE_HEADERS))
    warning_table.setSortingEnabled(False)

    item_view = getattr(qt_widgets, "QAbstractItemView", None)
    if item_view is None:
        return
    warning_table.setEditTriggers(item_view.NoEditTriggers)
    warning_table.setSelectionBehavior(item_view.SelectRows)
    warning_table.setSelectionMode(item_view.SingleSelection)


def _warning_table_values(warning: Any) -> tuple[str, str, str, str]:
    feature = (
        warning.feature_label
        or warning.feature_internal_name
        or "Unknown feature"
    )
    rule = warning.rule_id or "Unknown rule"
    severity = str(warning.severity or "").capitalize()
    return severity, feature, rule, warning.message


def _select_feature_by_internal_name(
    context: _AnalyzedDocumentContext | None,
    feature_internal_name: str,
    *,
    perform_selection: bool,
) -> str | None:
    """Validate and optionally select one object by document and object Name."""

    if context is None:
        return "No analyzed document is available for feature selection."
    if not feature_internal_name:
        return "The selected warning has no affected feature internal name."
    if not context.document_name:
        return "The analyzed document has no internal FreeCAD name."

    get_document = getattr(context.app_module, "getDocument", None)
    if not callable(get_document):
        return "The analyzed document cannot be resolved in FreeCAD."
    try:
        open_document = get_document(context.document_name)
    except Exception:
        return "The analyzed document is no longer open."
    if open_document is None:
        return "The analyzed document is no longer open."
    if open_document is not context.document:
        return "The document with that name is not the document that was analyzed."

    get_object = getattr(open_document, "getObject", None)
    if not callable(get_object):
        return "The analyzed document does not support internal-name lookup."
    try:
        target = get_object(feature_internal_name)
    except Exception:
        return f"Feature '{feature_internal_name}' could not be resolved."
    if target is None:
        return f"Feature '{feature_internal_name}' no longer exists in the analyzed document."
    target_name = getattr(target, "Name", feature_internal_name)
    if str(target_name or "") != feature_internal_name:
        return "The resolved feature does not match the warning's internal feature name."

    selection = getattr(context.gui_module, "Selection", None)
    clear_selection = getattr(selection, "clearSelection", None)
    add_selection = getattr(selection, "addSelection", None)
    if not callable(clear_selection) or not callable(add_selection):
        return "FreeCAD selection services are unavailable."
    if not perform_selection:
        return None

    try:
        clear_selection()
        add_selection(context.document_name, feature_internal_name)
    except Exception as exc:
        return (
            f"FreeCAD could not select feature '{feature_internal_name}': "
            f"{type(exc).__name__}: {exc}"
        )
    return None


def _show_selection_error(qt_widgets: Any, parent: Any, message: str) -> None:
    message_box = getattr(qt_widgets, "QMessageBox", None)
    show_warning = getattr(message_box, "warning", None)
    if callable(show_warning):
        show_warning(parent, "Model Health Assistant", message)


def _format_warning_details(warning: WarningDisplayItem) -> str:
    """Format stored warning facts without inferring unavailable information."""

    severity = str(warning.severity or "").capitalize() or "Not available"
    raw_penalty = (
        str(warning.raw_score_penalty)
        if warning.raw_score_penalty is not None
        else "Not available"
    )
    return "\n".join(
        (
            f"Severity: {severity}",
            f"Rule ID: {warning.rule_id or 'Not available'}",
            f"Feature label: {warning.feature_label or 'Not available'}",
            (
                "Feature internal name: "
                + (warning.feature_internal_name or "Not available")
            ),
            "",
            "Warning:",
            warning.message or "Not available",
            "",
            "Technical reason:",
            warning.technical_reason or "Not available",
            "",
            "Evidence:",
            _format_evidence(warning.evidence),
            "",
            (
                "Raw score penalty (before rule caps and prevalence adjustments): "
                + raw_penalty
            ),
        )
    )


def _format_evidence(evidence: dict[str, Any] | None) -> str:
    if not evidence:
        return "Not available"
    try:
        return json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return "Structured evidence is available but cannot be displayed safely."


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


def _load_qt_user_role() -> Any | None:
    try:  # pragma: no cover - depends on FreeCAD's bundled Qt runtime.
        from PySide2 import QtCore  # type: ignore

        return QtCore.Qt.UserRole
    except Exception:  # pragma: no cover
        try:
            from PySide import QtCore  # type: ignore

            return QtCore.Qt.UserRole
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
