import ast
import importlib
from pathlib import Path

import guard
from guard.action_guard import ActionGuard
from tests.test_isolation import find_sim_imports

ROOT = Path(__file__).resolve().parents[2]


def test_guard_package_exists():
    assert guard.__file__ is not None
    assert (ROOT / "guard" / "action_guard.py").is_file()


def test_action_guard_importable_without_sim():
    assert ActionGuard.client_id == "guard"


def test_guard_has_no_sim_imports():
    guard_violations = [v for v in find_sim_imports(ROOT) if v.startswith("guard")]
    assert guard_violations == []


def test_guard_source_has_no_exec_or_eval():
    guard_dir = ROOT / "guard"
    violations: list[str] = []
    for path in sorted(guard_dir.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {func.id}()")
    assert violations == []


def test_guard_modules_reload_without_sim():
    for name in ("guard", "guard.policy", "guard.action_guard"):
        mod = importlib.import_module(name)
        assert mod is not None
        assert "sim" not in getattr(mod, "__file__", "")
