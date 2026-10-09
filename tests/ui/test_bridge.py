"""UI 决策桥端到端冒烟：Plant → pending → DashboardStore → Guard.on_tick。"""

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
from ui.db import DashboardStore

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")
ABLATION = AblationConfig(use_human_gate=True, use_twin_lookahead=False)


def _publish_hold_lot(bus):
    topic = topics.line_command()
    bus.publish(
        topic,
        {"command": "hold_lot", "params": {"lot_id": "L1"}, "source": "edge", "reason": "test"},
        "edge",
    )
    return topic


def _open_plant_guard(db_path, bus, clock):
    store = TraceStore(db_path)
    scenario = Scenario(name="t", seed=5, n_ticks=20)
    plant = Plant(scenario, bus, store, clock)
    guard = ActionGuard(bus, store, RECIPE, clock, None, seed=5, ablation=ABLATION)
    return store, plant, guard


def test_e2e_decision_bridge_approve(tmp_path):
    db_path = tmp_path / "bridge.db"
    bus, clock = InMemoryBus(), SimClock()

    store, plant, _ = _open_plant_guard(db_path, bus, clock)
    _publish_hold_lot(bus)
    plant.step_tick()
    pending = store.list_approvals("pending")
    assert len(pending) == 1
    assert pending[0]["command"] == "hold_lot"
    rid = pending[0]["request_id"]
    assert not any(a.command == "hold_lot" and a.accepted for a in store.actions())
    store.close()

    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, True, t_decide=1.0, reason="ui_ok")
        row = [r for r in dash.list_approvals() if r["request_id"] == rid][0]
        assert row["decider"] == "ui"
        assert row["applied"] == 0

    store, plant, guard = _open_plant_guard(db_path, bus, clock)
    assert store.list_unapplied_decisions()
    guard.on_tick(clock)
    plant.step_tick()
    assert store.list_unapplied_decisions() == []
    applied = [r for r in store.list_approvals() if r["request_id"] == rid][0]
    assert applied["applied"] == 1
    assert applied["status"] == "approved"
    assert any(a.command == "hold_lot" and a.accepted is True for a in store.actions())
    assert store.verify_chain()[0] is True
    store.close()


def test_e2e_decision_bridge_reject(tmp_path):
    db_path = tmp_path / "bridge.db"
    bus, clock = InMemoryBus(), SimClock()

    store, plant, _ = _open_plant_guard(db_path, bus, clock)
    _publish_hold_lot(bus)
    plant.step_tick()
    rid = store.list_approvals("pending")[0]["request_id"]
    store.close()

    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, False, t_decide=2.0, reason="ui_no")

    store, plant, guard = _open_plant_guard(db_path, bus, clock)
    guard.on_tick(clock)
    plant.step_tick()
    row = [r for r in store.list_approvals() if r["request_id"] == rid][0]
    assert row["applied"] == 1
    assert row["status"] == "rejected"
    assert store.list_unapplied_decisions() == []
    rejected = [a for a in store.actions() if a.command == "hold_lot" and not a.accepted]
    assert rejected
    store.close()


def test_e2e_chain_tamper_verify_consistent(tmp_path):
    db_path = tmp_path / "bridge.db"
    bus, clock = InMemoryBus(), SimClock()

    store, plant, _ = _open_plant_guard(db_path, bus, clock)
    _publish_hold_lot(bus)
    plant.step_tick()
    rid = store.list_approvals("pending")[0]["request_id"]
    store.close()

    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, True, t_decide=1.0, reason="ui_ok")

    store, plant, guard = _open_plant_guard(db_path, bus, clock)
    guard.on_tick(clock)
    plant.step_tick()
    assert store.verify_chain()[0] is True

    store._conn.execute(
        "UPDATE trace_chain SET payload = ? WHERE kind = 'act'",
        ['{"command":"hacked"}'],
    )
    store._conn.commit()

    store_ok, store_reason = store.verify_chain()
    with DashboardStore(db_path) as dash:
        dash_ok, dash_reason = dash.verify_chain()

    assert store_ok is False
    assert dash_ok is False
    assert store_reason == dash_reason
    store.close()


def test_dashboard_decide_applied_on_guard_tick(tmp_path):
    db_path = tmp_path / "bridge.db"
    with TraceStore(db_path) as store:
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

    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, True, t_decide=1.0, reason="ui_ok")
        row = [r for r in dash.list_approvals() if r["request_id"] == rid][0]
        assert row["decider"] == "ui"
        assert row["applied"] == 0

    bus, clock = InMemoryBus(), SimClock()
    with TraceStore(db_path) as store:
        guard = ActionGuard(
            bus,
            store,
            RECIPE,
            clock,
            None,
            seed=5,
            ablation=AblationConfig(use_human_gate=True, use_twin_lookahead=False),
        )
        assert store.list_unapplied_decisions()
        guard.on_tick(clock)
        assert store.list_unapplied_decisions() == []
        applied = store.list_approvals()[0]
        assert applied["applied"] == 1
        assert applied["status"] == "approved"
        assert any(a.command == "hold_lot" and a.accepted is True for a in store.actions())
