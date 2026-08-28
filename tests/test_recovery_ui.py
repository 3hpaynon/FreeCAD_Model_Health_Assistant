from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from model_health_assistant import ui
from model_health_assistant.recovery_lifecycle import (
    DeletedSketchRecord,
    RecoveryLifecycleService,
)
from model_health_assistant.recovery_presentation import (
    build_deleted_object_presentations,
)
from model_health_assistant.recovery_ui import (
    EMPTY_DELETED_OBJECTS_MESSAGE,
    RECOVERY_TABLE_HEADERS,
    RecoveryTabController,
    create_recovery_tab,
)
from model_health_assistant.recovery_vault import RecoveryVault


class FakeLabel:
    def __init__(self) -> None:
        self.text = ""
        self.visible = True

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = text

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible = visible


class FakeButton:
    def __init__(self) -> None:
        self.enabled = True

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self.enabled = enabled


class FakeTable:
    def __init__(self) -> None:
        self.rows = []
        self.selected = False
        self.current_row = -1
        self.resize_count = 0
        self.visible = True

    def clearSelection(self) -> None:  # noqa: N802
        self.selected = False
        self.current_row = -1

    def setRowCount(self, count: int) -> None:  # noqa: N802
        self.rows = [[None] * 5 for _ in range(count)]

    def setItem(self, row: int, column: int, item) -> None:  # noqa: N802
        self.rows[row][column] = item

    def resizeRowsToContents(self) -> None:  # noqa: N802
        self.resize_count += 1

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible = visible

    def selectedItems(self):  # noqa: N802
        return [object()] if self.selected else []

    def currentRow(self) -> int:  # noqa: N802
        return self.current_row


def _record(*, eligibility: str = "available_with_warning") -> DeletedSketchRecord:
    return DeletedSketchRecord(
        schema_version="1.0",
        document_id=str(uuid4()),
        object_id=str(uuid4()),
        object_name="Sketch007",
        object_label="Rotor Sketch",
        type_id="Sketcher::SketchObject",
        status="deleted",
        deleted_at="2026-08-27T14:15:00+00:00",
        latest_snapshot_id=str(uuid4()),
        recovery_eligibility=eligibility,
        support_status="recorded_unresolved",
        unsupported_elements=(),
        warnings=("Original support requires validation.",),
        geometry_count=2,
        constraint_count=3,
    )


def _item():
    return build_deleted_object_presentations(
        (_record(),),
        now=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )[0]


def _controller(
    *,
    loader,
    details_handler=lambda _item: True,
    restore_handler=lambda _item, _context: True,
):
    table = FakeTable()
    empty = FakeLabel()
    count = FakeLabel()
    details = FakeButton()
    restore = FakeButton()
    controller = RecoveryTabController(
        table,
        empty,
        count,
        details,
        restore,
        lambda text: text,
        records_loader=loader,
        details_handler=details_handler,
        restore_handler=restore_handler,
    )
    return controller, table, empty, count, details, restore


def test_empty_state_uses_exact_message_and_disables_actions() -> None:
    controller, table, empty, count, details, restore = _controller(
        loader=lambda _context: (),
    )

    assert controller.refresh(object()) is True

    assert table.rows == []
    assert table.visible is False
    assert empty.text == EMPTY_DELETED_OBJECTS_MESSAGE
    assert empty.visible is True
    assert count.text == "0 deleted sketches"
    assert details.enabled is False
    assert restore.enabled is False


def test_deleted_row_selection_enables_details_and_safe_restore_action() -> None:
    selected = []
    item = _item()
    controller, table, empty, count, details, restore = _controller(
        loader=lambda _context: (item,),
        details_handler=lambda value: selected.append(value) or True,
    )

    assert controller.refresh(object()) is True
    assert table.rows == [list(item.table_values)]
    assert table.visible is True
    assert table.resize_count == 1
    assert empty.visible is False
    assert count.text == "1 deleted sketch"

    table.selected = True
    table.current_row = 0
    controller.update_selection()

    assert details.enabled is True
    assert restore.enabled is True
    assert controller.view_details() is True
    assert selected == [item]


def test_restore_action_calls_service_handler_and_refreshes_records() -> None:
    item = _item()
    restore_calls = []
    load_contexts = []

    def loader(context):
        load_contexts.append(context)
        return (item,)

    controller, table, _empty, _count, _details, restore = _controller(
        loader=loader,
        restore_handler=lambda value, context: (
            restore_calls.append((value, context)) or True
        ),
    )
    context = object()
    controller.refresh(context)
    table.selected = True
    table.current_row = 0
    controller.update_selection()

    assert restore.enabled is True
    assert controller.restore_as_copy() is True
    assert restore_calls == [(item, context)]
    assert load_contexts == [context, context]


def test_unsafe_record_never_enables_restore() -> None:
    item = build_deleted_object_presentations(
        (_record(eligibility="unavailable"),),
        now=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
    )[0]
    controller, table, _empty, _count, details, restore = _controller(
        loader=lambda _context: (item,),
    )
    controller.refresh(object())
    table.selected = True
    table.current_row = 0

    controller.update_selection()

    assert details.enabled is True
    assert restore.enabled is False
    assert controller.restore_as_copy() is False


def test_loader_failure_fails_closed_without_stale_records() -> None:
    item = _item()
    calls = 0

    def loader(_context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (item,)
        raise OSError("technical storage error")

    controller, table, empty, count, details, restore = _controller(loader=loader)
    assert controller.refresh(object()) is True

    assert controller.refresh(object()) is False

    assert controller.items == ()
    assert table.rows == []
    assert table.visible is False
    assert empty.text == "Deleted sketch recovery data could not be loaded safely."
    assert empty.visible is True
    assert count.text == "0 deleted sketches"
    assert details.enabled is False
    assert restore.enabled is False


def test_factory_creates_only_deleted_objects_actions() -> None:
    class Signal:
        def __init__(self) -> None:
            self.handlers = []

        def connect(self, handler) -> None:
            self.handlers.append(handler)

    class Widget:
        def __init__(self, parent=None) -> None:
            self.parent = parent

    class Layout:
        def __init__(self, parent=None) -> None:
            self.parent = parent
            self.children = []

        def addWidget(self, widget) -> None:  # noqa: N802
            self.children.append(widget)

        def addLayout(self, layout) -> None:  # noqa: N802
            self.children.append(layout)

        def addStretch(self) -> None:  # noqa: N802
            self.children.append("stretch")

    class Label(Widget):
        created = []

        def __init__(self, text, parent=None) -> None:
            super().__init__(parent)
            self.text = text
            self.visible = True
            Label.created.append(self)

        def setText(self, text) -> None:  # noqa: N802
            self.text = text

        def setVisible(self, visible) -> None:  # noqa: N802
            self.visible = visible

        def setWordWrap(self, _enabled) -> None:  # noqa: N802
            return None

    class Button(Widget):
        created = []

        def __init__(self, text, parent=None) -> None:
            super().__init__(parent)
            self.text = text
            self.enabled = True
            self.tool_tip = ""
            self.clicked = Signal()
            Button.created.append(self)

        def setEnabled(self, enabled) -> None:  # noqa: N802
            self.enabled = enabled

        def setToolTip(self, text) -> None:  # noqa: N802
            self.tool_tip = text

    class Header:
        def setStretchLastSection(self, _enabled) -> None:  # noqa: N802
            return None

    class Table(FakeTable):
        created = []

        def __init__(self, parent=None) -> None:
            super().__init__()
            self.parent = parent
            self.headers = []
            self.itemSelectionChanged = Signal()
            self.itemDoubleClicked = Signal()
            Table.created.append(self)

        def setColumnCount(self, _count) -> None:  # noqa: N802
            return None

        def setHorizontalHeaderLabels(self, labels) -> None:  # noqa: N802
            self.headers = labels

        def setSortingEnabled(self, _enabled) -> None:  # noqa: N802
            return None

        def setAlternatingRowColors(self, _enabled) -> None:  # noqa: N802
            return None

        def setColumnWidth(self, _column, _width) -> None:  # noqa: N802
            return None

        def horizontalHeader(self):  # noqa: N802
            return Header()

        def setEditTriggers(self, _value) -> None:  # noqa: N802
            return None

        def setSelectionBehavior(self, _value) -> None:  # noqa: N802
            return None

        def setSelectionMode(self, _value) -> None:  # noqa: N802
            return None

    widgets = SimpleNamespace(
        QWidget=Widget,
        QVBoxLayout=Layout,
        QHBoxLayout=Layout,
        QLabel=Label,
        QTableWidget=Table,
        QPushButton=Button,
        QTableWidgetItem=lambda text: text,
        QAbstractItemView=SimpleNamespace(
            NoEditTriggers=0,
            SelectRows=1,
            SingleSelection=2,
        ),
    )

    tab, controller = create_recovery_tab(
        widgets,
        object(),
        records_loader=lambda _context: (),
        details_handler=lambda _item: True,
        restore_handler=lambda _item, _context: True,
    )

    assert isinstance(tab, Widget)
    assert isinstance(controller, RecoveryTabController)
    assert Table.created[0].headers == list(RECOVERY_TABLE_HEADERS)
    assert [button.text for button in Button.created] == [
        "View Details",
        "Restore as Copy",
    ]
    assert all(button.enabled is False for button in Button.created)
    assert not any(label.text in {"History", "Checkpoints"} for label in Label.created)


def test_ui_loader_filters_to_the_analyzed_document_through_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    record = _record()
    document = object()
    document_id = record.document_id
    vault = RecoveryVault(tmp_path / "vault")
    observed_document_ids = []
    monkeypatch.setattr(
        RecoveryLifecycleService,
        "list_deleted_records",
        lambda _service, value=None: observed_document_ids.append(value) or (record,),
    )
    observer = SimpleNamespace(
        is_registered=True,
        vault=vault,
        document_id_for=lambda value: document_id if value is document else None,
    )
    app = SimpleNamespace(_model_health_assistant_recovery_observer=observer)
    context = ui._AnalyzedDocumentContext(
        document=document,
        document_name="RecoveryDocument",
        app_module=app,
        gui_module=SimpleNamespace(),
    )

    items = ui._load_deleted_object_presentations(context)

    assert len(items) == 1
    assert items[0].object_id == record.object_id
    assert observed_document_ids == [document_id]


def test_details_dialog_shows_structured_text_not_raw_snapshot_json() -> None:
    calls = []
    widgets = SimpleNamespace(
        QMessageBox=SimpleNamespace(information=lambda *args: calls.append(args))
    )
    item = _item()
    parent = object()

    assert ui._show_deleted_object_details(widgets, parent, item) is True

    assert calls[0][0] is parent
    assert calls[0][1] == "Deleted Sketch — Sketch007"
    assert "Original name: Sketch007" in calls[0][2]
    assert "Geometry: 2" in calls[0][2]
    assert "snapshot_id" not in calls[0][2]


def test_restore_confirmation_returns_only_explicit_safe_choice() -> None:
    class Signal:
        def connect(self, _handler) -> None:
            return None

    class Dialog:
        next_result = 0

        def __init__(self, _parent=None) -> None:
            self.title = ""

        def setWindowTitle(self, title) -> None:  # noqa: N802
            self.title = title

        def setModal(self, _modal) -> None:  # noqa: N802
            return None

        def done(self, _result) -> None:
            return None

        def reject(self) -> None:
            return None

        def exec(self):
            return self.next_result

    class Layout:
        def __init__(self, _parent=None) -> None:
            return None

        def addWidget(self, _widget) -> None:  # noqa: N802
            return None

        def addLayout(self, _layout) -> None:  # noqa: N802
            return None

        def addStretch(self) -> None:  # noqa: N802
            return None

    class Label:
        def __init__(self, text, _parent=None) -> None:
            self.text = text

        def setWordWrap(self, _enabled) -> None:  # noqa: N802
            return None

    class Button:
        created = []

        def __init__(self, text, _parent=None) -> None:
            self.text = text
            self.clicked = Signal()
            Button.created.append(self)

        def setShortcut(self, _shortcut) -> None:  # noqa: N802
            return None

    widgets = SimpleNamespace(
        QDialog=Dialog,
        QVBoxLayout=Layout,
        QHBoxLayout=Layout,
        QLabel=Label,
        QPushButton=Button,
    )
    assessment = SimpleNamespace(
        object_name="Sketch003",
        confirmation_text="Geometry: recoverable",
        allowed_modes=("original_support", "detached"),
    )

    Dialog.next_result = 2
    assert ui._show_restore_confirmation(widgets, object(), assessment) == (
        "original_support"
    )
    Dialog.next_result = 1
    assert ui._show_restore_confirmation(widgets, object(), assessment) == "detached"
    Dialog.next_result = 0
    assert ui._show_restore_confirmation(widgets, object(), assessment) is None
    assert {button.text for button in Button.created} >= {
        "Cancel",
        "Restore with Original Support",
        "Restore Detached",
    }
