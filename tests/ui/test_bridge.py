"""UI 决策桥冒烟：DashboardStore 写审批 → Guard.on_tick 应用。"""

from pathlib import Path

from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from guard.action_guard import ActionGuard
from ui.db import DashboardStore

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


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
