import dataclasses

import pytest

from bench.schema import (
    ACTION_WEIGHTS,
    HANDLERS,
    SOURCES,
    ActionRecord,
    EpisodeRecord,
    FaultRecord,
    PanelRecord,
    TraceStore,
)


def make_fault(fault_id="F001", t_start=100.0):
    return FaultRecord(
        fault_id=fault_id,
        process="etch",
        equipment="ETC-01",
        fault_type="nozzle_clog",
        params={"zone": 1, "severity": 0.4},
        t_start=t_start,
    )


def make_panel(panel_id="L0001-P01"):
    return PanelRecord(
        panel_id=panel_id,
        lot_id="L0001",
        part_no="PN-100",
        t_release=0.0,
        t_aoi=5400.0,
        drill={"bit_hits": 1200},
        plating={"thickness": [[25.1, 25.0, 24.9]]},
        etch={"line_width": 0.1},
        defects=[{"type": "open", "zone": [0, 1], "stage": "etch"}],
        root_cause_truth="nozzle_clog",
        scrapped=True,
    )


def make_action(t=200.0, source="edge", category="param_tune"):
    return ActionRecord(
        t=t,
        process="etch",
        equipment="ETC-01",
        command="set_pressure",
        params={"zone": 1, "delta": 0.05},
        source=source,
        category=category,
        affected_panels=3,
        accepted=True,
    )


def make_episode(episode_id="E001", t_detect=300.0, handler="edge", **kw):
    return EpisodeRecord(
        episode_id=episode_id,
        process="etch",
        trigger="spc_alarm",
        t_detect=t_detect,
        handler=handler,
        **kw,
    )


def write_sequence(store):
    store.record_fault(make_fault("F002", 50.0))
    store.record_fault(make_fault("F001", 50.0))
    store.update_fault("F001", t_end=90.0)
    store.record_panel(make_panel("L0001-P02"))
    store.record_panel(make_panel("L0001-P01"))
    aid = store.record_action(make_action())
    store.mark_action(aid, overridden=True)
    store.record_episode(make_episode("E002", 10.0, detail={"b": 1, "a": [1, 2]}))
    store.record_episode(make_episode("E001", 10.0))
    store.record_telemetry(1.0, "etch", "ETC-01", {"temp": 45.0, "pressure": 2.1})


@pytest.fixture
def store():
    with TraceStore() as s:
        yield s


def test_constants():
    assert SOURCES == ("edge", "peer", "cloud", "human", "rule")
    assert HANDLERS == ("edge", "cloud", "human")
    assert ACTION_WEIGHTS == {
        "param_tune": 0.1,
        "dosing": 0.2,
        "bit_change": 0.3,
        "maintenance": 0.3,
        "line_stop": 0.6,
        "lot_hold": 0.7,
        "scrap": 1.0,
    }


def test_records_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_fault().fault_id = "X"


def test_fault_roundtrip(store):
    rec = make_fault()
    store.record_fault(rec)
    assert store.faults() == [rec]


def test_faults_sorted_by_t_start_then_id(store):
    store.record_fault(make_fault("F003", 20.0))
    store.record_fault(make_fault("F002", 10.0))
    store.record_fault(make_fault("F001", 20.0))
    assert [f.fault_id for f in store.faults()] == ["F002", "F001", "F003"]


def test_update_fault_only_changes_given_fields(store):
    store.record_fault(make_fault())
    store.update_fault("F001", t_end=150.0)
    store.update_fault("F001", t_cleared=180.0, cleared_by="edge")
    store.update_fault("F001")
    assert store.faults() == [
        dataclasses.replace(make_fault(), t_end=150.0, t_cleared=180.0, cleared_by="edge")
    ]


def test_update_fault_does_not_affect_other_faults(store):
    store.record_fault(make_fault("F001"))
    store.record_fault(make_fault("F002"))
    store.update_fault("F001", t_end=150.0, cleared_by="edge")
    assert store.faults() == [
        dataclasses.replace(make_fault("F001"), t_end=150.0, cleared_by="edge"),
        make_fault("F002"),
    ]


def test_update_fault_unknown_id_raises(store):
    with pytest.raises(KeyError):
        store.update_fault("F404", t_end=1.0)
    store.update_fault("F404")


def test_panel_roundtrip(store):
    rec = make_panel()
    store.record_panel(rec)
    got = store.panels()
    assert got == [rec]
    assert got[0].scrapped is True


def test_panels_sorted_by_id(store):
    store.record_panel(make_panel("L0001-P02"))
    store.record_panel(make_panel("L0001-P01"))
    assert [p.panel_id for p in store.panels()] == ["L0001-P01", "L0001-P02"]


def test_action_roundtrip_and_incrementing_ids(store):
    first = store.record_action(make_action(t=1.0))
    second = store.record_action(make_action(t=2.0, source="cloud", category="scrap"))
    assert second > first
    got = store.actions()
    assert got == [
        dataclasses.replace(make_action(t=1.0), action_id=first),
        dataclasses.replace(make_action(t=2.0, source="cloud", category="scrap"), action_id=second),
    ]
    assert got[0].accepted is True
    assert got[0].overridden is False


def test_mark_action_only_changes_given_flags(store):
    aid = store.record_action(make_action())
    store.mark_action(aid, overridden=True)
    store.mark_action(aid, rolled_back=True)
    store.mark_action(aid)
    (got,) = store.actions()
    assert got.overridden is True
    assert got.rolled_back is True


def test_mark_action_unknown_id_raises(store):
    with pytest.raises(KeyError):
        store.mark_action(404, overridden=True)
    store.mark_action(404)


def test_record_action_ignores_given_action_id(store):
    aid = store.record_action(dataclasses.replace(make_action(), action_id=99))
    assert aid != 99
    assert [a.action_id for a in store.actions()] == [aid]


@pytest.mark.parametrize("kw", [{"source": "robot"}, {"category": "reboot"}])
def test_record_action_rejects_invalid(store, kw):
    with pytest.raises(ValueError):
        store.record_action(make_action(**kw))
    assert store.actions() == []


def test_episode_roundtrip(store):
    rec = make_episode(t_decide=310.0, t_execute=320.0, t_recover=900.0, violated=True, detail={"k": [1]})
    store.record_episode(rec)
    got = store.episodes()
    assert got == [rec]
    assert got[0].violated is True


def test_episode_same_id_replaces_row(store):
    store.record_episode(make_episode())
    store.record_episode(make_episode(handler="cloud", t_decide=400.0))
    assert store.episodes() == [make_episode(handler="cloud", t_decide=400.0)]


def test_episodes_sorted_by_t_detect_then_id(store):
    store.record_episode(make_episode("E003", 5.0))
    store.record_episode(make_episode("E002", 1.0))
    store.record_episode(make_episode("E001", 5.0))
    assert [e.episode_id for e in store.episodes()] == ["E002", "E001", "E003"]


def test_record_episode_rejects_invalid_handler(store):
    with pytest.raises(ValueError):
        store.record_episode(make_episode(handler="peer"))
    assert store.episodes() == []


def test_telemetry_sorted_keys_insertion_order_and_filters(store):
    store.record_telemetry(1.0, "etch", "ETC-01", {"temp": 45.0, "pressure": 2.1})
    store.record_telemetry(2.0, "drill", "DRL-01", {"spindle": 3.0})
    assert store.telemetry() == [
        (1.0, "etch", "ETC-01", "pressure", 2.1),
        (1.0, "etch", "ETC-01", "temp", 45.0),
        (2.0, "drill", "DRL-01", "spindle", 3.0),
    ]
    assert store.telemetry(process="drill") == [(2.0, "drill", "DRL-01", "spindle", 3.0)]
    assert store.telemetry(key="temp") == [(1.0, "etch", "ETC-01", "temp", 45.0)]
    assert store.telemetry(process="drill", key="temp") == []


def test_dump_deterministic_for_same_sequence():
    with TraceStore() as a, TraceStore() as b:
        write_sequence(a)
        write_sequence(b)
        dumped = a.dump()
        assert dumped == b.dump()
    assert set(dumped) == {
        "fault_truth",
        "panel_lineage",
        "action_log",
        "episode_log",
        "telemetry",
        "approval_queue",
        "trace_chain",
    }
    assert all(
        dumped[name]
        for name in ("fault_truth", "panel_lineage", "action_log", "episode_log", "telemetry")
    )
    assert all(isinstance(row, tuple) for rows in dumped.values() for row in rows)


def test_file_database_persists_after_reopen(tmp_path):
    path = tmp_path / "trace.db"
    with TraceStore(path) as s:
        write_sequence(s)
        expected = s.dump()
    with TraceStore(str(path)) as s:
        assert s.dump() == expected
        assert [f.fault_id for f in s.faults()] == ["F001", "F002"]


def test_file_database_uses_wal(tmp_path):
    with TraceStore(tmp_path / "trace.db") as s:
        assert s._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_trace_chain_tamper_detected():
    store = TraceStore()
    store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
    store.append_chain(lot_id="L1", kind="act", ref="a2", t=1.0, payload={"command": "resume"})
    assert store.verify_chain("L1")[0] is True
    store._conn.execute(
        "UPDATE trace_chain SET payload = ? WHERE ref = 'a1'", ['{"command":"hacked"}']
    )
    store._conn.commit()
    ok, reason = store.verify_chain("L1")
    assert ok is False and reason


def test_trace_chain_genesis_prev_hash():
    store = TraceStore()
    store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
    row = store._conn.execute(
        "SELECT prev_hash FROM trace_chain WHERE ref = 'a1'"
    ).fetchone()
    assert row["prev_hash"] == "GENESIS"


def test_enqueue_and_update_approval(store):
    rid = store.enqueue_approval(
        t_submit=100.0,
        process="etch",
        equipment="ETC-01",
        command="hold_lot",
        params={"lot_id": "L0001"},
        source="edge",
        lot_id="L0001",
        topic="plant/etch/ETC-01/command",
    )
    assert len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)
    pending = store.list_approvals(status="pending")
    assert len(pending) == 1
    assert pending[0]["request_id"] == rid
    assert pending[0]["status"] == "pending"
    assert pending[0]["params"] == {"lot_id": "L0001"}
    store.update_approval(
        rid, status="approved", t_decide=1900.0, decider="human_model", reason="ok"
    )
    assert store.list_approvals(status="pending") == []
    approved = store.list_approvals(status="approved")
    assert len(approved) == 1
    assert approved[0]["t_decide"] == 1900.0
    assert approved[0]["decider"] == "human_model"


def test_record_episode_appends_decide_chain(store):
    rec = make_episode(
        "E010",
        t_detect=300.0,
        t_decide=310.0,
        handler="human",
        detail={"lot_id": "L0001", "action": "hold"},
    )
    store.record_episode(rec)
    rows = store._conn.execute(
        "SELECT kind, ref, lot_id FROM trace_chain ORDER BY seq"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "decide"
    assert rows[0]["ref"] == "E010"
    assert rows[0]["lot_id"] == "L0001"
    assert store.verify_chain("L0001")[0] is True


def test_record_episode_no_chain_without_t_decide(store):
    store.record_episode(make_episode("E011", t_detect=300.0))
    rows = store._conn.execute("SELECT * FROM trace_chain").fetchall()
    assert len(rows) == 0


def test_enqueue_approval_survives_reopen(tmp_path):
    path = tmp_path / "trace.db"
    with TraceStore(path) as s:
        rid1 = s.enqueue_approval(
            t_submit=1.0,
            process="etch",
            equipment="ETC-01",
            command="hold_lot",
            params={"lot_id": "L0001"},
            source="edge",
            lot_id="L0001",
            topic="plant/etch/ETC-01/command",
        )
    with TraceStore(path) as s:
        rid2 = s.enqueue_approval(
            t_submit=2.0,
            process="etch",
            equipment="ETC-01",
            command="hold_lot",
            params={"lot_id": "L0002"},
            source="edge",
            lot_id="L0002",
            topic="plant/etch/ETC-01/command",
        )
    assert rid1 != rid2
    with TraceStore(path) as s:
        pending = s.list_approvals(status="pending")
        assert len(pending) == 2
        assert {p["request_id"] for p in pending} == {rid1, rid2}


def test_dump_includes_approval_and_chain(store):
    store.enqueue_approval(
        t_submit=1.0,
        process="etch",
        equipment="ETC-01",
        command="hold_lot",
        params={},
        source="edge",
        lot_id="L1",
        topic="plant/etch/ETC-01/command",
    )
    store.append_chain(lot_id="L1", kind="act", ref="x1", t=2.0, payload={"command": "stop"})
    dumped = store.dump()
    assert "approval_queue" in dumped
    assert "trace_chain" in dumped
