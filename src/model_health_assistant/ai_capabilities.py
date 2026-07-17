"""Lazy, workbench-safe capability checks for optional real-AI support."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from types import ModuleType

from model_health_assistant.keyring_backend import KeyringCredentialBackend
from model_health_assistant.runtime_dependencies import (
    activate_optional_dependency_directory,
)

OPENAI_DISTRIBUTION_NAME = "openai"
KEYRING_DISTRIBUTION_NAME = "keyring"

ModuleImporter = Callable[[str], ModuleType]
VersionResolver = Callable[[str], str]
DependencyPathActivator = Callable[[], object]


@dataclass(frozen=True, slots=True)
class ComponentCapability:
    """Non-secret availability information for one optional component."""

    component_identifier: str
    available: bool
    status_code: str
    message: str
    version: str | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        """Return JSON-compatible, non-secret status data."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class RealProviderCapabilities:
    """Combined dependency state for real-provider execution."""

    openai_sdk: ComponentCapability
    credential_package: ComponentCapability
    credential_backend: ComponentCapability

    @property
    def available(self) -> bool:
        """Return whether all required real-provider components are available."""

        return (
            self.openai_sdk.available
            and self.credential_package.available
            and self.credential_backend.available
        )

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible capability data without exception details."""

        return {
            "available": self.available,
            "openai_sdk": self.openai_sdk.to_dict(),
            "credential_package": self.credential_package.to_dict(),
            "credential_backend": self.credential_backend.to_dict(),
        }


def check_real_provider_capabilities(
    *,
    module_importer: ModuleImporter = importlib.import_module,
    version_resolver: VersionResolver = importlib.metadata.version,
    dependency_path_activator: DependencyPathActivator = (
        activate_optional_dependency_directory
    ),
    platform_identifier: str = sys.platform,
) -> RealProviderCapabilities:
    """Return a fail-closed snapshot of optional dependency availability.

    Optional packages are imported only while this function is explicitly
    called.  Unexpected import or backend errors are converted to non-sensitive
    unavailable states so workbench discovery and local analysis remain usable.
    """

    try:
        dependency_path_activator()
    except Exception:
        # An invalid optional path must not prevent checks against normal paths.
        pass

    openai_module, openai_status = _check_optional_package(
        "openai-sdk",
        "openai",
        OPENAI_DISTRIBUTION_NAME,
        module_importer,
        version_resolver,
    )
    del openai_module
    _, keyring_status = _check_optional_package(
        "credential-package",
        "keyring",
        KEYRING_DISTRIBUTION_NAME,
        module_importer,
        version_resolver,
    )
    backend_status = _check_credential_backend(
        module_importer,
        platform_identifier,
    )
    return RealProviderCapabilities(
        openai_sdk=openai_status,
        credential_package=keyring_status,
        credential_backend=backend_status,
    )


def _check_optional_package(
    component_identifier: str,
    module_name: str,
    distribution_name: str,
    module_importer: ModuleImporter,
    version_resolver: VersionResolver,
) -> tuple[ModuleType | None, ComponentCapability]:
    try:
        module = module_importer(module_name)
    except ModuleNotFoundError:
        return None, ComponentCapability(
            component_identifier=component_identifier,
            available=False,
            status_code="dependency_missing",
            message=f"Optional dependency '{distribution_name}' is not installed.",
        )
    except Exception:
        return None, ComponentCapability(
            component_identifier=component_identifier,
            available=False,
            status_code="dependency_error",
            message=f"Optional dependency '{distribution_name}' could not be loaded safely.",
        )

    version: str | None = None
    try:
        resolved_version = version_resolver(distribution_name)
    except Exception:
        resolved_version = getattr(module, "__version__", None)
    if isinstance(resolved_version, str) and resolved_version.strip():
        version = resolved_version.strip()

    return module, ComponentCapability(
        component_identifier=component_identifier,
        available=True,
        status_code="available",
        message=f"Optional dependency '{distribution_name}' is available.",
        version=version,
    )


def _check_credential_backend(
    module_importer: ModuleImporter,
    platform_identifier: str,
) -> ComponentCapability:
    adapter_status = KeyringCredentialBackend(
        module_importer=module_importer,
        dependency_path_activator=lambda: None,
        platform_identifier=platform_identifier,
    ).status()
    return ComponentCapability(
        component_identifier="credential-backend",
        available=adapter_status.available and adapter_status.functional,
        status_code=adapter_status.status_code,
        message=adapter_status.message,
    )


__all__ = [
    "KEYRING_DISTRIBUTION_NAME",
    "OPENAI_DISTRIBUTION_NAME",
    "ComponentCapability",
    "RealProviderCapabilities",
    "check_real_provider_capabilities",
]
