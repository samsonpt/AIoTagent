import os
from pathlib import Path

from common.env import load_env


def test_load_env_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    load_env(root=tmp_path / ".env")
    assert os.environ.get("DEEPSEEK_API_KEY") == "from-dotenv"


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "already-set")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    load_env(root=tmp_path / ".env")
    assert os.environ.get("DEEPSEEK_API_KEY") == "already-set"
