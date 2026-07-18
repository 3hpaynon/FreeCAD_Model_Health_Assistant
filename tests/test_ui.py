import json
import socket
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_health_assistant.presentation import AnalysisPresentation, WarningDisplayItem
from model_health_assistant import ui
from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning


def _presentation(
    document_name: str = "Demo",
    score: int = 90,
    report_path: Path | None = Path("reports/demo.json"),
    warning_items: tuple[WarningDisplayItem, ...] = (),
    total_warning_count: int = 3,
) -> AnalysisPresentation:
    return AnalysisPresentation(
        document_name=document_name,
        analysis_timestamp="2026-07-15T12:30:00+00:00",
        total_document_object_count=8,
        meaningful_modeling_feature_count=4,
        total_sketch_count=2,
        total_warning_count=total_warning_count,
        high_warning_count=1,
        medium_warning_count=1,
        low_warning_count=1,
        health_score=score,
        report_path=report_path,
        warning_items=warning_items,
    )


def _warning_item(
    warning_id: str,
    severity: str,
    *,
    feature_internal_name: str = "Feature001",
    feature_label: str | None = "Feature label",
    rule_id: str = "example_rule",
    message: str = "Example warning",
    technical_reason: str = "Example reason",
    evidence: dict | None = None,
    raw_score_penalty: int | None = None,
) -> WarningDisplayItem:
    return WarningDisplayItem(
        warning_id=warning_id,
        severity=severity,
        rule_id=rule_id,
        feature_internal_name=feature_internal_name,
        feature_label=feature_label,
        message=message,
        technical_reason=technical_reason,
        suggested_practice="Example practice",
        evidence=evidence,
        raw_score_penalty=raw_score_penalty,
    )


def test_show_results_dialog_is_safe_without_parent_or_qt(monkeypatch) -> None:
    monkeypatch.setattr(ui, "_results_dialog", None)
    monkeypatch.setattr(
        ui,
        "_create_results_dialog",
        lambda qt_widgets, parent: (_ for _ in ()).throw(
            AssertionError("dialog should not be created")
        ),
    )

    assert ui.show_results_dialog(_presentation(), gui_module=None) is False
    assert ui.show_results_dialog(_presentation(), gui_module=SimpleNamespace()) is False

    gui_with_parent = SimpleNamespace(getMainWindow=lambda: object())
    monkeypatch.setattr(ui, "_load_qt_widgets", lambda: None)
    assert ui.show_results_dialog(_presentation(), gui_module=gui_with_parent) is False


def test_show_results_dialog_reuses_and_updates_one_controller(monkeypatch) -> None:
    parent = object()
    qt_widgets = object()
    gui_module = SimpleNamespace(getMainWindow=lambda: parent)
    created = []

    class FakeController:
        def __init__(self) -> None:
            self.presentations = []
            self.show_count = 0

        def update_presentation(
            self,
            presentation,
            *,
            report=None,
            document_context=None,
            status_message="",
        ) -> None:
            self.presentations.append((presentation, status_message))

        def show(self) -> None:
            self.show_count += 1

    controller = FakeController()
    monkeypatch.setattr(ui, "_results_dialog", None)
    monkeypatch.setattr(ui, "_load_qt_widgets", lambda: qt_widgets)

    def create_dialog(received_qt, received_parent):
        created.append((received_qt, received_parent))
        return controller

    monkeypatch.setattr(ui, "_create_results_dialog", create_dialog)
    first = _presentation(document_name="First", score=95)
    second = _presentation(document_name="Second", score=80)

    assert ui.show_results_dialog(
        first,
        gui_module=gui_module,
        status_message="First status",
    ) is True
    assert ui.show_results_dialog(second, gui_module=gui_module) is True

    assert created == [(qt_widgets, parent)]
    assert controller.presentations == [(first, "First status"), (second, "")]
    assert controller.show_count == 2
    assert ui._results_dialog is controller

    ui._clear_results_dialog(controller)
    assert ui._results_dialog is None


def test_results_dialog_controller_formats_summary_and_reopens() -> None:
    class FakeLabel:
        def __init__(self) -> None:
            self.text = None

        def setText(self, text: str) -> None:  # noqa: N802 - mirrors Qt.
            self.text = text

    class FakeDialog:
        def __init__(self) -> None:
            self.show_count = 0
            self.raise_count = 0
            self.activate_count = 0

        def show(self) -> None:
            self.show_count += 1

        def raise_(self) -> None:
            self.raise_count += 1

        def activateWindow(self) -> None:  # noqa: N802 - mirrors Qt.
            self.activate_count += 1

    labels = {
        key: FakeLabel()
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    dialog = FakeDialog()
    controller = ui._ResultsDialogController(dialog, labels)

    controller.update_presentation(_presentation())
    controller.show()
    controller.show()

    assert labels["document"].text == "Demo"
    assert labels["score"].text == "90/100"
    assert labels["meaningful_features"].text == "4"
    assert labels["sketches"].text == "2"
    assert labels["warnings"].text == "3"
    assert labels["report"].text == str(Path("reports/demo.json"))
    assert dialog.show_count == 2
    assert dialog.raise_count == 2
    assert dialog.activate_count == 2


def test_summary_display_values_include_complete_existing_presentation_data() -> None:
    presentation = replace(
        _presentation(document_name="Demo", score=88, report_path=None),
        analysis_timestamp=None,
        total_document_object_count=12,
        meaningful_modeling_feature_count=7,
        total_sketch_count=3,
        total_warning_count=6,
        high_warning_count=2,
        medium_warning_count=3,
        low_warning_count=1,
    )

    assert ui._summary_display_values(presentation) == {
        "document": "Demo",
        "timestamp": "Not available",
        "score": "88/100",
        "document_objects": "12",
        "meaningful_features": "7",
        "sketches": "3",
        "warnings": "6",
        "high_warnings": "2",
        "medium_warnings": "3",
        "low_warnings": "1",
        "report": "Not available",
    }


def test_results_dialog_controller_marks_missing_report_path() -> None:
    values = {}
    labels = {
        key: SimpleNamespace(setText=lambda text, key=key: values.update({key: text}))
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    controller = ui._ResultsDialogController(SimpleNamespace(), labels)

    controller.update_presentation(_presentation(report_path=None))

    assert values["report"] == "Not available"


def test_results_dialog_status_replaces_and_clears_concise_messages() -> None:
    class FakeStatusLabel:
        def __init__(self) -> None:
            self.text = None
            self.visible = None

        def setText(self, text: str) -> None:  # noqa: N802 - mirrors Qt.
            self.text = text

        def setVisible(self, visible: bool) -> None:  # noqa: N802 - mirrors Qt.
            self.visible = visible

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    status_label = FakeStatusLabel()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        status_label=status_label,
    )

    controller.update_presentation(
        _presentation(report_path=None),
        status_message="Analysis completed, but the JSON report could not be written.",
    )
    assert status_label.text == (
        "Analysis completed, but the JSON report could not be written."
    )
    assert status_label.visible is True

    controller.update_presentation(_presentation(report_path=None))
    assert status_label.text == ""
    assert status_label.visible is False


def test_presentation_status_messages_cover_empty_and_partial_success() -> None:
    normal = _presentation(total_warning_count=2)
    empty_document = replace(
        normal,
        total_document_object_count=0,
        meaningful_modeling_feature_count=0,
        total_warning_count=0,
    )
    no_meaningful_features = replace(
        normal,
        total_document_object_count=3,
        meaningful_modeling_feature_count=0,
        total_warning_count=0,
    )
    no_warnings = replace(normal, total_warning_count=0)

    assert ui._presentation_status_message(normal) == ""
    assert ui._presentation_status_message(empty_document) == (
        "Analysis completed. The active document is empty."
    )
    assert ui._presentation_status_message(no_meaningful_features) == (
        "Analysis completed. No meaningful modeling features were found."
    )
    assert ui._presentation_status_message(no_warnings) == (
        "Analysis completed with no generated warnings. "
        "This does not guarantee that the model is error-free."
    )
    assert ui._presentation_status_message(
        empty_document,
        report_failure_message="Report writing failed.",
    ) == "Report writing failed."


def test_results_error_updates_existing_dialog_without_replacing_results(
    monkeypatch,
) -> None:
    statuses = []
    shown = []
    controller = SimpleNamespace(
        update_status=statuses.append,
        show=lambda: shown.append(True),
    )
    monkeypatch.setattr(ui, "_results_dialog", controller)

    assert ui._show_results_error("Analysis failed.", SimpleNamespace()) is True
    assert statuses == ["Analysis failed."]
    assert shown == [True]


def test_report_path_actions_revalidate_and_report_feedback(tmp_path) -> None:
    report_file = tmp_path / "model_health.json"
    report_file.write_text("{}", encoding="utf-8")
    messages = []
    errors = []
    opened_paths = []
    copied_paths = []

    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - mirrors Qt.
            self.enabled = enabled

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - mirrors FreeCAD.
            messages.append(message)

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    open_button = FakeButton()
    copy_button = FakeButton()
    context = ui._AnalyzedDocumentContext(
        object(),
        "Demo",
        SimpleNamespace(Console=FakeConsole),
        SimpleNamespace(),
    )
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        open_report_folder_button=open_button,
        copy_report_path_button=copy_button,
        open_report_folder_handler=lambda path: opened_paths.append(path),
        copy_report_path_handler=lambda path: copied_paths.append(path),
        report_action_error_handler=errors.append,
    )

    controller.update_presentation(
        _presentation(report_path=report_file),
        document_context=context,
    )

    complete_path = report_file.resolve()
    assert open_button.enabled is True
    assert copy_button.enabled is True
    assert controller.open_report_folder() is True
    assert controller.copy_report_path() is True
    assert opened_paths == [complete_path]
    assert copied_paths == [complete_path]
    assert messages[-2:] == [
        "Model Health Assistant: Opened report folder.\n",
        "Model Health Assistant: Copied report path to the clipboard.\n",
    ]

    report_file.unlink()
    assert controller.open_report_folder() is False
    assert open_button.enabled is False
    assert copy_button.enabled is False
    assert errors == [
        "The saved report file has been moved, deleted, or is unavailable."
    ]
    assert messages[-1] == (
        "Model Health Assistant: The saved report file has been moved, deleted, "
        "or is unavailable.\n"
    )


def test_local_report_helpers_open_only_folder_and_copy_complete_path(tmp_path) -> None:
    report_file = tmp_path / "nested" / "report.json"
    report_file.parent.mkdir()
    report_file.write_text("{}", encoding="utf-8")
    complete_path, error = ui._validated_report_path(report_file)
    assert error is None
    assert complete_path == report_file.resolve()

    opened_urls = []

    class FakeUrl:
        @staticmethod
        def fromLocalFile(path: str):  # noqa: N802 - mirrors Qt.
            return ("local-directory", path)

    qt_core = SimpleNamespace(QUrl=FakeUrl)
    qt_gui = SimpleNamespace(
        QDesktopServices=SimpleNamespace(
            openUrl=lambda url: opened_urls.append(url) or True
        )
    )
    assert ui._open_report_folder_with_qt(complete_path, qt_core, qt_gui) is None
    assert opened_urls == [("local-directory", str(report_file.parent.resolve()))]

    copied_text = []
    clipboard = SimpleNamespace(setText=copied_text.append)
    assert ui._copy_report_path_to_clipboard(complete_path, clipboard) is None
    assert copied_text == [str(report_file.resolve())]

    assert ui._validated_report_path(None) == (
        None,
        "No saved report path is available.",
    )


def test_refresh_analysis_runs_existing_pipeline_once_for_current_active_document(
    monkeypatch,
) -> None:
    previous_document = object()
    current_document = object()
    app_module = SimpleNamespace(ActiveDocument=current_document)
    gui_module = SimpleNamespace()
    context = ui._AnalyzedDocumentContext(
        previous_document,
        "Previous",
        app_module,
        gui_module,
    )
    calls = []

    def fake_run_analysis_command(*, app_module=None, gui_module=None):
        calls.append((app_module, gui_module, app_module.ActiveDocument))
        return _presentation(document_name="Current")

    monkeypatch.setattr(ui, "run_analysis_command", fake_run_analysis_command)
    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    controller = ui._ResultsDialogController(SimpleNamespace(), labels)
    controller.document_context = context

    assert controller.refresh_analysis(object()) is True
    assert calls == [(app_module, gui_module, current_document)]


def test_refresh_with_no_active_document_preserves_previous_results() -> None:
    messages = []
    values = {}

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - mirrors FreeCAD.
            messages.append(message)

    app_module = SimpleNamespace(ActiveDocument=None, Console=FakeConsole)
    context = ui._AnalyzedDocumentContext(
        object(),
        "Previous",
        app_module,
        SimpleNamespace(),
    )
    labels = {
        key: SimpleNamespace(
            setText=lambda text, key=key: values.update({key: text})
        )
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    controller = ui._ResultsDialogController(SimpleNamespace(), labels)
    controller.update_presentation(
        _presentation(document_name="Previous", score=77, report_path=None),
        document_context=context,
    )
    previous_values = dict(values)

    assert controller.refresh_analysis() is False
    assert values == previous_values
    assert controller.document_context is context
    assert messages == [
        "Model Health Assistant: No active FreeCAD document is open.\n"
    ]


def test_warning_table_is_read_only_and_selects_single_rows() -> None:
    calls = {}

    class FakeTable:
        def setColumnCount(self, count: int) -> None:  # noqa: N802 - mirrors Qt.
            calls["column_count"] = count

        def setHorizontalHeaderLabels(self, labels) -> None:  # noqa: N802
            calls["headers"] = labels

        def setSortingEnabled(self, enabled: bool) -> None:  # noqa: N802
            calls["sorting"] = enabled

        def setColumnWidth(self, column: int, width: int) -> None:  # noqa: N802
            calls.setdefault("column_widths", []).append((column, width))

        def horizontalHeader(self):  # noqa: N802 - mirrors Qt.
            return SimpleNamespace(
                setStretchLastSection=lambda enabled: calls.update(
                    {"stretch_last": enabled}
                )
            )

        def setEditTriggers(self, triggers) -> None:  # noqa: N802
            calls["edit_triggers"] = triggers

        def setSelectionBehavior(self, behavior) -> None:  # noqa: N802
            calls["selection_behavior"] = behavior

        def setSelectionMode(self, mode) -> None:  # noqa: N802
            calls["selection_mode"] = mode

    abstract_item_view = SimpleNamespace(
        NoEditTriggers="no-edit",
        SelectRows="rows",
        SingleSelection="single",
    )

    ui._configure_warning_table(
        SimpleNamespace(QAbstractItemView=abstract_item_view),
        FakeTable(),
    )

    assert calls == {
        "column_count": 4,
        "headers": ["Severity", "Feature", "Rule", "Warning"],
        "sorting": False,
        "column_widths": [(0, 85), (1, 170), (2, 190)],
        "stretch_last": True,
        "edit_triggers": "no-edit",
        "selection_behavior": "rows",
        "selection_mode": "single",
    }


def test_warning_table_replaces_rows_and_handles_fallbacks_and_empty_state() -> None:
    class FakeTableItem:
        def __init__(self, text: str) -> None:
            self.text = text
            self.data = {}

        def setData(self, role, value) -> None:  # noqa: N802 - mirrors Qt.
            self.data[role] = value

    class FakeTable:
        def __init__(self) -> None:
            self.rows = []

        def setRowCount(self, count: int) -> None:  # noqa: N802 - mirrors Qt.
            self.rows = [[None, None, None, None] for _ in range(count)]

        def setItem(self, row: int, column: int, item) -> None:  # noqa: N802
            self.rows[row][column] = item

    class FakeEmptyLabel:
        def __init__(self) -> None:
            self.text = ""
            self.visible = False

        def setText(self, text: str) -> None:  # noqa: N802 - mirrors Qt.
            self.text = text

        def setVisible(self, visible: bool) -> None:  # noqa: N802 - mirrors Qt.
            self.visible = visible

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    warning_table = FakeTable()
    empty_label = FakeEmptyLabel()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        warning_table=warning_table,
        empty_warning_label=empty_label,
        table_item_factory=FakeTableItem,
        feature_name_data_role=32,
    )
    first_items = (
        _warning_item(
            "HIGH-1",
            "high",
            feature_internal_name="Pocket001",
            feature_label="Drive pocket",
            rule_id="confirmed_error",
            message="Pocket failed.",
        ),
        _warning_item(
            "LOW-1",
            "low",
            feature_internal_name="Sketch001",
            feature_label=None,
            rule_id="unknown_support",
            message="Support is unknown.",
        ),
    )

    controller.update_presentation(
        _presentation(warning_items=first_items, total_warning_count=2)
    )

    assert [[item.text for item in row] for row in warning_table.rows] == [
        ["High", "Drive pocket", "confirmed_error", "Pocket failed."],
        ["Low", "Sketch001", "unknown_support", "Support is unknown."],
    ]
    assert warning_table.rows[0][1].data == {32: "Pocket001"}
    assert warning_table.rows[1][1].data == {32: "Sketch001"}
    assert empty_label.visible is False

    fallback_item = _warning_item(
        "UNKNOWN-1",
        "medium",
        feature_internal_name="",
        feature_label=None,
        rule_id="",
        message="Unmapped warning.",
    )
    controller.update_presentation(
        _presentation(warning_items=(fallback_item,), total_warning_count=1)
    )

    assert [[item.text for item in row] for row in warning_table.rows] == [
        ["Medium", "Unknown feature", "Unknown rule", "Unmapped warning."],
    ]
    assert warning_table.rows[0][1].data == {}

    controller.update_presentation(
        _presentation(warning_items=(), total_warning_count=0)
    )

    assert warning_table.rows == []
    assert empty_label.text == "No model-health warnings were generated."
    assert empty_label.visible is True


def test_warning_details_formatter_uses_readable_structured_data() -> None:
    warning = _warning_item(
        "HIGH-1",
        "high",
        feature_internal_name="Pocket001",
        feature_label="Drive pocket",
        rule_id="confirmed_error",
        message="Pocket failed.",
        technical_reason="FreeCAD reported an invalid shape.",
        evidence={
            "error_details": ["State: Invalid"],
            "has_confirmed_error": True,
        },
        raw_score_penalty=25,
    )

    text = ui._format_warning_details(warning)

    assert "Severity: High" in text
    assert "Rule ID: confirmed_error" in text
    assert "Feature label: Drive pocket" in text
    assert "Feature internal name: Pocket001" in text
    assert "Warning:\nPocket failed." in text
    assert "Technical reason:\nFreeCAD reported an invalid shape." in text
    assert '"error_details": [' in text
    assert '"State: Invalid"' in text
    assert '"has_confirmed_error": true' in text
    assert (
        "Raw score penalty (before rule caps and prevalence adjustments): 25"
        in text
    )
    assert "'error_details'" not in text


def test_warning_details_formatter_marks_missing_data_and_preserves_zero_penalty() -> None:
    missing = _warning_item(
        "MISSING-1",
        "",
        feature_internal_name="",
        feature_label=None,
        rule_id="",
        message="",
        technical_reason="",
        evidence=None,
        raw_score_penalty=None,
    )
    missing_text = ui._format_warning_details(missing)

    assert "Severity: Not available" in missing_text
    assert "Rule ID: Not available" in missing_text
    assert "Feature label: Not available" in missing_text
    assert "Feature internal name: Not available" in missing_text
    assert "Warning:\nNot available" in missing_text
    assert "Technical reason:\nNot available" in missing_text
    assert "Evidence:\nNot available" in missing_text
    assert missing_text.endswith("adjustments): Not available")

    zero_penalty = _warning_item("INFO-1", "low", raw_score_penalty=0)
    assert ui._format_warning_details(zero_penalty).endswith("adjustments): 0")


def test_warning_details_follow_selection_and_clear_on_update_or_no_selection() -> None:
    class FakeDetails:
        def __init__(self) -> None:
            self.text = None

        def setPlainText(self, text: str) -> None:  # noqa: N802 - mirrors Qt.
            self.text = text

    class FakeTableItem:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeTable:
        def __init__(self) -> None:
            self.rows = []
            self.selected_row = None

        def clearSelection(self) -> None:  # noqa: N802 - mirrors Qt.
            self.selected_row = None

        def setRowCount(self, count: int) -> None:  # noqa: N802 - mirrors Qt.
            self.rows = [[None, None, None, None] for _ in range(count)]

        def setItem(self, row: int, column: int, item) -> None:  # noqa: N802
            self.rows[row][column] = item

        def selectedItems(self):  # noqa: N802 - mirrors Qt.
            if self.selected_row is None:
                return []
            return self.rows[self.selected_row]

        def currentRow(self) -> int:  # noqa: N802 - mirrors Qt.
            return -1 if self.selected_row is None else self.selected_row

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    warning_table = FakeTable()
    warning_details = FakeDetails()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        warning_table=warning_table,
        table_item_factory=FakeTableItem,
        warning_details=warning_details,
    )
    warning_items = (
        _warning_item("HIGH-1", "high", message="First warning."),
        _warning_item("LOW-1", "low", message="Second warning."),
    )

    controller.update_presentation(
        _presentation(warning_items=warning_items, total_warning_count=2)
    )
    assert controller.warning_items == warning_items
    assert warning_details.text == ""

    warning_table.selected_row = 1
    controller.update_selected_warning_details()
    assert "Warning:\nSecond warning." in warning_details.text

    warning_table.selected_row = None
    controller.update_selected_warning_details()
    assert warning_details.text == ""

    warning_table.selected_row = 0
    controller.update_selected_warning_details()
    controller.update_presentation(_presentation(total_warning_count=0))
    assert controller.warning_items == ()
    assert warning_details.text == ""


def test_internal_name_selection_targets_original_document_without_model_access() -> None:
    events = []

    class GuardedTarget:
        @property
        def Label(self):  # noqa: N802 - must never be used for selection.
            raise AssertionError("visible label must not be read")

        @property
        def ViewObject(self):  # noqa: N802 - must never be modified.
            raise AssertionError("view state must not be read")

        def recompute(self):
            raise AssertionError("selection must not recompute")

    target = GuardedTarget()

    class AnalyzedDocument:
        def getObject(self, name: str):  # noqa: N802 - mirrors FreeCAD.
            events.append(("getObject", name))
            return target if name == "Pocket001" else None

    analyzed_document = AnalyzedDocument()
    other_document = object()
    app_module = SimpleNamespace(
        ActiveDocument=other_document,
        getDocument=lambda name: (
            events.append(("getDocument", name)) or analyzed_document
        ),
    )
    selection = SimpleNamespace(
        clearSelection=lambda: events.append(("clearSelection",)),
        addSelection=lambda document_name, feature_name: events.append(
            ("addSelection", document_name, feature_name)
        ),
    )
    context = ui._AnalyzedDocumentContext(
        document=analyzed_document,
        document_name="AnalyzedDoc",
        app_module=app_module,
        gui_module=SimpleNamespace(Selection=selection),
    )

    error = ui._select_feature_by_internal_name(
        context,
        "Pocket001",
        perform_selection=True,
    )

    assert error is None
    assert events == [
        ("getDocument", "AnalyzedDoc"),
        ("getObject", "Pocket001"),
        ("clearSelection",),
        ("addSelection", "AnalyzedDoc", "Pocket001"),
    ]


def test_internal_name_selection_rejects_closed_replaced_or_missing_targets_and_apis() -> None:
    selection_calls = []
    selection = SimpleNamespace(
        clearSelection=lambda: selection_calls.append("clear"),
        addSelection=lambda *_args: selection_calls.append("add"),
    )
    gui_module = SimpleNamespace(Selection=selection)
    original_document = SimpleNamespace(getObject=lambda _name: object())

    def context(app_module, document=original_document, gui=gui_module):
        return ui._AnalyzedDocumentContext(
            document=document,
            document_name="Demo",
            app_module=app_module,
            gui_module=gui,
        )

    assert "no affected feature internal name" in ui._select_feature_by_internal_name(
        context(SimpleNamespace(getDocument=lambda _name: original_document)),
        "",
        perform_selection=True,
    )
    assert "no longer open" in ui._select_feature_by_internal_name(
        context(SimpleNamespace(getDocument=lambda _name: None)),
        "Pad001",
        perform_selection=True,
    )
    assert "not the document that was analyzed" in ui._select_feature_by_internal_name(
        context(SimpleNamespace(getDocument=lambda _name: object())),
        "Pad001",
        perform_selection=True,
    )

    missing_lookup_document = object()
    assert "internal-name lookup" in ui._select_feature_by_internal_name(
        context(
            SimpleNamespace(getDocument=lambda _name: missing_lookup_document),
            document=missing_lookup_document,
        ),
        "Pad001",
        perform_selection=True,
    )
    missing_target_document = SimpleNamespace(getObject=lambda _name: None)
    assert "no longer exists" in ui._select_feature_by_internal_name(
        context(
            SimpleNamespace(getDocument=lambda _name: missing_target_document),
            document=missing_target_document,
        ),
        "Pad001",
        perform_selection=True,
    )
    mismatched_target_document = SimpleNamespace(
        getObject=lambda _name: SimpleNamespace(Name="DifferentFeature")
    )
    assert "does not match" in ui._select_feature_by_internal_name(
        context(
            SimpleNamespace(getDocument=lambda _name: mismatched_target_document),
            document=mismatched_target_document,
        ),
        "Pad001",
        perform_selection=True,
    )
    assert "cannot be resolved" in ui._select_feature_by_internal_name(
        context(SimpleNamespace()),
        "Pad001",
        perform_selection=True,
    )
    assert "selection services are unavailable" in ui._select_feature_by_internal_name(
        context(
            SimpleNamespace(getDocument=lambda _name: original_document),
            gui=SimpleNamespace(),
        ),
        "Pad001",
        perform_selection=True,
    )

    failing_selection = SimpleNamespace(
        clearSelection=lambda: selection_calls.append("failing-clear"),
        addSelection=lambda *_args: (_ for _ in ()).throw(RuntimeError("selection failed")),
    )
    assert "could not select" in ui._select_feature_by_internal_name(
        context(
            SimpleNamespace(getDocument=lambda _name: original_document),
            gui=SimpleNamespace(Selection=failing_selection),
        ),
        "Pad001",
        perform_selection=True,
    )
    assert selection_calls == ["failing-clear"]


def test_select_feature_button_tracks_selection_and_replaced_document_context() -> None:
    messages = []
    friendly_messages = []
    selection_calls = []

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - mirrors FreeCAD.
            messages.append(message)

    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - mirrors Qt.
            self.enabled = enabled

    class FakeItem:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeTable:
        def __init__(self) -> None:
            self.rows = []
            self.selected_row = None

        def clearSelection(self) -> None:  # noqa: N802 - mirrors Qt.
            self.selected_row = None

        def setRowCount(self, count: int) -> None:  # noqa: N802 - mirrors Qt.
            self.rows = [[None, None, None, None] for _ in range(count)]

        def setItem(self, row: int, column: int, item) -> None:  # noqa: N802
            self.rows[row][column] = item

        def selectedItems(self):  # noqa: N802 - mirrors Qt.
            return [] if self.selected_row is None else self.rows[self.selected_row]

        def currentRow(self) -> int:  # noqa: N802 - mirrors Qt.
            return -1 if self.selected_row is None else self.selected_row

    feature_a = object()
    feature_b = object()
    document_a = SimpleNamespace(
        getObject=lambda name: feature_a if name == "FeatureA" else None
    )
    document_b = SimpleNamespace(
        getObject=lambda name: feature_b if name == "FeatureB" else None
    )
    open_documents = {"DocA": document_a, "DocB": document_b}
    app_module = SimpleNamespace(
        Console=FakeConsole,
        getDocument=lambda name: open_documents.get(name),
    )
    selection = SimpleNamespace(
        clearSelection=lambda: selection_calls.append("clear"),
        addSelection=lambda document_name, feature_name: selection_calls.append(
            (document_name, feature_name)
        ),
    )
    gui_module = SimpleNamespace(Selection=selection)
    context_a = ui._AnalyzedDocumentContext(
        document_a,
        "DocA",
        app_module,
        gui_module,
    )
    context_b = ui._AnalyzedDocumentContext(
        document_b,
        "DocB",
        app_module,
        gui_module,
    )
    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    table = FakeTable()
    button = FakeButton()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        warning_table=table,
        table_item_factory=FakeItem,
        select_feature_button=button,
        selection_error_handler=friendly_messages.append,
    )
    warning_a = _warning_item(
        "A",
        "medium",
        feature_internal_name="FeatureA",
        feature_label="Renamed visible label",
    )
    warning_b = _warning_item(
        "B",
        "high",
        feature_internal_name="FeatureB",
        feature_label="Another visible label",
    )

    controller.update_presentation(
        _presentation(warning_items=(warning_a,), total_warning_count=1),
        document_context=context_a,
    )
    assert button.enabled is False
    table.selected_row = 0
    controller.update_selected_warning_details()
    assert button.enabled is True
    assert selection_calls == []
    assert controller.select_selected_feature() is True
    assert selection_calls[-2:] == ["clear", ("DocA", "FeatureA")]

    controller.update_presentation(
        _presentation(warning_items=(warning_b,), total_warning_count=1),
        document_context=context_b,
    )
    assert controller.document_context is context_b
    assert button.enabled is False
    table.selected_row = 0
    controller.update_selected_warning_details()
    assert button.enabled is True
    assert controller.select_selected_feature() is True
    assert selection_calls[-2:] == ["clear", ("DocB", "FeatureB")]

    del open_documents["DocB"]
    assert controller.select_selected_feature() is False
    assert button.enabled is False
    assert messages[-1] == (
        "Model Health Assistant: The analyzed document is no longer open.\n"
    )
    assert friendly_messages == ["The analyzed document is no longer open."]


def test_warning_table_double_click_reuses_selection_action() -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self.callback = None

        def connect(self, callback) -> None:
            self.callback = callback

        def emit(self, *args) -> None:
            assert self.callback is not None
            self.callback(*args)

    table = SimpleNamespace(
        itemSelectionChanged=FakeSignal(),
        itemDoubleClicked=FakeSignal(),
    )
    calls = []
    controller = SimpleNamespace(
        update_selected_warning_details=lambda: calls.append("details"),
        select_selected_feature=lambda *_args: calls.append("select"),
    )

    ui._connect_warning_table_actions(table, controller)
    table.itemSelectionChanged.emit()
    assert calls == ["details"]

    table.itemDoubleClicked.emit(object())
    assert calls == ["details", "select"]


def test_selected_warning_explanation_action_requires_selection_provider_and_idle_state() -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None
            self.tooltip = ""

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

        def setToolTip(self, text: str) -> None:  # noqa: N802
            self.tooltip = text

    class FakeItem:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeTable:
        def __init__(self) -> None:
            self.rows = []
            self.selected_row = None

        def clearSelection(self) -> None:  # noqa: N802
            self.selected_row = None

        def setRowCount(self, count: int) -> None:  # noqa: N802
            self.rows = [[None, None, None, None] for _ in range(count)]

        def setItem(self, row, column, item) -> None:  # noqa: N802
            self.rows[row][column] = item

        def selectedItems(self):  # noqa: N802
            return [] if self.selected_row is None else self.rows[self.selected_row]

        def currentRow(self) -> int:  # noqa: N802
            return -1 if self.selected_row is None else self.selected_row

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    status_values = {}
    status_label = SimpleNamespace(
        setText=lambda text: status_values.update(text=text),
        setVisible=lambda visible: status_values.update(visible=visible),
    )
    table = FakeTable()
    button = FakeButton()
    calls = []
    ready = SimpleNamespace(available=True, message="Provider ready.")
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        warning_table=table,
        table_item_factory=FakeItem,
        status_label=status_label,
        explain_selected_warning_button=button,
        explain_selected_warning_handler=lambda active: calls.append(active),
        ai_workflow_status_handler=lambda _active: ready,
    )
    warning = _warning_item("WARN-1", "high")
    report = _ai_preview_report()

    controller.update_presentation(
        _presentation(warning_items=(warning,), total_warning_count=1),
        report=report,
    )
    assert button.enabled is False
    assert button.tooltip == "Select one warning first."

    table.selected_row = 0
    controller.update_selected_warning_details()
    assert button.enabled is True
    assert controller.explain_selected_warning() is True
    assert calls == [controller]
    assert "No data was transmitted" in status_values["text"]

    assert controller.begin_ai_operation("Request in progress") is True
    assert button.enabled is False
    assert controller.begin_ai_operation("Duplicate") is False
    controller.complete_ai_operation("Done")
    assert button.enabled is True

    ready.available = False
    ready.message = "API key missing."
    controller.update_selected_warning_details()
    assert button.enabled is False
    assert "API key missing" in button.tooltip
    assert controller.explain_selected_warning() is False
    assert "API key missing" in status_values["text"]


def test_explanation_error_messages_distinguish_provider_failures_without_secrets() -> None:
    from model_health_assistant.ai_provider import (
        AIAuthenticationError,
        AIConnectionError,
        AIInvalidResponseError,
        AIRateLimitError,
        AIRequestTimeoutError,
    )

    messages = [
        ui._explanation_error_text(AIAuthenticationError("sk-secret-value")),
        ui._explanation_error_text(AIRateLimitError("provider details")),
        ui._explanation_error_text(AIRequestTimeoutError("provider details")),
        ui._explanation_error_text(AIConnectionError("provider details")),
        ui._explanation_error_text(AIInvalidResponseError("provider details")),
    ]

    assert "rejected the configured API key" in messages[0]
    assert "rate limit" in messages[1]
    assert "timed out" in messages[2]
    assert "could not be reached" in messages[3]
    assert "invalid structured output" in messages[4]
    assert all("sk-secret-value" not in message for message in messages)


def test_explanation_failure_logs_only_redacted_metadata_to_report_view() -> None:
    from model_health_assistant.ai_provider import AIAuthenticationError

    report_messages = []
    status_values = {}
    app_module = SimpleNamespace(
        Console=SimpleNamespace(PrintMessage=report_messages.append)
    )
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        status_label=SimpleNamespace(
            setText=lambda text: status_values.update(text=text),
            setVisible=lambda visible: status_values.update(visible=visible),
        ),
    )
    controller.document_context = SimpleNamespace(app_module=app_module)
    assert controller.begin_ai_operation(
        "Running",
        request_type="full-analysis",
        provider_identifier="openai",
    ) is True
    ticket = controller.active_ai_ticket

    ui._explanation_failed(
        controller,
        AIAuthenticationError(
            r"sk-private-secret Authorization: Bearer private "
            r"C:\Users\private\model.FCStd"
        ),
        ticket=ticket,
        request_type="full-analysis",
        provider_identifier="openai",
        model_identifier="gpt-5.6-terra",
    )

    assert "rejected the configured API key" in status_values["text"]
    diagnostic = "".join(report_messages)
    assert "category=invalid_authentication" in diagnostic
    assert "request_type=full-analysis" in diagnostic
    assert "provider=openai" in diagnostic
    assert "model=gpt-5.6-terra" in diagnostic
    assert "private" not in diagnostic
    assert "Authorization" not in diagnostic
    assert "Users" not in diagnostic


def test_full_analysis_action_requires_report_provider_and_idle_not_warning_selection() -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None
            self.tooltip = ""

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

        def setToolTip(self, text: str) -> None:  # noqa: N802
            self.tooltip = text

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    messages = {}
    status_label = SimpleNamespace(
        setText=lambda text: messages.update(text=text),
        setVisible=lambda visible: messages.update(visible=visible),
    )
    full_button = FakeButton()
    selected_button = FakeButton()
    calls = []
    readiness = SimpleNamespace(available=True, message="Provider ready.")
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        status_label=status_label,
        explain_selected_warning_button=selected_button,
        explain_selected_warning_handler=lambda _active: None,
        explain_full_analysis_button=full_button,
        explain_full_analysis_handler=lambda active: calls.append(active),
        ai_workflow_status_handler=lambda _active: readiness,
    )

    controller.update_presentation(
        _presentation(warning_items=(), total_warning_count=0),
        report=_ai_preview_report(),
    )
    assert selected_button.enabled is False
    assert full_button.enabled is True
    assert controller.explain_full_analysis() is True
    assert calls == [controller]
    assert "fresh final review" in messages["text"]
    assert "No data was transmitted" in messages["text"]

    assert controller.begin_ai_operation("Running") is True
    assert selected_button.enabled is False
    assert full_button.enabled is False
    controller.complete_ai_operation("Done")
    assert full_button.enabled is True

    readiness.available = False
    readiness.message = "Provider unavailable."
    controller._refresh_ai_action_state()
    assert full_button.enabled is False
    assert controller.explain_full_analysis() is False
    assert "Provider unavailable" in messages["text"]


def test_results_request_cancel_rejects_late_and_older_callbacks(monkeypatch) -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.enabled = False

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

    class FakeHandle:
        def __init__(self) -> None:
            self.cancelled = False
            self.detached = False

        def cancel(self) -> bool:
            self.cancelled = True
            return True

        def detach(self) -> bool:
            self.detached = True
            return True

    messages = {}
    status_label = SimpleNamespace(
        setText=lambda text: messages.update(text=text),
        setVisible=lambda visible: messages.update(visible=visible),
    )
    cancel_button = FakeButton()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        status_label=status_label,
        cancel_ai_request_button=cancel_button,
    )
    launched = []

    def fake_launcher(_qt_core, _parent, operation, success, failure):
        handle = FakeHandle()
        launched.append((operation, success, failure, handle))
        return handle

    monkeypatch.setattr(
        "model_health_assistant.qt_async.start_qt_background_operation",
        fake_launcher,
    )
    delivered = []
    first_plan = SimpleNamespace(
        request_type="full-analysis",
        provider_identifier="openai",
        execute=lambda: "first",
    )
    assert ui._launch_prepared_ai_execution(
        controller,
        first_plan,
        object(),
        object(),
        progress_message="First request running.",
        on_success=lambda result, ticket: delivered.append((result, ticket)),
    ) is None
    first_ticket = controller.active_ai_ticket
    assert type(first_ticket) is int
    assert cancel_button.enabled is True

    assert controller.cancel_ai_operation() is True
    cancelled_status = messages["text"]
    assert launched[0][3].cancelled is True
    assert cancel_button.enabled is False
    launched[0][1]("late first result")
    launched[0][2](RuntimeError("late first failure"))
    assert delivered == []
    assert messages["text"] == cancelled_status

    second_plan = SimpleNamespace(
        request_type="custom-question",
        provider_identifier="local-mock",
        execute=lambda: "second",
    )
    assert ui._launch_prepared_ai_execution(
        controller,
        second_plan,
        object(),
        object(),
        progress_message="Second request running.",
        on_success=lambda result, ticket: delivered.append((result, ticket)),
    ) is None
    second_ticket = controller.active_ai_ticket
    assert second_ticket != first_ticket
    launched[0][1]("older result after newer request")
    launched[1][1]("accepted second result")

    assert delivered == [("accepted second result", second_ticket)]


def test_results_request_detach_and_destroy_are_callback_safe() -> None:
    class FakeHandle:
        def __init__(self) -> None:
            self.detached = False

        def detach(self) -> bool:
            self.detached = True
            return True

    controller = ui._ResultsDialogController(SimpleNamespace(), {})
    assert controller.begin_ai_operation("Running") is True
    ticket = controller.active_ai_ticket
    handle = FakeHandle()
    assert controller.attach_ai_operation(ticket, handle) is True

    controller.detach_ai_operation()

    assert handle.detached is True
    assert controller.is_ai_operation_current(ticket) is False
    assert controller.begin_ai_operation("New request") is True
    controller.close_ai_request_state()
    assert controller.begin_ai_operation("After destruction") is False


def _ai_preview_report() -> ModelHealthReport:
    feature = FeatureInfo(
        internal_name="Pocket001",
        label="Drive pocket",
        type_id="PartDesign::Pocket",
        category="subtractive_feature",
        has_confirmed_error=True,
        error_details=[
            r"Error log: C:\Users\private-user\AppData\Local\FreeCAD\trace.txt"
        ],
    )
    warning = RiskWarning(
        warning_id="WARN-001",
        severity="high",
        affected_feature_name="Pocket001",
        message="Pocket001 reports an error.",
        technical_reason="FreeCAD exposed an explicit error state.",
        suggested_practice="Review the feature manually.",
        rule_id="confirmed_recompute_error",
    )
    return ModelHealthReport(
        document_name=r"C:\Users\private-user\CAD Models\private.FCStd",
        generated_at="2026-07-16T12:30:00+00:00",
        health_score=70,
        features=[feature],
        warnings=[warning],
        metadata={"report_path": r"C:\Users\private-user\reports\health.json"},
    )


def test_ai_context_preview_json_is_allowlisted_readable_and_local(monkeypatch) -> None:
    from model_health_assistant import extractor

    monkeypatch.setattr(
        extractor,
        "extract_document_features",
        lambda _document: (_ for _ in ()).throw(
            AssertionError("preview must not rerun extraction")
        ),
    )

    preview_json = ui._build_ai_context_preview_json(_ai_preview_report())
    data = json.loads(preview_json)

    context = data["privacy_safe_context"]
    assert data["provider"]["identifier"] == "local-mock"
    assert data["request_type"] == "full-analysis"
    assert data["context_schema_version"] == "1.0"
    assert context["health_score"] == 70
    assert context["total_warning_count"] == 1
    assert context["warnings"][0]["rule_id"] == "confirmed_recompute_error"
    assert context["warnings"][0]["severity"] == "high"
    assert context["metadata"]["limit_policy_version"] == "context-limits-v1"
    assert data["provider_request"]["context"] == context
    assert "C:\\Users" not in preview_json
    assert "private-user" not in preview_json
    assert "report_path" not in preview_json
    assert ".FCStd" not in preview_json


def test_results_controller_previews_exact_retained_report() -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

    labels = {
        key: SimpleNamespace(setText=lambda _text: None)
        for key in (
            "document",
            "score",
            "meaningful_features",
            "sketches",
            "warnings",
            "report",
        )
    }
    preview_button = FakeButton()
    received_reports = []
    report = _ai_preview_report()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        labels,
        preview_ai_context_button=preview_button,
        preview_ai_context_handler=lambda received: received_reports.append(received),
    )

    controller.update_presentation(
        _presentation(report_path=None),
        report=report,
    )

    assert controller.report is report
    assert preview_button.enabled is True
    assert controller.preview_ai_context() is True
    assert received_reports == [report]


def test_context_preview_requires_completed_report() -> None:
    statuses = []
    button_states = []
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        preview_ai_context_button=SimpleNamespace(
            setEnabled=lambda enabled: button_states.append(enabled)
        ),
        preview_ai_context_handler=lambda _report: None,
        status_label=SimpleNamespace(
            setText=statuses.append,
            setVisible=lambda _visible: None,
        ),
    )

    assert controller.preview_ai_context() is False
    assert button_states == [False]
    assert statuses[-1] == (
        "AI context preview failed: No completed report is available."
    )


def test_context_preview_reuses_one_dialog_controller(monkeypatch) -> None:
    created = []

    class FakePreviewController:
        def __init__(self) -> None:
            self.texts = []
            self.show_count = 0

        def update_text(self, text: str) -> None:
            self.texts.append(text)

        def update_review(self, review, _handler) -> None:
            self.texts.append(review.preview_json())

        def show(self) -> None:
            self.show_count += 1

    preview_controller = FakePreviewController()
    monkeypatch.setattr(ui, "_ai_context_preview_dialog", None)
    monkeypatch.setattr(
        ui,
        "_create_ai_context_preview_dialog",
        lambda qt_widgets, parent: (
            created.append((qt_widgets, parent)) or preview_controller
        ),
    )
    report = _ai_preview_report()

    assert ui._show_ai_context_preview(report, "qt", "parent") is None
    assert ui._show_ai_context_preview(report, "qt", "parent") is None

    assert created == [("qt", "parent")]
    assert preview_controller.show_count == 2
    assert len(preview_controller.texts) == 2
    assert preview_controller.texts[0] == preview_controller.texts[1]
    ui._clear_ai_context_preview_dialog(preview_controller)
    assert ui._ai_context_preview_dialog is None


def test_context_preview_reports_builder_failure_without_opening_dialog(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ui,
        "_build_ai_context_preview_review",
        lambda _report: (_ for _ in ()).throw(ValueError("malformed evidence")),
    )
    monkeypatch.setattr(ui, "_ai_context_preview_dialog", None)
    monkeypatch.setattr(
        ui,
        "_create_ai_context_preview_dialog",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("failed context must not open a preview")
        ),
    )

    error = ui._show_ai_context_preview(_ai_preview_report(), "qt", "parent")

    assert error == (
        "Could not build the privacy-safe context: ValueError: malformed evidence"
    )


def test_ai_context_preview_dialog_is_parented_read_only_and_closable() -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

    class FakeDialog:
        def __init__(self, parent) -> None:
            self.parent = parent
            self.destroyed = FakeSignal()
            self.title = ""
            self.modal = None
            self.minimum_size = None
            self.show_count = 0

        def setWindowTitle(self, title: str) -> None:  # noqa: N802
            self.title = title

        def setModal(self, modal: bool) -> None:  # noqa: N802
            self.modal = modal

        def setMinimumSize(self, width: int, height: int) -> None:  # noqa: N802
            self.minimum_size = (width, height)

        def show(self) -> None:
            self.show_count += 1

        def close(self) -> None:
            pass

    class FakeLayout:
        def __init__(self, *_args) -> None:
            pass

        def addWidget(self, _widget) -> None:  # noqa: N802
            pass

        def addLayout(self, _layout) -> None:  # noqa: N802
            pass

        def addStretch(self) -> None:  # noqa: N802
            pass

    class FakeLabel:
        def __init__(self, text: str, parent) -> None:
            self.text = text
            self.parent = parent
            self.word_wrap = False

        def setWordWrap(self, enabled: bool) -> None:  # noqa: N802
            self.word_wrap = enabled

    class FakeTextView:
        def __init__(self, parent) -> None:
            self.parent = parent
            self.read_only = False
            self.text = ""

        def setReadOnly(self, enabled: bool) -> None:  # noqa: N802
            self.read_only = enabled

        def setPlainText(self, text: str) -> None:  # noqa: N802
            self.text = text

    class FakeButton:
        def __init__(self, text: str, parent) -> None:
            self.text = text
            self.parent = parent
            self.clicked = FakeSignal()
            self.shortcut = None
            self.enabled = True

        def setShortcut(self, shortcut: str) -> None:  # noqa: N802
            self.shortcut = shortcut

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

    labels = []
    text_views = []
    buttons = []
    qt_widgets = SimpleNamespace(
        QDialog=FakeDialog,
        QVBoxLayout=FakeLayout,
        QHBoxLayout=FakeLayout,
        QLabel=lambda text, parent: labels.append(FakeLabel(text, parent)) or labels[-1],
        QPlainTextEdit=lambda parent: text_views.append(FakeTextView(parent)) or text_views[-1],
        QPushButton=lambda text, parent: buttons.append(FakeButton(text, parent)) or buttons[-1],
    )
    parent = object()

    controller = ui._create_ai_context_preview_dialog(qt_widgets, parent)
    controller.update_text('{"schema_version": "1.0"}')

    assert controller.dialog.parent is parent
    assert controller.dialog.title == "Preview AI Context — Local Only"
    assert controller.dialog.modal is False
    assert labels[0].text == ui.AI_CONTEXT_PREVIEW_NOTICE
    assert labels[0].word_wrap is True
    assert text_views[0].read_only is True
    assert text_views[0].text == '{"schema_version": "1.0"}'
    assert buttons[0].text == "Cancel"
    assert buttons[0].shortcut == "Esc"
    assert buttons[1].text == "Send to Provider"
    assert buttons[1].enabled is False


def test_ai_context_preview_performs_no_network_access(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    preview_json = ui._build_ai_context_preview_json(_ai_preview_report())

    assert json.loads(preview_json)["context_schema_version"] == "1.0"


def test_request_review_dialog_requires_explicit_send_or_cancel() -> None:
    closed = []
    displayed = []
    statuses = []
    enabled = []
    controller = ui._AIRequestReviewDialogController(
        SimpleNamespace(close=lambda: closed.append(True)),
        SimpleNamespace(setPlainText=displayed.append),
        SimpleNamespace(setText=statuses.append),
        SimpleNamespace(setEnabled=enabled.append),
    )
    review = ui._build_ai_context_preview_review(_ai_preview_report())
    sends = []
    controller.update_review(review, lambda consent: sends.append(consent) or None)

    assert controller.consent.approved is False
    assert displayed[-1] == review.preview_json()
    assert enabled[-1] is True
    assert controller.send_to_provider() is True
    assert len(sends) == 1
    assert sends[0].approved is True
    assert closed == [True]

    controller.update_review(review, lambda _consent: None)
    controller.cancel()
    assert controller.consent.approved is False
    assert closed == [True, True]


def test_results_controller_runs_mock_for_exact_retained_report() -> None:
    class FakeButton:
        def __init__(self) -> None:
            self.enabled = None

        def setEnabled(self, enabled: bool) -> None:  # noqa: N802
            self.enabled = enabled

    button = FakeButton()
    received_reports = []
    report = _ai_preview_report()
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        test_mock_explanation_button=button,
        test_mock_explanation_handler=lambda received: received_reports.append(
            received
        ),
    )

    controller.update_presentation(_presentation(report_path=None), report=report)

    assert controller.report is report
    assert button.enabled is True
    assert controller.test_mock_explanation() is True
    assert received_reports == [report]


def test_mock_explanation_requires_completed_report() -> None:
    statuses = []
    button_states = []
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        test_mock_explanation_button=SimpleNamespace(
            setEnabled=lambda enabled: button_states.append(enabled)
        ),
        test_mock_explanation_handler=lambda _report: None,
        status_label=SimpleNamespace(
            setText=statuses.append,
            setVisible=lambda _visible: None,
        ),
    )

    assert controller.test_mock_explanation() is False
    assert button_states == [False]
    assert statuses[-1] == (
        "Mock explanation failed: No completed report is available."
    )


def test_mock_explanation_uses_validated_local_service_without_mutation_or_network(
    monkeypatch,
) -> None:
    from model_health_assistant import extractor

    class FakeExplanationController:
        def __init__(self) -> None:
            self.texts = []
            self.show_count = 0

        def update_text(self, text: str) -> None:
            self.texts.append(text)

        def show(self) -> None:
            self.show_count += 1

    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )
    monkeypatch.setattr(
        extractor,
        "extract_document_features",
        lambda _document: (_ for _ in ()).throw(
            AssertionError("mock explanation must not rerun extraction")
        ),
    )
    controller = FakeExplanationController()
    created = []
    monkeypatch.setattr(ui, "_mock_explanation_dialog", None)
    monkeypatch.setattr(
        ui,
        "_create_mock_explanation_dialog",
        lambda qt_widgets, parent: (
            created.append((qt_widgets, parent)) or controller
        ),
    )
    report = _ai_preview_report()
    report_before = report.to_dict()

    assert ui._show_mock_explanation(report, "qt", "parent") is None
    assert ui._show_mock_explanation(report, "qt", "parent") is None

    assert created == [("qt", "parent")]
    assert controller.show_count == 2
    assert len(controller.texts) == 2
    explanation = controller.texts[-1]
    assert "Provider: Local Mock Provider (local-mock)" in explanation
    assert "Summary:" in explanation
    assert "Rule: confirmed_recompute_error" in explanation
    assert "Feature: Pocket001" in explanation
    assert "Severity: high" in explanation
    assert "Explanation:" in explanation
    assert "Manual review steps:" in explanation
    assert "Limitations:" in explanation
    assert report.to_dict() == report_before
    assert len(report.warnings) == 1
    ui._clear_mock_explanation_dialog(controller)
    assert ui._mock_explanation_dialog is None


def test_mock_explanation_formats_zero_warning_report(monkeypatch) -> None:
    class FakeExplanationController:
        def __init__(self) -> None:
            self.text = ""

        def update_text(self, text: str) -> None:
            self.text = text

        def show(self) -> None:
            pass

    controller = FakeExplanationController()
    monkeypatch.setattr(ui, "_mock_explanation_dialog", controller)
    report = ModelHealthReport(
        document_name="Empty",
        generated_at="2026-07-16T12:30:00+00:00",
        health_score=100,
    )

    assert ui._show_mock_explanation(report, "qt", "parent") is None
    assert "Provider: Local Mock Provider (local-mock)" in controller.text
    assert "No source warnings were available to explain." in controller.text
    assert "Overall limitations:" in controller.text


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            "unavailable",
            "The deterministic local test provider is unavailable.",
        ),
        (
            "invalid",
            "The deterministic local provider returned an invalid structured "
            "response. The result was not displayed.",
        ),
        (
            "context",
            "The privacy-safe context could not be built. No explanation was "
            "displayed.",
        ),
        (
            "request",
            "The provider-neutral explanation request could not be built. No "
            "explanation was displayed.",
        ),
    ],
)
def test_mock_explanation_maps_controlled_failures_without_opening_dialog(
    monkeypatch,
    error: str,
    expected_message: str,
) -> None:
    from model_health_assistant.ai_provider import (
        AIInvalidResponseError,
        AIProviderUnavailableError,
    )
    from model_health_assistant.ai_service import (
        AIContextBuildError,
        AIExplanationService,
        AIRequestBuildError,
    )

    errors = {
        "unavailable": AIProviderUnavailableError("unavailable"),
        "invalid": AIInvalidResponseError("invalid response"),
        "context": AIContextBuildError("context failed"),
        "request": AIRequestBuildError("request failed"),
    }
    monkeypatch.setattr(
        AIExplanationService,
        "explain_report",
        lambda self, report: (_ for _ in ()).throw(errors[error]),
    )
    monkeypatch.setattr(ui, "_mock_explanation_dialog", None)
    monkeypatch.setattr(
        ui,
        "_create_mock_explanation_dialog",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid output must not open an explanation dialog")
        ),
    )

    result = ui._show_mock_explanation(_ai_preview_report(), "qt", "parent")

    assert result == expected_message


def test_ai_actions_disclose_closed_or_changed_analyzed_document_snapshot() -> None:
    analyzed_document = object()

    def context(app_module) -> ui._AnalyzedDocumentContext:
        return ui._AnalyzedDocumentContext(
            document=analyzed_document,
            document_name="AnalyzedDoc",
            app_module=app_module,
            gui_module=SimpleNamespace(),
        )

    closed = context(
        SimpleNamespace(
            ActiveDocument=None,
            getDocument=lambda _name: None,
        )
    )
    changed = context(
        SimpleNamespace(
            ActiveDocument=object(),
            getDocument=lambda _name: analyzed_document,
        )
    )
    replaced = context(
        SimpleNamespace(
            ActiveDocument=object(),
            getDocument=lambda _name: object(),
        )
    )

    assert "no longer open" in ui._retained_analysis_state_notice(closed)
    assert "active document has changed" in ui._retained_analysis_state_notice(changed)
    assert "different document" in ui._retained_analysis_state_notice(replaced)

    statuses = []
    controller = ui._ResultsDialogController(
        SimpleNamespace(),
        {},
        preview_ai_context_handler=lambda _report: None,
        test_mock_explanation_handler=lambda _report: None,
        status_label=SimpleNamespace(
            setText=statuses.append,
            setVisible=lambda _visible: None,
        ),
    )
    controller.update_presentation(
        _presentation(report_path=None),
        report=_ai_preview_report(),
        document_context=closed,
    )

    assert controller.preview_ai_context() is True
    assert "no longer open" in statuses[-1]
    assert "retained completed analysis snapshot" in statuses[-1]
    assert controller.test_mock_explanation() is True
    assert "no longer open" in statuses[-1]


def test_mock_explanation_dialog_is_parented_read_only_and_closable() -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

    class FakeDialog:
        def __init__(self, parent) -> None:
            self.parent = parent
            self.destroyed = FakeSignal()
            self.title = ""
            self.modal = None
            self.minimum_size = None

        def setWindowTitle(self, title: str) -> None:  # noqa: N802
            self.title = title

        def setModal(self, modal: bool) -> None:  # noqa: N802
            self.modal = modal

        def setMinimumSize(self, width: int, height: int) -> None:  # noqa: N802
            self.minimum_size = (width, height)

        def close(self) -> None:
            pass

    class FakeLayout:
        def __init__(self, *_args) -> None:
            pass

        def addWidget(self, _widget) -> None:  # noqa: N802
            pass

        def addLayout(self, _layout) -> None:  # noqa: N802
            pass

        def addStretch(self) -> None:  # noqa: N802
            pass

    class FakeLabel:
        def __init__(self, text: str, parent) -> None:
            self.text = text
            self.parent = parent
            self.word_wrap = False

        def setWordWrap(self, enabled: bool) -> None:  # noqa: N802
            self.word_wrap = enabled

    class FakeTextView:
        def __init__(self, parent) -> None:
            self.parent = parent
            self.read_only = False
            self.text = ""

        def setReadOnly(self, enabled: bool) -> None:  # noqa: N802
            self.read_only = enabled

        def setPlainText(self, text: str) -> None:  # noqa: N802
            self.text = text

    class FakeButton:
        def __init__(self, text: str, parent) -> None:
            self.text = text
            self.parent = parent
            self.clicked = FakeSignal()
            self.shortcut = None

        def setShortcut(self, shortcut: str) -> None:  # noqa: N802
            self.shortcut = shortcut

    labels = []
    text_views = []
    buttons = []
    qt_widgets = SimpleNamespace(
        QDialog=FakeDialog,
        QVBoxLayout=FakeLayout,
        QHBoxLayout=FakeLayout,
        QLabel=lambda text, parent: labels.append(FakeLabel(text, parent))
        or labels[-1],
        QPlainTextEdit=lambda parent: text_views.append(FakeTextView(parent))
        or text_views[-1],
        QPushButton=lambda text, parent: buttons.append(FakeButton(text, parent))
        or buttons[-1],
    )
    parent = object()

    controller = ui._create_mock_explanation_dialog(qt_widgets, parent)
    controller.update_text("Local mock result")

    assert controller.dialog.parent is parent
    assert controller.dialog.title == "Mock Explanation - Local Test Provider"
    assert controller.dialog.modal is False
    assert labels[0].text == ui.MOCK_EXPLANATION_NOTICE
    assert labels[0].word_wrap is True
    assert text_views[0].read_only is True
    assert text_views[0].text == "Local mock result"
    assert buttons[0].text == "Close"
    assert buttons[0].shortcut == "Esc"
