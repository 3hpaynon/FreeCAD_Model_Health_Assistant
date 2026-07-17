"""Lazy, fail-closed adapter for operating-system credential storage."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hmac import compare_digest
from math import isfinite
from types import ModuleType
from uuid import uuid4

from model_health_assistant.credentials import (
    CredentialBackendStatus,
    CredentialBackendUnavailableError,
    CredentialError,
    CredentialStorageError,
    InvalidCredentialValueError,
)
from model_health_assistant.runtime_dependencies import (
    activate_optional_dependency_directory,
)

KEYRING_SELF_TEST_SERVICE_NAME = (
    "freecad-model-health-assistant:credential-backend-self-test"
)
KEYRING_SELF_TEST_ACCOUNT_PREFIX = "backend-self-test:"
KEYRING_SELF_TEST_SENTINEL_PREFIX = (
    "MODEL_HEALTH_ASSISTANT_CREDENTIAL_BACKEND_SELF_TEST_"
)

_ALLOWED_OS_BACKEND_MODULE_PREFIXES = {
    "windows": ("keyring.backends.windows",),
    "macos": ("keyring.backends.macos",),
    "linux": (
        "keyring.backends.secretservice",
        "keyring.backends.libsecret",
        "keyring.backends.kwallet",
    ),
}
_INSECURE_BACKEND_MARKERS = (
    "chainer",
    "fail",
    "file",
    "null",
    "plaintext",
)

ModuleImporter = Callable[[str], ModuleType]
DependencyPathActivator = Callable[[], object]
TokenFactory = Callable[[], str]


class _BackendValidationError(RuntimeError):
    """Internal marker for a missing, unsupported, or insecure backend."""


@dataclass(frozen=True, slots=True)
class CredentialBackendSelfTestResult:
    """Non-secret result of an explicitly requested disposable self-test."""

    success: bool
    status_code: str
    message: str

    def to_dict(self) -> dict[str, str | bool]:
        """Return JSON-compatible result data that never contains the sentinel."""

        return asdict(self)


class KeyringCredentialBackend:
    """Store credentials only through an allowlisted OS ``keyring`` backend."""

    def __init__(
        self,
        *,
        module_importer: ModuleImporter = importlib.import_module,
        dependency_path_activator: DependencyPathActivator = (
            activate_optional_dependency_directory
        ),
        platform_identifier: str = sys.platform,
        token_factory: TokenFactory | None = None,
    ) -> None:
        self._module_importer = module_importer
        self._dependency_path_activator = dependency_path_activator
        self._platform_identifier = platform_identifier
        self._token_factory = token_factory or _new_self_test_token

    def status(self) -> CredentialBackendStatus:
        """Inspect availability without reading or writing any credential."""

        try:
            self._load_validated_keyring_module()
        except ModuleNotFoundError:
            return CredentialBackendStatus(
                available=False,
                functional=False,
                status_code="dependency_missing",
                message="Secure credential storage requires the optional keyring package.",
            )
        except _BackendValidationError:
            return CredentialBackendStatus(
                available=False,
                functional=False,
                status_code="backend_unavailable",
                message="No approved operating-system credential backend is available.",
            )
        except Exception:
            return CredentialBackendStatus(
                available=False,
                functional=False,
                status_code="backend_error",
                message="Secure credential storage could not be inspected safely.",
            )
        return CredentialBackendStatus(
            available=True,
            functional=True,
            status_code="available",
            message=(
                "An approved operating-system credential backend is available; "
                "run the explicit self-test before storing a real credential."
            ),
        )

    def read_secret(self, service_name: str, account_name: str) -> str | None:
        """Read one credential while translating backend-specific failures."""

        self._validate_lookup_identifier(service_name, account_name)
        keyring_module = self._require_keyring_module()
        storage_failed = False
        try:
            value = keyring_module.get_password(service_name, account_name)
        except Exception:
            storage_failed = True
            value = None
        if storage_failed:
            raise CredentialStorageError(
                "The operating-system credential backend could not read the entry."
            )
        if value is not None and not isinstance(value, str):
            raise CredentialStorageError(
                "The operating-system credential backend returned an invalid value."
            )
        return value

    def write_secret(
        self,
        service_name: str,
        account_name: str,
        secret: str,
    ) -> None:
        """Write one credential without any file or preference fallback."""

        self._validate_lookup_identifier(service_name, account_name)
        if not isinstance(secret, str) or not secret.strip() or "\x00" in secret:
            raise InvalidCredentialValueError(
                "Credential input must be non-empty text."
            )
        keyring_module = self._require_keyring_module()
        storage_failed = False
        try:
            keyring_module.set_password(service_name, account_name, secret)
        except Exception:
            storage_failed = True
        if storage_failed:
            raise CredentialStorageError(
                "The operating-system credential backend could not save the entry."
            )

    def delete_secret(self, service_name: str, account_name: str) -> None:
        """Delete one credential while translating backend-specific failures."""

        self._validate_lookup_identifier(service_name, account_name)
        keyring_module = self._require_keyring_module()
        storage_failed = False
        try:
            keyring_module.delete_password(service_name, account_name)
        except Exception:
            storage_failed = True
        if storage_failed:
            raise CredentialStorageError(
                "The operating-system credential backend could not remove the entry."
            )

    def self_test(self) -> CredentialBackendSelfTestResult:
        """Explicitly store, verify, and remove a disposable non-secret sentinel."""

        backend_status = self.status()
        if not backend_status.available or not backend_status.functional:
            return CredentialBackendSelfTestResult(
                success=False,
                status_code="self_test_unavailable",
                message="The credential backend is unavailable, so its self-test did not run.",
            )

        try:
            token = self._token_factory()
            if not isinstance(token, str) or not token or "\x00" in token:
                raise ValueError
        except Exception:
            return CredentialBackendSelfTestResult(
                success=False,
                status_code="self_test_failed",
                message="The credential-backend self-test could not be prepared safely.",
            )

        account_name = f"{KEYRING_SELF_TEST_ACCOUNT_PREFIX}{token}"
        sentinel = f"{KEYRING_SELF_TEST_SENTINEL_PREFIX}{token}"
        operation_status_code = "self_test_passed"
        operation_message = "The operating-system credential backend self-test passed."

        try:
            self.write_secret(KEYRING_SELF_TEST_SERVICE_NAME, account_name, sentinel)
            retrieved = self.read_secret(KEYRING_SELF_TEST_SERVICE_NAME, account_name)
            if retrieved is None or not compare_digest(retrieved, sentinel):
                operation_status_code = "self_test_verification_failed"
                operation_message = (
                    "The credential-backend self-test could not verify its temporary entry."
                )
        except CredentialError:
            operation_status_code = "self_test_failed"
            operation_message = "The credential-backend self-test operation failed safely."

        try:
            self.delete_secret(KEYRING_SELF_TEST_SERVICE_NAME, account_name)
        except CredentialError:
            return CredentialBackendSelfTestResult(
                success=False,
                status_code="self_test_cleanup_failed",
                message=(
                    "The credential-backend self-test could not confirm removal of its "
                    "temporary entry."
                ),
            )

        return CredentialBackendSelfTestResult(
            success=operation_status_code == "self_test_passed",
            status_code=operation_status_code,
            message=operation_message,
        )

    def _require_keyring_module(self) -> ModuleType:
        backend_unavailable = False
        try:
            keyring_module = self._load_validated_keyring_module()
        except Exception:
            backend_unavailable = True
            keyring_module = None
        if backend_unavailable or keyring_module is None:
            raise CredentialBackendUnavailableError(
                "An approved operating-system credential backend is unavailable."
            )
        return keyring_module

    def _load_validated_keyring_module(self) -> ModuleType:
        try:
            self._dependency_path_activator()
        except Exception:
            # A broken optional path must not hide a normal environment install.
            pass

        keyring_module = self._module_importer("keyring")
        get_keyring = getattr(keyring_module, "get_keyring", None)
        if not callable(get_keyring):
            raise _BackendValidationError
        backend = get_keyring()
        self._validate_backend(backend, keyring_module)
        return keyring_module

    def _validate_backend(self, backend: object, keyring_module: ModuleType) -> None:
        try:
            priority = float(getattr(backend, "priority", 0))
        except (TypeError, ValueError):
            raise _BackendValidationError from None
        if not isfinite(priority) or priority <= 0:
            raise _BackendValidationError

        backend_type = type(backend)
        backend_name = backend_type.__name__.casefold()
        backend_module = backend_type.__module__.casefold()
        if any(
            marker in backend_name or marker in backend_module
            for marker in _INSECURE_BACKEND_MARKERS
        ):
            raise _BackendValidationError

        platform_family = _platform_family(self._platform_identifier)
        allowed_prefixes = _ALLOWED_OS_BACKEND_MODULE_PREFIXES.get(platform_family, ())
        if not any(
            backend_module == prefix or backend_module.startswith(f"{prefix}.")
            for prefix in allowed_prefixes
        ):
            raise _BackendValidationError

        backend_methods_available = all(
            callable(getattr(backend, method_name, None))
            for method_name in ("get_password", "set_password", "delete_password")
        )
        module_methods_available = all(
            callable(getattr(keyring_module, method_name, None))
            for method_name in ("get_password", "set_password", "delete_password")
        )
        if not backend_methods_available or not module_methods_available:
            raise _BackendValidationError

    @staticmethod
    def _validate_lookup_identifier(service_name: str, account_name: str) -> None:
        if any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in (service_name, account_name)
        ):
            raise CredentialStorageError(
                "The operating-system credential identifier is invalid."
            )


def _platform_family(platform_identifier: str) -> str:
    normalized = platform_identifier.casefold()
    if normalized.startswith("win"):
        return "windows"
    if normalized == "darwin":
        return "macos"
    if normalized.startswith("linux"):
        return "linux"
    return "unsupported"


def _new_self_test_token() -> str:
    return uuid4().hex


__all__ = [
    "KEYRING_SELF_TEST_ACCOUNT_PREFIX",
    "KEYRING_SELF_TEST_SENTINEL_PREFIX",
    "KEYRING_SELF_TEST_SERVICE_NAME",
    "CredentialBackendSelfTestResult",
    "KeyringCredentialBackend",
]
