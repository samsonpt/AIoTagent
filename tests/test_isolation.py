import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISOLATED_PACKAGES = ("twin", "edge", "cloud", "guard")
FORBIDDEN = "sim"


def _is_forbidden(module: str | None) -> bool:
    return module is not None and (module == FORBIDDEN or module.startswith(FORBIDDEN + "."))


def _from_module(path: Path, root: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    pkg = list(path.relative_to(root).parts[:-1])
    up = node.level - 1
    if up > len(pkg):
        return node.module
    base = pkg[: len(pkg) - up]
    prefix = ".".join(base)
    if node.module:
        return f"{prefix}.{node.module}" if prefix else node.module
    return prefix or None


def find_sim_imports(root: Path) -> list[str]:
    violations = []
    for package in ISOLATED_PACKAGES:
        pkg_dir = root / package
        if not pkg_dir.is_dir():
            continue
        for path in sorted(pkg_dir.rglob("*.py")):
            tree = ast.parse(path.read_bytes(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [_from_module(path, root, node)]
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
    (tmp_path / "edge" / "rel.py").write_text("from ..sim.drill import DrillStation\n", encoding="utf-8")
    (tmp_path / "twin").mkdir()
    (tmp_path / "sim").mkdir()
    (tmp_path / "sim" / "core.py").write_text("import sim\n", encoding="utf-8")

    violations = find_sim_imports(tmp_path)

    assert len(violations) == 5
    modules = [v.rsplit(": ", 1)[1] for v in violations]
    assert modules == ["sim.drill", "sim", "sim.plant", "sim", "sim.models"]
    assert violations[0].startswith(str(Path("edge/rel.py")))


def test_checker_handles_bom_files(tmp_path):
    (tmp_path / "guard").mkdir()
    (tmp_path / "guard" / "bom.py").write_text("from sim import etch\n", encoding="utf-8-sig")

    assert find_sim_imports(tmp_path) == [f"{Path('guard/bom.py')}:1: sim"]
