"""FreeCAD startup hook for Model Health Assistant.

FreeCAD loads this module when the addon is present in its Mod/addon path. The
project keeps importable Python code under ``src`` for normal development, so
this hook exposes that package layout to FreeCAD without requiring installation.
"""

from __future__ import annotations

import os
import sys

ADDON_NAME = "ModelHealthAssistant"

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    App = None


def get_addon_root(_addon_name: str = ADDON_NAME) -> str:
    """Return the addon root even when FreeCAD does not define ``__file__``."""

    module_file = globals().get("__file__")
    if module_file:
        return os.path.normpath(os.path.dirname(os.path.abspath(module_file)))

    if App is not None and hasattr(App, "getUserAppDataDir"):
        try:
            return os.path.normpath(os.path.join(App.getUserAppDataDir(), "Mod", _addon_name))
        except Exception:
            pass

    appdata_dir = os.environ.get("APPDATA")
    if appdata_dir:
        return os.path.normpath(os.path.join(appdata_dir, "FreeCAD", "Mod", _addon_name))

    return os.path.normpath(
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "FreeCAD", "Mod", _addon_name)
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

    addon_root = _get_addon_root()
    src_dir = os.path.normpath(os.path.join(addon_root, "src"))
    _write_message("Model Health Assistant addon root: " + addon_root + "\n")
    _write_message("Model Health Assistant src path: " + src_dir + "\n")

    if not os.path.isdir(src_dir):
        _write_error(
            "Model Health Assistant could not find its local src folder: "
            + src_dir
            + "\n"
        )
        return src_dir

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    return src_dir


SRC_DIR = ensure_src_on_path()
