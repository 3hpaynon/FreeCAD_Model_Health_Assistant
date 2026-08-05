import ast
import logging
import traceback
from pathlib import Path
from types import ModuleType

import pytest

from model_health_assistant.credentials import (
    CredentialBackendUnavailableError,
    CredentialStorageError,
    ProviderCredentialStore,
)
from model_health_assistant.keyring_backend import (
    KEYRING_SELF_TEST_SENTINEL_PREFIX,
    CredentialBackendSelfTestResult,
    KeyringCredentialBackend,
)

SENTINEL = "UNIT_TEST_CREDENTIAL_SENTINEL_DO_NOT_LOG"
SELF_TEST_TOKEN = "unit-test-disposable-token"


class _SecureWindowsBackend:
    priority = 5

    def __init__(self, entries):
        self._entries = entries

    def get_password(self, service, account):
        return self._entries.get((service, account))

    def set_password(self, service, account, password):
        self._entries[(service, account)] = password

    def delete_password(self, service, account):
        self._entries.pop((service, account), None)


_SecureWindowsBackend.__module__ = "keyring.backends.Windows"


class _SecureMacOSBackend(_SecureWindowsBackend):
    pass


_SecureMacOSBackend.__module__ = "keyring.backends.macOS"


class _PlaintextBackend(_SecureWindowsBackend):
    pass


_PlaintextBackend.__module__ = "keyrings.alt.file"


def _fake_keyring_module(
    *,
    backend=None,
    read_override=None,
    write_error=None,
    delete_error=None,
):
    entries = {}
    backend_instance = backend or _SecureWindowsBackend(entries)
    module = ModuleType("keyring")
    module.get_keyring = lambda: backend_instance

    def get_password(service, account):
        if read_override is not None:
            return read_override
        return backend_instance.get_password(service, account)

    def set_password(service, account, password):
        if write_error is not None:
            raise write_error
        backend_instance.set_password(service, account, password)

    def delete_password(service, account):
        if delete_error is not None:
            raise delete_error
        backend_instance.delete_password(service, account)

    module.get_password = get_password
    module.set_password = set_password
    module.delete_password = delete_password
    return module, entries


def _adapter(module, *, activator=lambda: None):
    return KeyringCredentialBackend(
        module_importer=lambda name: module,
        dependency_path_activator=activator,
        platform_identifier="win32",
        token_factory=lambda: SELF_TEST_TOKEN,
    )


def test_adapter_construction_does_not_import_or_inspect_keyring() -> None:
    calls = []

    KeyringCredentialBackend(
        module_importer=lambda name: calls.append(("import", name)),
        dependency_path_activator=lambda: calls.append(("activate", None)),
        platform_identifier="win32",
    )

    assert calls == []


def test_status_activates_optional_path_and_accepts_windows_backend() -> None:
    module, _entries = _fake_keyring_module()
    calls = []

    status = _adapter(module, activator=lambda: calls.append("activated")).status()

    assert calls == ["activated"]
    assert status.available is True
    assert status.functional is True
    assert status.status_code == "available"
    assert "self-test" in status.message


def test_status_accepts_macos_backend_on_darwin() -> None:
    entries = {}
    module, _entries = _fake_keyring_module(
        backend=_SecureMacOSBackend(entries),
    )
    adapter = KeyringCredentialBackend(
        module_importer=lambda name: module,
        dependency_path_activator=lambda: None,
        platform_identifier="darwin",
    )

    status = adapter.status()

    assert status.available is True
    assert status.functional is True
    assert status.status_code == "available"
    assert entries == {}


def test_missing_keyring_fails_closed() -> None:
    def missing_importer(name):
        raise ModuleNotFoundError(name)

    adapter = KeyringCredentialBackend(
        module_importer=missing_importer,
        dependency_path_activator=lambda: None,
        platform_identifier="win32",
    )

    status = adapter.status()

    assert status.available is False
    assert status.functional is False
    assert status.status_code == "dependency_missing"
    with pytest.raises(CredentialBackendUnavailableError):
        adapter.read_secret("service", "account")


def test_plaintext_fallback_backend_is_rejected() -> None:
    entries = {}
    module, _entries = _fake_keyring_module(
        backend=_PlaintextBackend(entries),
    )

    status = _adapter(module).status()

    assert status.available is False
    assert status.functional is False
    assert status.status_code == "backend_unavailable"
    assert entries == {}


def test_unsupported_platform_fails_closed() -> None:
    module, _entries = _fake_keyring_module()
    adapter = KeyringCredentialBackend(
        module_importer=lambda name: module,
        dependency_path_activator=lambda: None,
        platform_identifier="unsupported-os",
    )

    assert adapter.status().status_code == "backend_unavailable"


def test_provider_store_uses_fake_os_backend_for_full_lifecycle() -> None:
    module, entries = _fake_keyring_module()
    store = ProviderCredentialStore(_adapter(module))

    store.save_credential("openai", SENTINEL)
    assert store.credential_exists("openai") is True
    assert store.retrieve_for_provider_execution("openai") == SENTINEL

    store.replace_credential("openai", "UNIT_TEST_REPLACEMENT_SENTINEL")
    assert store.retrieve_for_provider_execution("openai") == (
        "UNIT_TEST_REPLACEMENT_SENTINEL"
    )
    assert store.remove_credential("openai") is True
    assert store.credential_exists("openai") is False
    assert entries == {}


def test_backend_specific_write_exception_is_redacted() -> None:
    module, entries = _fake_keyring_module(
        write_error=RuntimeError(f"backend leaked {SENTINEL}"),
    )
    adapter = _adapter(module)

    with pytest.raises(CredentialStorageError) as captured:
        adapter.write_secret("service", "account", SENTINEL)

    rendered_traceback = "".join(
        traceback.format_exception(captured.type, captured.value, captured.tb)
    )
    assert SENTINEL not in str(captured.value)
    assert SENTINEL not in rendered_traceback
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert entries == {}


def test_explicit_self_test_writes_reads_and_removes_disposable_sentinel(
    caplog,
) -> None:
    module, entries = _fake_keyring_module()
    adapter = _adapter(module)

    with caplog.at_level(logging.DEBUG):
        result = adapter.self_test()

    assert result == CredentialBackendSelfTestResult(
        success=True,
        status_code="self_test_passed",
        message="The operating-system credential backend self-test passed.",
    )
    assert result.to_dict()["success"] is True
    assert entries == {}
    assert KEYRING_SELF_TEST_SENTINEL_PREFIX not in caplog.text
    assert SELF_TEST_TOKEN not in caplog.text


def test_self_test_mismatch_fails_and_still_removes_entry() -> None:
    module, entries = _fake_keyring_module(read_override="WRONG_TEST_VALUE")

    result = _adapter(module).self_test()

    assert result.success is False
    assert result.status_code == "self_test_verification_failed"
    assert entries == {}
    assert SELF_TEST_TOKEN not in str(result.to_dict())


def test_self_test_reports_cleanup_failure_without_disclosing_sentinel() -> None:
    module, _entries = _fake_keyring_module(
        delete_error=RuntimeError(
            f"could not delete {KEYRING_SELF_TEST_SENTINEL_PREFIX}{SELF_TEST_TOKEN}"
        )
    )

    result = _adapter(module).self_test()

    assert result.success is False
    assert result.status_code == "self_test_cleanup_failed"
    assert KEYRING_SELF_TEST_SENTINEL_PREFIX not in str(result.to_dict())
    assert SELF_TEST_TOKEN not in str(result.to_dict())


def test_backend_module_has_no_eager_optional_freecad_or_network_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "keyring_backend.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported_roots.isdisjoint(
        {
            "FreeCAD",
            "FreeCADGui",
            "PySide",
            "PySide2",
            "httpx",
            "keyring",
            "openai",
            "requests",
            "urllib",
        }
    )
