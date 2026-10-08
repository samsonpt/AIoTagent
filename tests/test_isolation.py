import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISOLATED_PACKAGES = ("twin", "edge", "cloud", "guard")
FORBIDDEN = "sim"


def _is_forbidden(module: str | None) -> bool:
    return module is not None and (module == FORBIDDEN or module.startswith(FORBIDDEN + "."))


def find_sim_imports(root: Path) -> list[str]:
    violations = []
    for package in ISOLATED_PACKAGES:
        pkg_dir = root / package
        if not pkg_dir.is_dir():
            continue
        for path in sorted(pkg_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    if _is_forbidden(module):
                        violations.append(f"{path.relative_to(root)}:{node.lineno}: {module}")
    return violations


def test_isolated_packages_do_not_import_sim():
    assert find_sim_imports(ROOT) == []


def test_checker_detects_violations(tmp_path):
    (tmp_path / "edge" / "sub").mkdir(parents=True)
    (tmp_path / "edge" / "ok.py").write_text(
        "import simulation\nfrom simple import x\nfrom common import sim\nfrom . import sim\n",
        encoding="utf-8",
    )
    (tmp_path / "edge" / "sub" / "bad.py").write_text(
        "import sim\nimport os, sim.plant\nfrom sim import etch\nfrom sim.models import drill\n",
        encoding="utf-8",
    )
    (tmp_path / "twin").mkdir()
    (tmp_path / "sim").mkdir()
    (tmp_path / "sim" / "core.py").write_text("import sim\n", encoding="utf-8")

    violations = find_sim_imports(tmp_path)

    assert len(violations) == 4
    assert all(v.startswith(str(Path("edge/sub/bad.py"))) for v in violations)
    assert [v.rsplit(": ", 1)[1] for v in violations] == ["sim", "sim.plant", "sim", "sim.models"]
