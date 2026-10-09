from pathlib import Path

from bench.human_model import HumanModel
from bench.schema import FaultRecord, TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from guard.action_guard import ActionGuard

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _guard_setup():
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    ablation = AblationConfig(use_human_gate=True, use_twin_lookahead=False)
    guard = ActionGuard(bus, store, RECIPE, clock, None, seed=5, ablation=ablation)
    return bus, store, clock, guard


def test_ui_decision_applied_on_guard_tick():
    _, store, clock, guard = _guard_setup()
    rid = store.enqueue_approval(
        t_submit=0.0,
        process="line",
        equipment="line",
        command="hold_lot",
        params={"lot_id": "L1"},
        source="edge",
        lot_id="L1",
        topic=topics.line_command(),
    )
    store.update_approval(
        rid,
        status="approved",
        t_decide=clock.now,
        decider="ui",
        reason="ui_approved",
    )
    assert store.list_unapplied_decisions()
    guard.on_tick(clock)
    assert store.list_unapplied_decisions() == []
    row = store.list_approvals()[0]
    assert row["applied"] == 1
    assert any(a.command == "hold_lot" and a.accepted is True for a in store.actions())


def test_human_model_skips_approvals_when_auto_approve_false():
    bus, store, clock, guard = _guard_setup()
    human = HumanModel(
        bus,
        RECIPE,
        clock,
        store=store,
        guard=guard,
        seed=42,
        ocap=False,
        approval_delay_s=900,
        auto_approve=False,
    )
    store.record_fault(
        FaultRecord(
            fault_id="f1",
            process="line",
            equipment="line",
            fault_type="hold",
            params={},
            t_start=0.0,
        )
    )
    rid = store.enqueue_approval(
        t_submit=0.0,
        process="line",
        equipment="line",
        command="hold_lot",
        params={"lot_id": "L1"},
        source="edge",
        lot_id="L1",
        topic=topics.line_command(),
    )
    while clock.now < 900:
        clock.advance_tick()
    human.on_tick(clock)
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["request_id"] == rid
    assert store.list_unapplied_decisions() == []


def test_human_model_marks_applied_when_auto_approve_true():
    bus, store, clock, guard = _guard_setup()
    human = HumanModel(
        bus,
        RECIPE,
        clock,
        store=store,
        guard=guard,
        seed=42,
        ocap=False,
        approval_delay_s=900,
        auto_approve=True,
    )
    store.record_fault(
        FaultRecord(
            fault_id="f1",
            process="line",
            equipment="line",
            fault_type="hold",
            params={},
            t_start=0.0,
        )
    )
    rid = store.enqueue_approval(
        t_submit=0.0,
        process="line",
        equipment="line",
        command="hold_lot",
        params={"lot_id": "L1"},
        source="edge",
        lot_id="L1",
        topic=topics.line_command(),
    )
    while clock.now < 900:
        clock.advance_tick()
    human.on_tick(clock)
    row = [r for r in store.list_approvals() if r["request_id"] == rid][0]
    assert row["status"] in ("approved", "rejected")
    assert row["applied"] == 1
    assert store.list_unapplied_decisions() == []
