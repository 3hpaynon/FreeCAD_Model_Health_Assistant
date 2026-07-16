from pathlib import Path
from types import SimpleNamespace

from model_health_assistant.presentation import AnalysisPresentation, WarningDisplayItem
from model_health_assistant import ui


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

        def update_presentation(self, presentation, *, document_context=None) -> None:
            self.presentations.append(presentation)

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

    assert ui.show_results_dialog(first, gui_module=gui_module) is True
    assert ui.show_results_dialog(second, gui_module=gui_module) is True

    assert created == [(qt_widgets, parent)]
    assert controller.presentations == [first, second]
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
