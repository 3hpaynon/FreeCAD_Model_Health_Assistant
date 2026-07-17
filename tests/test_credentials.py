import ast
import logging
import traceback
from pathlib import Path

import pytest

from model_health_assistant.credentials import (
    CREDENTIAL_APPLICATION_IDENTIFIER,
    CredentialAlreadyConfiguredError,
    CredentialBackendStatus,
    CredentialBackendUnavailableError,
    CredentialIdentifier,
    CredentialNotConfiguredError,
    CredentialStorageError,
    InvalidCredentialIdentifierError,
    InvalidCredentialValueError,
    ProviderCredentialStore,
)

UNIT_TEST_SECRET = "UNIT_TEST_SECRET_DO_NOT_LOG"
REPLACEMENT_SECRET = "UNIT_TEST_REPLACEMENT_SECRET_DO_NOT_LOG"


class _MemoryBackend:
    def __init__(self, *, available=True, functional=True) -> None:
        self.values = {}
        self.available = available
        self.functional = functional

    def status(self):
        return CredentialBackendStatus(
            available=self.available,
            functional=self.functional,
            status_code="available" if self.available else "unavailable",
            message="Test backend status.",
        )

    def read_secret(self, service_name, account_name):
        return self.values.get((service_name, account_name))

    def write_secret(self, service_name, account_name, secret):
        self.values[(service_name, account_name)] = secret

    def delete_secret(self, service_name, account_name):
        del self.values[(service_name, account_name)]


class _FailingBackend(_MemoryBackend):
    def read_secret(self, service_name, account_name):
        raise RuntimeError(UNIT_TEST_SECRET)

    def write_secret(self, service_name, account_name, secret):
        raise RuntimeError(secret)

    def delete_secret(self, service_name, account_name):
        raise RuntimeError(UNIT_TEST_SECRET)


def test_identifier_distinguishes_application_provider_and_profile() -> None:
    identifier = CredentialIdentifier("openai", "engineering")

    assert identifier.application_identifier == CREDENTIAL_APPLICATION_IDENTIFIER
    assert identifier.service_name == (
        "freecad-model-health-assistant:provider-credential"
    )
    assert identifier.account_name == "openai:engineering"
    assert "secret" not in repr(identifier).casefold()


@pytest.mark.parametrize(
    "value",
    ["", "UPPERCASE", "contains space", "slash/value", ".leading-dot"],
)
def test_malformed_provider_identifier_is_rejected(value: str) -> None:
    with pytest.raises(InvalidCredentialIdentifierError):
        CredentialIdentifier(value)


def test_save_exists_and_narrow_retrieve() -> None:
    backend = _MemoryBackend()
    store = ProviderCredentialStore(backend)

    store.save_credential("openai", UNIT_TEST_SECRET)

    assert store.credential_exists("openai") is True
    assert store.credential_status("openai").configured is True
    assert (
        store.retrieve_for_provider_execution("openai") == UNIT_TEST_SECRET
    )
    assert UNIT_TEST_SECRET not in repr(store.credential_status("openai"))
    assert UNIT_TEST_SECRET not in str(store.credential_status("openai").to_dict())


def test_save_requires_explicit_replace_and_replace_requires_existing_entry() -> None:
    store = ProviderCredentialStore(_MemoryBackend())

    with pytest.raises(CredentialNotConfiguredError):
        store.replace_credential("openai", REPLACEMENT_SECRET)

    store.save_credential("openai", UNIT_TEST_SECRET)
    with pytest.raises(CredentialAlreadyConfiguredError):
        store.save_credential("openai", REPLACEMENT_SECRET)

    store.replace_credential("openai", REPLACEMENT_SECRET)
    assert (
        store.retrieve_for_provider_execution("openai") == REPLACEMENT_SECRET
    )


def test_remove_returns_whether_a_credential_was_removed() -> None:
    store = ProviderCredentialStore(_MemoryBackend())

    assert store.remove_credential("openai") is False
    store.save_credential("openai", UNIT_TEST_SECRET)
    assert store.remove_credential("openai") is True
    assert store.credential_exists("openai") is False
    assert store.remove_credential("openai") is False


def test_provider_and_profile_entries_are_isolated() -> None:
    store = ProviderCredentialStore(_MemoryBackend())

    store.save_credential("openai", UNIT_TEST_SECRET)
    store.save_credential(
        "future-provider",
        "FUTURE_PROVIDER_SENTINEL",
    )
    store.save_credential(
        "openai",
        "SECOND_PROFILE_SENTINEL",
        profile_identifier="secondary",
    )

    assert store.retrieve_for_provider_execution("openai") == UNIT_TEST_SECRET
    assert store.retrieve_for_provider_execution("future-provider") == (
        "FUTURE_PROVIDER_SENTINEL"
    )
    assert store.retrieve_for_provider_execution(
        "openai",
        profile_identifier="secondary",
    ) == "SECOND_PROFILE_SENTINEL"


def test_missing_credential_raises_non_secret_error() -> None:
    store = ProviderCredentialStore(_MemoryBackend())

    with pytest.raises(CredentialNotConfiguredError) as exc_info:
        store.retrieve_for_provider_execution("openai")

    assert "openai" not in str(exc_info.value)
    assert "credential" in str(exc_info.value).casefold()


def test_unavailable_backend_fails_closed_without_persistence() -> None:
    backend = _MemoryBackend(available=False, functional=False)
    store = ProviderCredentialStore(backend)

    with pytest.raises(CredentialBackendUnavailableError):
        store.save_credential("openai", UNIT_TEST_SECRET)

    assert backend.values == {}
    status = store.credential_status("openai")
    assert status.configured is False
    assert status.backend_available is False
    assert status.status_code == "backend_unavailable"


@pytest.mark.parametrize("credential", ["", "   ", "bad\x00value", None])
def test_empty_or_invalid_credential_is_rejected(credential) -> None:
    store = ProviderCredentialStore(_MemoryBackend())

    with pytest.raises(InvalidCredentialValueError):
        store.save_credential("openai", credential)


def test_backend_exceptions_are_redacted_from_errors_status_and_traceback() -> None:
    store = ProviderCredentialStore(_FailingBackend())

    with pytest.raises(CredentialStorageError) as exc_info:
        store.save_credential("openai", UNIT_TEST_SECRET)

    rendered_exception = "".join(
        traceback.format_exception(exc_info.value)
    )
    assert UNIT_TEST_SECRET not in str(exc_info.value)
    assert UNIT_TEST_SECRET not in repr(exc_info.value)
    assert UNIT_TEST_SECRET not in rendered_exception
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None

    status = store.credential_status("openai")
    assert UNIT_TEST_SECRET not in repr(status)
    assert UNIT_TEST_SECRET not in str(status.to_dict())


def test_credential_operations_emit_no_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    store = ProviderCredentialStore(_MemoryBackend())

    store.save_credential("openai", UNIT_TEST_SECRET)
    store.credential_status("openai")
    store.retrieve_for_provider_execution("openai")
    store.remove_credential("openai")

    assert caplog.records == []
    assert UNIT_TEST_SECRET not in caplog.text


def test_credential_domain_has_no_file_freecad_keyring_or_network_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "credentials.py"
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
            "json",
            "keyring",
            "openai",
            "os",
            "pathlib",
            "requests",
            "socket",
            "urllib",
        }
    )
