from pathlib import Path
from types import SimpleNamespace

from model_health_assistant.presentation import AnalysisPresentation
from model_health_assistant import ui


def _presentation(
    document_name: str = "Demo",
    score: int = 90,
    report_path: Path | None = Path("reports/demo.json"),
) -> AnalysisPresentation:
    return AnalysisPresentation(
        document_name=document_name,
        analysis_timestamp="2026-07-15T12:30:00+00:00",
        total_document_object_count=8,
        meaningful_modeling_feature_count=4,
        total_sketch_count=2,
        total_warning_count=3,
        high_warning_count=1,
        medium_warning_count=1,
        low_warning_count=1,
        health_score=score,
        report_path=report_path,
        warning_items=(),
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

        def update_presentation(self, presentation) -> None:
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
