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


MINUS_KEYS = [
    "use_cloud",
    "use_twin_lookahead",
    "use_peer_feedforward",
    "use_event_trigger",
    "use_confidence_modulation",
    "use_rag",
    "use_human_gate",
    "use_counterfactual_rca",
    "use_twin_confidence_gate",
]


def test_full_minus_files_exist_and_differ():
    full = AblationConfig()
    for key in MINUS_KEYS:
        cfg = load_ablation(CONFIGS / f"full_minus_{key}.yaml")
        assert cfg.name == f"full_minus_{key}"
        assert getattr(cfg, key) is False
        for other in MINUS_KEYS:
            if other != key:
                assert getattr(cfg, other) is True


def test_full_twin_variants():
    assert load_ablation(CONFIGS / "full_twin_none.yaml").twin_fidelity == "none"
    assert load_ablation(CONFIGS / "full_twin_mechanistic.yaml").twin_fidelity == "mechanistic"
