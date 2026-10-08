from pathlib import Path

from common.config import AblationConfig, load_ablation

CONFIGS = Path(__file__).resolve().parents[2] / "bench" / "configs"


def test_baseline_rule():
    cfg = load_ablation(CONFIGS / "baseline_rule.yaml")
    assert cfg.name == "baseline_rule"
    assert cfg.use_edge_agent is False
    assert cfg.use_cloud is False


def test_edge_only():
    cfg = load_ablation(CONFIGS / "edge_only.yaml")
    assert cfg.name == "edge_only"
    assert cfg.use_edge_agent is True
    assert cfg.use_cloud is False


def test_full_is_default():
    cfg = load_ablation(CONFIGS / "full.yaml")
    assert cfg == AblationConfig()
    assert cfg.twin_fidelity == "hybrid"
