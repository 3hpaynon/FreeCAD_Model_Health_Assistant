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

    assert list(fake_gui.commands) == ["MHA_TestCommand"]
    resources = fake_gui.commands["MHA_TestCommand"].GetResources()
    assert resources["MenuText"] == "Test Model Health Assistant"
    assert resources["ToolTip"] == "Run a basic Model Health Assistant test command"
    assert resources["Pixmap"] == ""
    assert toolbars == [("Model Health Assistant", ["MHA_TestCommand"])]
    assert menus == [("Model Health Assistant", ["MHA_TestCommand"])]


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

    module = _load_module("mha_test_init_gui_click", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == ["Model Health Assistant: No active FreeCAD document is open.\n"]


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

    from model_health_assistant import report_writer

    output_path = tmp_path / "model_health_reports" / "active_document_report.json"
    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: output_path,
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
        "Features: 0\n"
        "Sketches: 0\n"
        "Warnings: 0\n"
        "Health score: 100/100\n"
        "Report saved to:\n"
        + str(output_path)
        + "\n------------------------------------------------------------------------------------\n"
    ]
    assert output_path.exists()


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

    from model_health_assistant import report_writer

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
        "Features: 2\n"
        "Sketches: 1\n"
        "Warnings: 1\n"
        "Health score: 100/100\n"
        "Report saved to:\n"
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

    from model_health_assistant import report_writer

    monkeypatch.setattr(
        report_writer,
        "build_report_path",
        lambda document_name, document_file_name, generated_at: tmp_path / "report.json",
    )

    def fail_save(report, output_path):
        raise OSError("could not write report")

    monkeypatch.setattr(report_writer, "save_report_json", fail_save)

    module = _load_module("mha_test_init_gui_report_failure", REPO_ROOT / "InitGui.py")
    workbench = module.ModelHealthAssistantWorkbench()
    workbench.Initialize()
    command = fake_gui.commands["MHA_TestCommand"]
    messages.clear()

    command.Activated()

    assert messages == [
        "Model Health Assistant: Report writing failed: OSError: could not write report\n"
    ]


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
    assert local_classes == ["_TestCommand"]
