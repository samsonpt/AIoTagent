import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cloud.llm import DeepSeekLLM
from cloud.schemas import SupervisorDecision

ROOT = Path(__file__).resolve().parents[2]


def test_env_example_exists_without_real_keys():
    path = ROOT / ".env.example"
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in content
    for line in content.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip() == "DEEPSEEK_API_KEY":
            assert not value.strip() or value.strip().startswith("your-")


def test_cloud_source_has_no_exec_or_eval():
    cloud_dir = ROOT / "cloud"
    violations: list[str] = []
    for path in sorted(cloud_dir.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {func.id}()")
    assert violations == []


def test_deepseek_llm_error_does_not_leak_api_key():
    secret = "sk-test-secret-key-not-real-abc123"
    llm = DeepSeekLLM(api_key=secret)
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError(
        f"authentication failed for key {secret}"
    )
    llm._client = mock_client

    with pytest.raises(ValueError) as exc_info:
        llm.complete([{"role": "user", "content": "x"}], response_model=SupervisorDecision)

    message = str(exc_info.value)
    assert secret not in message
