from pathlib import Path

from bench.schema import TraceStore
from common.config import AblationConfig, load_ablation
from sim.faults import load_scenario
from sim.runner import run

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "bench" / "scenarios"


def _cloud_client_ids(bus) -> set[str]:
    return {client_id for _, _, client_id in bus._subs if client_id == "cloud"}


def test_edge_only_does_not_subscribe_cloud():
    from common.bus import InMemoryBus

    bus = InMemoryBus()
    ablation = load_ablation(ROOT / "bench" / "configs" / "edge_only.yaml")
    run(load_scenario(SCENARIOS / "nominal.yaml"), TraceStore(), bus=bus, ablation=ablation, n_ticks=4)
    assert _cloud_client_ids(bus) == set()


def test_use_cloud_short_run_does_not_crash():
    ablation = AblationConfig(name="cloud_on", use_rag=False, use_counterfactual_rca=False)
    run(load_scenario(SCENARIOS / "nominal.yaml"), TraceStore(), ablation=ablation, n_ticks=8)


def test_use_cloud_nominal_length_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    ablation = AblationConfig(name="cloud_on", use_rag=False, use_counterfactual_rca=False)
    run(load_scenario(SCENARIOS / "nominal.yaml"), TraceStore(), ablation=ablation, n_ticks=48)
