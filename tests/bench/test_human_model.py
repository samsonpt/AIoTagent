from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from bench.human_model import HumanModel
from bench.schema import TraceStore
from guard.action_guard import ActionGuard

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_human_ocap_delayed_two_ticks():
    bus, clock = InMemoryBus(), SimClock()
    human = HumanModel(bus, RECIPE, clock, delay_ticks=2)
    commands = []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    clock.advance_tick()
    clock.advance_tick()
    bus.publish(
        topics.events("etch"),
        {"t": 3600.0, "tick": 1, "process": "etch", "key": "sg", "rule": "R1", "value": 1.40, "replayed": False},
        "edge-etch",
    )
    human.on_tick(clock)
    assert commands == []
    clock.advance_tick()
    human.on_tick(clock)
    clock.advance_tick()
    human.on_tick(clock)
    assert [c["source"] for c in commands] == ["human", "human"]
    assert {c["command"] for c in commands} == {"repair_regenerator", "adjust_sg"}


def test_ocap_false_does_not_emit_commands():
    bus, clock = InMemoryBus(), SimClock()
    HumanModel(bus, RECIPE, clock, delay_ticks=0, ocap=False)
    commands = []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    bus.publish(
        topics.events("etch"),
        {"t": 0.0, "tick": 0, "process": "etch", "key": "sg", "rule": "R1", "value": 1.40, "replayed": False},
        "edge-etch",
    )
    clock.advance_tick()
    assert commands == []


def _approval_decision(seed: int) -> str:
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    ablation = AblationConfig(use_human_gate=True)
    guard = ActionGuard(bus, store, RECIPE, clock, twin=None, seed=seed, ablation=ablation)
    human = HumanModel(
        bus, RECIPE, clock, store=store, guard=guard, seed=seed, ocap=False, approval_delay_s=900
    )
    bus.publish(
        topics.line_command(),
        {"command": "hold_lot", "params": {"lot_id": "L1"}, "source": "edge", "reason": "test"},
        "edge",
    )
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    t_submit = pending[0]["t_submit"]
    human.on_tick(clock)
    assert store.list_approvals("pending")
    # 900s delay; one tick is 1800s
    clock.advance_tick()
    assert clock.now >= t_submit + 900
    human.on_tick(clock)
    decided = [r for r in store.list_approvals() if r["status"] != "pending"]
    assert len(decided) == 1
    return decided[0]["status"]


def test_approval_delay_and_seed_stable():
    a = _approval_decision(7)
    b = _approval_decision(7)
    assert a == b
    assert a in {"approved", "rejected"}
