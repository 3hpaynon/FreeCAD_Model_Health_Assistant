"""FreeCAD startup hook for Model Health Assistant.

FreeCAD loads this module when the addon is present in its Mod/addon path. The
project keeps importable Python code under ``src`` for normal development, so
this hook exposes that package layout to FreeCAD without requiring installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ADDON_NAME = "ModelHealthAssistant"

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None


def get_addon_root(_addon_name: str = ADDON_NAME) -> str:
    """Return the addon root even when FreeCAD does not define ``__file__``."""

    module_file = globals().get("__file__")
    if module_file:
        return str(Path(module_file).resolve().parent)

    if App is not None and hasattr(App, "getUserAppDataDir"):
        try:
            return str(Path(App.getUserAppDataDir()).expanduser() / "Mod" / _addon_name)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    raise RuntimeError(
        "Model Health Assistant could not determine its addon root because "
        "__file__ and FreeCAD.getUserAppDataDir() are unavailable."
    )


def write_console_message(message: str) -> None:
    """Write an informational message to the FreeCAD console when available."""

    if App is not None and hasattr(App, "Console"):
        try:
            App.Console.PrintMessage(message)
            return
        except Exception:
            pass

    sys.stderr.write(message)


def write_console_error(message: str) -> None:
    """Write an error to the FreeCAD console when available."""

    if App is not None and hasattr(App, "Console"):
        try:
            App.Console.PrintError(message)
            return
        except Exception:
            pass

    sys.stderr.write(message)


def ensure_src_on_path(
    _get_addon_root=get_addon_root,
    _write_message=write_console_message,
    _write_error=write_console_error,
) -> str:
    """Add the local ``src`` directory to ``sys.path`` when available."""

    addon_root = Path(_get_addon_root()).expanduser()
    src_dir = addon_root / "src"
    addon_root_text = str(addon_root)
    src_dir_text = str(src_dir)
    _write_message("Model Health Assistant addon root: " + addon_root_text + "\n")
    _write_message("Model Health Assistant src path: " + src_dir_text + "\n")

    if not src_dir.is_dir():
        _write_error(
            "Model Health Assistant could not find its local src folder: "
            + src_dir_text
            + "\n"
        )
        return src_dir_text

    if src_dir_text not in sys.path:
        sys.path.insert(0, src_dir_text)
    return src_dir_text


SRC_DIR = ensure_src_on_path()
