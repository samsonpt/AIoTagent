import json

import pytest

from bench.schema import TraceStore
from ui.db import DashboardStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "dash.db"


def _enqueue(path, *, lot_id="L1"):
    with TraceStore(path) as store:
        return store.enqueue_approval(
            t_submit=0.0,
            process="line",
            equipment="line",
            command="hold_lot",
            params={"lot_id": lot_id},
            source="edge",
            lot_id=lot_id,
            topic="plant/line/command",
        )


def test_decide_approval_leaves_unapplied_with_ui_decider(db_path):
    rid = _enqueue(db_path)
    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, True, t_decide=1.0, reason="ok")
    with TraceStore(db_path) as store:
        rows = store.list_unapplied_decisions()
        assert len(rows) == 1
        assert rows[0]["request_id"] == rid
        assert rows[0]["decider"] == "ui"
        assert rows[0]["status"] == "approved"
        assert rows[0]["applied"] == 0


def test_decide_approval_reject(db_path):
    rid = _enqueue(db_path)
    with DashboardStore(db_path) as dash:
        dash.decide_approval(rid, False, t_decide=2.0, reason="no")
    with TraceStore(db_path) as store:
        rows = store.list_unapplied_decisions()
        assert len(rows) == 1
        assert rows[0]["status"] == "rejected"
        assert rows[0]["decider"] == "ui"


def test_list_approvals_delegates(db_path):
    rid = _enqueue(db_path)
    with DashboardStore(db_path) as dash:
        pending = dash.list_approvals(status="pending")
        assert len(pending) == 1 and pending[0]["request_id"] == rid
        dash.decide_approval(rid, True, t_decide=1.0)
        approved = dash.list_approvals(status="approved")
        assert len(approved) == 1 and approved[0]["decider"] == "ui"


def test_verify_chain_delegates(db_path):
    with TraceStore(db_path) as store:
        store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
    with DashboardStore(db_path) as dash:
        ok, reason = dash.verify_chain("L1")
        assert ok is True and reason == ""


def test_chain_rows_and_lot_ids(db_path):
    with TraceStore(db_path) as store:
        store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
        store.append_chain(lot_id="L2", kind="act", ref="a2", t=1.0, payload={"command": "hold"})
    with DashboardStore(db_path) as dash:
        assert dash.lot_ids() == ["L1", "L2"]
        rows = dash.chain_rows("L1")
        assert len(rows) == 1
        assert rows[0]["ref"] == "a1"
        assert rows[0]["payload"] == {"command": "stop"}


def test_telemetry_limit(db_path):
    with TraceStore(db_path) as store:
        for i in range(10):
            store.record_telemetry(float(i), "etch", "ETC-01", {"temp": float(i)})
    with DashboardStore(db_path) as dash:
        rows = dash.telemetry(process="etch", limit=3)
        assert len(rows) == 3
        assert rows[-1][0] == 9.0


def test_panels_and_episodes_skeleton(db_path):
    with DashboardStore(db_path) as dash:
        assert dash.panels() == []
        assert dash.episodes() == []
