"""Guarded FreeCAD/PySide interface for provider settings and credentials.

Opening, saving, or closing the dialog cannot make a network request. Only the
explicitly confirmed Test Connection action can start a minimal provider call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from model_health_assistant.ai_connection import (
    AIConnectionTestResult,
    AIConnectionTester,
)
from model_health_assistant.ai_error_handling import categorize_ai_error
from model_health_assistant.ai_model_capabilities import (
    get_openai_model_capabilities,
    validate_model_identifier_syntax,
)
from model_health_assistant.ai_provider import (
    AIInvalidRequestError,
    AIProviderError,
    AIUnsupportedModelError,
)
from model_health_assistant.ai_request_state import AIRequestStateController
from model_health_assistant.ai_usage_presentation import (
    build_ai_usage_presentation,
)

from model_health_assistant.credentials import (
    CredentialAlreadyConfiguredError,
    CredentialBackendUnavailableError,
    CredentialError,
    CredentialNotConfiguredError,
    ProviderCredentialStatus,
    ProviderCredentialStore,
)
from model_health_assistant.freecad_provider_settings import (
    FreeCADProviderSettingsBackend,
)
from model_health_assistant.keyring_backend import KeyringCredentialBackend
from model_health_assistant.openai_provider import (
    DEFAULT_OPENAI_MODEL_IDENTIFIER,
    DEFAULT_OPENAI_REASONING_EFFORT,
    DEFAULT_OPENAI_RESPONSE_VERBOSITY,
    SUPPORTED_OPENAI_MODEL_IDENTIFIERS,
    OpenAIExplanationProvider,
    OpenAIProviderConfiguration,
)
from model_health_assistant.provider_settings import (
    LOCAL_MOCK_PROVIDER_IDENTIFIER,
    MAXIMUM_OUTPUT_TOKENS,
    MAXIMUM_REQUEST_TIMEOUT_SECONDS,
    MAXIMUM_RETRY_COUNT,
    MINIMUM_OUTPUT_TOKENS,
    MINIMUM_REQUEST_TIMEOUT_SECONDS,
    OPENAI_PROVIDER_IDENTIFIER,
    ProviderSettings,
    ProviderSettingsError,
    ProviderSettingsStore,
)
from model_health_assistant.qt_async import start_qt_background_operation

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCADGui as Gui  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    Gui = None


PROVIDER_SETTINGS_COMMAND_NAME = "MHA_ProviderSettings"
PROVIDER_SETTINGS_WINDOW_TITLE = "Model Health Assistant Provider Settings"
PROVIDER_NOTICE = (
    "You supply your own OpenAI API key. OpenAI API usage may incur charges "
    "from the provider."
)
PRIVACY_NOTICE = (
    "No CAD data is sent while a key or these settings are stored. Test "
    "Connection sends a small provider request and no CAD data. Connection "
    "testing occurs only after explicit confirmation."
)
CREDENTIAL_STORAGE_NOTICE = (
    "The API key is stored by the operating-system credential backend. It is "
    "never stored in the project repository or in FreeCAD preferences."
)

ConfirmRemoval = Callable[[], bool]
ConfirmConnectionTest = Callable[[str], bool]
ConnectionProviderFactory = Callable[
    [OpenAIProviderConfiguration],
    AIConnectionTester,
]
WorkerLauncher = Callable[
    [Callable[[], object], Callable[[object], None], Callable[[BaseException], None]],
    object,
]


@dataclass(slots=True)
class _ProviderSettingsWidgets:
    dialog: Any
    provider_combo: Any
    api_key_input: Any
    credential_status_label: Any
    model_combo: Any
    timeout_input: Any
    retry_input: Any
    maximum_output_input: Any
    save_key_button: Any
    replace_key_button: Any
    remove_key_button: Any
    test_connection_button: Any
    cancel_request_button: Any
    save_settings_button: Any
    close_button: Any
    operation_status_label: Any


class _ProviderSettingsDialogController:
    """Bind tested settings and credential services to small Qt-like widgets."""

    def __init__(
        self,
        widgets: _ProviderSettingsWidgets,
        settings_store: ProviderSettingsStore,
        credential_store: ProviderCredentialStore,
        confirm_removal: ConfirmRemoval,
        confirm_connection_test: ConfirmConnectionTest | None = None,
        connection_provider_factory: ConnectionProviderFactory | None = None,
        worker_launcher: WorkerLauncher | None = None,
    ) -> None:
        self.widgets = widgets
        self._settings_store = settings_store
        self._credential_store = credential_store
        self._confirm_removal = confirm_removal
        self._confirm_connection_test = confirm_connection_test
        self._connection_provider_factory = connection_provider_factory
        self._worker_launcher = worker_launcher
        self._loaded_settings = ProviderSettings()
        self._credential_configured = False
        self._credential_backend_available = False
        self._connection_operation: object | None = None
        self._request_state = AIRequestStateController()

    def refresh(self) -> None:
        """Reload non-secret state while keeping the API key field empty."""

        request_was_running = self._request_state.is_busy
        self.clear_api_key_input()
        try:
            settings = self._settings_store.load()
        except ProviderSettingsError:
            settings = ProviderSettings()
            self._set_operation_status(
                "Provider settings could not be loaded; safe defaults are shown."
            )
        else:
            if request_was_running:
                self._set_operation_status(
                    self._request_state.snapshot.progress_message
                )
            else:
                self._set_operation_status("")
        self._loaded_settings = settings
        self._apply_settings(settings)
        self.refresh_credential_status()
        self._update_openai_control_state()

    def show(self) -> None:
        self.widgets.dialog.show()
        raise_dialog = getattr(self.widgets.dialog, "raise_", None)
        if callable(raise_dialog):
            raise_dialog()
        activate_window = getattr(self.widgets.dialog, "activateWindow", None)
        if callable(activate_window):
            activate_window()

    def close(self) -> None:
        self._detach_connection_request()
        self.clear_api_key_input()
        self.widgets.dialog.close()

    def dialog_finished(self) -> None:
        """Detach late worker callbacks when the reusable dialog is closed."""

        self._detach_connection_request()
        self.clear_api_key_input()

    def clear_api_key_input(self) -> None:
        self.widgets.api_key_input.clear()

    def provider_changed(self) -> None:
        self._update_openai_control_state()

    def test_connection(self) -> None:
        """Confirm and start one minimal real request off the GUI thread."""

        if not self._require_openai_selection():
            return
        if self._connection_is_running():
            self._set_operation_status("A provider connection test is already running.")
            return
        model_identifier, model_error = self._validated_openai_model_identifier()
        if model_error is not None:
            self._set_operation_status(model_error)
            return
        self.refresh_credential_status()
        if not self._credential_backend_available:
            self._set_operation_status("Secure credential storage unavailable.")
            return
        if not self._credential_configured:
            self._set_operation_status("No secure OpenAI key is configured.")
            return
        if (
            self._connection_provider_factory is None
            or self._worker_launcher is None
            or self._confirm_connection_test is None
        ):
            self._set_operation_status(
                "Connection testing is unavailable in this Qt runtime."
            )
            return

        try:
            configuration = self._openai_configuration(model_identifier)
            provider = self._connection_provider_factory(configuration)
        except (AIProviderError, TypeError, ValueError) as error:
            if isinstance(error, AIProviderError):
                message = categorize_ai_error(error).user_message
            else:
                message = "The OpenAI settings are invalid."
            self._set_operation_status(message)
            return
        try:
            sdk_available = provider.is_available()
        except Exception:
            sdk_available = False
        if not sdk_available:
            self._set_operation_status("OpenAI SDK dependency is unavailable.")
            return

        confirmation_text = (
            "Send one minimal real request to OpenAI using model "
            f"{configuration.model_identifier}? The request may incur a small "
            "provider charge. No CAD data, model-health report, document name, "
            "feature label, file, or geometry will be sent."
        )
        if not self._confirm_connection_test(confirmation_text):
            self._set_operation_status("Connection test cancelled. No request was sent.")
            return

        progress_message = "Testing the OpenAI connection..."
        try:
            ticket = self._request_state.begin(
                "connection-test",
                OPENAI_PROVIDER_IDENTIFIER,
                progress_message=progress_message,
            )
        except Exception:
            self._set_operation_status(
                "A provider connection test is already running."
            )
            self._update_openai_control_state()
            return
        self._set_operation_status(progress_message)
        self._update_openai_control_state()
        try:
            operation = self._worker_launcher(
                provider.test_connection,
                lambda result, held_ticket=ticket: self._connection_test_succeeded(
                    held_ticket,
                    configuration.model_identifier,
                    result,
                ),
                lambda error, held_ticket=ticket: self._connection_test_failed(
                    held_ticket,
                    error,
                ),
            )
        except Exception:
            self._connection_operation = None
            failure_message = "The connection-test worker could not start."
            self._request_state.accept_failure(
                ticket,
                progress_message=failure_message,
            )
            self._set_operation_status(failure_message)
            self._update_openai_control_state()
            return
        if self._request_state.active_ticket == ticket:
            self._connection_operation = operation
        else:
            self._connection_operation = None

    def cancel_connection_request(self) -> bool:
        """Logically cancel the active connection test without stopping its thread."""

        ticket = self._request_state.active_ticket
        if ticket is None:
            self._set_operation_status("No provider request is currently running.")
            self._update_openai_control_state()
            return False
        cancellation_message = (
            "Cancelled. The in-progress connection-test result will be ignored."
        )
        if not self._request_state.cancel(
            ticket,
            progress_message=cancellation_message,
        ):
            return False
        operation = self._connection_operation
        cancel = getattr(operation, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
        self._connection_operation = None
        self._set_operation_status(cancellation_message)
        self._update_openai_control_state()
        return True

    def _detach_connection_request(self) -> None:
        operation = self._connection_operation
        if self._request_state.is_busy:
            self._request_state.detach(
                progress_message="Connection test detached from the closed dialog."
            )
        detach = getattr(operation, "detach", None)
        if callable(detach):
            try:
                detach()
            except Exception:
                pass
        self._connection_operation = None
        self._update_openai_control_state()

    def _connection_test_succeeded(
        self,
        ticket: int,
        configured_model_identifier: str,
        result: object,
    ) -> None:
        if self._request_state.active_ticket != ticket:
            return
        if not isinstance(result, AIConnectionTestResult):
            failure_message = "Invalid structured connection-test response."
            if not self._request_state.accept_failure(
                ticket,
                progress_message=failure_message,
            ):
                return
            self._connection_operation = None
            self._set_operation_status(failure_message)
            self._update_openai_control_state()
            return
        metadata = build_ai_usage_presentation(
            result,
            configured_model_identifier=configured_model_identifier,
        )
        success_message = "Connection successful. " + " ".join(
            line + "." for line in metadata.lines()
        )
        if not self._request_state.accept_success(
            ticket,
            progress_message=success_message,
        ):
            return
        self._connection_operation = None
        self._set_operation_status(success_message)
        self._update_openai_control_state()

    def _connection_test_failed(self, ticket: int, error: BaseException) -> None:
        failure_message = _connection_error_text(error)
        if not self._request_state.accept_failure(
            ticket,
            progress_message=failure_message,
        ):
            return
        self._connection_operation = None
        self._set_operation_status(failure_message)
        self._update_openai_control_state()

    def save_settings(self) -> None:
        provider_identifier = self._selected_provider_identifier()
        if provider_identifier == OPENAI_PROVIDER_IDENTIFIER:
            model_identifier, model_error = self._validated_openai_model_identifier()
            if model_error is not None:
                self._set_operation_status(model_error)
                return
        else:
            model_identifier = ""
        try:
            settings = ProviderSettings(
                provider_identifier=provider_identifier,
                preferred_model_identifier=model_identifier,
                request_timeout_seconds=int(self.widgets.timeout_input.value()),
                maximum_retries=int(self.widgets.retry_input.value()),
                maximum_output_tokens=int(self.widgets.maximum_output_input.value()),
                reasoning_effort=(
                    (
                        self._loaded_settings.reasoning_effort
                        or DEFAULT_OPENAI_REASONING_EFFORT
                    )
                    if provider_identifier == OPENAI_PROVIDER_IDENTIFIER
                    else self._loaded_settings.reasoning_effort
                ),
                response_verbosity=(
                    (
                        self._loaded_settings.response_verbosity
                        or DEFAULT_OPENAI_RESPONSE_VERBOSITY
                    )
                    if provider_identifier == OPENAI_PROVIDER_IDENTIFIER
                    else self._loaded_settings.response_verbosity
                ),
                real_provider_enabled=(
                    provider_identifier == OPENAI_PROVIDER_IDENTIFIER
                ),
                last_explanation_mode=self._loaded_settings.last_explanation_mode,
            )
            self._settings_store.save(settings)
        except (ProviderSettingsError, TypeError, ValueError):
            self._set_operation_status(
                "Provider settings could not be saved. No provider request was made."
            )
            return
        self._loaded_settings = settings
        self._set_operation_status(
            "Provider settings saved. No provider request was made."
        )

    def save_key(self) -> None:
        if not self._require_openai_selection():
            return
        credential = self.widgets.api_key_input.text()
        try:
            self._credential_store.save_credential(
                OPENAI_PROVIDER_IDENTIFIER,
                credential,
            )
        except CredentialAlreadyConfiguredError:
            self._set_operation_status(
                "A secure key is already configured. Use Replace Key explicitly."
            )
            return
        except CredentialError:
            self._set_operation_status(
                "The key could not be saved by secure credential storage."
            )
            self.refresh_credential_status()
            return
        except Exception:
            self._set_operation_status("Credential backend error.")
            self.refresh_credential_status()
            return
        credential = ""
        self.clear_api_key_input()
        self.refresh_credential_status()
        self._set_operation_status(
            "Secure key saved. No CAD data was sent and no connection was tested."
        )

    def replace_key(self) -> None:
        if not self._require_openai_selection():
            return
        credential = self.widgets.api_key_input.text()
        try:
            self._credential_store.replace_credential(
                OPENAI_PROVIDER_IDENTIFIER,
                credential,
            )
        except CredentialNotConfiguredError:
            self._set_operation_status(
                "No secure key is configured. Use Save Key explicitly."
            )
            return
        except CredentialError:
            self._set_operation_status(
                "The key could not be replaced by secure credential storage."
            )
            self.refresh_credential_status()
            return
        except Exception:
            self._set_operation_status("Credential backend error.")
            self.refresh_credential_status()
            return
        credential = ""
        self.clear_api_key_input()
        self.refresh_credential_status()
        self._set_operation_status(
            "Secure key replaced. No CAD data was sent and no connection was tested."
        )

    def remove_key(self) -> None:
        if not self._require_openai_selection():
            return
        if not self._confirm_removal():
            self._set_operation_status("Secure key removal was cancelled.")
            return
        try:
            removed = self._credential_store.remove_credential(
                OPENAI_PROVIDER_IDENTIFIER
            )
        except CredentialBackendUnavailableError:
            self._set_operation_status("Secure credential storage unavailable.")
            self.refresh_credential_status()
            return
        except CredentialError:
            self._set_operation_status("Credential backend error.")
            self.refresh_credential_status()
            return
        except Exception:
            self._set_operation_status("Credential backend error.")
            self.refresh_credential_status()
            return
        self.clear_api_key_input()
        self.refresh_credential_status()
        self._set_operation_status(
            "Secure key removed."
            if removed
            else "No secure key was configured."
        )

    def refresh_credential_status(self) -> None:
        try:
            status = self._credential_store.credential_status(
                OPENAI_PROVIDER_IDENTIFIER
            )
        except Exception:
            status = ProviderCredentialStatus(
                provider_identifier=OPENAI_PROVIDER_IDENTIFIER,
                profile_identifier="default",
                configured=False,
                backend_available=False,
                status_code="backend_error",
                message="Credential status could not be inspected safely.",
            )
        self._credential_configured = bool(status.configured)
        self._credential_backend_available = bool(status.backend_available)
        self.widgets.credential_status_label.setText(
            _credential_status_text(status)
        )
        self._update_openai_control_state()

    def _apply_settings(self, settings: ProviderSettings) -> None:
        provider_index = (
            1 if settings.provider_identifier == OPENAI_PROVIDER_IDENTIFIER else 0
        )
        self.widgets.provider_combo.setCurrentIndex(provider_index)
        preferred_model = (
            settings.preferred_model_identifier or DEFAULT_OPENAI_MODEL_IDENTIFIER
        )
        model_index = self.widgets.model_combo.findText(preferred_model)
        if model_index < 0 and settings.preferred_model_identifier:
            self.widgets.model_combo.addItem(settings.preferred_model_identifier)
            model_index = self.widgets.model_combo.findText(preferred_model)
        self.widgets.model_combo.setCurrentIndex(max(0, model_index))
        self.widgets.timeout_input.setValue(settings.request_timeout_seconds)
        self.widgets.retry_input.setValue(settings.maximum_retries)
        self.widgets.maximum_output_input.setValue(settings.maximum_output_tokens)

    def _selected_provider_identifier(self) -> str:
        return (
            OPENAI_PROVIDER_IDENTIFIER
            if self.widgets.provider_combo.currentIndex() == 1
            else LOCAL_MOCK_PROVIDER_IDENTIFIER
        )

    def _require_openai_selection(self) -> bool:
        if self._selected_provider_identifier() == OPENAI_PROVIDER_IDENTIFIER:
            return True
        self._set_operation_status(
            "Select OpenAI before managing an OpenAI API key."
        )
        return False

    def _validated_openai_model_identifier(self) -> tuple[str, str | None]:
        model_identifier = str(self.widgets.model_combo.currentText())
        try:
            validate_model_identifier_syntax(model_identifier)
            get_openai_model_capabilities(model_identifier)
        except AIUnsupportedModelError:
            return model_identifier, (
                "The configured OpenAI model is unsupported. Choose a supported "
                "model explicitly; the current model selection was not changed."
            )
        except AIInvalidRequestError:
            if not model_identifier.strip():
                return "", (
                    "An OpenAI model identifier is required. The current model "
                    "selection was not changed."
                )
            return model_identifier, (
                "The OpenAI model identifier is malformed. Use only lowercase "
                "letters, numbers, '.', '_', ':', or '-'. The current model "
                "selection was not changed."
            )
        return model_identifier, None

    def _openai_configuration(
        self,
        model_identifier: str,
    ) -> OpenAIProviderConfiguration:
        return OpenAIProviderConfiguration(
            model_identifier=model_identifier,
            request_timeout_seconds=int(self.widgets.timeout_input.value()),
            maximum_retries=int(self.widgets.retry_input.value()),
            maximum_output_tokens=int(self.widgets.maximum_output_input.value()),
            reasoning_effort=(
                self._loaded_settings.reasoning_effort
                or DEFAULT_OPENAI_REASONING_EFFORT
            ),
            response_verbosity=(
                self._loaded_settings.response_verbosity
                or DEFAULT_OPENAI_RESPONSE_VERBOSITY
            ),
        )

    def _connection_is_running(self) -> bool:
        return self._request_state.is_busy

    def _update_openai_control_state(self, *, force_busy: bool = False) -> None:
        openai_selected = (
            self._selected_provider_identifier() == OPENAI_PROVIDER_IDENTIFIER
        )
        for widget in (
            self.widgets.api_key_input,
            self.widgets.model_combo,
            self.widgets.timeout_input,
            self.widgets.retry_input,
            self.widgets.maximum_output_input,
        ):
            widget.setEnabled(openai_selected)
        self.widgets.save_key_button.setEnabled(
            openai_selected
            and self._credential_backend_available
            and not self._credential_configured
        )
        self.widgets.replace_key_button.setEnabled(
            openai_selected
            and self._credential_backend_available
            and self._credential_configured
        )
        self.widgets.remove_key_button.setEnabled(
            openai_selected
            and self._credential_backend_available
            and self._credential_configured
        )
        connection_busy = force_busy or self._connection_is_running()
        self.widgets.test_connection_button.setEnabled(
            openai_selected
            and self._credential_backend_available
            and self._credential_configured
            and not connection_busy
            and self._connection_provider_factory is not None
            and self._worker_launcher is not None
            and self._confirm_connection_test is not None
        )
        self.widgets.cancel_request_button.setEnabled(connection_busy)

    def _set_operation_status(self, text: str) -> None:
        self.widgets.operation_status_label.setText(text)


_provider_settings_dialog: _ProviderSettingsDialogController | None = None


def show_provider_settings_dialog(
    *,
    app_module: Any | None = None,
    gui_module: Any | None = None,
    settings_store: ProviderSettingsStore | None = None,
    credential_store: ProviderCredentialStore | None = None,
    qt_widgets: Any | None = None,
    qt_core: Any | None = None,
) -> bool:
    """Show one non-modal settings dialog without executing a provider request."""

    active_app = App if app_module is None else app_module
    active_gui = Gui if gui_module is None else gui_module
    parent = _main_window(active_gui)
    if parent is None:
        return False
    active_qt_widgets = qt_widgets or _load_qt_widgets()
    if active_qt_widgets is None:
        return False
    active_qt_core = qt_core or _load_qt_core()

    try:
        effective_settings_store = settings_store or ProviderSettingsStore(
            FreeCADProviderSettingsBackend(active_app)
        )
        effective_credential_store = credential_store or ProviderCredentialStore(
            KeyringCredentialBackend()
        )
    except Exception:
        return False

    global _provider_settings_dialog
    if _provider_settings_dialog is None:
        def connection_provider_factory(
            configuration: OpenAIProviderConfiguration,
        ) -> OpenAIExplanationProvider:
            return OpenAIExplanationProvider(
                lambda: effective_credential_store.retrieve_for_provider_execution(
                    OPENAI_PROVIDER_IDENTIFIER
                ),
                configuration=configuration,
            )

        worker_launcher = None
        if active_qt_core is not None:
            worker_launcher = lambda operation, succeeded, failed: (
                start_qt_background_operation(
                    active_qt_core,
                    parent,
                    operation,
                    succeeded,
                    failed,
                )
            )
        _provider_settings_dialog = _create_provider_settings_dialog(
            active_qt_widgets,
            parent,
            effective_settings_store,
            effective_credential_store,
            connection_provider_factory=connection_provider_factory,
            worker_launcher=worker_launcher,
        )
    _provider_settings_dialog.refresh()
    _provider_settings_dialog.show()
    return True


def _create_provider_settings_dialog(
    qt_widgets: Any,
    parent: Any,
    settings_store: ProviderSettingsStore,
    credential_store: ProviderCredentialStore,
    *,
    connection_provider_factory: ConnectionProviderFactory | None = None,
    worker_launcher: WorkerLauncher | None = None,
) -> _ProviderSettingsDialogController:
    dialog = qt_widgets.QDialog(parent)
    dialog.setWindowTitle(PROVIDER_SETTINGS_WINDOW_TITLE)
    dialog.setModal(False)
    dialog.resize(610, 500)

    root_layout = qt_widgets.QVBoxLayout(dialog)
    for notice_text in (
        PROVIDER_NOTICE,
        PRIVACY_NOTICE,
        CREDENTIAL_STORAGE_NOTICE,
    ):
        notice = qt_widgets.QLabel(notice_text, dialog)
        notice.setWordWrap(True)
        root_layout.addWidget(notice)

    form_layout = qt_widgets.QFormLayout()
    provider_combo = qt_widgets.QComboBox(dialog)
    provider_combo.addItem("Local Mock Provider")
    provider_combo.addItem("OpenAI")
    form_layout.addRow("Provider:", provider_combo)

    credential_status_label = qt_widgets.QLabel("No key configured.", dialog)
    form_layout.addRow("Credential status:", credential_status_label)

    api_key_input = qt_widgets.QLineEdit(dialog)
    api_key_input.setEchoMode(qt_widgets.QLineEdit.Password)
    api_key_input.setPlaceholderText("Enter a new OpenAI API key")
    api_key_input.setToolTip("")
    form_layout.addRow("API key:", api_key_input)

    key_button_layout = qt_widgets.QHBoxLayout()
    save_key_button = qt_widgets.QPushButton("Save Key", dialog)
    replace_key_button = qt_widgets.QPushButton("Replace Key", dialog)
    remove_key_button = qt_widgets.QPushButton("Remove Key", dialog)
    key_button_layout.addWidget(save_key_button)
    key_button_layout.addWidget(replace_key_button)
    key_button_layout.addWidget(remove_key_button)
    form_layout.addRow("", key_button_layout)

    model_combo = qt_widgets.QComboBox(dialog)
    for model_identifier in SUPPORTED_OPENAI_MODEL_IDENTIFIERS:
        model_combo.addItem(model_identifier)
    set_model_editable = getattr(model_combo, "setEditable", None)
    if callable(set_model_editable):
        set_model_editable(True)
    form_layout.addRow("Model:", model_combo)

    timeout_input = qt_widgets.QSpinBox(dialog)
    timeout_input.setRange(
        MINIMUM_REQUEST_TIMEOUT_SECONDS,
        MAXIMUM_REQUEST_TIMEOUT_SECONDS,
    )
    timeout_input.setSuffix(" seconds")
    form_layout.addRow("Request timeout:", timeout_input)

    retry_input = qt_widgets.QSpinBox(dialog)
    retry_input.setRange(0, MAXIMUM_RETRY_COUNT)
    retry_input.setToolTip(
        "Retries are owned only by the OpenAI SDK and are bounded by this value. "
        "Connection, timeout, rate-limit, and server failures may be retried; "
        "authentication and permission failures are not retried."
    )
    form_layout.addRow("Maximum retries:", retry_input)

    maximum_output_input = qt_widgets.QSpinBox(dialog)
    maximum_output_input.setRange(MINIMUM_OUTPUT_TOKENS, MAXIMUM_OUTPUT_TOKENS)
    maximum_output_input.setSingleStep(64)
    maximum_output_input.setSuffix(" tokens")
    form_layout.addRow("Maximum output:", maximum_output_input)
    root_layout.addLayout(form_layout)

    operation_status_label = qt_widgets.QLabel("", dialog)
    operation_status_label.setWordWrap(True)
    root_layout.addWidget(operation_status_label)

    bottom_button_layout = qt_widgets.QHBoxLayout()
    test_connection_button = qt_widgets.QPushButton("Test Connection", dialog)
    test_connection_button.setToolTip(
        "Explicitly send a small provider request containing no CAD data."
    )
    cancel_request_button = qt_widgets.QPushButton("Cancel Request", dialog)
    cancel_request_button.setToolTip(
        "Ignore the in-progress result without terminating its worker thread."
    )
    cancel_request_button.setEnabled(False)
    save_settings_button = qt_widgets.QPushButton("Save Settings", dialog)
    close_button = qt_widgets.QPushButton("Close", dialog)
    bottom_button_layout.addWidget(test_connection_button)
    bottom_button_layout.addWidget(cancel_request_button)
    bottom_button_layout.addStretch(1)
    bottom_button_layout.addWidget(save_settings_button)
    bottom_button_layout.addWidget(close_button)
    root_layout.addLayout(bottom_button_layout)

    widgets = _ProviderSettingsWidgets(
        dialog=dialog,
        provider_combo=provider_combo,
        api_key_input=api_key_input,
        credential_status_label=credential_status_label,
        model_combo=model_combo,
        timeout_input=timeout_input,
        retry_input=retry_input,
        maximum_output_input=maximum_output_input,
        save_key_button=save_key_button,
        replace_key_button=replace_key_button,
        remove_key_button=remove_key_button,
        test_connection_button=test_connection_button,
        cancel_request_button=cancel_request_button,
        save_settings_button=save_settings_button,
        close_button=close_button,
        operation_status_label=operation_status_label,
    )

    def confirm_removal() -> bool:
        message_box = qt_widgets.QMessageBox
        answer = message_box.question(
            dialog,
            "Remove OpenAI API key",
            "Remove the securely stored OpenAI API key?",
            message_box.Yes | message_box.No,
            message_box.No,
        )
        return answer == message_box.Yes

    def confirm_connection_test(message: str) -> bool:
        message_box = qt_widgets.QMessageBox
        answer = message_box.question(
            dialog,
            "Test OpenAI connection",
            message,
            message_box.Yes | message_box.No,
            message_box.No,
        )
        return answer == message_box.Yes

    controller = _ProviderSettingsDialogController(
        widgets,
        settings_store,
        credential_store,
        confirm_removal,
        confirm_connection_test,
        connection_provider_factory,
        worker_launcher,
    )
    provider_combo.currentIndexChanged.connect(
        lambda *_args: controller.provider_changed()
    )
    save_key_button.clicked.connect(lambda *_args: controller.save_key())
    replace_key_button.clicked.connect(lambda *_args: controller.replace_key())
    remove_key_button.clicked.connect(lambda *_args: controller.remove_key())
    test_connection_button.clicked.connect(
        lambda *_args: controller.test_connection()
    )
    cancel_request_button.clicked.connect(
        lambda *_args: controller.cancel_connection_request()
    )
    save_settings_button.clicked.connect(lambda *_args: controller.save_settings())
    close_button.clicked.connect(lambda *_args: controller.close())
    finished_signal = getattr(dialog, "finished", None)
    if finished_signal is not None and hasattr(finished_signal, "connect"):
        finished_signal.connect(
            lambda *_args: controller.dialog_finished()
        )
    controller.refresh()
    return controller


def _credential_status_text(status: ProviderCredentialStatus) -> str:
    if status.status_code == "configured" and status.configured:
        return "Secure key configured."
    if status.status_code == "not_configured" and status.backend_available:
        return "No key configured."
    if status.status_code == "storage_error":
        return "Credential backend error."
    if not status.backend_available:
        return "Secure credential storage unavailable."
    return "Credential backend error."


def _connection_error_text(error: BaseException) -> str:
    return "Connection failed: " + categorize_ai_error(error).user_message


def _main_window(gui_module: Any | None) -> Any | None:
    if gui_module is None:
        return None
    getter = getattr(gui_module, "getMainWindow", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _load_qt_widgets() -> Any | None:
    try:  # pragma: no cover - depends on FreeCAD's bundled Qt runtime.
        from PySide2 import QtWidgets  # type: ignore

        return QtWidgets
    except Exception:
        try:  # pragma: no cover - legacy FreeCAD/PySide runtime.
            from PySide import QtGui as QtWidgets  # type: ignore

            return QtWidgets
        except Exception:
            return None


def _load_qt_core() -> Any | None:
    try:  # pragma: no cover - depends on FreeCAD's bundled Qt runtime.
        from PySide2 import QtCore  # type: ignore

        return QtCore
    except Exception:
        try:  # pragma: no cover - legacy FreeCAD/PySide runtime.
            from PySide import QtCore  # type: ignore

            return QtCore
        except Exception:
            return None


__all__ = [
    "CREDENTIAL_STORAGE_NOTICE",
    "PRIVACY_NOTICE",
    "PROVIDER_NOTICE",
    "PROVIDER_SETTINGS_COMMAND_NAME",
    "PROVIDER_SETTINGS_WINDOW_TITLE",
    "show_provider_settings_dialog",
]
