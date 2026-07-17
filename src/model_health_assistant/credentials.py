"""Provider-neutral credential domain with no plaintext persistence fallback."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

CREDENTIAL_APPLICATION_IDENTIFIER = "freecad-model-health-assistant"
DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER = "default"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class CredentialError(RuntimeError):
    """Base class for controlled, non-secret credential failures."""


class CredentialBackendUnavailableError(CredentialError):
    """Raised when no secure credential backend can be used."""


class CredentialStorageError(CredentialError):
    """Raised when a secure backend operation fails."""


class CredentialNotConfiguredError(CredentialError):
    """Raised when provider execution requests a missing credential."""


class CredentialAlreadyConfiguredError(CredentialError):
    """Raised when save is used instead of the explicit replace operation."""


class InvalidCredentialIdentifierError(CredentialError):
    """Raised for malformed application, provider, or profile identifiers."""


class InvalidCredentialValueError(CredentialError):
    """Raised when a credential value is empty or structurally unsafe."""


@dataclass(frozen=True, slots=True)
class CredentialIdentifier:
    """Stable non-secret identity for one provider credential."""

    provider_identifier: str
    profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER
    application_identifier: str = CREDENTIAL_APPLICATION_IDENTIFIER

    def __post_init__(self) -> None:
        for field_name, value in (
            ("application_identifier", self.application_identifier),
            ("provider_identifier", self.provider_identifier),
            ("profile_identifier", self.profile_identifier),
        ):
            if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
                raise InvalidCredentialIdentifierError(
                    f"{field_name} must be a lowercase stable identifier."
                )

    @property
    def service_name(self) -> str:
        """Return the stable application-scoped backend service name."""

        return f"{self.application_identifier}:provider-credential"

    @property
    def account_name(self) -> str:
        """Return the provider/profile backend account name."""

        return f"{self.provider_identifier}:{self.profile_identifier}"


@dataclass(frozen=True, slots=True)
class CredentialBackendStatus:
    """Non-secret operational status supplied by a credential backend."""

    available: bool
    functional: bool
    status_code: str
    message: str

    def to_dict(self) -> dict[str, str | bool]:
        """Return JSON-compatible status data containing no credential."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderCredentialStatus:
    """Non-secret UI-safe status for one provider/profile credential."""

    provider_identifier: str
    profile_identifier: str
    configured: bool
    backend_available: bool
    status_code: str
    message: str

    def to_dict(self) -> dict[str, str | bool]:
        """Return JSON-compatible status data containing no credential."""

        return asdict(self)


@runtime_checkable
class CredentialBackend(Protocol):
    """Minimal secure-storage boundary implemented by OS backend adapters."""

    def status(self) -> CredentialBackendStatus:
        """Return a non-secret backend availability snapshot."""

        ...

    def read_secret(self, service_name: str, account_name: str) -> str | None:
        """Return one secret or ``None`` when no entry exists."""

        ...

    def write_secret(
        self,
        service_name: str,
        account_name: str,
        secret: str,
    ) -> None:
        """Store one secret using secure backend persistence."""

        ...

    def delete_secret(self, service_name: str, account_name: str) -> None:
        """Remove one secret from secure backend persistence."""

        ...


class ProviderCredentialStore:
    """Coordinate provider credentials without exposing them in status data."""

    def __init__(self, backend: CredentialBackend) -> None:
        if not isinstance(backend, CredentialBackend):
            raise TypeError("backend must satisfy CredentialBackend")
        self._backend = backend

    def backend_status(self) -> CredentialBackendStatus:
        """Return a fail-closed, non-secret secure-backend status."""

        try:
            status = self._backend.status()
        except Exception:
            return CredentialBackendStatus(
                available=False,
                functional=False,
                status_code="backend_error",
                message="Secure credential storage could not be inspected safely.",
            )
        if not isinstance(status, CredentialBackendStatus):
            return CredentialBackendStatus(
                available=False,
                functional=False,
                status_code="backend_error",
                message="Secure credential storage returned an invalid status.",
            )
        return status

    def credential_status(
        self,
        provider_identifier: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> ProviderCredentialStatus:
        """Return whether a credential exists without returning its value."""

        identifier = CredentialIdentifier(
            provider_identifier=provider_identifier,
            profile_identifier=profile_identifier,
        )
        backend_status = self.backend_status()
        if not backend_status.available or not backend_status.functional:
            return ProviderCredentialStatus(
                provider_identifier=identifier.provider_identifier,
                profile_identifier=identifier.profile_identifier,
                configured=False,
                backend_available=False,
                status_code="backend_unavailable",
                message="Secure credential storage is unavailable.",
            )

        try:
            configured = self._read_secret(identifier) is not None
        except CredentialError:
            return ProviderCredentialStatus(
                provider_identifier=identifier.provider_identifier,
                profile_identifier=identifier.profile_identifier,
                configured=False,
                backend_available=True,
                status_code="storage_error",
                message="Credential status could not be read from secure storage.",
            )
        return ProviderCredentialStatus(
            provider_identifier=identifier.provider_identifier,
            profile_identifier=identifier.profile_identifier,
            configured=configured,
            backend_available=True,
            status_code="configured" if configured else "not_configured",
            message=(
                "Secure credential is configured."
                if configured
                else "No secure credential is configured."
            ),
        )

    def credential_exists(
        self,
        provider_identifier: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> bool:
        """Return only whether a secure credential is configured."""

        return self.credential_status(
            provider_identifier,
            profile_identifier=profile_identifier,
        ).configured

    def save_credential(
        self,
        provider_identifier: str,
        credential: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> None:
        """Store a new credential, requiring explicit replace when one exists."""

        identifier = self._identifier(provider_identifier, profile_identifier)
        self._validate_credential(credential)
        self._require_backend()
        if self._read_secret(identifier) is not None:
            raise CredentialAlreadyConfiguredError(
                "A secure credential is already configured; use replace explicitly."
            )
        self._write_secret(identifier, credential)

    def replace_credential(
        self,
        provider_identifier: str,
        credential: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> None:
        """Replace an existing credential through an explicit operation."""

        identifier = self._identifier(provider_identifier, profile_identifier)
        self._validate_credential(credential)
        self._require_backend()
        if self._read_secret(identifier) is None:
            raise CredentialNotConfiguredError(
                "No secure credential is configured to replace."
            )
        self._write_secret(identifier, credential)

    def retrieve_for_provider_execution(
        self,
        provider_identifier: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> str:
        """Retrieve a secret only for narrow request-time provider execution."""

        identifier = self._identifier(provider_identifier, profile_identifier)
        self._require_backend()
        credential = self._read_secret(identifier)
        if credential is None:
            raise CredentialNotConfiguredError(
                "No secure credential is configured for this provider."
            )
        return credential

    def remove_credential(
        self,
        provider_identifier: str,
        *,
        profile_identifier: str = DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER,
    ) -> bool:
        """Remove a configured credential and return whether one was removed."""

        identifier = self._identifier(provider_identifier, profile_identifier)
        self._require_backend()
        if self._read_secret(identifier) is None:
            return False
        self._delete_secret(identifier)
        return True

    @staticmethod
    def _identifier(
        provider_identifier: str,
        profile_identifier: str,
    ) -> CredentialIdentifier:
        return CredentialIdentifier(
            provider_identifier=provider_identifier,
            profile_identifier=profile_identifier,
        )

    @staticmethod
    def _validate_credential(credential: str) -> None:
        if (
            not isinstance(credential, str)
            or not credential.strip()
            or "\x00" in credential
        ):
            raise InvalidCredentialValueError(
                "Credential input must be non-empty text."
            )

    def _require_backend(self) -> None:
        status = self.backend_status()
        if not status.available or not status.functional:
            raise CredentialBackendUnavailableError(
                "Secure credential storage is unavailable."
            )

    def _read_secret(self, identifier: CredentialIdentifier) -> str | None:
        storage_failed = False
        try:
            value = self._backend.read_secret(
                identifier.service_name,
                identifier.account_name,
            )
        except CredentialError:
            raise
        except Exception:
            storage_failed = True
            value = None
        if storage_failed:
            raise CredentialStorageError(
                "Secure credential storage could not read the requested entry."
            )
        if value is not None and not isinstance(value, str):
            raise CredentialStorageError(
                "Secure credential storage returned an invalid value."
            )
        return value

    def _write_secret(
        self,
        identifier: CredentialIdentifier,
        credential: str,
    ) -> None:
        storage_failed = False
        try:
            self._backend.write_secret(
                identifier.service_name,
                identifier.account_name,
                credential,
            )
        except CredentialError:
            raise
        except Exception:
            storage_failed = True
        if storage_failed:
            raise CredentialStorageError(
                "Secure credential storage could not save the credential."
            )

    def _delete_secret(self, identifier: CredentialIdentifier) -> None:
        storage_failed = False
        try:
            self._backend.delete_secret(
                identifier.service_name,
                identifier.account_name,
            )
        except CredentialError:
            raise
        except Exception:
            storage_failed = True
        if storage_failed:
            raise CredentialStorageError(
                "Secure credential storage could not remove the credential."
            )


__all__ = [
    "CREDENTIAL_APPLICATION_IDENTIFIER",
    "DEFAULT_CREDENTIAL_PROFILE_IDENTIFIER",
    "CredentialAlreadyConfiguredError",
    "CredentialBackend",
    "CredentialBackendStatus",
    "CredentialBackendUnavailableError",
    "CredentialError",
    "CredentialIdentifier",
    "CredentialNotConfiguredError",
    "CredentialStorageError",
    "InvalidCredentialIdentifierError",
    "InvalidCredentialValueError",
    "ProviderCredentialStatus",
    "ProviderCredentialStore",
]
