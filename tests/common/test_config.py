import pytest
from pydantic import ValidationError

from common.config import AblationConfig, load_ablation


def test_defaults_enable_everything():
    cfg = AblationConfig()
    assert cfg.name == "full"
    assert cfg.twin_fidelity == "hybrid"
    flags = cfg.model_dump()
    flags.pop("name")
    flags.pop("twin_fidelity")
    assert flags and all(v is True for v in flags.values())


def test_frozen():
    cfg = AblationConfig()
    with pytest.raises(ValidationError):
        cfg.use_cloud = False


def test_invalid_twin_fidelity_rejected():
    with pytest.raises(ValidationError):
        AblationConfig(twin_fidelity="perfect")


def test_load_partial_keeps_other_defaults(tmp_path):
    path = tmp_path / "no_cloud.yaml"
    path.write_text("name: no_cloud\nuse_cloud: false\n", encoding="utf-8")
    cfg = load_ablation(path)
    assert cfg.name == "no_cloud"
    assert cfg.use_cloud is False
    assert cfg.use_edge_agent is True
    assert cfg.twin_fidelity == "hybrid"


def test_load_unknown_key_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("use_magic: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_ablation(str(path))
