import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_health_assistant.ai_connection import AIConnectionTestResult
from model_health_assistant.ai_models import AIUsageMetadata
from model_health_assistant.ai_provider import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIMissingCredentialError,
    AIPermissionError,
    AIProviderServerError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIRequestTimeoutError,
    AIUnsupportedModelError,
)
from model_health_assistant.credentials import (
    CredentialBackendStatus,
    ProviderCredentialStore,
)
from model_health_assistant.provider_settings import (
    ProviderSettingsStore,
)
from model_health_assistant.provider_settings_ui import (
    CREDENTIAL_STORAGE_NOTICE,
    PRIVACY_NOTICE,
    PROVIDER_NOTICE,
    _create_provider_settings_dialog,
    _credential_status_text,
)

_TEST_SECRET = "DISPOSABLE_UI_TEST_SECRET_DO_NOT_DISPLAY"
_REPLACEMENT_SECRET = "DISPOSABLE_REPLACEMENT_SECRET_DO_NOT_DISPLAY"


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _Widget:
    def __init__(self, *_args) -> None:
        self.enabled = True
        self.tooltip = None

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self.enabled = enabled

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self.tooltip = text


class _Dialog(_Widget):
    def __init__(self, parent=None) -> None:
        super().__init__()
        self.parent = parent
        self.title = ""
        self.modal = None
        self.size = None
        self.shown = False
        self.raised = False
        self.activated = False
        self.finished = _Signal()

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        self.title = title

    def setModal(self, modal: bool) -> None:  # noqa: N802
        self.modal = modal

    def resize(self, width: int, height: int) -> None:
        self.size = (width, height)

    def show(self) -> None:
        self.shown = True

    def raise_(self) -> None:
        self.raised = True

    def activateWindow(self) -> None:  # noqa: N802
        self.activated = True

    def close(self) -> None:
        self.shown = False
        self.finished.emit(0)


class _Label(_Widget):
    def __init__(self, text: str = "", *_args) -> None:
        super().__init__()
        self.text = text
        self.word_wrap = False

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = text

    def setWordWrap(self, enabled: bool) -> None:  # noqa: N802
        self.word_wrap = enabled


class _LineEdit(_Widget):
    Password = 2

    def __init__(self, *_args) -> None:
        super().__init__()
        self.value = ""
        self.echo_mode = None
        self.placeholder = ""

    def setEchoMode(self, mode: int) -> None:  # noqa: N802
        self.echo_mode = mode

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.placeholder = text

    def setText(self, text: str) -> None:  # noqa: N802
        self.value = text

    def text(self) -> str:
        return self.value

    def clear(self) -> None:
        self.value = ""


class _ComboBox(_Widget):
    def __init__(self, *_args) -> None:
        super().__init__()
        self.items: list[str] = []
        self.index = -1
        self.currentIndexChanged = _Signal()

    def addItem(self, text: str) -> None:  # noqa: N802
        self.items.append(text)
        if self.index < 0:
            self.index = 0

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self.index = index
        self.currentIndexChanged.emit(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self.index

    def currentText(self) -> str:  # noqa: N802
        return self.items[self.index]

    def findText(self, text: str) -> int:  # noqa: N802
        try:
            return self.items.index(text)
        except ValueError:
            return -1


class _SpinBox(_Widget):
    def __init__(self, *_args) -> None:
        super().__init__()
        self.minimum = 0
        self.maximum = 0
        self.current_value = 0
        self.suffix = ""
        self.single_step = 1

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self.minimum = minimum
        self.maximum = maximum

    def setValue(self, value: int) -> None:  # noqa: N802
        self.current_value = value

    def value(self) -> int:
        return self.current_value

    def setSuffix(self, suffix: str) -> None:  # noqa: N802
        self.suffix = suffix

    def setSingleStep(self, step: int) -> None:  # noqa: N802
        self.single_step = step


class _Button(_Widget):
    def __init__(self, text: str, *_args) -> None:
        super().__init__()
        self.text = text
        self.clicked = _Signal()


class _Layout:
    def __init__(self, *_args) -> None:
        self.items = []

    def addWidget(self, widget) -> None:  # noqa: N802
        self.items.append(widget)

    def addLayout(self, layout) -> None:  # noqa: N802
        self.items.append(layout)

    def addStretch(self, amount: int) -> None:  # noqa: N802
        self.items.append(("stretch", amount))


class _FormLayout(_Layout):
    def addRow(self, label, widget) -> None:  # noqa: N802
        self.items.append((label, widget))


class _MessageBox:
    Yes = 1
    No = 2
    answer = No
    questions = []

    @classmethod
    def question(cls, *args):
        cls.questions.append(args)
        return cls.answer


_QT_WIDGETS = SimpleNamespace(
    QDialog=_Dialog,
    QVBoxLayout=_Layout,
    QLabel=_Label,
    QFormLayout=_FormLayout,
    QComboBox=_ComboBox,
    QLineEdit=_LineEdit,
    QHBoxLayout=_Layout,
    QPushButton=_Button,
    QSpinBox=_SpinBox,
    QMessageBox=_MessageBox,
)


class _MemorySettingsBackend:
    def __init__(self) -> None:
        self.values = {}
        self.write_count = 0

    def read_values(self):
        return dict(self.values)

    def write_values(self, values) -> None:
        self.values = dict(values)
        self.write_count += 1


class _MemoryCredentialBackend:
    def __init__(self, secret: str | None = None, *, available: bool = True) -> None:
        self.secret = secret
        self.available = available
        self.calls: list[str] = []
        self.fail_reads = False

    def status(self) -> CredentialBackendStatus:
        self.calls.append("status")
        return CredentialBackendStatus(
            available=self.available,
            functional=self.available,
            status_code="available" if self.available else "backend_unavailable",
            message="Non-secret test backend status.",
        )

    def read_secret(self, _service: str, _account: str) -> str | None:
        self.calls.append("read")
        if self.fail_reads:
            raise RuntimeError(_TEST_SECRET)
        return self.secret

    def write_secret(self, _service: str, _account: str, secret: str) -> None:
        self.calls.append("write")
        self.secret = secret

    def delete_secret(self, _service: str, _account: str) -> None:
        self.calls.append("delete")
        self.secret = None


def _controller(
    *,
    secret: str | None = None,
    backend_available: bool = True,
    connection_provider_factory=None,
    worker_launcher=None,
):
    settings_backend = _MemorySettingsBackend()
    credential_backend = _MemoryCredentialBackend(
        secret,
        available=backend_available,
    )
    controller = _create_provider_settings_dialog(
        _QT_WIDGETS,
        object(),
        ProviderSettingsStore(settings_backend),
        ProviderCredentialStore(credential_backend),
        connection_provider_factory=connection_provider_factory,
        worker_launcher=worker_launcher,
    )
    return controller, settings_backend, credential_backend


def test_dialog_has_required_hidden_empty_key_and_disabled_connection_test() -> None:
    controller, _settings_backend, _credential_backend = _controller()
    widgets = controller.widgets

    assert widgets.provider_combo.items == ["Local Mock Provider", "OpenAI"]
    assert widgets.api_key_input.echo_mode == _LineEdit.Password
    assert widgets.api_key_input.text() == ""
    assert widgets.api_key_input.tooltip == ""
    assert widgets.test_connection_button.text == "Test Connection"
    assert widgets.test_connection_button.enabled is False
    assert len(widgets.test_connection_button.clicked.callbacks) == 1
    assert widgets.close_button.text == "Close"
    assert widgets.model_combo.items == [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    ]


def test_notices_cover_byok_charges_privacy_test_and_os_storage() -> None:
    combined = " ".join(
        (PROVIDER_NOTICE, PRIVACY_NOTICE, CREDENTIAL_STORAGE_NOTICE)
    ).casefold()

    assert "supply your own" in combined
    assert "may incur charges" in combined
    assert "no cad data is sent" in combined
    assert "test connection sends a small provider request" in combined
    assert "operating-system credential backend" in combined
    assert "project repository" in combined


def test_opening_and_reopening_never_prefills_or_masks_saved_key() -> None:
    controller, _settings_backend, credential_backend = _controller(
        secret=_TEST_SECRET
    )
    widgets = controller.widgets

    assert widgets.api_key_input.text() == ""
    assert widgets.credential_status_label.text == "Secure key configured."
    assert _TEST_SECRET not in widgets.credential_status_label.text
    widgets.api_key_input.setText(_REPLACEMENT_SECRET)

    controller.refresh()

    assert widgets.api_key_input.text() == ""
    assert credential_backend.secret == _TEST_SECRET
    assert "*" not in widgets.credential_status_label.text


def test_save_key_is_explicit_clears_input_and_never_enters_settings() -> None:
    controller, settings_backend, credential_backend = _controller()
    widgets = controller.widgets
    widgets.provider_combo.setCurrentIndex(1)
    widgets.api_key_input.setText(_TEST_SECRET)

    widgets.save_key_button.clicked.emit()

    assert credential_backend.secret == _TEST_SECRET
    assert widgets.api_key_input.text() == ""
    assert widgets.credential_status_label.text == "Secure key configured."
    assert _TEST_SECRET not in widgets.operation_status_label.text
    assert settings_backend.values == {}
    assert widgets.save_key_button.enabled is False
    assert widgets.replace_key_button.enabled is True
    assert widgets.remove_key_button.enabled is True


def test_save_settings_does_not_store_key_replace_key_or_touch_credentials() -> None:
    controller, settings_backend, credential_backend = _controller(
        secret=_TEST_SECRET
    )
    widgets = controller.widgets
    widgets.provider_combo.setCurrentIndex(1)
    widgets.model_combo.setCurrentIndex(2)
    widgets.timeout_input.setValue(50)
    widgets.retry_input.setValue(3)
    widgets.maximum_output_input.setValue(2048)
    widgets.api_key_input.setText(_REPLACEMENT_SECRET)
    credential_backend.calls.clear()

    widgets.save_settings_button.clicked.emit()

    assert credential_backend.secret == _TEST_SECRET
    assert credential_backend.calls == []
    assert widgets.api_key_input.text() == _REPLACEMENT_SECRET
    assert settings_backend.values["provider_identifier"] == "openai"
    assert settings_backend.values["preferred_model_identifier"] == "gpt-5.6-sol"
    assert settings_backend.values["request_timeout_seconds"] == 50
    assert settings_backend.values["maximum_retries"] == 3
    assert settings_backend.values["maximum_output_tokens"] == 2048
    serialized = repr(settings_backend.values)
    assert _TEST_SECRET not in serialized
    assert _REPLACEMENT_SECRET not in serialized


def test_local_mock_settings_remain_available_without_key_or_provider_request() -> None:
    controller, settings_backend, credential_backend = _controller(
        backend_available=False
    )
    widgets = controller.widgets
    credential_backend.calls.clear()

    widgets.save_settings_button.clicked.emit()

    assert settings_backend.values["provider_identifier"] == "local-mock"
    assert settings_backend.values["preferred_model_identifier"] == ""
    assert settings_backend.values["real_provider_enabled"] is False
    assert settings_backend.values["reasoning_effort"] is None
    assert settings_backend.values["response_verbosity"] is None
    assert credential_backend.calls == []
    assert widgets.operation_status_label.text == (
        "Provider settings saved. No provider request was made."
    )


def test_replace_requires_replace_action_and_clears_after_success() -> None:
    controller, _settings_backend, credential_backend = _controller(
        secret=_TEST_SECRET
    )
    widgets = controller.widgets
    widgets.provider_combo.setCurrentIndex(1)
    widgets.api_key_input.setText(_REPLACEMENT_SECRET)

    widgets.replace_key_button.clicked.emit()

    assert credential_backend.secret == _REPLACEMENT_SECRET
    assert widgets.api_key_input.text() == ""
    assert _REPLACEMENT_SECRET not in widgets.operation_status_label.text


def test_remove_requires_confirmation_and_updates_status() -> None:
    _MessageBox.questions.clear()
    controller, _settings_backend, credential_backend = _controller(
        secret=_TEST_SECRET
    )
    widgets = controller.widgets
    widgets.provider_combo.setCurrentIndex(1)

    _MessageBox.answer = _MessageBox.No
    widgets.remove_key_button.clicked.emit()
    assert credential_backend.secret == _TEST_SECRET
    assert len(_MessageBox.questions) == 1

    _MessageBox.answer = _MessageBox.Yes
    widgets.remove_key_button.clicked.emit()

    assert credential_backend.secret is None
    assert widgets.credential_status_label.text == "No key configured."
    assert len(_MessageBox.questions) == 2


def test_close_button_and_window_finish_clear_key_input() -> None:
    controller, _settings_backend, _credential_backend = _controller()
    widgets = controller.widgets
    widgets.api_key_input.setText(_TEST_SECRET)

    widgets.close_button.clicked.emit()

    assert widgets.api_key_input.text() == ""
    widgets.api_key_input.setText(_TEST_SECRET)
    widgets.dialog.finished.emit(0)
    assert widgets.api_key_input.text() == ""


def test_unavailable_and_error_statuses_are_non_secret() -> None:
    unavailable, _settings_backend, _credential_backend = _controller(
        backend_available=False
    )
    assert (
        unavailable.widgets.credential_status_label.text
        == "Secure credential storage unavailable."
    )

    errored, _settings_backend, credential_backend = _controller()
    credential_backend.fail_reads = True
    errored.refresh_credential_status()
    assert errored.widgets.credential_status_label.text == "Credential backend error."
    assert _TEST_SECRET not in errored.widgets.credential_status_label.text


def test_credential_status_mapping_uses_only_approved_messages() -> None:
    statuses = [
        SimpleNamespace(
            configured=True,
            backend_available=True,
            status_code="configured",
        ),
        SimpleNamespace(
            configured=False,
            backend_available=True,
            status_code="not_configured",
        ),
        SimpleNamespace(
            configured=False,
            backend_available=False,
            status_code="backend_unavailable",
        ),
        SimpleNamespace(
            configured=False,
            backend_available=True,
            status_code="storage_error",
        ),
    ]

    assert [_credential_status_text(status) for status in statuses] == [
        "Secure key configured.",
        "No key configured.",
        "Secure credential storage unavailable.",
        "Credential backend error.",
    ]


def test_ui_source_has_no_clipboard_eager_pyside_or_explanation_execution() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "provider_settings_ui.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_imports = {
        alias.name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "PySide" not in top_level_imports
    assert "PySide2" not in top_level_imports
    assert ".explain(" not in source
    assert "responses.create" not in source
    assert "clipboard" not in source.casefold()
    assert "reveal" not in source.casefold()


class _FakeConnectionProvider:
    def __init__(
        self,
        *,
        available: bool = True,
        result: AIConnectionTestResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.available = available
        self.result = result or AIConnectionTestResult(
            provider_identifier="openai",
            provider_display_name="OpenAI",
            actual_model_identifier="gpt-5.6-terra-test-snapshot",
            request_identifier="resp_connection_ui_test",
            usage=AIUsageMetadata(
                input_tokens=8,
                output_tokens=3,
                total_tokens=11,
            ),
        )
        self.error = error
        self.test_calls = 0

    def is_available(self) -> bool:
        return self.available

    def test_connection(self) -> AIConnectionTestResult:
        self.test_calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class _DeferredLauncher:
    def __init__(self) -> None:
        self.operation = None
        self.success = None
        self.failure = None
        self.handle = SimpleNamespace(is_running=True)

    def __call__(self, operation, success, failure):
        self.operation = operation
        self.success = success
        self.failure = failure
        return self.handle

    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:
            self.handle.is_running = False
            self.failure(error)
            return
        self.handle.is_running = False
        self.success(result)


def test_connection_requires_explicit_confirmation_and_runs_via_worker() -> None:
    provider = _FakeConnectionProvider()
    launcher = _DeferredLauncher()
    controller, _settings_backend, _credential_backend = _controller(
        secret=_TEST_SECRET,
        connection_provider_factory=lambda _configuration: provider,
        worker_launcher=launcher,
    )
    widgets = controller.widgets
    widgets.provider_combo.setCurrentIndex(1)
    _MessageBox.questions.clear()

    _MessageBox.answer = _MessageBox.No
    widgets.test_connection_button.clicked.emit()
    assert provider.test_calls == 0
    assert launcher.operation is None
    assert "cancelled" in widgets.operation_status_label.text.casefold()

    _MessageBox.answer = _MessageBox.Yes
    widgets.test_connection_button.clicked.emit()
    assert provider.test_calls == 0
    assert launcher.operation is not None
    assert "testing" in widgets.operation_status_label.text.casefold()
    confirmation = str(_MessageBox.questions[-1]).casefold()
    assert "small provider charge" in confirmation
    assert "no cad data" in confirmation
    assert "model-health report" in confirmation
    assert "document name" in confirmation
    assert "feature label" in confirmation
    assert _TEST_SECRET.casefold() not in confirmation

    launcher.run()

    assert provider.test_calls == 1
    status = widgets.operation_status_label.text
    assert "Connection successful" in status
    assert "gpt-5.6-terra-test-snapshot" in status
    assert "resp_connection_ui_test" in status
    assert "total 11" in status
    assert _TEST_SECRET not in status


def test_connection_distinguishes_dependency_key_and_provider_failures() -> None:
    unavailable_provider = _FakeConnectionProvider(available=False)
    controller, _settings, _credentials = _controller(
        secret=_TEST_SECRET,
        connection_provider_factory=lambda _configuration: unavailable_provider,
        worker_launcher=_DeferredLauncher(),
    )
    controller.widgets.provider_combo.setCurrentIndex(1)
    controller.test_connection()
    assert controller.widgets.operation_status_label.text == (
        "OpenAI SDK dependency is unavailable."
    )

    missing, _settings, _credentials = _controller(
        connection_provider_factory=lambda _configuration: _FakeConnectionProvider(),
        worker_launcher=_DeferredLauncher(),
    )
    missing.widgets.provider_combo.setCurrentIndex(1)
    missing.test_connection()
    assert missing.widgets.operation_status_label.text == (
        "No secure OpenAI key is configured."
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AIMissingCredentialError(), "no secure OpenAI key"),
        (AIAuthenticationError(), "key is invalid"),
        (AIPermissionError(), "permission was denied"),
        (AIUnsupportedModelError(), "model is unsupported"),
        (AIRateLimitError(), "rate limit"),
        (AIRequestTimeoutError(), "timed out"),
        (AIConnectionError(), "offline or unreachable"),
        (AIProviderServerError(), "server failure"),
        (AIInvalidResponseError(), "invalid structured response"),
        (AIProviderUnavailableError(), "SDK dependency"),
    ],
)
def test_connection_error_categories_are_safe_and_specific(
    error: BaseException,
    expected: str,
) -> None:
    provider = _FakeConnectionProvider(error=error)
    launcher = _DeferredLauncher()
    controller, _settings, _credentials = _controller(
        secret=_TEST_SECRET,
        connection_provider_factory=lambda _configuration: provider,
        worker_launcher=launcher,
    )
    controller.widgets.provider_combo.setCurrentIndex(1)
    _MessageBox.answer = _MessageBox.Yes

    controller.test_connection()
    launcher.run()

    status = controller.widgets.operation_status_label.text
    assert expected in status
    assert _TEST_SECRET not in status
