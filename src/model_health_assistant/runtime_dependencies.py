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
    appdata_directory: str | os.PathLike[str] | None = None,
    version_info: Sequence[int] | None = None,
    machine: str | None = None,
) -> Path | None:
    """Return the isolated optional-dependency directory for this Python ABI.

    ``None`` is returned when no Windows roaming application-data directory is
    available.  The path is calculated only; no filesystem mutation occurs.
    """

    appdata_value = (
        os.fspath(appdata_directory)
        if appdata_directory is not None
        else os.environ.get("APPDATA", "")
    )
    if not appdata_value.strip():
        return None

    active_version = sys.version_info if version_info is None else version_info
    if len(active_version) < 2:
        raise ValueError("version_info must contain major and minor values")
    major = int(active_version[0])
    minor = int(active_version[1])
    architecture = _normalized_architecture(platform.machine() if machine is None else machine)
    return (
        Path(appdata_value)
        / "FreeCAD"
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


def _normalized_architecture(machine: str) -> str:
    normalized = str(machine or "unknown").strip().lower().replace("-", "_")
    aliases = {
        "amd64": "win_amd64",
        "x86_64": "win_amd64",
        "x64": "win_amd64",
        "arm64": "win_arm64",
        "aarch64": "win_arm64",
        "x86": "win32",
        "i386": "win32",
        "i686": "win32",
    }
    return aliases.get(normalized, normalized or "unknown")


__all__ = [
    "RUNTIME_DEPENDENCY_DIRECTORY_NAME",
    "activate_optional_dependency_directory",
    "get_optional_dependency_directory",
]
