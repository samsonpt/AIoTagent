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


def make_plant_guard(*, ablation: AblationConfig | None = None):
    scenario = Scenario(name="ooda", seed=7, n_ticks=20)
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    plant = Plant(scenario, bus, store, clock)
    ablation = ablation or AblationConfig()
    guard = ActionGuard(bus, store, RECIPE, clock, None, seed=7, ablation=ablation)
    return plant, bus, store, guard


def publish_unguarded(bus, process, command, params=None, source="edge", reason="ooda"):
    topic = topics.line_command() if process == "line" else topics.command(process)
    bus.publish(
        topic,
        {
            "command": command,
            "params": params or {},
            "source": source,
            "reason": reason,
        },
        source,
    )
    return topic


def test_ooda_stop_unguarded_via_guard_to_plant():
    plant, bus, store, _guard = make_plant_guard()
    assert plant.stations["drill"].stopped is False

    publish_unguarded(bus, "drill", "stop")
    plant.step_tick()

    assert any(a.command == "stop" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped is True
    assert store.verify_chain()[0] is True


def test_ooda_unguarded_stop_ignored_without_guard():
    scenario = Scenario(name="ooda-no-guard", seed=7, n_ticks=20)
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    plant = Plant(scenario, bus, store, clock)

    publish_unguarded(bus, "drill", "stop")
    plant.step_tick()

    assert store.actions() == []
    assert plant.stations["drill"].stopped is False
