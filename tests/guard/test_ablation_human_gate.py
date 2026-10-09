from pathlib import Path

from bench.schema import TraceStore
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from guard.action_guard import ActionGuard
from sim.faults import Scenario
from sim.plant import Plant

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def make_guarded(*, ablation: AblationConfig | None = None, twin=None):
    scenario = Scenario(name="t", seed=5, n_ticks=20)
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    plant = Plant(scenario, bus, store, clock)
    ablation = ablation or AblationConfig()
    guard = ActionGuard(
        bus, store, RECIPE, clock, twin, seed=5, ablation=ablation
    )
    return plant, bus, store, clock, guard


def publish(bus, process, command, params=None, source="edge", reason="test"):
    from common import topics

    topic = topics.line_command() if process == "line" else topics.command(process)
    bus.publish(
        topic,
        {
            "command": command,
            "params": params or {},
            "source": source,
            "reason": reason,
        },
        "edge",
    )
    return topic


def steps(plant, n=1):
    for _ in range(n):
        plant.step_tick()


def test_high_risk_stamps_without_human_gate():
    ablation = AblationConfig(use_human_gate=False, use_twin_lookahead=False)
    plant, bus, store, _, _ = make_guarded(ablation=ablation)
    publish(bus, "line", "hold_lot", {"lot_id": "L1"})
    steps(plant)
    assert store.list_approvals("pending") == []
    assert any(a.command == "hold_lot" and a.accepted for a in store.actions())


def test_scrap_lot_stamps_without_human_gate():
    ablation = AblationConfig(use_human_gate=False, use_twin_lookahead=False)
    plant, bus, store, _, _ = make_guarded(ablation=ablation)
    publish(bus, "line", "scrap_lot", {"lot_id": "L2"})
    steps(plant)
    assert store.list_approvals("pending") == []
    assert any(a.command == "scrap_lot" and a.accepted for a in store.actions())
