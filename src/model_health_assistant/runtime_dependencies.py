"""Locate optional runtime dependencies without installing or importing them.

FreeCAD uses a different Python runtime from the repository virtual environment.
Optional real-provider packages may therefore be installed into a versioned,
user-writable directory outside both the addon repository and the FreeCAD
installation.  Merely importing this module never creates that directory or
changes ``sys.path``.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Sequence

RUNTIME_DEPENDENCY_DIRECTORY_NAME = "ModelHealthAssistantDependencies"


def get_optional_dependency_directory(
    *,
    freecad_user_data_directory: str | os.PathLike[str] | None = None,
    appdata_directory: str | os.PathLike[str] | None = None,
    version_info: Sequence[int] | None = None,
    machine: str | None = None,
    platform_identifier: str | None = None,
) -> Path | None:
    """Return the isolated optional-dependency directory for this Python ABI.

    FreeCAD's own user-data directory is preferred because FreeCAD supplies the
    correct platform-specific location.  ``appdata_directory`` remains as a
    Windows-compatible fallback and injectable test boundary.  The path is
    calculated only; no filesystem mutation occurs.
    """

    if freecad_user_data_directory is not None and appdata_directory is not None:
        raise ValueError(
            "Specify either freecad_user_data_directory or appdata_directory, not both."
        )

    active_platform = sys.platform if platform_identifier is None else platform_identifier
    platform_family = _platform_family(active_platform)
    if freecad_user_data_directory is not None:
        freecad_user_data_value = os.fspath(freecad_user_data_directory)
        if not freecad_user_data_value.strip():
            return None
        user_data_directory = Path(freecad_user_data_value).expanduser()
    else:
        user_data_directory = _active_freecad_user_data_directory()

    if user_data_directory is None:
        appdata_value = (
            os.fspath(appdata_directory)
            if appdata_directory is not None
            else os.environ.get("APPDATA", "")
            if platform_family == "windows"
            else ""
        )
        if not appdata_value.strip():
            return None
        user_data_directory = Path(appdata_value).expanduser() / "FreeCAD"

    if not str(user_data_directory).strip():
        return None

    active_version = sys.version_info if version_info is None else version_info
    if len(active_version) < 2:
        raise ValueError("version_info must contain major and minor values")
    major = int(active_version[0])
    minor = int(active_version[1])
    architecture = _normalized_architecture(
        platform.machine() if machine is None else machine,
        platform_family,
    )
    return (
        user_data_directory
        / RUNTIME_DEPENDENCY_DIRECTORY_NAME
        / f"py{major}{minor}-{architecture}"
    )


def activate_optional_dependency_directory(
    dependency_directory: str | os.PathLike[str] | None = None,
    *,
    search_path: list[str] | None = None,
) -> Path | None:
    """Append an existing dependency directory to the active import path.

    The directory is appended rather than prepended so FreeCAD's bundled
    standard-library and application modules retain priority.  Missing paths
    are ignored and never created.
    """

    directory = (
        get_optional_dependency_directory()
        if dependency_directory is None
        else Path(dependency_directory)
    )
    if directory is None or not directory.is_dir():
        return None

    normalized = os.path.normcase(os.path.normpath(str(directory.resolve())))
    active_search_path = sys.path if search_path is None else search_path
    existing_paths = {
        os.path.normcase(os.path.normpath(item))
        for item in active_search_path
        if isinstance(item, str) and item
    }
    if normalized not in existing_paths:
        active_search_path.append(str(directory.resolve()))
    return directory.resolve()


def _active_freecad_user_data_directory() -> Path | None:
    app_module = sys.modules.get("FreeCAD")
    getter = getattr(app_module, "getUserAppDataDir", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value).strip():
        return None
    return Path(value).expanduser()


def _platform_family(platform_identifier: str) -> str:
    normalized = str(platform_identifier or "").strip().casefold()
    if normalized.startswith("win"):
        return "windows"
    if normalized == "darwin":
        return "macos"
    if normalized.startswith("linux"):
        return "linux"
    return "other"


def _normalized_architecture(machine: str, platform_family: str) -> str:
    normalized = str(machine or "unknown").strip().lower().replace("-", "_")
    architecture_aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
        "x86": "x86",
        "i386": "x86",
        "i686": "x86",
    }
    architecture = architecture_aliases.get(normalized, normalized or "unknown")
    platform_prefixes = {
        "windows": "win",
        "macos": "macos",
        "linux": "linux",
    }
    platform_prefix = platform_prefixes.get(platform_family)
    if platform_prefix is None:
        return architecture
    if platform_family == "windows":
        if architecture == "x86":
            return "win32"
        if architecture == "x86_64":
            architecture = "amd64"
    return f"{platform_prefix}_{architecture}"


__all__ = [
    "RUNTIME_DEPENDENCY_DIRECTORY_NAME",
    "activate_optional_dependency_directory",
    "get_optional_dependency_directory",
]
