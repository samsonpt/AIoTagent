import math

import pytest

from bench.metrics import (
    PIPELINE_LATENCY_S,
    bit_life_utilization,
    decision_latency,
    dosing_consumption,
    drill_break_count,
    envelope_violations,
    false_action_count,
    fpr,
    mttd,
    mttc,
    outage_fpy_retention,
    unplanned_downtime_s,
)
from bench.schema import ActionRecord, EpisodeRecord, FaultRecord, PanelRecord, TraceStore


def _panel(pid, lot, t_aoi, defects, root):
    return PanelRecord(
        panel_id=pid,
        lot_id=lot,
        part_no="PN",
        t_release=t_aoi - PIPELINE_LATENCY_S,
        t_aoi=t_aoi,
        drill={},
        plating={},
        etch={},
        defects=defects,
        root_cause_truth=root,
        scrapped=False,
    )


@pytest.fixture
def store():
    with TraceStore() as s:
        yield s


def test_mttc_recovers_after_five_good_lots(store: TraceStore):
    # 预热 2 批无缺陷 → b=0 → θ = 1/12
    for i in range(24):
        store.record_panel(_panel(f"L0-P{i}", "L0", 1000.0, [], "none"))
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=2000.0, t_end=8000.0, t_cleared=5000.0,
    ))
    # 受影响批：缺陷 root=etch
    for i in range(12):
        store.record_panel(_panel(
            f"L1-P{i}", "L1", 3000.0,
            [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}], "etch",
        ))
    # 恢复搜索起点后连续 5 个好批
    for li, t in enumerate([9000, 9100, 9200, 9300, 9400], start=2):
        for i in range(12):
            store.record_panel(_panel(f"L{li}-P{i}", f"L{li}", float(t), [], "none"))
    out = mttc(store)
    assert out["n_no_impact"] == 0
    assert out["n_censored"] == 0
    assert out["mean"] == pytest.approx(9400.0 - 2000.0)


def test_mttd_mean(store: TraceStore):
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=1000.0,
    ))
    store.record_fault(FaultRecord(
        "f2", "drill", "d", "drill_break", {}, t_start=1500.0,
    ))
    store.record_fault(FaultRecord(
        "fs", "plating", "p", "sensor_drift", {}, t_start=1000.0,
    ))
    store.record_episode(EpisodeRecord("e0", "etch", "spike", 500.0, "edge"))
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 2800.0, "edge"))
    store.record_episode(EpisodeRecord("e2", "etch", "spike", 4000.0, "cloud"))
    out = mttd(store)
    assert out["n"] == 1
    assert out["n_undetected"] == 1
    assert out["mean"] == pytest.approx(1800.0)


def test_mttc_no_impact_zero(store: TraceStore):
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=2000.0, t_end=8000.0, t_cleared=5000.0,
    ))
    for i in range(12):
        store.record_panel(_panel(f"L1-P{i}", "L1", 3000.0, [], "none"))
    out = mttc(store)
    assert out["mean"] == pytest.approx(0.0)
    assert out["n"] == 1
    assert out["n_no_impact"] == 1
    assert out["n_censored"] == 0


def test_mttc_censored(store: TraceStore):
    for i in range(12):
        store.record_panel(_panel(f"L0-P{i}", "L0", 1000.0, [], "none"))
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=2000.0, t_end=20000.0, t_cleared=5000.0,
    ))
    for i in range(12):
        store.record_panel(_panel(
            f"L1-P{i}", "L1", 3000.0,
            [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}], "etch",
        ))
    for li, t in enumerate([9000, 9100, 9200, 9300], start=2):
        for i in range(12):
            store.record_panel(_panel(f"L{li}-P{i}", f"L{li}", float(t), [], "none"))
    out = mttc(store)
    assert out["n"] == 0
    assert out["n_no_impact"] == 0
    assert out["n_censored"] == 1
    assert math.isnan(out["mean"])


def test_fpr_t_correct_prefers_mttc_over_t_cleared(store: TraceStore):
    # t_cleared+latency = 8400；受影响批在 9000，旧窗口不计入，MTTC 窗口计入。
    for i in range(12):
        store.record_panel(_panel(f"L0-P{i}", "L0", 1000.0, [], "none"))
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=2000.0, t_end=20000.0, t_cleared=3000.0,
    ))
    for i in range(12):
        store.record_panel(_panel(
            f"L1-P{i}", "L1", 9000.0,
            [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}], "etch",
        ))
    for li, t in enumerate([9100, 9200, 9300, 9400, 9500], start=2):
        for i in range(12):
            store.record_panel(_panel(f"L{li}-P{i}", f"L{li}", float(t), [], "none"))
    assert fpr(store).fpr == pytest.approx(1.0)
    assert fpr(store).n_faults == 1


def _action(t, process, category, *, accepted=True, params=None, reason="", command=None):
    return ActionRecord(
        t=t,
        process=process,
        equipment="e1",
        command=command or category,
        params={} if params is None else params,
        source="edge",
        category=category,
        affected_panels=1,
        accepted=accepted,
        reason=reason,
    )


def _defect():
    return [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}]


def test_bit_life_utilization_mean_of_change_ratios(store: TraceStore):
    store.record_action(_action(
        100.0, "drill", "bit_change",
        params={"bit_hits": 4500, "bit_rated_life_hits": 5000},
    ))
    store.record_action(_action(
        200.0, "drill", "bit_change",
        params={"bit_hits": 2500, "rated_life": 5000},
    ))
    store.record_action(_action(
        300.0, "drill", "bit_change", accepted=False,
        params={"bit_hits": 5000, "bit_rated_life_hits": 5000},
    ))
    assert bit_life_utilization(store) == pytest.approx((0.9 + 0.5) / 2)


def test_bit_life_utilization_uses_telemetry_before_change(store: TraceStore):
    assert math.isnan(bit_life_utilization(store))
    store.record_telemetry(50.0, "drill", "e1", {"bit_hits": 8000.0, "bit_rated_life_hits": 10000.0})
    store.record_telemetry(150.0, "drill", "e1", {"bit_hits": 100.0, "bit_rated_life_hits": 10000.0})
    store.record_action(_action(100.0, "drill", "bit_change", params={}))
    assert bit_life_utilization(store) == pytest.approx(0.8)


def test_bit_life_utilization_nan_when_rated_missing(store: TraceStore):
    store.record_action(_action(100.0, "drill", "bit_change", params={"bit_hits": 100}))
    assert math.isnan(bit_life_utilization(store))


def test_drill_break_count(store: TraceStore):
    assert drill_break_count(store) == 0.0
    store.record_fault(FaultRecord("a", "drill", "d", "drill_break", {}, t_start=1.0))
    store.record_fault(FaultRecord("b", "drill", "d", "drill_break", {}, t_start=2.0))
    store.record_fault(FaultRecord("c", "drill", "d", "drill_abnormal_wear", {}, t_start=3.0))
    assert drill_break_count(store) == 2.0


def test_unplanned_downtime_uses_duration_or_one_tick(store: TraceStore):
    assert unplanned_downtime_s(store) == 0.0
    store.record_action(_action(10.0, "line", "line_stop", params={"duration_s": 100}))
    store.record_action(_action(20.0, "line", "line_stop", params={}))
    store.record_action(_action(30.0, "line", "line_stop", accepted=False, params={"duration_s": 9999}))
    store.record_action(_action(40.0, "etch", "dosing", params={"duration_s": 50, "amount": 1}))
    assert unplanned_downtime_s(store) == pytest.approx(1900.0)


def test_dosing_consumption_sums_accepted_amount(store: TraceStore):
    assert dosing_consumption(store) == 0.0
    store.record_action(_action(1.0, "plating", "dosing", params={"amount": 1.5}))
    store.record_action(_action(2.0, "plating", "dosing", params={"amount": 2.5}))
    store.record_action(_action(3.0, "plating", "dosing", accepted=False, params={"amount": 9}))
    store.record_action(_action(4.0, "plating", "param_tune", params={"amount": 8}))
    assert dosing_consumption(store) == pytest.approx(4.0)


def test_envelope_violations_counts_true_flags(store: TraceStore):
    assert envelope_violations(store) == 0.0
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 1.0, "edge", violated=True))
    store.record_episode(EpisodeRecord("e2", "etch", "spike", 2.0, "edge", violated=False))
    store.record_episode(EpisodeRecord("e3", "drill", "spike", 3.0, "cloud", violated=True))
    assert envelope_violations(store) == 2.0


def test_false_action_count_param_tune_without_fault(store: TraceStore):
    store.record_action(_action(1000.0, "etch", "param_tune", reason="纠正"))
    assert false_action_count(store) == 1.0


def test_false_action_count_excludes_fault_preventive_and_rejected(store: TraceStore):
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=500.0, t_end=1500.0,
    ))
    store.record_fault(FaultRecord(
        "fs", "plating", "p", "sensor_drift", {}, t_start=0.0, t_end=5000.0,
    ))
    store.record_fault(FaultRecord(
        "fd", "drill", "d", "drill_break", {}, t_start=0.0, t_end=10000.0,
    ))
    store.record_action(_action(1000.0, "etch", "param_tune", reason="纠正"))
    store.record_action(_action(1500.0, "etch", "param_tune", reason="纠正"))
    store.record_action(_action(1000.0, "plating", "param_tune", reason="纠正"))
    store.record_action(_action(2000.0, "drill", "bit_change", reason="预防性换针"))
    store.record_action(_action(2000.0, "plating", "param_tune", accepted=False, reason="纠正"))
    store.record_action(_action(3000.0, "drill", "param_tune", reason="纠正"))
    assert false_action_count(store) == 2.0


def test_decision_latency_p50_p95(store: TraceStore):
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 100.0, "edge", t_decide=110.0))
    store.record_episode(EpisodeRecord("e2", "etch", "spike", 200.0, "edge", t_decide=230.0))
    store.record_episode(EpisodeRecord("e3", "etch", "spike", 300.0, "cloud"))
    out = decision_latency(store)
    assert out["p50"] == pytest.approx(20.0)
    assert out["p95"] == pytest.approx(29.0)


def test_decision_latency_empty_is_nan(store: TraceStore):
    out = decision_latency(store)
    assert math.isnan(out["p50"])
    assert math.isnan(out["p95"])


def test_outage_fpy_retention_window(store: TraceStore):
    store.record_fault(FaultRecord(
        "n1", "etch", "e", "network_outage", {}, t_start=1000.0, t_end=2000.0,
    ))
    store.record_panel(_panel("A1", "A", 500.0, [], "none"))
    store.record_panel(_panel("A2", "A", 600.0, _defect(), "etch"))
    store.record_panel(_panel("B1", "B", 1000.0, [], "none"))
    store.record_panel(_panel("B2", "B", 3000.0, [], "none"))
    store.record_panel(_panel("B3", "B", 4000.0, _defect(), "etch"))
    store.record_panel(_panel("B4", "B", 7400.0, _defect(), "etch"))
    store.record_panel(_panel("C1", "C", 7401.0, [], "none"))
    store.record_panel(_panel("C2", "C", 8000.0, [], "none"))
    assert outage_fpy_retention(store) == pytest.approx(0.5 / 0.75)


def test_outage_fpy_retention_nan_without_outage_or_rest(store: TraceStore):
    store.record_panel(_panel("A1", "A", 500.0, [], "none"))
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=1000.0, t_end=2000.0,
    ))
    assert math.isnan(outage_fpy_retention(store))
