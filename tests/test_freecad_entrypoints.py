from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_init_adds_src_to_python_path() -> None:
    module = _load_module("mha_test_init", REPO_ROOT / "Init.py")

    src_dir = REPO_ROOT / "src"

    assert module.ensure_src_on_path() == src_dir
    assert str(src_dir) in sys.path


def test_init_gui_is_import_safe_without_freecad() -> None:
    module = _load_module("mha_test_init_gui", REPO_ROOT / "InitGui.py")

    workbench = module.ModelHealthAssistantWorkbench()

    assert module.register_workbench() is False
    assert workbench.MenuText == "Model Health Assistant"
    assert workbench.GetClassName() == "Gui::PythonWorkbench"
    assert module.COMMANDS == ["ModelHealthAssistant_AnalyzeFeatureTree"]
