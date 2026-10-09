"""Smoke tests for Streamlit page pure helpers (no Streamlit server)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from bench.schema import EpisodeRecord, PanelRecord, TraceStore
from ui.db import DashboardStore
from ui.pages.aoi import build_defect_summary
from ui.pages.approvals import (
    build_approval_table,
    resolve_t_decide,
)
from ui.pages.episodes import build_episode_table
from ui.pages.monitor import build_telemetry_frame
from ui.pages.trace import build_chain_table


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "ui"


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


def test_ui_modules_have_no_sim_import():
    forbidden = []
    for path in UI_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sim" or alias.name.startswith("sim."):
                        forbidden.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "sim" or node.module.startswith("sim."):
                    forbidden.append(f"{path.name}: from {node.module}")
    assert forbidden == []


def test_build_approval_table_columns():
    rows = [
        {
            "request_id": "r1",
            "t_submit": 1.0,
            "process": "line",
            "equipment": "line",
            "command": "hold_lot",
            "lot_id": "L1",
            "status": "pending",
            "source": "edge",
            "applied": 0,
            "params": {"lot_id": "L1"},
        }
    ]
    df = build_approval_table(rows)
    assert list(df.columns) == [
        "request_id",
        "t_submit",
        "process",
        "equipment",
        "command",
        "lot_id",
        "status",
        "source",
        "applied",
    ]
    assert len(df) == 1
    assert df.iloc[0]["request_id"] == "r1"


def test_resolve_t_decide_injected_and_fallback(tmp_path):
    db_path = tmp_path / "dash.db"
    with TraceStore(db_path) as store:
        store.record_telemetry(3.5, "etch", "ETC-01", {"temp": 1.0})
        store.record_telemetry(1.0, "etch", "ETC-01", {"temp": 0.5})
    with DashboardStore(db_path) as store:
        assert resolve_t_decide(store, t_decide=9.0) == 9.0
        assert resolve_t_decide(store, t_decide=None) == 3.5

    empty = tmp_path / "empty.db"
    with DashboardStore(empty) as store:
        assert resolve_t_decide(store, t_decide=None) == 0.0


def test_decide_approval_via_helper_path(tmp_path):
    db_path = tmp_path / "dash.db"
    rid = _enqueue(db_path)
    with DashboardStore(db_path) as store:
        t = resolve_t_decide(store, t_decide=2.5)
        store.decide_approval(rid, True, t_decide=t, reason="ui-ok")
        rows = store.list_approvals(status="approved")
    assert len(rows) == 1
    assert rows[0]["decider"] == "ui"
    assert rows[0]["t_decide"] == 2.5
    assert rows[0]["applied"] == 0


def test_build_chain_table():
    rows = [
        {
            "seq": 1,
            "lot_id": "L1",
            "kind": "act",
            "ref": "a1",
            "t": 0.0,
            "payload": {"command": "stop"},
            "prev_hash": "GENESIS",
            "entry_hash": "abc",
        }
    ]
    df = build_chain_table(rows)
    assert list(df.columns) == [
        "seq",
        "lot_id",
        "kind",
        "ref",
        "t",
        "payload",
        "prev_hash",
        "entry_hash",
    ]
    assert df.iloc[0]["ref"] == "a1"


def test_build_telemetry_frame():
    rows = [
        (0.0, "etch", "ETC-01", "temp", 40.0),
        (1.0, "etch", "ETC-01", "temp", 41.0),
        (1.0, "etch", "ETC-01", "speed", 2.0),
    ]
    df = build_telemetry_frame(rows)
    assert "t" in df.columns
    assert "temp" in df.columns
    assert "speed" in df.columns
    assert len(df) == 2
    assert float(df.loc[df["t"] == 1.0, "temp"].iloc[0]) == 41.0


def test_import_app_module():
    pytest.importorskip("streamlit")
    import ui.app as app

    assert callable(app.main)
    assert callable(app.parse_db_arg)
    assert app.parse_db_arg(["streamlit", "run", "ui/app.py", "--", "--db", "x.db"]) == "x.db"
    assert app.parse_db_arg(["--db", "y.db"]) == "y.db"
    assert app.parse_db_arg([]) is None


def test_build_defect_summary():
    panels = [
        PanelRecord(
            panel_id="P1",
            lot_id="L1",
            part_no="PN",
            t_release=0.0,
            t_aoi=1.0,
            drill={},
            plating={},
            etch={},
            defects=[{"type": "open"}, {"type": "short"}, {"type": "open"}],
            root_cause_truth="drill_wear",
            scrapped=False,
        )
    ]
    df = build_defect_summary(panels)
    assert list(df.columns) == [
        "panel_id",
        "lot_id",
        "part_no",
        "t_aoi",
        "n_defects",
        "defect_types",
        "root_cause_truth",
        "scrapped",
    ]
    assert int(df.iloc[0]["n_defects"]) == 3
    assert df.iloc[0]["defect_types"] == "open,short"


def test_build_episode_table():
    eps = [
        EpisodeRecord(
            episode_id="e1",
            process="etch",
            trigger="spc",
            t_detect=1.0,
            handler="edge",
            t_decide=2.0,
            violated=False,
        )
    ]
    df = build_episode_table(eps)
    assert df.iloc[0]["episode_id"] == "e1"
    assert df.iloc[0]["handler"] == "edge"
    assert build_episode_table([]).empty
