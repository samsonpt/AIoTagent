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


def test_no_twin_param_tune_enqueues_with_human_gate():
    ablation = AblationConfig(use_human_gate=True)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=None)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["command"] == "set_current_density"
    assert not any(
        a.command == "set_current_density" and a.accepted for a in store.actions()
    )


def test_twin_lookahead_off_high_risk_stamps_without_simulate():
    """use_twin_lookahead=False + high-risk must not call simulate; Gate 3 auto-stamps."""
    ablation = AblationConfig(use_twin_lookahead=False, use_human_gate=False)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=None)
    publish(bus, "plating", "set_current_density", {"asd": 2.4})
    steps(plant)
    accepted = [
        a for a in store.actions() if a.command == "set_current_density" and a.accepted
    ]
    assert accepted
    assert any("high_risk_auto" in a.reason for a in accepted)
    assert store.list_approvals("pending") == []


def test_no_twin_high_risk_param_tune_enqueues_with_human_gate():
    """twin=None + human gate + high-risk param_tune → enqueue, no exception."""
    ablation = AblationConfig(use_human_gate=True)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=None)
    publish(bus, "plating", "set_current_density", {"asd": 2.4})
    steps(plant)
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["command"] == "set_current_density"
    assert not any(
        a.command == "set_current_density" and a.accepted for a in store.actions()
    )


def test_auto_disabled_rejects_non_fast_stop_still_stamps():
    plant, bus, store, _, guard = make_guarded()
    guard.auto_actions_enabled = False
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    rejected = [a for a in store.actions() if a.command == "set_current_density"]
    assert rejected
    assert all(a.accepted is False for a in rejected)
    assert "chain_invalid_auto_disabled" in rejected[0].reason

    publish(bus, "drill", "stop")
    steps(plant)
    assert any(a.command == "stop" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped


def test_resolve_approval_approved_and_rejected():
    ablation = AblationConfig(use_human_gate=True)
    plant, bus, store, _, guard = make_guarded(ablation=ablation, twin=None)
    stamped = []

    def spy(_topic, payload):
        if payload.get("guarded") is True:
            stamped.append(payload)

    bus.subscribe("plant/+/+/command", spy, "spy_cmd")
    bus.subscribe(topics.line_command(), spy, "spy_line")

    publish(bus, "plating", "set_current_density", {"asd": 2.2})
    steps(plant)
    req_ok = store.list_approvals("pending")[0]["request_id"]
    guard.resolve_approval(req_ok, True, "human_ok")
    assert stamped
    assert stamped[-1]["guard_id"] == "guard"
    assert stamped[-1]["command"] == "set_current_density"
    assert any(
        a.command == "set_current_density" and a.accepted for a in store.actions()
    )

    publish(bus, "line", "hold_lot", {"lot_id": "L2"})
    steps(plant)
    req_no = [r for r in store.list_approvals("pending") if r["command"] == "hold_lot"][
        0
    ]["request_id"]
    guard.resolve_approval(req_no, False, "human_deny")
    denied = [
        a
        for a in store.actions()
        if a.command == "hold_lot" and a.accepted is False
    ]
    assert denied
    assert "human_deny" in denied[-1].reason
