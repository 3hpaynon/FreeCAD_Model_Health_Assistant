"""FreeCAD analysis workflow and native results interface.

The module remains safe to import outside FreeCAD.  FreeCAD and PySide are
optional at import time, and Qt is loaded only when a parented results dialog is
actually requested.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model_health_assistant.ai_request_state import (
    AIRequestAlreadyRunningError,
    AIRequestStateClosedError,
    AIRequestStateController,
)
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
_WARNING_COLUMN_WIDTHS = (85, 170, 190)
_NO_WARNINGS_MESSAGE = "No model-health warnings were generated."
_HEALTH_SCORE_DISCLAIMER = (
    "The health score is a rule-based engineering aid and does not guarantee "
    "that the model is error-free."
)
AI_CONTEXT_PREVIEW_NOTICE = (
    "Final privacy review. No data has been sent. The displayed request is the "
    "exact request that may be transmitted only after Send to Provider."
)
MOCK_EXPLANATION_NOTICE = (
    "This explanation was produced by a deterministic local test provider. "
    "No AI model or network service was used."
)


@dataclass(frozen=True, slots=True)
class _AnalyzedDocumentContext:
    """UI-only identity and API context for the document that was analyzed."""

    document: Any
    document_name: str
    app_module: Any
    gui_module: Any


class _ReadOnlyTextDialogController:
    """Update and foreground one reusable read-only child dialog."""

    def __init__(self, dialog: Any, text_view: Any) -> None:
        self.dialog = dialog
        self.text_view = text_view

    def update_text(self, text: str) -> None:
        set_plain_text = getattr(self.text_view, "setPlainText", None)
        if callable(set_plain_text):
            set_plain_text(text)
            return
        set_text = getattr(self.text_view, "setText", None)
        if callable(set_text):
            set_text(text)

    def show(self) -> None:
        self.dialog.show()
        raise_dialog = getattr(self.dialog, "raise_", None)
        if callable(raise_dialog):
            raise_dialog()
        activate_window = getattr(self.dialog, "activateWindow", None)
        if callable(activate_window):
            activate_window()


class _AIRequestReviewDialogController(_ReadOnlyTextDialogController):
    """Own one ephemeral consent session for the exact displayed request."""

    def __init__(
        self,
        dialog: Any,
        text_view: Any,
        status_label: Any,
        send_button: Any,
    ) -> None:
        super().__init__(dialog, text_view)
        self.status_label = status_label
        self.send_button = send_button
        self.consent = None
        self._send_handler = None

    def update_review(self, review: Any, send_handler: Any) -> None:
        from model_health_assistant.ai_consent import (
            AIRequestConsent,
            AIRequestReview,
        )

        if not isinstance(review, AIRequestReview):
            raise TypeError("review must be an AIRequestReview")
        self.consent = AIRequestConsent(review)
        self._send_handler = send_handler
        self.update_text(review.preview_json())
        self.status_label.setText("")
        self.send_button.setEnabled(callable(send_handler))

    def cancel(self, *_args: Any) -> None:
        if self.consent is not None:
            self.consent.cancel()
        self._send_handler = None
        self.update_text("")
        self.dialog.close()

    def send_to_provider(self, *_args: Any) -> bool:
        if self.consent is None or not callable(self._send_handler):
            self.status_label.setText("No reviewed provider request is available.")
            return False
        consent = self.consent
        send_handler = self._send_handler
        consent.approve()
        error = send_handler(consent)
        if error:
            self.status_label.setText(str(error))
            return False
        self.consent = None
        self._send_handler = None
        self.update_text("")
        self.dialog.close()
        return True


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
        open_report_folder_button: Any | None = None,
        copy_report_path_button: Any | None = None,
        open_report_folder_handler: Any | None = None,
        copy_report_path_handler: Any | None = None,
        report_action_error_handler: Any | None = None,
        refresh_analysis_button: Any | None = None,
        status_label: Any | None = None,
        preview_ai_context_button: Any | None = None,
        preview_ai_context_handler: Any | None = None,
        test_mock_explanation_button: Any | None = None,
        test_mock_explanation_handler: Any | None = None,
        explain_selected_warning_button: Any | None = None,
        explain_selected_warning_handler: Any | None = None,
        explain_full_analysis_button: Any | None = None,
        explain_full_analysis_handler: Any | None = None,
        ask_about_analysis_button: Any | None = None,
        ask_about_analysis_handler: Any | None = None,
        cancel_ai_request_button: Any | None = None,
        ai_workflow_status_handler: Any | None = None,
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
        self.open_report_folder_button = open_report_folder_button
        self.copy_report_path_button = copy_report_path_button
        self.open_report_folder_handler = open_report_folder_handler
        self.copy_report_path_handler = copy_report_path_handler
        self.report_action_error_handler = report_action_error_handler
        self.refresh_analysis_button = refresh_analysis_button
        self.status_label = status_label
        self.preview_ai_context_button = preview_ai_context_button
        self.preview_ai_context_handler = preview_ai_context_handler
        self.test_mock_explanation_button = test_mock_explanation_button
        self.test_mock_explanation_handler = test_mock_explanation_handler
        self.explain_selected_warning_button = explain_selected_warning_button
        self.explain_selected_warning_handler = explain_selected_warning_handler
        self.explain_full_analysis_button = explain_full_analysis_button
        self.explain_full_analysis_handler = explain_full_analysis_handler
        self.ask_about_analysis_button = ask_about_analysis_button
        self.ask_about_analysis_handler = ask_about_analysis_handler
        self.cancel_ai_request_button = cancel_ai_request_button
        self.ai_workflow_status_handler = ai_workflow_status_handler
        self._ai_request_state = AIRequestStateController()
        self._active_ai_ticket: int | None = None
        self._ai_operation_running = False
        self._ai_operation = None
        self.warning_items: tuple[WarningDisplayItem, ...] = ()
        self.document_context: _AnalyzedDocumentContext | None = None
        self.report_path: Path | None = None
        self.report: ModelHealthReport | None = None

    def update_presentation(
        self,
        presentation: AnalysisPresentation,
        *,
        report: ModelHealthReport | None = None,
        document_context: _AnalyzedDocumentContext | None = None,
        status_message: str = "",
    ) -> None:
        """Replace every displayed summary value."""

        self.document_context = document_context
        self.report = report
        for key, text in _summary_display_values(presentation).items():
            label = self.value_labels.get(key)
            set_text = getattr(label, "setText", None)
            if callable(set_text):
                set_text(text)
        self.report_path = presentation.report_path
        _, report_path_error = _validated_report_path(self.report_path)
        self._set_report_actions_enabled(report_path_error is None)
        self._set_preview_ai_context_enabled(report is not None)
        self._set_test_mock_explanation_enabled(report is not None)
        self._refresh_ai_action_state()
        self.update_status(status_message)
        self._update_warning_table(presentation)

    def update_status(self, message: str) -> None:
        """Display one concise user-facing state without technical traceback text."""

        if self.status_label is None:
            return
        set_text = getattr(self.status_label, "setText", None)
        if callable(set_text):
            set_text(message)
        set_visible = getattr(self.status_label, "setVisible", None)
        if callable(set_visible):
            set_visible(bool(message))

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
        self._refresh_ai_action_state()
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
        self._refresh_ai_action_state()

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
        self.update_status(f"Selection failed: {error}")
        if callable(self.selection_error_handler):
            self.selection_error_handler(error)
        app_module = (
            self.document_context.app_module
            if self.document_context is not None
            else App
        )
        _print_message(f"Model Health Assistant: {error}\n", app_module)
        return False

    def open_report_folder(self, *_args: Any) -> bool:
        """Open only the directory containing the current saved JSON report."""

        return self._run_report_action(
            self.open_report_folder_handler,
            "Opened report folder.",
        )

    def copy_report_path(self, *_args: Any) -> bool:
        """Copy the complete current JSON report path, never its contents."""

        return self._run_report_action(
            self.copy_report_path_handler,
            "Copied report path to the clipboard.",
        )

    def refresh_analysis(self, *_args: Any) -> bool:
        """Run the existing synchronous pipeline once for the active document."""

        app_module = (
            self.document_context.app_module
            if self.document_context is not None
            else App
        )
        gui_module = (
            self.document_context.gui_module
            if self.document_context is not None
            else Gui
        )
        return (
            run_analysis_command(
                app_module=app_module,
                gui_module=gui_module,
            )
            is not None
        )

    def preview_ai_context(self, *_args: Any) -> bool:
        """Display the privacy-safe context for the already completed report."""

        if self.report is None:
            self._set_preview_ai_context_enabled(False)
            self.update_status("AI context preview failed: No completed report is available.")
            return False
        if not callable(self.preview_ai_context_handler):
            self._set_preview_ai_context_enabled(False)
            self.update_status(
                "AI context preview failed: Preview is unavailable in this Qt runtime."
            )
            return False
        try:
            error = self.preview_ai_context_handler(self.report)
        except Exception as exc:
            error = (
                "The local preview failed safely: "
                f"{type(exc).__name__}: {exc}"
            )
        if error is not None:
            self.update_status(f"AI context preview failed: {error}")
            return False

        status = "Opened local AI context preview. No data was transmitted."
        snapshot_notice = _retained_analysis_state_notice(self.document_context)
        if snapshot_notice:
            status += " " + snapshot_notice
        self.update_status(status)
        return True

    def test_mock_explanation(self, *_args: Any) -> bool:
        """Explain the retained report with the deterministic local provider."""

        if self.report is None:
            self._set_test_mock_explanation_enabled(False)
            self.update_status(
                "Mock explanation failed: No completed report is available."
            )
            return False
        if not callable(self.test_mock_explanation_handler):
            self._set_test_mock_explanation_enabled(False)
            self.update_status(
                "Mock explanation failed: The local test provider is unavailable "
                "in this Qt runtime."
            )
            return False
        try:
            error = self.test_mock_explanation_handler(self.report)
        except Exception as exc:
            error = (
                "The deterministic local explanation failed safely: "
                f"{type(exc).__name__}: {exc}"
            )
        if error is not None:
            self.update_status(f"Mock explanation failed: {error}")
            return False

        status = (
            "Opened deterministic local mock explanation. No AI model or network "
            "service was used."
        )
        snapshot_notice = _retained_analysis_state_notice(self.document_context)
        if snapshot_notice:
            status += " " + snapshot_notice
        self.update_status(status)
        return True

    def explain_selected_warning(self, *_args: Any) -> bool:
        """Open final review for only the currently selected source warning."""

        warning = self._selected_warning()
        status = self._ai_workflow_status()
        if self.report is None or warning is None:
            self._refresh_ai_action_state()
            self.update_status(
                "Selected-warning explanation is unavailable: select one warning first."
            )
            return False
        if status is None or not getattr(status, "available", False):
            self._refresh_ai_action_state()
            message = getattr(
                status,
                "message",
                "The selected provider is unavailable.",
            )
            self.update_status(f"Selected-warning explanation is unavailable: {message}")
            return False
        if self._ai_operation_running:
            self.update_status("An explanation request is already running.")
            return False
        if not callable(self.explain_selected_warning_handler):
            self._refresh_ai_action_state()
            self.update_status(
                "Selected-warning explanation is unavailable in this Qt runtime."
            )
            return False
        try:
            error = self.explain_selected_warning_handler(self)
        except Exception as exc:
            error = f"Could not prepare the request safely: {type(exc).__name__}: {exc}"
        if error:
            self.update_status(f"Selected-warning explanation failed: {error}")
            return False
        self.update_status(
            "Opened final review for the selected warning. No data was transmitted."
        )
        return True

    def explain_full_analysis(self, *_args: Any) -> bool:
        """Open a fresh final review for the completed full analysis snapshot."""

        status = self._ai_workflow_status()
        if self.report is None:
            self._refresh_ai_action_state()
            self.update_status(
                "Full-analysis explanation is unavailable: no completed report exists."
            )
            return False
        if status is None or not getattr(status, "available", False):
            self._refresh_ai_action_state()
            message = getattr(
                status,
                "message",
                "The selected provider is unavailable.",
            )
            self.update_status(f"Full-analysis explanation is unavailable: {message}")
            return False
        if self._ai_operation_running:
            self.update_status("An explanation request is already running.")
            return False
        if not callable(self.explain_full_analysis_handler):
            self._refresh_ai_action_state()
            self.update_status(
                "Full-analysis explanation is unavailable in this Qt runtime."
            )
            return False
        try:
            error = self.explain_full_analysis_handler(self)
        except Exception as exc:
            error = f"Could not prepare the request safely: {type(exc).__name__}: {exc}"
        if error:
            self.update_status(f"Full-analysis explanation failed: {error}")
            return False
        self.update_status(
            "Opened a fresh final review for the full analysis. No data was "
            "transmitted."
        )
        return True

    def ask_about_analysis(self, *_args: Any) -> bool:
        """Collect one question and open its exact privacy review."""

        status = self._ai_workflow_status()
        if self.report is None:
            self._refresh_ai_action_state()
            self.update_status(
                "Grounded questions are unavailable: no completed report exists."
            )
            return False
        if status is None or not getattr(status, "available", False):
            self._refresh_ai_action_state()
            message = getattr(
                status,
                "message",
                "The selected provider is unavailable.",
            )
            self.update_status(f"Grounded questions are unavailable: {message}")
            return False
        if self._ai_operation_running:
            self.update_status("An explanation request is already running.")
            return False
        if not callable(self.ask_about_analysis_handler):
            self._refresh_ai_action_state()
            self.update_status(
                "Grounded questions are unavailable in this Qt runtime."
            )
            return False
        try:
            error = self.ask_about_analysis_handler(self)
        except Exception as exc:
            error = f"Could not prepare the question safely: {type(exc).__name__}: {exc}"
        if error:
            self.update_status(f"Grounded question failed: {error}")
            return False
        self.update_status(
            "Opened final review for one grounded question. No data was transmitted."
        )
        return True

    @property
    def active_ai_ticket(self) -> int | None:
        """Return the ticket for the request currently accepted by this dialog."""

        return self._active_ai_ticket

    def begin_ai_operation(
        self,
        message: str,
        *,
        request_type: str = "explanation",
        provider_identifier: str = "selected-provider",
    ) -> bool:
        """Mark one provider request active and disable competing AI actions."""

        try:
            ticket = self._ai_request_state.begin(
                request_type,
                provider_identifier,
                progress_message=message,
            )
        except (AIRequestAlreadyRunningError, AIRequestStateClosedError):
            return False
        self._active_ai_ticket = ticket
        self._ai_operation_running = True
        self._refresh_ai_action_state()
        self.update_status(
            f"{message} Request: {request_type}; provider: {provider_identifier}."
        )
        return True

    def attach_ai_operation(self, ticket: object, operation: object) -> bool:
        """Retain a worker handle only while its ticket is still current."""

        if not self.is_ai_operation_current(ticket):
            detach = getattr(operation, "detach", None)
            if callable(detach):
                detach()
            return False
        self._ai_operation = operation
        return True

    def is_ai_operation_current(self, ticket: object) -> bool:
        """Return whether a callback ticket still owns this dialog's UI state."""

        snapshot = self._ai_request_state.snapshot
        return (
            snapshot.accepts_callbacks
            and type(ticket) is int
            and ticket == snapshot.ticket
        )

    def complete_ai_operation(
        self,
        message: str,
        *,
        ticket: object | None = None,
        failed: bool = False,
    ) -> bool:
        """Return the explanation actions to their request-time readiness state."""

        effective_ticket = self._active_ai_ticket if ticket is None else ticket
        accept = (
            self._ai_request_state.accept_failure
            if failed
            else self._ai_request_state.accept_success
        )
        if not accept(effective_ticket, progress_message=message):
            return False
        self._ai_operation_running = False
        self._ai_operation = None
        self._active_ai_ticket = None
        self._refresh_ai_action_state()
        self.update_status(message)
        return True

    def cancel_ai_operation(self, *_args: Any) -> bool:
        """Logically cancel one request and suppress all of its late callbacks."""

        ticket = self._active_ai_ticket
        if ticket is None or not self._ai_request_state.cancel(
            ticket,
            progress_message="Request cancelled.",
        ):
            return False
        cancel = getattr(self._ai_operation, "cancel", None)
        if callable(cancel):
            cancel()
        self._ai_operation = None
        self._active_ai_ticket = None
        self._ai_operation_running = False
        self._refresh_ai_action_state()
        self.update_status(
            "Request cancelled. A provider operation already in progress may finish "
            "in the background, but its result will not be displayed."
        )
        return True

    def detach_ai_operation(self, *_args: Any) -> None:
        """Detach a hidden dialog without terminating an in-flight worker."""

        self._ai_request_state.detach(progress_message="Dialog detached.")
        detach = getattr(self._ai_operation, "detach", None)
        if callable(detach):
            detach()
        self._ai_operation = None
        self._active_ai_ticket = None
        self._ai_operation_running = False
        self._refresh_ai_action_state()

    def close_ai_request_state(self) -> None:
        """Permanently reject callbacks once the underlying dialog is destroyed."""

        detach = getattr(self._ai_operation, "detach", None)
        if callable(detach):
            detach()
        self._ai_request_state.close(progress_message="Dialog destroyed.")
        self._ai_operation = None
        self._active_ai_ticket = None
        self._ai_operation_running = False

    def current_selected_warning(self) -> WarningDisplayItem | None:
        """Return the current table selection for consent revalidation."""

        return self._selected_warning()

    def _run_report_action(self, handler: Any, success_message: str) -> bool:
        report_path, error = _validated_report_path(self.report_path)
        if error is None and not callable(handler):
            error = "This report action is unavailable in the current Qt runtime."
        if error is None and report_path is not None:
            try:
                error = handler(report_path)
            except Exception as exc:
                error = (
                    "The report action failed safely: "
                    f"{type(exc).__name__}: {exc}"
                )
        if error is not None:
            self._set_report_actions_enabled(False)
            self.update_status(f"Report action failed: {error}")
            if callable(self.report_action_error_handler):
                self.report_action_error_handler(error)
            self._print_report_action_message(error)
            return False

        self._print_report_action_message(success_message)
        return True

    def _print_report_action_message(self, message: str) -> None:
        app_module = (
            self.document_context.app_module
            if self.document_context is not None
            else App
        )
        _print_message(f"Model Health Assistant: {message}\n", app_module)

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

    def _set_report_actions_enabled(self, enabled: bool) -> None:
        buttons_and_handlers = (
            (self.open_report_folder_button, self.open_report_folder_handler),
            (self.copy_report_path_button, self.copy_report_path_handler),
        )
        for button, handler in buttons_and_handlers:
            if button is None:
                continue
            set_enabled = getattr(button, "setEnabled", None)
            if callable(set_enabled):
                set_enabled(enabled and callable(handler))

    def _set_preview_ai_context_enabled(self, enabled: bool) -> None:
        if self.preview_ai_context_button is None:
            return
        set_enabled = getattr(self.preview_ai_context_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(enabled and callable(self.preview_ai_context_handler))

    def _set_test_mock_explanation_enabled(self, enabled: bool) -> None:
        if self.test_mock_explanation_button is None:
            return
        set_enabled = getattr(
            self.test_mock_explanation_button,
            "setEnabled",
            None,
        )
        if callable(set_enabled):
            set_enabled(enabled and callable(self.test_mock_explanation_handler))

    def _ai_workflow_status(self) -> Any | None:
        if not callable(self.ai_workflow_status_handler):
            return None
        try:
            return self.ai_workflow_status_handler(self)
        except Exception:
            return None

    def _refresh_ai_action_state(self) -> None:
        status = self._ai_workflow_status()
        ready = bool(status is not None and getattr(status, "available", False))
        selected_enabled = (
            self.report is not None
            and self._selected_warning() is not None
            and not self._ai_operation_running
            and ready
            and callable(self.explain_selected_warning_handler)
        )
        selected_button = self.explain_selected_warning_button
        set_enabled = getattr(selected_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(selected_enabled)
        set_tool_tip = getattr(selected_button, "setToolTip", None)
        if callable(set_tool_tip):
            if self._ai_operation_running:
                tool_tip = "An explanation request is already running."
            elif self._selected_warning() is None:
                tool_tip = "Select one warning first."
            else:
                tool_tip = getattr(status, "message", "Provider unavailable.")
            set_tool_tip(tool_tip)

        cancel_button = self.cancel_ai_request_button
        set_enabled = getattr(cancel_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(self._ai_operation_running)

        full_enabled = (
            self.report is not None
            and not self._ai_operation_running
            and ready
            and callable(self.explain_full_analysis_handler)
        )
        full_button = self.explain_full_analysis_button
        set_enabled = getattr(full_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(full_enabled)
        set_tool_tip = getattr(full_button, "setToolTip", None)
        if callable(set_tool_tip):
            if self._ai_operation_running:
                tool_tip = "An explanation request is already running."
            elif self.report is None:
                tool_tip = "Run an analysis first."
            else:
                tool_tip = getattr(status, "message", "Provider unavailable.")
            set_tool_tip(tool_tip)

        question_enabled = (
            self.report is not None
            and not self._ai_operation_running
            and ready
            and callable(self.ask_about_analysis_handler)
        )
        question_button = self.ask_about_analysis_button
        set_enabled = getattr(question_button, "setEnabled", None)
        if callable(set_enabled):
            set_enabled(question_enabled)
        set_tool_tip = getattr(question_button, "setToolTip", None)
        if callable(set_tool_tip):
            if self._ai_operation_running:
                tool_tip = "An explanation request is already running."
            elif self.report is None:
                tool_tip = "Run an analysis first."
            else:
                tool_tip = getattr(status, "message", "Provider unavailable.")
            set_tool_tip(tool_tip)

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
_ai_context_preview_dialog: _AIRequestReviewDialogController | None = None
_mock_explanation_dialog: _ReadOnlyTextDialogController | None = None
_ai_explanation_dialog: _ReadOnlyTextDialogController | None = None


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
        technical_message = (
            "Model Health Assistant: No active FreeCAD document is open.\n"
        )
        _print_message(technical_message, active_app)
        _show_results_error(
            "No active FreeCAD document is open. Previous results were preserved.",
            active_gui,
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
        _publish_analysis_failure(
            "Analysis could not start because the local extractor is unavailable.",
            _failure_message("Could not load the local extractor", exc),
            active_app,
            active_gui,
        )
        return None
    except Exception as exc:
        _publish_analysis_failure(
            "Analysis failed while reading the active document.",
            _failure_message("Extractor failed", exc),
            active_app,
            active_gui,
        )
        return None

    try:
        from model_health_assistant import risk_engine

        report = risk_engine.analyze_model_health(
            features,
            document_name=document_name,
        )
    except ImportError as exc:
        _publish_analysis_failure(
            "Analysis could not continue because the local risk engine is unavailable.",
            _failure_message("Could not load the local risk engine", exc),
            active_app,
            active_gui,
        )
        return None
    except Exception as exc:
        _publish_analysis_failure(
            "Analysis failed before warnings could be generated.",
            _failure_message("Risk engine failed", exc),
            active_app,
            active_gui,
        )
        return None

    saved_path: str | Path | None = None
    report_failure_message = ""
    try:
        from model_health_assistant import report_writer

        output_path = report_writer.build_report_path(
            str(getattr(document, "Name", None) or document_name),
            str(getattr(document, "FileName", "") or ""),
            report.generated_at,
        )
        saved_path = report_writer.save_report_json(report, output_path)
    except ImportError as exc:
        report_failure_message = (
            "Analysis completed, but the local JSON report writer is unavailable."
        )
        _print_message(
            _failure_message("Could not load the local report writer", exc),
            active_app,
        )
    except Exception as exc:
        report_failure_message = (
            "Analysis completed, but the JSON report could not be written."
        )
        _print_message(_failure_message("Report writing failed", exc), active_app)

    _print_message(_format_report_view_summary(report, saved_path), active_app)
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
            report=report,
            gui_module=active_gui,
            document_context=document_context,
            status_message=_presentation_status_message(
                presentation,
                report_failure_message=report_failure_message,
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive FreeCAD runtime boundary.
        _print_error(_failure_message("Results dialog failed", exc), active_app)
    return presentation


def show_results_dialog(
    presentation: AnalysisPresentation,
    *,
    report: ModelHealthReport | None = None,
    gui_module: Any | None = None,
    document_context: _AnalyzedDocumentContext | None = None,
    status_message: str = "",
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
        report=report,
        document_context=document_context,
        status_message=status_message,
    )
    _results_dialog.show()
    return True


def _publish_analysis_failure(
    user_message: str,
    technical_message: str,
    app_module: Any | None,
    gui_module: Any | None,
) -> None:
    """Keep concise UI text separate from technical Report View diagnostics."""

    _print_message(technical_message, app_module)
    _show_results_error(user_message, gui_module)


def _show_results_error(message: str, gui_module: Any | None) -> bool:
    """Update an open results dialog, or use a parented warning when none exists."""

    if _results_dialog is not None:
        _results_dialog.update_status(message)
        _results_dialog.show()
        return True

    parent = _main_window(gui_module)
    qt_widgets = _load_qt_widgets() if parent is not None else None
    if qt_widgets is None:
        return False
    _show_selection_error(qt_widgets, parent, message)
    return True


def _presentation_status_message(
    presentation: AnalysisPresentation,
    *,
    report_failure_message: str = "",
) -> str:
    """Convert successful partial/empty outcomes into concise interface text."""

    if report_failure_message:
        return report_failure_message
    if presentation.total_document_object_count == 0:
        return "Analysis completed. The active document is empty."
    if presentation.meaningful_modeling_feature_count == 0:
        return "Analysis completed. No meaningful modeling features were found."
    if presentation.total_warning_count == 0:
        return (
            "Analysis completed with no generated warnings. "
            "This does not guarantee that the model is error-free."
        )
    return ""


def _format_report_view_summary(
    report: ModelHealthReport,
    saved_path: str | Path | None,
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
        + (str(saved_path) if saved_path is not None else "Not available")
        + "\n"
        + "------------------------------------------------------------------------------------\n"
    )


def _create_results_dialog(qt_widgets: Any, parent: Any) -> _ResultsDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle("Model Health Assistant Results")
    dialog.setModal(False)
    dialog.setMinimumSize(760, 560)

    layout = qt_widgets.QVBoxLayout(dialog)
    if hasattr(layout, "setSpacing"):
        layout.setSpacing(8)
    form_layout = qt_widgets.QFormLayout()
    layout.addLayout(form_layout)

    value_labels: dict[str, Any] = {}
    rows = (
        ("document", "Document:"),
        ("timestamp", "Analysis timestamp:"),
        ("score", "Health score:"),
        ("document_objects", "Document objects:"),
        ("meaningful_features", "Meaningful modeling features:"),
        ("sketches", "Sketches:"),
        ("warnings", "Total warnings:"),
        ("high_warnings", "High-severity warnings:"),
        ("medium_warnings", "Medium-severity warnings:"),
        ("low_warnings", "Low-severity warnings:"),
        ("report", "Report:"),
    )
    for key, caption in rows:
        value_label = qt_widgets.QLabel("", dialog)
        if key == "report" and hasattr(value_label, "setWordWrap"):
            value_label.setWordWrap(True)
        value_labels[key] = value_label
        form_layout.addRow(caption, value_label)

    status_label = qt_widgets.QLabel("", dialog)
    if hasattr(status_label, "setWordWrap"):
        status_label.setWordWrap(True)
    status_label.setVisible(False)
    layout.addWidget(status_label)

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
    warning_details.setMinimumHeight(140)
    layout.addWidget(warning_details)

    disclaimer_label = qt_widgets.QLabel(_HEALTH_SCORE_DISCLAIMER, dialog)
    if hasattr(disclaimer_label, "setWordWrap"):
        disclaimer_label.setWordWrap(True)
    layout.addWidget(disclaimer_label)

    set_stretch_factor = getattr(layout, "setStretchFactor", None)
    if callable(set_stretch_factor):
        set_stretch_factor(warning_table, 3)
        set_stretch_factor(warning_details, 2)

    button_layout = qt_widgets.QHBoxLayout()
    refresh_analysis_button = qt_widgets.QPushButton("Refresh Analysis", dialog)
    open_report_folder_button = qt_widgets.QPushButton(
        "Open Report Folder",
        dialog,
    )
    open_report_folder_button.setEnabled(False)
    copy_report_path_button = qt_widgets.QPushButton("Copy Report Path", dialog)
    copy_report_path_button.setEnabled(False)
    preview_ai_context_button = qt_widgets.QPushButton(
        "Preview AI Context",
        dialog,
    )
    preview_ai_context_button.setEnabled(False)
    test_mock_explanation_button = qt_widgets.QPushButton(
        "Test Mock Explanation",
        dialog,
    )
    test_mock_explanation_button.setEnabled(False)
    explain_selected_warning_button = qt_widgets.QPushButton(
        "Explain Selected Warning",
        dialog,
    )
    explain_selected_warning_button.setEnabled(False)
    explain_full_analysis_button = qt_widgets.QPushButton(
        "Explain Full Analysis",
        dialog,
    )
    explain_full_analysis_button.setEnabled(False)
    ask_about_analysis_button = qt_widgets.QPushButton(
        "Ask About This Analysis",
        dialog,
    )
    ask_about_analysis_button.setEnabled(False)
    cancel_ai_request_button = qt_widgets.QPushButton(
        "Cancel Request",
        dialog,
    )
    cancel_ai_request_button.setEnabled(False)
    cancel_ai_request_button.setToolTip(
        "Logically cancel the active provider request without terminating its thread."
    )
    select_feature_button = qt_widgets.QPushButton("Select Feature", dialog)
    select_feature_button.setEnabled(False)
    button_layout.addWidget(select_feature_button)
    button_layout.addWidget(open_report_folder_button)
    button_layout.addWidget(copy_report_path_button)
    button_layout.addWidget(preview_ai_context_button)
    button_layout.addWidget(test_mock_explanation_button)
    button_layout.addWidget(explain_selected_warning_button)
    button_layout.addWidget(explain_full_analysis_button)
    button_layout.addWidget(ask_about_analysis_button)
    button_layout.addWidget(cancel_ai_request_button)
    button_layout.addWidget(refresh_analysis_button)
    button_layout.addStretch()
    close_button = qt_widgets.QPushButton("Close", dialog)
    close_button.setShortcut("Esc")
    close_button.clicked.connect(dialog.close)
    button_layout.addWidget(close_button)
    layout.addLayout(button_layout)

    qt_services = _load_qt_services()
    qt_core = None
    open_report_folder_handler = None
    copy_report_path_handler = None
    if qt_services is not None:
        qt_core, qt_gui = qt_services
        open_report_folder_handler = lambda path: _open_report_folder_with_qt(
            path,
            qt_core,
            qt_gui,
        )
        clipboard_method = getattr(
            getattr(qt_widgets, "QApplication", None),
            "clipboard",
            None,
        )
        if callable(clipboard_method):
            copy_report_path_handler = lambda path: _copy_report_path_to_clipboard(
                path,
                clipboard_method(),
            )

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
        open_report_folder_button=open_report_folder_button,
        copy_report_path_button=copy_report_path_button,
        open_report_folder_handler=open_report_folder_handler,
        copy_report_path_handler=copy_report_path_handler,
        report_action_error_handler=lambda message: _show_selection_error(
            qt_widgets,
            dialog,
            message,
        ),
        refresh_analysis_button=refresh_analysis_button,
        status_label=status_label,
        preview_ai_context_button=preview_ai_context_button,
        preview_ai_context_handler=lambda report: _show_ai_context_preview(
            report,
            qt_widgets,
            dialog,
        ),
        test_mock_explanation_button=test_mock_explanation_button,
        test_mock_explanation_handler=lambda report: _show_mock_explanation(
            report,
            qt_widgets,
            dialog,
        ),
        explain_selected_warning_button=explain_selected_warning_button,
        explain_selected_warning_handler=lambda active_controller: (
            _start_selected_warning_review(
                active_controller,
                qt_widgets,
                dialog,
                qt_core,
            )
        ),
        explain_full_analysis_button=explain_full_analysis_button,
        explain_full_analysis_handler=lambda active_controller: (
            _start_full_analysis_review(
                active_controller,
                qt_widgets,
                dialog,
                qt_core,
            )
        ),
        ask_about_analysis_button=ask_about_analysis_button,
        ask_about_analysis_handler=lambda active_controller: (
            _start_custom_question_review(
                active_controller,
                qt_widgets,
                dialog,
                qt_core,
            )
        ),
        cancel_ai_request_button=cancel_ai_request_button,
        ai_workflow_status_handler=lambda active_controller: (
            _ai_workflow_status_for_controller(active_controller)
        ),
    )
    _connect_warning_table_actions(warning_table, controller)
    select_feature_button.clicked.connect(controller.select_selected_feature)
    open_report_folder_button.clicked.connect(controller.open_report_folder)
    copy_report_path_button.clicked.connect(controller.copy_report_path)
    preview_ai_context_button.clicked.connect(controller.preview_ai_context)
    test_mock_explanation_button.clicked.connect(controller.test_mock_explanation)
    explain_selected_warning_button.clicked.connect(
        controller.explain_selected_warning
    )
    explain_full_analysis_button.clicked.connect(controller.explain_full_analysis)
    ask_about_analysis_button.clicked.connect(controller.ask_about_analysis)
    cancel_ai_request_button.clicked.connect(controller.cancel_ai_operation)
    refresh_analysis_button.clicked.connect(controller.refresh_analysis)
    finished_signal = getattr(dialog, "finished", None)
    if finished_signal is not None and hasattr(finished_signal, "connect"):
        finished_signal.connect(controller.detach_ai_operation)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_results_dialog(
                held_controller
            )
        )
    return controller


def _build_ai_context_preview_json(report: ModelHealthReport) -> str:
    """Return the complete standalone privacy review for the retained report."""

    return _build_ai_context_preview_review(report).preview_json()


def _build_ai_context_preview_review(report: ModelHealthReport) -> Any:
    """Build the exact provider-neutral request shown by standalone preview."""

    from model_health_assistant.ai_consent import build_ai_request_review

    return build_ai_request_review(
        report,
        provider_identifier="local-mock",
        provider_display_name="Local Mock Provider",
        model_identifier="deterministic-local-v1",
        request_type="full-analysis",
    )


def _show_ai_context_preview(
    report: ModelHealthReport,
    qt_widgets: Any,
    parent: Any,
) -> str | None:
    """Create or update the single local context-preview child dialog."""

    try:
        review = _build_ai_context_preview_review(report)
    except Exception as exc:
        return (
            "Could not build the privacy-safe context: "
            f"{type(exc).__name__}: {exc}"
        )

    global _ai_context_preview_dialog
    if _ai_context_preview_dialog is None:
        _ai_context_preview_dialog = _create_ai_context_preview_dialog(
            qt_widgets,
            parent,
        )
    _ai_context_preview_dialog.update_review(
        review,
        lambda _consent: (
            "Standalone preview only: choose a specific explanation action before "
            "a provider request can be sent."
        ),
    )
    _ai_context_preview_dialog.show()
    return None


def _create_ai_context_preview_dialog(
    qt_widgets: Any,
    parent: Any,
) -> _AIRequestReviewDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle("Preview AI Context — Local Only")
    dialog.setModal(False)
    dialog.setMinimumSize(720, 520)

    layout = qt_widgets.QVBoxLayout(dialog)
    notice_label = qt_widgets.QLabel(AI_CONTEXT_PREVIEW_NOTICE, dialog)
    if hasattr(notice_label, "setWordWrap"):
        notice_label.setWordWrap(True)
    layout.addWidget(notice_label)

    context_view = qt_widgets.QPlainTextEdit(dialog)
    context_view.setReadOnly(True)
    layout.addWidget(context_view)

    status_label = qt_widgets.QLabel("", dialog)
    if hasattr(status_label, "setWordWrap"):
        status_label.setWordWrap(True)
    layout.addWidget(status_label)

    button_layout = qt_widgets.QHBoxLayout()
    button_layout.addStretch()
    cancel_button = qt_widgets.QPushButton("Cancel", dialog)
    cancel_button.setShortcut("Esc")
    send_button = qt_widgets.QPushButton("Send to Provider", dialog)
    send_button.setEnabled(False)
    button_layout.addWidget(cancel_button)
    button_layout.addWidget(send_button)
    layout.addLayout(button_layout)

    controller = _AIRequestReviewDialogController(
        dialog,
        context_view,
        status_label,
        send_button,
    )
    cancel_button.clicked.connect(controller.cancel)
    send_button.clicked.connect(controller.send_to_provider)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_ai_context_preview_dialog(
                held_controller
            )
        )
    return controller


def _clear_ai_context_preview_dialog(
    controller: _AIRequestReviewDialogController,
) -> None:
    global _ai_context_preview_dialog
    if _ai_context_preview_dialog is controller:
        _ai_context_preview_dialog = None


def _ai_workflow_status_for_controller(
    controller: _ResultsDialogController,
) -> Any:
    """Return non-secret provider readiness for the active results dialog."""

    try:
        return _build_ai_explanation_workflow(controller).status()
    except Exception:
        from model_health_assistant.ai_workflow import AIWorkflowStatus

        return AIWorkflowStatus(
            provider_identifier="",
            provider_display_name="Selected provider",
            model_identifier="",
            available=False,
            status_code="runtime_unavailable",
            message="Explanation provider services are unavailable in this runtime.",
        )


def _build_ai_explanation_workflow(
    controller: _ResultsDialogController,
) -> Any:
    """Create a request-time workflow from current settings and secure storage."""

    from model_health_assistant.ai_workflow import AIExplanationWorkflow
    from model_health_assistant.credentials import ProviderCredentialStore
    from model_health_assistant.freecad_provider_settings import (
        FreeCADProviderSettingsBackend,
    )
    from model_health_assistant.keyring_backend import KeyringCredentialBackend
    from model_health_assistant.provider_settings import ProviderSettingsStore

    app_module = (
        controller.document_context.app_module
        if controller.document_context is not None
        else App
    )
    if app_module is None:
        raise RuntimeError("FreeCAD provider settings are unavailable")
    settings_store = ProviderSettingsStore(
        FreeCADProviderSettingsBackend(app_module)
    )
    credential_store = ProviderCredentialStore(KeyringCredentialBackend())
    return AIExplanationWorkflow(
        settings_store.load,
        credential_store=credential_store,
    )


def _start_selected_warning_review(
    controller: _ResultsDialogController,
    qt_widgets: Any,
    parent: Any,
    qt_core: Any | None,
) -> str | None:
    """Build and show final consent for only the selected warning."""

    report = controller.report
    warning = controller.current_selected_warning()
    if report is None or warning is None:
        return "Select one warning from the completed analysis first."
    if qt_core is None:
        return "Background provider execution is unavailable in this Qt runtime."
    target_error = _validate_selected_warning_snapshot(
        controller.document_context,
        warning,
    )
    if target_error:
        return target_error

    try:
        workflow = _build_ai_explanation_workflow(controller)
        review = workflow.build_review(
            report,
            request_type="selected-warning",
            selected_warning_identifier=warning.warning_id,
            selected_warning_rule_identifier=warning.rule_id,
            selected_warning_feature_internal_name=warning.feature_internal_name,
        )
    except Exception as exc:
        return _explanation_error_text(exc)

    def send_handler(consent: Any) -> str | None:
        current_report = controller.report
        current_warning = controller.current_selected_warning()
        if current_report is None or current_warning is None:
            return "The report or warning selection changed; review the request again."
        validation_error = _validate_selected_warning_snapshot(
            controller.document_context,
            current_warning,
        )
        if validation_error:
            return validation_error
        if controller._ai_operation_running:
            return "Another explanation request is already running."
        try:
            plan = workflow.prepare_execution(
                consent,
                current_report,
                selected_warning_identifier=current_warning.warning_id,
                selected_warning_rule_identifier=current_warning.rule_id,
                selected_warning_feature_internal_name=(
                    current_warning.feature_internal_name
                ),
            )
        except Exception as exc:
            return _explanation_error_text(exc)
        return _launch_prepared_ai_execution(
            controller,
            plan,
            qt_core,
            parent,
            progress_message="Request in progress. The model remains read-only.",
            on_success=lambda response, ticket: (
                _selected_explanation_succeeded(
                    controller,
                    response,
                    current_warning,
                    review,
                    qt_widgets,
                    parent,
                    ticket=ticket,
                )
            ),
        )

    global _ai_context_preview_dialog
    if _ai_context_preview_dialog is None:
        _ai_context_preview_dialog = _create_ai_context_preview_dialog(
            qt_widgets,
            parent,
        )
    _ai_context_preview_dialog.dialog.setWindowTitle(
        "Final Review - Explain Selected Warning"
    )
    _ai_context_preview_dialog.update_review(review, send_handler)
    _ai_context_preview_dialog.show()
    return None


def _validate_selected_warning_snapshot(
    context: _AnalyzedDocumentContext | None,
    warning: WarningDisplayItem,
) -> str | None:
    """Fail closed if the analyzed document or warning target changed."""

    error = _select_feature_by_internal_name(
        context,
        warning.feature_internal_name,
        perform_selection=False,
    )
    if error:
        return (
            error
            + " Refresh the analysis and review a newly generated request."
        )
    return None


def _start_full_analysis_review(
    controller: _ResultsDialogController,
    qt_widgets: Any,
    parent: Any,
    qt_core: Any | None,
) -> str | None:
    """Build a distinct fresh consent review for the full analysis snapshot."""

    report = controller.report
    if report is None:
        return "Run an analysis before requesting a full explanation."
    if qt_core is None:
        return "Background provider execution is unavailable in this Qt runtime."
    try:
        workflow = _build_ai_explanation_workflow(controller)
        review = workflow.build_review(
            report,
            request_type="full-analysis",
        )
    except Exception as exc:
        return _explanation_error_text(exc)

    def send_handler(consent: Any) -> str | None:
        current_report = controller.report
        if current_report is None:
            return "The completed report changed; review a fresh request."
        if controller._ai_operation_running:
            return "Another explanation request is already running."
        try:
            plan = workflow.prepare_execution(consent, current_report)
        except Exception as exc:
            return _explanation_error_text(exc)
        return _launch_prepared_ai_execution(
            controller,
            plan,
            qt_core,
            parent,
            progress_message=(
                "Full-analysis request in progress. The model remains read-only."
            ),
            on_success=lambda response, ticket: (
                _full_explanation_succeeded(
                    controller,
                    response,
                    review,
                    qt_widgets,
                    parent,
                    ticket=ticket,
                )
            ),
        )

    global _ai_context_preview_dialog
    if _ai_context_preview_dialog is None:
        _ai_context_preview_dialog = _create_ai_context_preview_dialog(
            qt_widgets,
            parent,
        )
    _ai_context_preview_dialog.dialog.setWindowTitle(
        "Final Review - Explain Full Analysis"
    )
    _ai_context_preview_dialog.update_review(review, send_handler)
    _ai_context_preview_dialog.show()
    return None


def _start_custom_question_review(
    controller: _ResultsDialogController,
    qt_widgets: Any,
    parent: Any,
    qt_core: Any | None,
) -> str | None:
    """Collect one ephemeral question and show its exact consent review."""

    report = controller.report
    if report is None:
        return "Run an analysis before asking a grounded question."
    if qt_core is None:
        return "Background provider execution is unavailable in this Qt runtime."

    question, question_error = _prompt_for_grounded_question(qt_widgets, parent)
    if question_error is not None:
        return question_error
    try:
        workflow = _build_ai_explanation_workflow(controller)
        review = workflow.build_review(
            report,
            request_type="custom-question",
            user_question=question,
        )
    except Exception as exc:
        return _explanation_error_text(exc)

    def send_handler(consent: Any) -> str | None:
        current_report = controller.report
        if current_report is None:
            return "The completed report changed; review a fresh question."
        if controller._ai_operation_running:
            return "Another explanation request is already running."
        try:
            plan = workflow.prepare_execution(consent, current_report)
        except Exception as exc:
            return _explanation_error_text(exc)
        return _launch_prepared_ai_execution(
            controller,
            plan,
            qt_core,
            parent,
            progress_message=(
                "Grounded-question request in progress. The model remains read-only."
            ),
            on_success=lambda response, ticket: (
                _custom_question_succeeded(
                    controller,
                    response,
                    review,
                    qt_widgets,
                    parent,
                    ticket=ticket,
                )
            ),
        )

    global _ai_context_preview_dialog
    if _ai_context_preview_dialog is None:
        _ai_context_preview_dialog = _create_ai_context_preview_dialog(
            qt_widgets,
            parent,
        )
    _ai_context_preview_dialog.dialog.setWindowTitle(
        "Final Review - Ask About This Analysis"
    )
    _ai_context_preview_dialog.update_review(review, send_handler)
    _ai_context_preview_dialog.show()
    return None


def _prompt_for_grounded_question(
    qt_widgets: Any,
    parent: Any,
) -> tuple[str | None, str | None]:
    """Return one unpersisted question from Qt's standard multiline dialog."""

    from model_health_assistant.ai_prompts import MAX_GROUNDED_QUESTION_CHARACTERS

    input_dialog = getattr(qt_widgets, "QInputDialog", None)
    get_multiline_text = getattr(input_dialog, "getMultiLineText", None)
    if not callable(get_multiline_text):
        return None, "Question entry is unavailable in this Qt runtime."
    result = get_multiline_text(
        parent,
        "Ask About This Analysis",
        (
            "Ask one question grounded only in the current model-health analysis "
            f"(maximum {MAX_GROUNDED_QUESTION_CHARACTERS} characters):"
        ),
        "",
    )
    if not isinstance(result, tuple) or len(result) < 2:
        return None, "Question entry failed safely."
    question, accepted = result[0], result[1]
    if not accepted:
        return None, "Question entry was cancelled. No data was transmitted."
    if not isinstance(question, str):
        return None, "Question entry returned invalid text."
    return question, None


def _launch_prepared_ai_execution(
    controller: _ResultsDialogController,
    plan: Any,
    qt_core: Any,
    parent: Any,
    *,
    progress_message: str,
    on_success: Any,
) -> str | None:
    """Start one prevalidated plan and ticket-gate every UI callback."""

    if not controller.begin_ai_operation(
        progress_message,
        request_type=plan.request_type,
        provider_identifier=plan.provider_identifier,
    ):
        return "Another explanation request is already running."
    ticket = controller.active_ai_ticket
    if ticket is None:
        return "The explanation request could not acquire a UI request ticket."

    from model_health_assistant.qt_async import start_qt_background_operation

    try:
        operation = start_qt_background_operation(
            qt_core,
            parent,
            plan.execute,
            lambda response: (
                on_success(response, ticket)
                if controller.is_ai_operation_current(ticket)
                else None
            ),
            lambda error: _explanation_failed(
                controller,
                error,
                ticket=ticket,
                request_type=plan.request_type,
                provider_identifier=plan.provider_identifier,
                model_identifier=getattr(plan, "model_identifier", None),
            ),
        )
    except Exception as exc:
        controller.complete_ai_operation(
            "Explanation failed: " + _explanation_error_text(exc),
            ticket=ticket,
            failed=True,
        )
        return _explanation_error_text(exc)
    controller.attach_ai_operation(ticket, operation)
    return None


def _selected_explanation_succeeded(
    controller: _ResultsDialogController,
    response: Any,
    source_warning: WarningDisplayItem,
    review: Any,
    qt_widgets: Any,
    parent: Any,
    *,
    ticket: object | None = None,
) -> None:
    from model_health_assistant.ai_result_presentation import (
        format_selected_warning_explanation,
    )
    from model_health_assistant.ai_traceability import (
        build_ai_response_traceability,
    )

    try:
        from model_health_assistant.ai_consent import AIConsentInvalidatedError

        current_report = controller.report
        current_warning = controller.current_selected_warning()
        if current_report is None or current_warning is None:
            raise AIConsentInvalidatedError(
                "The report or selected warning changed while the provider request "
                "was running. Refresh and review a new request."
            )
        target_error = _validate_selected_warning_snapshot(
            controller.document_context,
            current_warning,
        )
        if target_error:
            raise AIConsentInvalidatedError(
                target_error + " Refresh and review a new request."
            )
        traceability = build_ai_response_traceability(
            review,
            response,
            current_report,
            selected_warning_identifier=current_warning.warning_id,
            selected_warning_rule_identifier=current_warning.rule_id,
            selected_warning_feature_internal_name=(
                current_warning.feature_internal_name
            ),
        )
        text = format_selected_warning_explanation(
            response,
            source_warning,
            review,
            traceability=traceability,
        )
    except Exception as exc:
        _explanation_failed(
            controller,
            exc,
            ticket=ticket,
            request_type=review.request_type,
            provider_identifier=review.provider_identifier,
            model_identifier=review.model_identifier,
        )
        return
    if not controller.complete_ai_operation(
        "Explanation completed. The original warning and advisory AI text are "
        "shown separately; no model changes were made.",
        ticket=ticket,
    ):
        return
    _show_ai_explanation_text(
        text,
        "Selected Warning AI Explanation",
        qt_widgets,
        parent,
    )


def _full_explanation_succeeded(
    controller: _ResultsDialogController,
    response: Any,
    review: Any,
    qt_widgets: Any,
    parent: Any,
    *,
    ticket: object | None = None,
) -> None:
    from model_health_assistant.ai_result_presentation import (
        format_full_analysis_explanation,
    )
    from model_health_assistant.ai_traceability import (
        build_ai_response_traceability,
    )

    try:
        from model_health_assistant.ai_consent import AIConsentInvalidatedError

        current_report = controller.report
        if current_report is None:
            raise AIConsentInvalidatedError(
                "The completed report changed while the provider request was "
                "running. Refresh and review a new request."
            )
        traceability = build_ai_response_traceability(
            review,
            response,
            current_report,
        )
        text = format_full_analysis_explanation(
            response,
            review,
            traceability=traceability,
        )
    except Exception as exc:
        _explanation_failed(
            controller,
            exc,
            ticket=ticket,
            request_type=review.request_type,
            provider_identifier=review.provider_identifier,
            model_identifier=review.model_identifier,
        )
        return
    if not controller.complete_ai_operation(
        "Full-analysis explanation completed. The advisory response is read-only; "
        "no model changes were made.",
        ticket=ticket,
    ):
        return
    _show_ai_explanation_text(
        text,
        "Full Analysis AI Explanation",
        qt_widgets,
        parent,
    )


def _custom_question_succeeded(
    controller: _ResultsDialogController,
    response: Any,
    review: Any,
    qt_widgets: Any,
    parent: Any,
    *,
    ticket: object | None = None,
) -> None:
    from model_health_assistant.ai_result_presentation import (
        format_grounded_question_response,
    )
    from model_health_assistant.ai_traceability import (
        build_ai_response_traceability,
    )

    try:
        from model_health_assistant.ai_consent import AIConsentInvalidatedError

        current_report = controller.report
        if current_report is None:
            raise AIConsentInvalidatedError(
                "The completed report changed while the provider request was "
                "running. Refresh and review a new question."
            )
        traceability = build_ai_response_traceability(
            review,
            response,
            current_report,
        )
        text = format_grounded_question_response(
            response,
            review,
            traceability=traceability,
        )
    except Exception as exc:
        _explanation_failed(
            controller,
            exc,
            ticket=ticket,
            request_type=review.request_type,
            provider_identifier=review.provider_identifier,
            model_identifier=review.model_identifier,
        )
        return
    if not controller.complete_ai_operation(
        "Grounded answer completed. It is one-shot advisory text based only on "
        "the reviewed context; no model changes were made.",
        ticket=ticket,
    ):
        return
    _show_ai_explanation_text(
        text,
        "Ask About This Analysis - Grounded AI Answer",
        qt_widgets,
        parent,
    )


def _explanation_failed(
    controller: _ResultsDialogController,
    error: BaseException,
    *,
    ticket: object | None = None,
    request_type: str | None = None,
    provider_identifier: str | None = None,
    model_identifier: str | None = None,
) -> None:
    accepted = controller.complete_ai_operation(
        "Explanation failed: " + _explanation_error_text(error),
        ticket=ticket,
        failed=True,
    )
    if not accepted:
        return
    from model_health_assistant.ai_error_handling import (
        format_redacted_ai_diagnostic,
    )

    diagnostic = format_redacted_ai_diagnostic(
        error,
        request_type=request_type,
        provider_identifier=provider_identifier,
        model_identifier=model_identifier,
    )
    app_module = (
        controller.document_context.app_module
        if controller.document_context is not None
        else App
    )
    _print_message(f"Model Health Assistant: {diagnostic}\n", app_module)


def _explanation_error_text(error: BaseException) -> str:
    """Map workflow/provider failures to concise messages without secret data."""

    from model_health_assistant.ai_consent import AIConsentError
    from model_health_assistant.ai_error_handling import categorize_ai_error

    if isinstance(error, AIConsentError):
        return str(error)
    return categorize_ai_error(error).user_message


def _show_ai_explanation_text(
    text: str,
    title: str,
    qt_widgets: Any,
    parent: Any,
) -> None:
    global _ai_explanation_dialog
    if _ai_explanation_dialog is None:
        _ai_explanation_dialog = _create_ai_explanation_dialog(
            qt_widgets,
            parent,
        )
    _ai_explanation_dialog.dialog.setWindowTitle(title)
    _ai_explanation_dialog.update_text(text)
    _ai_explanation_dialog.show()


def _create_ai_explanation_dialog(
    qt_widgets: Any,
    parent: Any,
) -> _ReadOnlyTextDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle("AI Explanation")
    dialog.setModal(False)
    dialog.setMinimumSize(760, 560)

    layout = qt_widgets.QVBoxLayout(dialog)
    notice_label = qt_widgets.QLabel(
        "Read-only advisory explanation. No model actions are available here.",
        dialog,
    )
    if hasattr(notice_label, "setWordWrap"):
        notice_label.setWordWrap(True)
    layout.addWidget(notice_label)
    explanation_view = qt_widgets.QPlainTextEdit(dialog)
    explanation_view.setReadOnly(True)
    layout.addWidget(explanation_view)
    close_button = qt_widgets.QPushButton("Close", dialog)
    close_button.setShortcut("Esc")
    close_button.clicked.connect(dialog.close)
    button_layout = qt_widgets.QHBoxLayout()
    button_layout.addStretch()
    button_layout.addWidget(close_button)
    layout.addLayout(button_layout)

    controller = _ReadOnlyTextDialogController(dialog, explanation_view)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_ai_explanation_dialog(
                held_controller
            )
        )
    return controller


def _clear_ai_explanation_dialog(
    controller: _ReadOnlyTextDialogController,
) -> None:
    global _ai_explanation_dialog
    if _ai_explanation_dialog is controller:
        _ai_explanation_dialog = None


def _show_mock_explanation(
    report: ModelHealthReport,
    qt_widgets: Any,
    parent: Any,
) -> str | None:
    """Run the deterministic local provider and show its validated response."""

    try:
        from model_health_assistant.ai_provider import (
            AIInvalidResponseError,
            AIProviderError,
            AIProviderUnavailableError,
        )
        from model_health_assistant.ai_service import (
            AIContextBuildError,
            AIExplanationService,
            AIRequestBuildError,
        )
        from model_health_assistant.mock_ai_provider import LocalMockAIProvider
    except Exception as exc:
        return (
            "The local explanation components are unavailable: "
            f"{type(exc).__name__}: {exc}"
        )

    try:
        response = AIExplanationService(LocalMockAIProvider()).explain_report(report)
        explanation_text = _format_mock_explanation_response(response)
    except AIProviderUnavailableError:
        return "The deterministic local test provider is unavailable."
    except AIInvalidResponseError:
        return (
            "The deterministic local provider returned an invalid structured "
            "response. The result was not displayed."
        )
    except AIContextBuildError:
        return (
            "The privacy-safe context could not be built. No explanation was "
            "displayed."
        )
    except AIRequestBuildError:
        return (
            "The provider-neutral explanation request could not be built. No "
            "explanation was displayed."
        )
    except AIProviderError as exc:
        return (
            "The deterministic local provider rejected the request safely: "
            f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:
        return (
            "Could not produce the deterministic local explanation: "
            f"{type(exc).__name__}: {exc}"
        )

    global _mock_explanation_dialog
    if _mock_explanation_dialog is None:
        _mock_explanation_dialog = _create_mock_explanation_dialog(
            qt_widgets,
            parent,
        )
    _mock_explanation_dialog.update_text(explanation_text)
    _mock_explanation_dialog.show()
    return None


def _format_mock_explanation_response(response: Any) -> str:
    """Format a validated explanation response for a read-only local view."""

    lines = [
        f"Provider: {response.provider_display_name} "
        f"({response.provider_identifier})",
        "",
        "Summary:",
        response.overall_summary,
        "",
        "Warning explanations:",
    ]

    if not response.warning_explanations:
        lines.append("No source warnings were available to explain.")

    for index, warning in enumerate(response.warning_explanations, start=1):
        feature_name = warning.affected_feature_internal_name or "(document-level)"
        lines.extend(
            (
                "",
                f"Warning {index}",
                f"Rule: {warning.rule_id}",
                f"Feature: {feature_name}",
                f"Severity: {warning.severity}",
                "Explanation:",
                warning.explanation,
                "Why it matters:",
                warning.why_it_matters,
                "Manual review steps:",
            )
        )
        if warning.manual_review_steps:
            lines.extend(
                f"{step_index}. {step}"
                for step_index, step in enumerate(
                    warning.manual_review_steps,
                    start=1,
                )
            )
        else:
            lines.append("None supplied.")
        if warning.confidence:
            lines.extend(("Confidence:", warning.confidence))
        lines.append("Limitations:")
        if warning.limitations:
            lines.extend(f"- {limitation}" for limitation in warning.limitations)
        else:
            lines.append("- None supplied.")

    lines.extend(("", "Overall limitations:"))
    if response.limitations:
        lines.extend(f"- {limitation}" for limitation in response.limitations)
    else:
        lines.append("- None supplied.")
    return "\n".join(lines)


def _create_mock_explanation_dialog(
    qt_widgets: Any,
    parent: Any,
) -> _ReadOnlyTextDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle("Mock Explanation - Local Test Provider")
    dialog.setModal(False)
    dialog.setMinimumSize(720, 520)

    layout = qt_widgets.QVBoxLayout(dialog)
    notice_label = qt_widgets.QLabel(MOCK_EXPLANATION_NOTICE, dialog)
    if hasattr(notice_label, "setWordWrap"):
        notice_label.setWordWrap(True)
    layout.addWidget(notice_label)

    explanation_view = qt_widgets.QPlainTextEdit(dialog)
    explanation_view.setReadOnly(True)
    layout.addWidget(explanation_view)

    button_layout = qt_widgets.QHBoxLayout()
    button_layout.addStretch()
    close_button = qt_widgets.QPushButton("Close", dialog)
    close_button.setShortcut("Esc")
    close_button.clicked.connect(dialog.close)
    button_layout.addWidget(close_button)
    layout.addLayout(button_layout)

    controller = _ReadOnlyTextDialogController(dialog, explanation_view)
    destroyed_signal = getattr(dialog, "destroyed", None)
    if destroyed_signal is not None and hasattr(destroyed_signal, "connect"):
        destroyed_signal.connect(
            lambda *_args, held_controller=controller: _clear_mock_explanation_dialog(
                held_controller
            )
        )
    return controller


def _clear_mock_explanation_dialog(
    controller: _ReadOnlyTextDialogController,
) -> None:
    global _mock_explanation_dialog
    if _mock_explanation_dialog is controller:
        _mock_explanation_dialog = None


def _connect_warning_table_actions(
    warning_table: Any,
    controller: _ResultsDialogController,
) -> None:
    """Keep single-click details and double-click selection behavior distinct."""

    warning_table.itemSelectionChanged.connect(
        controller.update_selected_warning_details
    )
    warning_table.itemDoubleClicked.connect(controller.select_selected_feature)


def _configure_warning_table(qt_widgets: Any, warning_table: Any) -> None:
    """Configure the warning table as a read-only, row-selecting view."""

    warning_table.setColumnCount(len(_WARNING_TABLE_HEADERS))
    warning_table.setHorizontalHeaderLabels(list(_WARNING_TABLE_HEADERS))
    warning_table.setSortingEnabled(False)
    for column_index, width in enumerate(_WARNING_COLUMN_WIDTHS):
        set_column_width = getattr(warning_table, "setColumnWidth", None)
        if callable(set_column_width):
            set_column_width(column_index, width)
    horizontal_header = getattr(warning_table, "horizontalHeader", None)
    if callable(horizontal_header):
        header = horizontal_header()
        set_stretch_last = getattr(header, "setStretchLastSection", None)
        if callable(set_stretch_last):
            set_stretch_last(True)

    item_view = getattr(qt_widgets, "QAbstractItemView", None)
    if item_view is None:
        return
    warning_table.setEditTriggers(item_view.NoEditTriggers)
    warning_table.setSelectionBehavior(item_view.SelectRows)
    warning_table.setSelectionMode(item_view.SingleSelection)


def _summary_display_values(
    presentation: AnalysisPresentation,
) -> dict[str, str]:
    """Format existing presentation facts consistently for summary labels."""

    return {
        "document": presentation.document_name,
        "timestamp": presentation.analysis_timestamp or "Not available",
        "score": f"{presentation.health_score}/100",
        "document_objects": str(presentation.total_document_object_count),
        "meaningful_features": str(
            presentation.meaningful_modeling_feature_count
        ),
        "sketches": str(presentation.total_sketch_count),
        "warnings": str(presentation.total_warning_count),
        "high_warnings": str(presentation.high_warning_count),
        "medium_warnings": str(presentation.medium_warning_count),
        "low_warnings": str(presentation.low_warning_count),
        "report": (
            str(presentation.report_path)
            if presentation.report_path is not None
            else "Not available"
        ),
    }


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


def _retained_analysis_state_notice(
    context: _AnalyzedDocumentContext | None,
) -> str:
    """Describe document changes without invalidating the retained report."""

    if context is None:
        return ""
    if not context.document_name:
        return (
            "The analyzed document identity is unavailable; this view uses the "
            "retained completed analysis snapshot."
        )

    get_document = getattr(context.app_module, "getDocument", None)
    if not callable(get_document):
        return (
            "The analyzed document cannot be resolved in FreeCAD; this view uses "
            "the retained completed analysis snapshot."
        )
    try:
        open_document = get_document(context.document_name)
    except Exception:
        open_document = None
    if open_document is None:
        return (
            "The analyzed document is no longer open; this view uses the retained "
            "completed analysis snapshot."
        )
    if open_document is not context.document:
        return (
            "A different document now uses the analyzed document name; this view "
            "uses the retained completed analysis snapshot."
        )

    active_document = getattr(context.app_module, "ActiveDocument", context.document)
    if active_document is None:
        return (
            "No document is currently active; this view uses the retained completed "
            "analysis snapshot."
        )
    if active_document is not context.document:
        return (
            "The active document has changed since analysis; this view uses the "
            "retained completed analysis snapshot."
        )
    return ""


def _show_selection_error(qt_widgets: Any, parent: Any, message: str) -> None:
    message_box = getattr(qt_widgets, "QMessageBox", None)
    show_warning = getattr(message_box, "warning", None)
    if callable(show_warning):
        show_warning(parent, "Model Health Assistant", message)


def _validated_report_path(
    report_path: str | Path | None,
) -> tuple[Path | None, str | None]:
    """Return an absolute existing JSON-file path or a friendly local error."""

    if report_path is None or not str(report_path).strip():
        return None, "No saved report path is available."
    report_path_text = str(report_path)
    try:
        path = Path(report_path).expanduser().resolve(strict=True)
        is_file = path.is_file()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, (
            "The saved report file has been moved, deleted, or is unavailable: "
            f"'{report_path_text}' ({type(exc).__name__}, platform={sys.platform})."
        )
    if not is_file:
        return None, f"The saved report path does not identify a file: '{path}'."
    return path, None


def _open_report_folder_with_qt(
    report_path: Path,
    qt_core: Any,
    qt_gui: Any,
) -> str | None:
    """Ask Qt to open a local directory URL without opening the JSON report."""

    q_url = getattr(qt_core, "QUrl", None)
    from_local_file = getattr(q_url, "fromLocalFile", None)
    desktop_services = getattr(qt_gui, "QDesktopServices", None)
    open_url = getattr(desktop_services, "openUrl", None)
    if not callable(from_local_file) or not callable(open_url):
        return "Opening local report folders is unavailable in this Qt runtime."
    folder_path = report_path.parent
    try:
        folder_url = from_local_file(str(folder_path))
        opened = open_url(folder_url)
    except Exception as exc:  # noqa: BLE001 - defensive boundary around Qt runtime APIs.
        return (
            f"The report folder '{folder_path}' could not be opened "
            f"({type(exc).__name__}, platform={sys.platform})."
        )
    if opened is False:
        return (
            f"The operating system could not open the report folder '{folder_path}' "
            f"(platform={sys.platform})."
        )
    return None


def _copy_report_path_to_clipboard(
    report_path: Path,
    clipboard: Any,
) -> str | None:
    """Copy only a complete filesystem path to the Qt clipboard."""

    set_text = getattr(clipboard, "setText", None)
    if not callable(set_text):
        return "The clipboard is unavailable in the current Qt runtime."
    set_text(str(report_path))
    return None


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
    close_request_state = getattr(controller, "close_ai_request_state", None)
    if callable(close_request_state):
        close_request_state()
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


def _load_qt_services() -> tuple[Any, Any] | None:
    try:  # pragma: no cover - depends on FreeCAD's bundled Qt runtime.
        from PySide2 import QtCore, QtGui  # type: ignore

        return QtCore, QtGui
    except Exception:  # pragma: no cover
        try:
            from PySide import QtCore, QtGui  # type: ignore

            return QtCore, QtGui
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
