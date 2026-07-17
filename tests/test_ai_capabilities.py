import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace

from model_health_assistant.ai_capabilities import (
    ComponentCapability,
    check_real_provider_capabilities,
)
from model_health_assistant.runtime_dependencies import (
    activate_optional_dependency_directory,
    get_optional_dependency_directory,
)


class _SecureBackend:
    priority = 5

    def get_password(self, service, account):
        return None

    def set_password(self, service, account, password):
        pass

    def delete_password(self, service, account):
        pass


_SecureBackend.__module__ = "keyring.backends.Windows"


def _modules(*, backend=None):
    openai_module = ModuleType("openai")
    openai_module.__version__ = "2.45.0"
    keyring_module = ModuleType("keyring")
    backend_instance = backend or _SecureBackend()
    keyring_module.get_keyring = lambda: backend_instance
    keyring_module.get_password = backend_instance.get_password
    keyring_module.set_password = backend_instance.set_password
    keyring_module.delete_password = backend_instance.delete_password
    return {"openai": openai_module, "keyring": keyring_module}


def _importer(modules):
    def import_module(name):
        if name not in modules:
            raise ModuleNotFoundError(name)
        return modules[name]

    return import_module


def test_all_real_provider_dependencies_are_available() -> None:
    capabilities = check_real_provider_capabilities(
        module_importer=_importer(_modules()),
        version_resolver=lambda name: {
            "openai": "2.45.0",
            "keyring": "25.7.0",
        }[name],
        dependency_path_activator=lambda: None,
        platform_identifier="win32",
    )

    assert capabilities.available is True
    assert capabilities.openai_sdk.version == "2.45.0"
    assert capabilities.credential_package.version == "25.7.0"
    assert capabilities.credential_backend.available is True
    assert "self-test" in capabilities.credential_backend.message


def test_missing_openai_sdk_is_reported_without_affecting_keyring() -> None:
    modules = _modules()
    del modules["openai"]

    capabilities = check_real_provider_capabilities(
        module_importer=_importer(modules),
        version_resolver=lambda name: "25.7.0",
        dependency_path_activator=lambda: None,
        platform_identifier="win32",
    )

    assert capabilities.available is False
    assert capabilities.openai_sdk.status_code == "dependency_missing"
    assert capabilities.credential_package.available is True
    assert capabilities.credential_backend.available is True


def test_missing_keyring_disables_secure_backend() -> None:
    modules = _modules()
    del modules["keyring"]

    capabilities = check_real_provider_capabilities(
        module_importer=_importer(modules),
        version_resolver=lambda name: "2.45.0",
        dependency_path_activator=lambda: None,
        platform_identifier="win32",
    )

    assert capabilities.available is False
    assert capabilities.credential_package.status_code == "dependency_missing"
    assert capabilities.credential_backend.status_code == "dependency_missing"


def test_unavailable_credential_backend_fails_closed() -> None:
    unavailable_backend = SimpleNamespace(
        priority=0,
        get_password=lambda *_args: None,
        set_password=lambda *_args: None,
        delete_password=lambda *_args: None,
    )

    capabilities = check_real_provider_capabilities(
        module_importer=_importer(_modules(backend=unavailable_backend)),
        version_resolver=lambda name: "test-version",
        dependency_path_activator=lambda: None,
        platform_identifier="win32",
    )

    assert capabilities.available is False
    assert capabilities.credential_backend.status_code == "backend_unavailable"


def test_unexpected_dependency_check_errors_become_safe_status() -> None:
    def failing_importer(name):
        raise RuntimeError("unexpected loader failure")

    capabilities = check_real_provider_capabilities(
        module_importer=failing_importer,
        version_resolver=lambda name: "unused",
        dependency_path_activator=lambda: (_ for _ in ()).throw(
            RuntimeError("optional path failure")
        ),
        platform_identifier="win32",
    )

    assert capabilities.available is False
    assert capabilities.openai_sdk.status_code == "dependency_error"
    assert capabilities.credential_package.status_code == "dependency_error"
    assert "unexpected loader failure" not in str(capabilities.to_dict())


def test_capability_status_is_non_secret_json_compatible_data() -> None:
    status = ComponentCapability(
        component_identifier="example",
        available=False,
        status_code="unavailable",
        message="Example unavailable state.",
    )

    assert status.to_dict() == {
        "component_identifier": "example",
        "available": False,
        "status_code": "unavailable",
        "message": "Example unavailable state.",
        "version": None,
    }


def test_optional_dependency_directory_is_versioned_and_not_created(tmp_path) -> None:
    appdata = tmp_path / "Roaming"

    directory = get_optional_dependency_directory(
        appdata_directory=appdata,
        version_info=(3, 11, 13),
        machine="AMD64",
    )

    assert directory == (
        appdata
        / "FreeCAD"
        / "ModelHealthAssistantDependencies"
        / "py311-win_amd64"
    )
    assert directory.exists() is False


def test_optional_dependency_directory_is_appended_only_when_present(tmp_path) -> None:
    dependency_directory = tmp_path / "dependencies"
    search_path = ["existing"]

    assert (
        activate_optional_dependency_directory(
            dependency_directory,
            search_path=search_path,
        )
        is None
    )
    assert search_path == ["existing"]

    dependency_directory.mkdir()
    activated = activate_optional_dependency_directory(
        dependency_directory,
        search_path=search_path,
    )
    activate_optional_dependency_directory(
        dependency_directory,
        search_path=search_path,
    )

    assert activated == dependency_directory.resolve()
    assert search_path == ["existing", str(dependency_directory.resolve())]


def test_capability_module_has_no_eager_optional_or_freecad_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "model_health_assistant"
        / "ai_capabilities.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported_roots.isdisjoint(
        {"FreeCAD", "FreeCADGui", "PySide", "PySide2", "keyring", "openai"}
    )
