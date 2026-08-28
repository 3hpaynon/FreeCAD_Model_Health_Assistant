"""Qt-neutral controller and small Qt factory for the Deleted Objects tab."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .recovery_presentation import DeletedObjectPresentation

EMPTY_DELETED_OBJECTS_MESSAGE = "No recoverable deleted sketches."
RECOVERY_TABLE_HEADERS = ("Sketch", "Deleted", "Type", "Recovery", "Support")
RECOVERY_COLUMN_WIDTHS = (190, 100, 70, 155)

RecoveryLoader = Callable[[Any], Sequence[DeletedObjectPresentation]]
DetailsHandler = Callable[[DeletedObjectPresentation], bool]
RestoreHandler = Callable[[DeletedObjectPresentation, Any], bool]


class RecoveryTabController:
    """Render deleted-object records while keeping recovery services outside Qt."""

    def __init__(
        self,
        table: Any,
        empty_label: Any,
        count_label: Any,
        view_details_button: Any,
        restore_as_copy_button: Any,
        table_item_factory: Any,
        *,
        records_loader: RecoveryLoader,
        details_handler: DetailsHandler,
        restore_handler: RestoreHandler,
    ) -> None:
        self.table = table
        self.empty_label = empty_label
        self.count_label = count_label
        self.view_details_button = view_details_button
        self.restore_as_copy_button = restore_as_copy_button
        self.table_item_factory = table_item_factory
        self.records_loader = records_loader
        self.details_handler = details_handler
        self.restore_handler = restore_handler
        self.items: tuple[DeletedObjectPresentation, ...] = ()
        self.document_context: Any = None
        self._set_action_state(False)

    def refresh(self, document_context: Any) -> bool:
        """Reload durable records through the injected recovery-service boundary."""

        self.document_context = document_context
        try:
            items = tuple(self.records_loader(document_context))
        except Exception:  # noqa: BLE001 - recovery UI must fail closed.
            self.items = ()
            self._render_rows()
            self._set_table_visible(False)
            self._set_empty_message(
                "Deleted sketch recovery data could not be loaded safely."
            )
            self._set_count(0)
            return False

        self.items = items
        self._render_rows()
        self._set_table_visible(bool(items))
        self._set_empty_message(
            EMPTY_DELETED_OBJECTS_MESSAGE,
            visible=not items,
        )
        self._set_count(len(items))
        return True

    def update_selection(self, *_args: Any) -> None:
        self._set_action_state(self.selected_item() is not None)

    def selected_item(self) -> DeletedObjectPresentation | None:
        selected_items = getattr(self.table, "selectedItems", None)
        current_row = getattr(self.table, "currentRow", None)
        if not callable(selected_items) or not callable(current_row):
            return None
        if not selected_items():
            return None
        row = current_row()
        if not isinstance(row, int) or not 0 <= row < len(self.items):
            return None
        return self.items[row]

    def view_details(self, *_args: Any) -> bool:
        item = self.selected_item()
        if item is None:
            self._set_action_state(False)
            return False
        try:
            return bool(self.details_handler(item))
        except Exception:  # noqa: BLE001 - details remain a read-only action.
            return False

    def restore_as_copy(self, *_args: Any) -> bool:
        item = self.selected_item()
        if item is None or not item.can_attempt_restore:
            self._set_action_state(item is not None)
            return False
        try:
            restored = bool(self.restore_handler(item, self.document_context))
        except Exception:  # noqa: BLE001 - restoration must fail safely at UI boundary.
            return False
        if restored:
            self.refresh(self.document_context)
        return restored

    def _render_rows(self) -> None:
        clear_selection = getattr(self.table, "clearSelection", None)
        if callable(clear_selection):
            clear_selection()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            for column, text in enumerate(item.table_values):
                self.table.setItem(row, column, self.table_item_factory(text))
        resize_rows = getattr(self.table, "resizeRowsToContents", None)
        if callable(resize_rows):
            resize_rows()
        self._set_action_state(False)

    def _set_empty_message(self, text: str, *, visible: bool = True) -> None:
        set_text = getattr(self.empty_label, "setText", None)
        if callable(set_text):
            set_text(text)
        set_visible = getattr(self.empty_label, "setVisible", None)
        if callable(set_visible):
            set_visible(visible)

    def _set_count(self, count: int) -> None:
        noun = "deleted sketch" if count == 1 else "deleted sketches"
        set_text = getattr(self.count_label, "setText", None)
        if callable(set_text):
            set_text(f"{count} {noun}")

    def _set_table_visible(self, visible: bool) -> None:
        set_visible = getattr(self.table, "setVisible", None)
        if callable(set_visible):
            set_visible(visible)

    def _set_action_state(self, has_selection: bool) -> None:
        set_enabled = getattr(self.view_details_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(has_selection)
        set_enabled = getattr(self.restore_as_copy_button, "setEnabled", None)
        if callable(set_enabled):
            selected = self.selected_item() if has_selection else None
            set_enabled(bool(selected is not None and selected.can_attempt_restore))


def create_recovery_tab(
    qt_widgets: Any,
    parent: Any,
    *,
    records_loader: RecoveryLoader,
    details_handler: DetailsHandler,
    restore_handler: RestoreHandler,
) -> tuple[Any, RecoveryTabController]:
    """Create the compact Recovery / Deleted Objects page."""

    tab = qt_widgets.QWidget(parent)
    layout = qt_widgets.QVBoxLayout(tab)

    header_layout = qt_widgets.QHBoxLayout()
    heading = qt_widgets.QLabel("Deleted Objects", tab)
    _set_label_bold(heading)
    count_label = qt_widgets.QLabel("0 deleted sketches", tab)
    header_layout.addWidget(heading)
    header_layout.addStretch()
    header_layout.addWidget(count_label)
    layout.addLayout(header_layout)

    description = qt_widgets.QLabel(
        "Recoverable sketches deleted from this analyzed document.",
        tab,
    )
    set_word_wrap = getattr(description, "setWordWrap", None)
    if callable(set_word_wrap):
        set_word_wrap(True)
    layout.addWidget(description)

    table = qt_widgets.QTableWidget(tab)
    _configure_recovery_table(qt_widgets, table)
    layout.addWidget(table)

    empty_label = qt_widgets.QLabel(EMPTY_DELETED_OBJECTS_MESSAGE, tab)
    set_word_wrap = getattr(empty_label, "setWordWrap", None)
    if callable(set_word_wrap):
        set_word_wrap(True)
    layout.addWidget(empty_label)

    action_layout = qt_widgets.QHBoxLayout()
    view_details_button = qt_widgets.QPushButton("View Details", tab)
    view_details_button.setEnabled(False)
    restore_as_copy_button = qt_widgets.QPushButton("Restore as Copy", tab)
    restore_as_copy_button.setEnabled(False)
    restore_as_copy_button.setToolTip(
        "Create a new sketch without replacing or rolling back current model objects."
    )
    action_layout.addWidget(view_details_button)
    action_layout.addWidget(restore_as_copy_button)
    action_layout.addStretch()
    layout.addLayout(action_layout)

    controller = RecoveryTabController(
        table,
        empty_label,
        count_label,
        view_details_button,
        restore_as_copy_button,
        qt_widgets.QTableWidgetItem,
        records_loader=records_loader,
        details_handler=details_handler,
        restore_handler=restore_handler,
    )
    table.itemSelectionChanged.connect(controller.update_selection)
    table.itemDoubleClicked.connect(controller.view_details)
    view_details_button.clicked.connect(controller.view_details)
    restore_as_copy_button.clicked.connect(controller.restore_as_copy)
    return tab, controller


def _configure_recovery_table(qt_widgets: Any, table: Any) -> None:
    table.setColumnCount(len(RECOVERY_TABLE_HEADERS))
    table.setHorizontalHeaderLabels(list(RECOVERY_TABLE_HEADERS))
    table.setSortingEnabled(False)
    set_alternating_rows = getattr(table, "setAlternatingRowColors", None)
    if callable(set_alternating_rows):
        set_alternating_rows(True)
    for column, width in enumerate(RECOVERY_COLUMN_WIDTHS):
        set_column_width = getattr(table, "setColumnWidth", None)
        if callable(set_column_width):
            set_column_width(column, width)
    horizontal_header = getattr(table, "horizontalHeader", None)
    if callable(horizontal_header):
        set_stretch_last = getattr(horizontal_header(), "setStretchLastSection", None)
        if callable(set_stretch_last):
            set_stretch_last(True)
    item_view = getattr(qt_widgets, "QAbstractItemView", None)
    if item_view is not None:
        table.setEditTriggers(item_view.NoEditTriggers)
        table.setSelectionBehavior(item_view.SelectRows)
        table.setSelectionMode(item_view.SingleSelection)


def _set_label_bold(label: Any) -> None:
    font_method = getattr(label, "font", None)
    set_font = getattr(label, "setFont", None)
    if not callable(font_method) or not callable(set_font):
        return
    font = font_method()
    set_bold = getattr(font, "setBold", None)
    if callable(set_bold):
        set_bold(True)
        set_font(font)


__all__ = [
    "EMPTY_DELETED_OBJECTS_MESSAGE",
    "RECOVERY_TABLE_HEADERS",
    "RecoveryTabController",
    "create_recovery_tab",
]
