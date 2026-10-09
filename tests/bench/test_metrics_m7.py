import math

import pytest

from bench.metrics import (
    COMPUTE_ALL_KEYS,
    PIPELINE_LATENCY_S,
    arg,
    bit_life_utilization,
    caf,
    compute_all,
    decision_latency,
    dosing_consumption,
    drill_break_count,
    envelope_violations,
    es,
    false_action_count,
    fpr,
    guard_error_rates,
    mttd,
    mttc,
    outage_fpy_retention,
    twin_recalibration_time,
    unplanned_downtime_s,
)
from bench.schema import SOURCES, ActionRecord, EpisodeRecord, FaultRecord, PanelRecord, TraceStore


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


def test_twin_recalibration_time_nan_without_series(store: TraceStore):
    store.record_fault(FaultRecord(
        "d1", "etch", "e", "sensor_drift", {}, t_start=1000.0,
    ))
    assert math.isnan(twin_recalibration_time(store, None))
    assert math.isnan(twin_recalibration_time(store, []))


def test_twin_recalibration_time_back_within_1_2_baseline(store: TraceStore):
    # 漂移前 MAPE 均值 2.0，阈值 2.4；1100 仍高，1500 回到阈值内。
    store.record_fault(FaultRecord(
        "d1", "etch", "e", "sensor_drift", {}, t_start=1000.0,
    ))
    # 物理故障不是漂移：若把它也当校准起点，t_start=200 会在 500 处“恢复”，均值变成 400。
    store.record_fault(FaultRecord(
        "p1", "etch", "e", "nozzle_clog", {}, t_start=200.0,
    ))
    series = [(100.0, 2.0), (500.0, 2.0), (1100.0, 8.0), (1500.0, 2.2)]
    assert twin_recalibration_time(store, series) == pytest.approx(500.0)


def test_twin_recalibration_time_nan_if_never_returns_or_no_drift(store: TraceStore):
    series = [(100.0, 2.0), (1500.0, 9.0)]
    assert math.isnan(twin_recalibration_time(store, series))
    store.record_fault(FaultRecord(
        "d1", "etch", "e", "sensor_drift", {}, t_start=1000.0,
    ))
    assert math.isnan(twin_recalibration_time(store, series))


def test_twin_recalibration_time_means_recovered_drifts(store: TraceStore):
    store.record_fault(FaultRecord("d1", "etch", "e", "sensor_drift", {}, t_start=100.0))
    store.record_fault(FaultRecord("d2", "plating", "p", "sensor_drift", {}, t_start=1000.0))
    series = [
        (0.0, 10.0),
        (50.0, 10.0),
        (160.0, 11.0),
        (500.0, 10.0),
        (1100.0, 20.0),
        (1300.0, 11.0),
    ]
    # d1: 基线 10，阈值 12，160-100=60；d2: 基线 10.25，阈值 12.3，1300-1000=300
    assert twin_recalibration_time(store, series) == pytest.approx((60.0 + 300.0) / 2)


def test_guard_false_accept_rate_high_risk_without_fault(store: TraceStore):
    store.record_action(_action(10.0, "etch", "lot_hold"))
    store.record_action(_action(20.0, "etch", "scrap", accepted=False, reason="guard blocked"))
    store.record_action(_action(30.0, "etch", "line_stop"))
    store.record_action(_action(40.0, "etch", "param_tune"))
    out = guard_error_rates(store)
    assert out["false_accept_rate"] == pytest.approx(2.0 / 3.0)
    assert math.isnan(out["false_reject_rate"])


def test_guard_false_reject_during_active_physical_fault(store: TraceStore):
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=0.0, t_end=5000.0,
    ))
    store.record_fault(FaultRecord(
        "fs", "plating", "p", "sensor_drift", {}, t_start=0.0, t_end=5000.0,
    ))
    store.record_action(_action(1000.0, "etch", "param_tune", accepted=False, reason="Guard rejected"))
    store.record_action(_action(1100.0, "etch", "dosing"))
    store.record_action(_action(1200.0, "etch", "bit_change", accepted=False, reason="human hold"))
    store.record_action(_action(1300.0, "plating", "lot_hold"))
    store.record_action(_action(5000.0, "etch", "line_stop", reason="停线"))
    out = guard_error_rates(store)
    assert out["false_reject_rate"] == pytest.approx(1.0 / 3.0)
    assert out["false_accept_rate"] == pytest.approx(1.0)


def test_guard_error_rates_empty_store_is_nan(store: TraceStore):
    out = guard_error_rates(store)
    assert math.isnan(out["false_accept_rate"])
    assert math.isnan(out["false_reject_rate"])


def _lot(store: TraceStore, lot: str, t_aoi: float, n_defect: int = 0) -> None:
    for i in range(12):
        defects = [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}] if i < n_defect else []
        root = "etch" if i < n_defect else "none"
        store.record_panel(_panel(f"{lot}-P{i}", lot, t_aoi, defects, root))


def _arg_warmup(store: TraceStore) -> None:
    """故障前一批全好 → b=0，θ=1/12。2/12 缺陷批超过阈值。"""
    _lot(store, "W0", 1000.0)
    store.record_fault(FaultRecord(
        "fw", "etch", "e", "nozzle_clog", {}, t_start=5000.0,
    ))


def _act(t, equipment, source, *, accepted=True, category="param_tune", affected=1, rolled_back=False):
    return ActionRecord(
        t=t,
        process="etch",
        equipment=equipment,
        command="x",
        params={},
        source=source,
        category=category,
        affected_panels=affected,
        accepted=accepted,
        rolled_back=rolled_back,
    )


def test_es_weights(store: TraceStore):
    for i in range(10):
        store.record_panel(_panel(f"P{i}", "L0", 1000.0, [], "none"))
    store.record_action(ActionRecord(
        t=1, process="etch", equipment="etch", command="x", params={},
        source="edge", category="param_tune", affected_panels=10,
        accepted=True, rolled_back=False,
    ))
    # ES = 0.1 * 10 / 10 * 1000 = 100
    assert es(store) == pytest.approx(100.0)


def test_es_excludes_rejected_and_rolled_back(store: TraceStore):
    for i in range(10):
        store.record_panel(_panel(f"P{i}", "L0", 1000.0, [], "none"))
    store.record_action(_act(1, "etch", "edge", category="scrap", affected=10, accepted=False))
    store.record_action(_act(2, "etch", "edge", category="scrap", affected=10, rolled_back=True))
    store.record_action(_act(3, "etch", "edge", category="dosing", affected=5))
    # 只剩 0.2 * 5 / 10 * 1000 = 100
    assert es(store) == pytest.approx(100.0)


def test_es_nan_without_panels_and_zero_without_actions(store: TraceStore):
    assert math.isnan(es(store))
    store.record_panel(_panel("P0", "L0", 1000.0, [], "none"))
    assert es(store) == pytest.approx(0.0)


def test_caf_mean_normalized_entropy(store: TraceStore):
    # k1 窗 0：两条 edge；被拒绝的 human 不进熵
    store.record_action(_act(100.0, "k1", "edge"))
    store.record_action(_act(200.0, "k1", "edge"))
    store.record_action(_act(150.0, "k1", "human", accepted=False))
    # t=1800 → floor(t/1800)=1，与 t=1900 同窗，edge/cloud 各半
    store.record_action(_act(1800.0, "k1", "edge"))
    store.record_action(_act(1900.0, "k1", "cloud"))
    for i, source in enumerate(SOURCES):
        store.record_action(_act(100.0 + i, "k2", source))
    h_half = math.log(2) / math.log(len(SOURCES))
    assert caf(store)["caf"] == pytest.approx((0.0 + h_half + 1.0) / 3)


def test_caf_window_s_splits_bins(store: TraceStore):
    store.record_action(_act(100.0, "k1", "edge"))
    store.record_action(_act(1500.0, "k1", "cloud"))
    assert caf(store, window_s=1000)["caf"] == pytest.approx(0.0)
    assert caf(store)["caf"] == pytest.approx(math.log(2) / math.log(len(SOURCES)))


def test_conflict_rate_other_source_within_300s(store: TraceStore):
    # Δ=300 覆盖；Δ=301 不覆盖；同来源、另一执行器不覆盖。未接受的指令仍进入冲突分母。
    store.record_action(_act(0.0, "m1", "edge"))
    store.record_action(_act(10.0, "m1", "human", accepted=False))
    store.record_action(_act(300.0, "m1", "cloud"))
    store.record_action(_act(601.0, "m1", "peer"))
    store.record_action(_act(0.0, "m2", "edge"))
    store.record_action(_act(100.0, "m2", "edge"))
    # 被覆盖：t=0 的 edge、t=10 的 human。6 条里 2 条。
    assert caf(store)["conflict_rate"] == pytest.approx(2.0 / 6.0)


def test_caf_empty_is_nan(store: TraceStore):
    out = caf(store)
    assert math.isnan(out["caf"])
    assert math.isnan(out["conflict_rate"])


def test_arg_five_consecutive_lots(store: TraceStore):
    _arg_warmup(store)
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 6000.0, "edge", t_recover=None))
    _lot(store, "B0", 6100.0, n_defect=2)
    for k, t in enumerate([6200, 6300, 6400, 6500, 6600]):
        _lot(store, f"G{k}", float(t))
    assert arg(store) == pytest.approx(0.0)


def test_arg_counts_only_edge_without_violation(store: TraceStore):
    _arg_warmup(store)
    for k, t in enumerate([6100, 6200, 6300, 6400, 6500]):
        _lot(store, f"G{k}", float(t))
    store.record_episode(EpisodeRecord("e-edge", "etch", "spike", 6000.0, "edge"))
    store.record_episode(EpisodeRecord("e-human", "drill", "spike", 6000.0, "human"))
    store.record_episode(EpisodeRecord("e-bad", "plating", "spike", 6000.0, "edge", violated=True))
    assert arg(store) == pytest.approx(1.0 - 1.0 / 3.0)


def test_arg_recovery_must_finish_before_next_same_process(store: TraceStore):
    _arg_warmup(store)
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 6000.0, "edge"))
    store.record_episode(EpisodeRecord("e2", "etch", "spike", 6400.0, "edge"))
    _lot(store, "B0", 6100.0, n_defect=2)
    for k, t in enumerate([6500, 6600, 6700, 6800, 6900]):
        _lot(store, f"G{k}", float(t))
    assert arg(store) == pytest.approx(0.5)


def test_arg_other_process_does_not_close_window(store: TraceStore):
    _arg_warmup(store)
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 6000.0, "edge"))
    store.record_episode(EpisodeRecord("e2", "drill", "spike", 6300.0, "edge"))
    _lot(store, "B0", 6100.0, n_defect=2)
    for k, t in enumerate([6400, 6500, 6600, 6700, 6800]):
        _lot(store, f"G{k}", float(t))
    assert arg(store) == pytest.approx(0.0)


def test_arg_censored_ignores_self_reported_t_recover(store: TraceStore):
    _arg_warmup(store)
    store.record_episode(EpisodeRecord("e1", "etch", "spike", 6000.0, "edge", t_recover=100.0))
    for k, t in enumerate([6100, 6200, 6300, 6400]):
        _lot(store, f"G{k}", float(t))
    assert arg(store) == pytest.approx(1.0)


def test_arg_nan_without_episodes(store: TraceStore):
    _arg_warmup(store)
    for k, t in enumerate([6100, 6200, 6300, 6400, 6500]):
        _lot(store, f"G{k}", float(t))
    assert math.isnan(arg(store))


def test_compute_all_keys_on_empty_store(store: TraceStore):
    out = compute_all(store)
    assert set(out.keys()) == set(COMPUTE_ALL_KEYS)
    for key, value in out.items():
        assert isinstance(value, float), f"{key} is {type(value)}"
