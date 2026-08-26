"""Minimal FreeCAD GUI workbench entrypoint for Model Health Assistant tests."""

from __future__ import annotations

try:  # pragma: no cover - exercised inside FreeCAD.
    import FreeCAD as App  # type: ignore
except Exception:  # pragma: no cover - expected outside FreeCAD.
    App = None


def ensure_src_on_path(
    _module_file=locals().get("__file__"),
    _app_module=None,
) -> str | None:
    """Expose the sibling ``src`` package when FreeCAD loads only InitGui.py."""

    import sys
    from pathlib import Path

    plugin_root = None
    if _module_file:
        try:
            plugin_root = Path(_module_file).resolve().parent
        except (OSError, RuntimeError, TypeError, ValueError):
            plugin_root = None

    active_app = _app_module
    if plugin_root is None and active_app is None:
        try:
            active_app = __import__("FreeCAD")
        except Exception:  # pragma: no cover - expected outside FreeCAD.
            active_app = None

    if (
        plugin_root is None
        and active_app is not None
        and hasattr(active_app, "getUserAppDataDir")
    ):
        try:
            plugin_root = (
                Path(active_app.getUserAppDataDir()).expanduser()
                / "Mod"
                / "ModelHealthAssistant"
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            plugin_root = None

    if plugin_root is None:
        return None
    src_dir = plugin_root / "src"
    if not src_dir.is_dir():
        return None

    src_dir_text = str(src_dir)
    if src_dir_text not in sys.path:
        sys.path.insert(0, src_dir_text)
    return src_dir_text


SRC_DIR = ensure_src_on_path()


def initialize_recovery_tracking(_app_module=None):
    """Register selective-recovery observation once per FreeCAD process."""

    active_app = App if _app_module is None else _app_module
    if active_app is None or not hasattr(active_app, "addDocumentObserver"):
        return None
    try:
        from model_health_assistant.recovery_observer import (
            ensure_recovery_observer_registered,
        )

        return ensure_recovery_observer_registered(active_app)
    except Exception:
        # Recovery must fail closed without preventing the workbench or the
        # user's document from loading.  The observer exposes diagnostics for
        # later UI work; startup remains deliberately quiet here.
        return None

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
        """Register the local analysis and provider-settings commands."""

        class _TestCommand:
            def GetResources(self):  # noqa: N802
                return {
                    "MenuText": "Analyze Model Health",
                    "ToolTip": (
                        "Analyze the active FreeCAD document and display model health results"
                    ),
                    "Pixmap": "",
                }

            def Activated(self):  # noqa: N802
                try:
                    from model_health_assistant.ui import run_analysis_command
                except ImportError as exc:
                    message = (
                        "Model Health Assistant: Could not load the local UI: "
                        + type(exc).__name__
                        + ": "
                        + str(exc)
                        + "\n"
                    )
                except Exception as exc:
                    message = (
                        "Model Health Assistant: UI initialization failed: "
                        + type(exc).__name__
                        + ": "
                        + str(exc)
                        + "\n"
                    )
                else:
                    try:
                        run_analysis_command(app_module=App, gui_module=Gui)
                    except Exception as exc:
                        message = (
                            "Model Health Assistant: Analysis command failed: "
                            + type(exc).__name__
                            + ": "
                            + str(exc)
                            + "\n"
                        )
                    else:
                        return

                try:
                    if App is not None and hasattr(App, "Console"):
                        App.Console.PrintMessage(message)
                        return
                except Exception:
                    pass
                print(message, end="")

            def IsActive(self):  # noqa: N802
                return True

        class _ProviderSettingsCommand:
            def GetResources(self):  # noqa: N802
                return {
                    "MenuText": "AI Provider Settings",
                    "ToolTip": (
                        "Configure local mock or bring-your-own-key OpenAI settings"
                    ),
                    "Pixmap": "",
                }

            def Activated(self):  # noqa: N802
                try:
                    from model_health_assistant.provider_settings_ui import (
                        show_provider_settings_dialog,
                    )

                    shown = show_provider_settings_dialog(
                        app_module=App,
                        gui_module=Gui,
                    )
                    if shown:
                        return
                    message = (
                        "Model Health Assistant: Provider settings UI is unavailable.\n"
                    )
                except Exception as exc:
                    message = (
                        "Model Health Assistant: Provider settings failed safely: "
                        + type(exc).__name__
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
            Gui.addCommand("MHA_ProviderSettings", _ProviderSettingsCommand())

        self.appendToolbar("Model Health Assistant", ["MHA_TestCommand"])
        self.appendMenu(
            "Model Health Assistant",
            ["MHA_TestCommand", "MHA_ProviderSettings"],
        )

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


RECOVERY_OBSERVER = initialize_recovery_tracking()


if Gui is not None:  # pragma: no cover - exercised inside FreeCAD.
    Gui.addWorkbench(ModelHealthAssistantWorkbench())
