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


def test_chain_tamper_on_tick_disables_auto_actions():
    plant, bus, store, clock, guard = make_guarded()
    publish(bus, "drill", "stop")
    steps(plant)
    assert store.verify_chain()[0] is True

    store._conn.execute(
        "UPDATE trace_chain SET payload = ? WHERE kind = 'act'",
        ['{"command":"hacked"}'],
    )
    store._conn.commit()
    assert store.verify_chain()[0] is False

    assert guard.auto_actions_enabled is True
    guard.on_tick(clock)
    assert guard.auto_actions_enabled is False


def test_degraded_rejects_param_tune_stop_still_ok():
    plant, bus, store, clock, guard = make_guarded()
    publish(bus, "drill", "stop")
    steps(plant)
    store._conn.execute(
        "UPDATE trace_chain SET payload = ? WHERE kind = 'act'",
        ['{"command":"hacked"}'],
    )
    store._conn.commit()
    guard.on_tick(clock)

    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    rejected = [
        a for a in store.actions() if a.command == "set_current_density" and not a.accepted
    ]
    assert rejected
    assert "chain_invalid_auto_disabled" in rejected[-1].reason

    publish(bus, "drill", "resume")
    steps(plant)
    assert any(a.command == "resume" and a.accepted for a in store.actions())


def test_resolve_approval_stamps_when_degraded():
    ablation = AblationConfig(use_human_gate=True, use_twin_lookahead=False)
    plant, bus, store, clock, guard = make_guarded(ablation=ablation)
    stamped = []

    def spy(_topic, payload):
        if payload.get("guarded") is True:
            stamped.append(payload)

    bus.subscribe("plant/+/+/command", spy, "spy_cmd")
    bus.subscribe(topics.line_command(), spy, "spy_line")

    publish(bus, "drill", "stop")
    steps(plant)
    publish(bus, "plating", "set_current_density", {"asd": 2.2})
    steps(plant)
    req_id = store.list_approvals("pending")[0]["request_id"]

    stop_action = next(a for a in store.actions() if a.command == "stop")
    store._conn.execute(
        "UPDATE trace_chain SET payload = ? WHERE ref = ?",
        ['{"command":"hacked"}', str(stop_action.action_id)],
    )
    store._conn.commit()
    guard.on_tick(clock)
    assert guard.auto_actions_enabled is False

    guard.resolve_approval(req_id, True, "human_ok")
    assert stamped
    assert stamped[-1]["guard_id"] == "guard"
    assert any(
        a.command == "set_current_density" and a.accepted for a in store.actions()
    )
