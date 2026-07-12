"""Minimal FreeCAD GUI workbench entrypoint for Model Health Assistant tests."""

from __future__ import annotations

try:  # pragma: no cover - exercised inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected outside FreeCAD.
    App = None

try:  # pragma: no cover - exercised inside FreeCAD.
    import FreeCADGui as Gui  # type: ignore
except Exception:  # pragma: no cover - expected outside FreeCAD.
    Gui = None

try:  # pragma: no cover - FreeCAD provides Workbench at load time.
    BaseWorkbench = Workbench  # type: ignore[name-defined]
except NameError:  # pragma: no cover - expected outside FreeCAD.

    class BaseWorkbench:
        """Small fallback base class for tests outside FreeCAD."""

        def appendToolbar(self, name, commands):  # noqa: N802
            """No-op outside FreeCAD."""

        def appendMenu(self, name, commands):  # noqa: N802
            """No-op outside FreeCAD."""

        def appendContextMenu(self, name, commands):  # noqa: N802
            """No-op outside FreeCAD."""


class ModelHealthAssistantWorkbench(BaseWorkbench):
    """Basic test workbench for verifying FreeCAD can load the addon."""

    MenuText = "Model Health Assistant"
    ToolTip = "Basic test workbench for Model Health Assistant"
    Icon = ""

    def Initialize(self):  # noqa: N802
        """Register the simple test command."""

        class _TestCommand:
            def GetResources(self):  # noqa: N802
                return {
                    "MenuText": "Test Model Health Assistant",
                    "ToolTip": "Run a basic Model Health Assistant test command",
                    "Pixmap": "",
                }

            def Activated(self):  # noqa: N802
                document = getattr(App, "ActiveDocument", None) if App is not None else None
                if document is None:
                    message = "Model Health Assistant: No active FreeCAD document is open.\n"
                else:
                    document_name = (
                        getattr(document, "Label", None)
                        or getattr(document, "Name", None)
                        or "Untitled"
                    )
                    message = (
                        "Model Health Assistant: Active document detected: "
                        + str(document_name)
                        + "\n"
                    )
                try:
                    if App is not None and hasattr(App, "Console"):
                        App.Console.PrintMessage(message)
                        return
                except Exception:
                    pass
                print(message, end="")

            def IsActive(self):  # noqa: N802
                return True

        if Gui is not None and hasattr(Gui, "addCommand"):
            Gui.addCommand("MHA_TestCommand", _TestCommand())

        self.appendToolbar("Model Health Assistant", ["MHA_TestCommand"])
        self.appendMenu("Model Health Assistant", ["MHA_TestCommand"])

        try:
            if App is not None and hasattr(App, "Console"):
                App.Console.PrintMessage(
                    "Model Health Assistant test workbench initialized.\n"
                )
        except Exception:
            pass

    def Activated(self):  # noqa: N802
        """Run when the workbench becomes active."""

    def Deactivated(self):  # noqa: N802
        """Run when the workbench becomes inactive."""

    def ContextMenu(self, recipient):  # noqa: N802
        """Run when FreeCAD asks the workbench to populate a context menu."""

    def GetClassName(self):  # noqa: N802
        """Return the FreeCAD workbench base class name."""

        return "Gui::PythonWorkbench"


if Gui is not None:  # pragma: no cover - exercised inside FreeCAD.
    Gui.addWorkbench(ModelHealthAssistantWorkbench())
