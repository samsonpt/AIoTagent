import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_requires_python_312():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.12"


def test_gitignore_excludes_workflow_scratch_and_secrets():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in (".worktrees/", ".superpowers/", ".env", "*.db"):
        assert entry in lines
