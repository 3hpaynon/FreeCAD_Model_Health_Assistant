"""FreeCAD startup hook for Model Health Assistant.

FreeCAD loads this module when the addon is present in its Mod/addon path. The
project keeps importable Python code under ``src`` for normal development, so
this hook exposes that package layout to FreeCAD without requiring installation.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_src_on_path() -> Path:
    """Add the local ``src`` directory to ``sys.path`` when available."""

    addon_root = Path(__file__).resolve().parent
    src_dir = addon_root / "src"
    if src_dir.is_dir():
        src_text = str(src_dir)
        if src_text not in sys.path:
            sys.path.insert(0, src_text)
    return src_dir


ensure_src_on_path()
