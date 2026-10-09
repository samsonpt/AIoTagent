from pathlib import Path

from bench.schema import TraceStore
from common.bus import InMemoryBus
from common.config import AblationConfig
from sim.faults import load_scenario
from sim.runner import run

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "bench" / "scenarios"


def test_runner_guard_short_scenario():
    bus = InMemoryBus()
    stamped = []

    def spy(_topic, payload):
        if payload.get("guarded") is True and payload.get("guard_id") == "guard":
            stamped.append(payload)

    bus.subscribe("plant/+/+/command", spy, "spy")
    bus.subscribe("plant/line/command", spy, "spy-line")

    store = TraceStore()
    summary = run(
        load_scenario(SCENARIOS / "nominal.yaml"),
        store,
        bus=bus,
        ablation=AblationConfig(use_cloud=False),
        n_ticks=3,
    )
    assert summary.scenario == "nominal"
    assert summary.n_panels >= 0
    guard_actions = [
        a
        for a in store.actions()
        if a.reason
        in ("fast_path", "passed_gates", "high_risk_auto")
        or (a.reason or "").startswith("rejected_by=guard")
        or (a.reason or "").startswith("human_")
    ]
    assert stamped or guard_actions or store.list_approvals()
    assert store.verify_chain()[0] is True
