from __future__ import annotations

import ast
import importlib.util
import sys
import types
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

    assert module.get_addon_root() == str(REPO_ROOT)
    assert module.ensure_src_on_path() == str(src_dir)
    assert str(src_dir) in sys.path


def test_init_uses_freecad_user_data_when_file_location_is_unavailable(
    tmp_path,
) -> None:
    module = _load_module("mha_test_init_freecad_user_data", REPO_ROOT / "Init.py")
    del module.__file__
    module.App = types.SimpleNamespace(getUserAppDataDir=lambda: str(tmp_path))

    assert module.get_addon_root() == str(
        tmp_path / "Mod" / "ModelHealthAssistant"
    )


def test_init_gui_is_import_safe_without_freecad(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "FreeCAD", raising=False)
    monkeypatch.delitem(sys.modules, "FreeCADGui", raising=False)

    module = _load_module("mha_test_init_gui", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()

    assert module.Gui is None
    assert not hasattr(module, "ADDON_NAME")
    assert not hasattr(module, "WORKBENCH_TITLE")
    assert not hasattr(module, "COMMAND_NAME")
    assert workbench.MenuText == "Model Health Assistant"
    assert workbench.ToolTip == "Basic test workbench for Model Health Assistant"
    assert workbench.Icon == ""
    assert workbench.GetClassName() == "Gui::PythonWorkbench"

    workbench.Initialize()
    workbench.Activated()
    workbench.Deactivated()
    workbench.ContextMenu("view")


def test_init_gui_registers_workbench_when_freecad_gui_is_available(monkeypatch) -> None:
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.workbenches = []
    fake_gui.addWorkbench = lambda workbench: fake_gui.workbenches.append(workbench)

    monkeypatch.delitem(sys.modules, "FreeCAD", raising=False)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    module = _load_module("mha_test_init_gui_registration", REPO_ROOT / "InitGui.py")

    assert len(fake_gui.workbenches) == 1
    assert isinstance(fake_gui.workbenches[0], module.ModelHealthAssistantWorkbench)


def test_init_gui_initialize_adds_test_command_to_toolbar_and_menu(monkeypatch) -> None:
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    monkeypatch.delitem(sys.modules, "FreeCAD", raising=False)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    module = _load_module("mha_test_init_gui_command", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    toolbars = []
    menus = []
    workbench.appendToolbar = lambda name, commands: toolbars.append((name, commands))
    workbench.appendMenu = lambda name, commands: menus.append((name, commands))

    workbench.Initialize()

    assert list(fake_gui.commands) == ["MHA_TestCommand", "MHA_ProviderSettings"]
    resources = fake_gui.commands["MHA_TestCommand"].GetResources()
    assert resources["MenuText"] == "Analyze Model Health"
    assert resources["ToolTip"] == (
        "Analyze the active FreeCAD document and display model health results"
    )
    assert resources["Pixmap"] == ""
    settings_resources = fake_gui.commands["MHA_ProviderSettings"].GetResources()
    assert settings_resources == {
        "MenuText": "AI Provider Settings",
        "ToolTip": "Configure local mock or bring-your-own-key OpenAI settings",
        "Pixmap": "",
    }
    assert toolbars == [("Model Health Assistant", ["MHA_TestCommand"])]
    assert menus == [
        (
            "Model Health Assistant",
            ["MHA_TestCommand", "MHA_ProviderSettings"],
        )
    ]


def test_provider_settings_command_opens_only_settings_ui(monkeypatch) -> None:
    calls = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import provider_settings_ui

    monkeypatch.setattr(
        provider_settings_ui,
        "show_provider_settings_dialog",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    module = _load_module(
        "mha_test_init_gui_provider_settings",
        REPO_ROOT / "InitGui.py",
    )
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()

    fake_gui.commands["MHA_ProviderSettings"].Activated()

    assert calls == [{"app_module": fake_app, "gui_module": fake_gui}]


def test_init_gui_initialize_prints_when_freecad_console_is_available(monkeypatch) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.delitem(sys.modules, "FreeCADGui", raising=False)

    module = _load_module("mha_test_init_gui_console", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()

    workbench.Initialize()

    assert messages == ["Model Health Assistant test workbench initialized.\n"]


def test_test_command_prints_when_clicked(monkeypatch) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import ui

    shown_presentations = []
    monkeypatch.setattr(
        ui,
        "show_results_dialog",
        lambda presentation, *, report=None, gui_module=None, document_context=None, status_message="": (
            shown_presentations.append(presentation)
        ),
    )

    module = _load_module("mha_test_init_gui_click", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == ["Model Health Assistant: No active FreeCAD document is open.\n"]
    assert shown_presentations == []


def test_test_command_prints_active_document_name(monkeypatch, tmp_path) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = types.SimpleNamespace(
        Name="Mechanical_EM_Coupler",
        Label="Mechanical-EM Coupler Clutch Practice Model",
    )

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import report_writer, ui

    output_path = tmp_path / "model_health_reports" / "active_document_report.json"
    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: output_path,
    )
    shown_presentations = []
    monkeypatch.setattr(
        ui,
        "show_results_dialog",
        lambda presentation, *, report=None, gui_module=None, document_context=None, status_message="": shown_presentations.append(
            (presentation, report, gui_module, document_context, status_message)
        ),
    )

    module = _load_module("mha_test_init_gui_active_document", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "------------------------------------------------------------------------------------\n"
        "Model Health Assistant Analysis\n\n"
        "Document: Mechanical-EM Coupler Clutch Practice Model\n"
        "Document objects: 0\n"
        "Meaningful modeling features: 0\n"
        "Sketches: 0\n\n"
        "Warnings:\n"
        "High: 0\n"
        "Medium: 0\n"
        "Low: 0\n"
        "Total: 0\n\n"
        "Health score: 100/100\n\n"
        "Top issues:\n\n"
        "None.\n\n"
        "Report:\n"
        + str(output_path)
        + "\n------------------------------------------------------------------------------------\n"
    ]
    assert output_path.exists()
    assert len(shown_presentations) == 1
    presentation, report, passed_gui, document_context, status_message = shown_presentations[0]
    assert presentation.document_name == "Mechanical-EM Coupler Clutch Practice Model"
    assert report.document_name == "Mechanical-EM Coupler Clutch Practice Model"
    assert presentation.health_score == 100
    assert presentation.meaningful_modeling_feature_count == 0
    assert presentation.total_sketch_count == 0
    assert presentation.total_warning_count == 0
    assert presentation.report_path == output_path
    assert passed_gui is fake_gui
    assert document_context.document is fake_app.ActiveDocument
    assert document_context.document_name == "Mechanical_EM_Coupler"
    assert document_context.app_module is fake_app
    assert document_context.gui_module is fake_gui
    assert status_message == "Analysis completed. The active document is empty."


def test_test_command_prints_extracted_feature_and_sketch_counts(monkeypatch, tmp_path) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = types.SimpleNamespace(
        Name="Demo",
        Label="Demo Model",
        Objects=[
            types.SimpleNamespace(Name="Sketch", TypeId="Sketcher::SketchObject"),
            types.SimpleNamespace(Name="Pad", TypeId="PartDesign::Pad"),
        ],
    )

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import report_writer, ui

    output_path = tmp_path / "model_health_reports" / "extracted_model_report.json"
    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: output_path,
    )

    module = _load_module("mha_test_init_gui_extraction", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "------------------------------------------------------------------------------------\n"
        "Model Health Assistant Analysis\n\n"
        "Document: Demo Model\n"
        "Document objects: 2\n"
        "Meaningful modeling features: 1\n"
        "Sketches: 1\n\n"
        "Warnings:\n"
        "High: 0\n"
        "Medium: 0\n"
        "Low: 1\n"
        "Total: 1\n\n"
        "Health score: 100/100\n\n"
        "Top issues:\n\n"
        "1. Sketch has missing or unknown support information.\n\n"
        "Report:\n"
        + str(output_path)
        + "\n------------------------------------------------------------------------------------\n"
    ]
    assert output_path.exists()


def test_test_command_prints_top_issues_in_severity_order(monkeypatch, tmp_path) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = types.SimpleNamespace(
        Name="Ranked_Model",
        Label="Ranked Model",
        Objects=[],
    )

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import report_writer, risk_engine
    from model_health_assistant.models import FeatureInfo, ModelHealthReport, RiskWarning

    warnings = [
        RiskWarning(
            warning_id=f"TEST-{index}",
            severity=severity,
            affected_feature_name="Feature001",
            message=message,
            technical_reason="Example reason",
            suggested_practice="Example practice",
            rule_id=f"example_rule_{index}",
        )
        for index, (severity, message) in enumerate(
            (
                ("low", "Low issue first in source order."),
                ("high", "High issue."),
                ("medium", "Medium issue."),
                ("low", "Fourth issue should not appear."),
            ),
            start=1,
        )
    ]
    report = ModelHealthReport(
        document_name="Ranked Model",
        generated_at="2026-07-15T06:45:00+00:00",
        health_score=64,
        features=[
            FeatureInfo(internal_name="Body", category="container"),
            FeatureInfo(internal_name="Sketch", category="sketch", is_sketch=True),
            FeatureInfo(internal_name="Pad", category="additive_feature"),
        ],
        warnings=warnings,
    )
    monkeypatch.setattr(
        risk_engine,
        "analyze_model_health",
        lambda features, document_name: report,
    )

    output_path = tmp_path / "model_health_reports" / "ranked_model_report.json"
    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: output_path,
    )

    module = _load_module("mha_test_init_gui_ranked_issues", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "------------------------------------------------------------------------------------\n"
        "Model Health Assistant Analysis\n\n"
        "Document: Ranked Model\n"
        "Document objects: 3\n"
        "Meaningful modeling features: 1\n"
        "Sketches: 1\n\n"
        "Warnings:\n"
        "High: 1\n"
        "Medium: 1\n"
        "Low: 2\n"
        "Total: 4\n\n"
        "Health score: 64/100\n\n"
        "Top issues:\n\n"
        "1. High issue.\n"
        "2. Medium issue.\n"
        "3. Low issue first in source order.\n\n"
        "Report:\n"
        + str(output_path)
        + "\n------------------------------------------------------------------------------------\n"
    ]
    assert output_path.exists()


def test_test_command_reports_extractor_failure(monkeypatch) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    class BrokenDocument:
        Name = "Broken"

        @property
        def Objects(self):  # noqa: N802 - FreeCAD API name.
            raise RuntimeError("could not read document objects")

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = BrokenDocument()

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    module = _load_module("mha_test_init_gui_extractor_failure", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "Model Health Assistant: Extractor failed: RuntimeError: "
        "could not read document objects\n"
    ]


def test_test_command_reports_risk_engine_failure(monkeypatch) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = types.SimpleNamespace(Name="Demo", Objects=[])

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import risk_engine

    def fail_analysis(features, document_name):
        raise RuntimeError("could not analyze extracted features")

    monkeypatch.setattr(risk_engine, "analyze_model_health", fail_analysis)

    module = _load_module("mha_test_init_gui_risk_failure", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "Model Health Assistant: Risk engine failed: RuntimeError: "
        "could not analyze extracted features\n"
    ]


def test_test_command_reports_report_writing_failure(monkeypatch, tmp_path) -> None:
    messages = []
    fake_app = types.ModuleType("FreeCAD")
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    class FakeConsole:
        @staticmethod
        def PrintMessage(message: str) -> None:  # noqa: N802 - FreeCAD API name.
            messages.append(message)

    fake_app.Console = FakeConsole
    fake_app.ActiveDocument = types.SimpleNamespace(Name="Demo", Objects=[])

    monkeypatch.setitem(sys.modules, "FreeCAD", fake_app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    from model_health_assistant import report_writer, ui

    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: tmp_path / "report.json",
    )

    def fail_save(report, output_path):
        raise OSError("could not write report")

    monkeypatch.setattr(report_writer, "save_report_json", fail_save)
    shown_results = []
    monkeypatch.setattr(
        ui,
        "show_results_dialog",
        lambda presentation, **kwargs: shown_results.append((presentation, kwargs)),
    )

    module = _load_module("mha_test_init_gui_report_failure", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "Model Health Assistant: Report writing failed: OSError: could not write report\n",
        "------------------------------------------------------------------------------------\n"
        "Model Health Assistant Analysis\n\n"
        "Document: Demo\n"
        "Document objects: 0\n"
        "Meaningful modeling features: 0\n"
        "Sketches: 0\n\n"
        "Warnings:\n"
        "High: 0\n"
        "Medium: 0\n"
        "Low: 0\n"
        "Total: 0\n\n"
        "Health score: 100/100\n\n"
        "Top issues:\n\n"
        "None.\n\n"
        "Report:\n"
        "Not available\n"
        "------------------------------------------------------------------------------------\n",
    ]
    assert len(shown_results) == 1
    presentation, kwargs = shown_results[0]
    assert presentation.document_name == "Demo"
    assert presentation.health_score == 100
    assert presentation.report_path is None
    assert kwargs["document_context"].document is fake_app.ActiveDocument
    assert kwargs["status_message"] == (
        "Analysis completed, but the JSON report could not be written."
    )


def test_test_command_prints_normally_without_freecad_console(monkeypatch, capsys) -> None:
    fake_gui = types.ModuleType("FreeCADGui")
    fake_gui.commands = {}
    fake_gui.addCommand = lambda name, command: fake_gui.commands.update({name: command})
    fake_gui.addWorkbench = lambda workbench: None

    monkeypatch.delitem(sys.modules, "FreeCAD", raising=False)
    monkeypatch.setitem(sys.modules, "FreeCADGui", fake_gui)

    module = _load_module("mha_test_init_gui_print_fallback", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]

    command.Activated()

    assert (
        capsys.readouterr().out
        == "Model Health Assistant: No active FreeCAD document is open.\n"
    )


def test_test_command_is_local_to_initialize() -> None:
    text = (REPO_ROOT / "InitGui.py").read_text(encoding="utf-8")
    module_ast = ast.parse(text)
    top_level_classes = [
        node.name for node in module_ast.body if isinstance(node, ast.ClassDef)
    ]
    workbench_class = next(
        node
        for node in module_ast.body
        if isinstance(node, ast.ClassDef) and node.name == "ModelHealthAssistantWorkbench"
    )
    initialize_method = next(
        node
        for node in workbench_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "Initialize"
    )
    local_classes = [
        node.name for node in initialize_method.body if isinstance(node, ast.ClassDef)
    ]

    assert "TestModelHealthAssistantCommand" not in text
    assert "TestModelHealthAssistantCommand" not in top_level_classes
    assert "ModelHealthAssistantWorkbench" in top_level_classes
    assert local_classes == ["_TestCommand", "_ProviderSettingsCommand"]
