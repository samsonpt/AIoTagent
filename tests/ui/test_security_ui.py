import ast
import importlib
from pathlib import Path

import ui
from tests.test_isolation import find_sim_imports

ROOT = Path(__file__).resolve().parents[2]


def test_ui_package_exists():
    assert ui.__file__ is not None
    assert (ROOT / "ui" / "db.py").is_file()
    assert (ROOT / "ui" / "app.py").is_file()


def test_ui_has_no_sim_imports():
    ui_violations = [v for v in find_sim_imports(ROOT) if v.startswith("ui")]
    assert ui_violations == []


def test_ui_source_has_no_exec_or_eval():
    ui_dir = ROOT / "ui"
    violations: list[str] = []
    for path in sorted(ui_dir.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {func.id}()")
    assert violations == []


def test_ui_modules_import_without_sim():
    for name in (
        "ui",
        "ui.db",
        "ui.app",
        "ui.pages.aoi",
        "ui.pages.episodes",
        "ui.pages.twin",
        "ui.pages.approvals",
        "ui.pages.trace",
        "ui.pages.monitor",
    ):
        mod = importlib.import_module(name)
        assert mod is not None
        assert "sim" not in Path(getattr(mod, "__file__", "")).parts
