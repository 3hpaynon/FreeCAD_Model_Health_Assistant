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
                    try:
                        from model_health_assistant.extractor import extract_document_features

                        features = extract_document_features(document)
                    except ImportError as exc:
                        message = (
                            "Model Health Assistant: Could not load the local extractor: "
                            + type(exc).__name__
                            + ": "
                            + str(exc)
                            + "\n"
                        )
                    except Exception as exc:
                        message = (
                            "Model Health Assistant: Extractor failed: "
                            + type(exc).__name__
                            + ": "
                            + str(exc)
                            + "\n"
                        )
                    else:
                        try:
                            from model_health_assistant.risk_engine import analyze_model_health

                            report = analyze_model_health(
                                features,
                                document_name=str(document_name),
                            )
                        except ImportError as exc:
                            message = (
                                "Model Health Assistant: Could not load the local risk engine: "
                                + type(exc).__name__
                                + ": "
                                + str(exc)
                                + "\n"
                            )
                        except Exception as exc:
                            message = (
                                "Model Health Assistant: Risk engine failed: "
                                + type(exc).__name__
                                + ": "
                                + str(exc)
                                + "\n"
                            )
                        else:
                            try:
                                from model_health_assistant.report_writer import (
                                    build_report_path,
                                    save_report_json,
                                )

                                output_path = build_report_path(
                                    str(getattr(document, "Name", None) or document_name),
                                    str(getattr(document, "FileName", "") or ""),
                                    report.generated_at,
                                )
                                saved_path = save_report_json(report, output_path)
                                message = (
                                    "Model Health Assistant: Analysis complete.\n"
                                    + "Document: "
                                    + report.document_name
                                    + "\n"
                                    + "Total features: "
                                    + str(report.total_feature_count)
                                    + "\n"
                                    + "Total sketches: "
                                    + str(report.total_sketch_count)
                                    + "\n"
                                    + "Total warnings: "
                                    + str(report.total_warning_count)
                                    + "\n"
                                    + "Health score: "
                                    + str(report.health_score)
                                    + "/100\n"
                                    + "Report saved to:\n"
                                    + str(saved_path)
                                    + "\n"
                                )
                            except ImportError as exc:
                                message = (
                                    "Model Health Assistant: Could not load the local report writer: "
                                    + type(exc).__name__
                                    + ": "
                                    + str(exc)
                                    + "\n"
                                )
                            except Exception as exc:
                                message = (
                                    "Model Health Assistant: Report writing failed: "
                                    + type(exc).__name__
                                    + ": "
                                    + str(exc)
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
