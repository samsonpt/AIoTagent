from pathlib import Path

from bench.schema import TraceStore
from common import topics
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


def test_fast_path_stop_stamped_and_executed():
    plant, bus, store, clock, guard = make_guarded()
    publish(bus, "drill", "stop")
    steps(plant)
    assert any(a.command == "stop" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped
    assert store.verify_chain()[0] is True


def test_envelope_reject_unknown_command():
    plant, bus, store, _, _ = make_guarded()
    rpm_before = plant.stations["etch"].etch_temp_c
    publish(bus, "etch", "levitate")
    steps(plant)
    rejected = [a for a in store.actions() if a.command == "levitate"]
    assert rejected
    assert all(a.accepted is False for a in rejected)
    assert plant.stations["etch"].etch_temp_c == rpm_before


def test_hold_lot_enqueued_when_human_gate():
    ablation = AblationConfig(use_human_gate=True)
    plant, bus, store, _, _ = make_guarded(ablation=ablation)
    publish(bus, "line", "hold_lot", {"lot_id": "L1"})
    steps(plant)
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["command"] == "hold_lot"
    assert not any(a.command == "hold_lot" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped is False
