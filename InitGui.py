"""FreeCAD GUI workbench registration for Model Health Assistant."""

from __future__ import annotations
from pathlib import Path
import sys



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

try:  # pragma: no cover - exercised only inside FreeCAD.
    import FreeCADGui as Gui  # type: ignore
except Exception:  # pragma: no cover - expected in normal unit tests.
    Gui = None


COMMANDS = ["ModelHealthAssistant_AnalyzeFeatureTree"]


class ModelHealthAssistantWorkbench:
    """Minimal FreeCAD workbench foundation for local model-health analysis."""

    MenuText = "Model Health Assistant"
    ToolTip = "Run local model-health analysis tools."
    Icon = ""

    def Initialize(self) -> None:  # noqa: N802 - FreeCAD API name.
        """Register commands, toolbar entries, and menu entries."""

        from model_health_assistant.ui import register_command

        register_command()
        self.appendToolbar("Model Health", COMMANDS)
        self.appendMenu("Model Health Assistant", COMMANDS)

    def Activated(self) -> None:  # noqa: N802 - FreeCAD API name.
        """Run when the workbench becomes active."""

    def Deactivated(self) -> None:  # noqa: N802 - FreeCAD API name.
        """Run when the workbench becomes inactive."""

    def ContextMenu(self, recipient: str) -> None:  # noqa: N802 - FreeCAD API name.
        """Add the workbench command to the FreeCAD context menu."""

        self.appendContextMenu("Model Health Assistant", COMMANDS)

    def GetClassName(self) -> str:  # noqa: N802 - FreeCAD API name.
        """Return the FreeCAD workbench base class name."""

        return "Gui::PythonWorkbench"


def register_workbench() -> bool:
    """Register the workbench with FreeCADGui when available."""

    if Gui is None:
        return False
    Gui.addWorkbench(ModelHealthAssistantWorkbench())
    return True


if Gui is not None:  # pragma: no cover - exercised only inside FreeCAD.
    register_workbench()
