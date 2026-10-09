import math

import numpy as np
import pytest

from bench.metrics import (
    PHYSICAL_FAULT_TYPES,
    PIPELINE_LATENCY_S,
    FprResult,
    action_accept_rate,
    coverage90,
    fpr,
    fpy,
    llm_usage,
    mape,
    root_cause_top1,
    scrap_rate,
)
from bench.schema import FaultRecord, PanelRecord, TraceStore
from sim.faults import FAULT_DEFECT_LINKS


def make_panel(panel_id, t_aoi=5400.0, defects=(), root="none", scrapped=False):
    return PanelRecord(
        panel_id=panel_id,
        lot_id=panel_id.split("-")[0],
        part_no="PN-4L-001",
        t_release=t_aoi - PIPELINE_LATENCY_S,
        t_aoi=t_aoi,
        drill={},
        plating={},
        etch={},
        defects=[{"type": t, "zone": [0, 0], "stage": s} for t, s in defects],
        root_cause_truth=root,
        scrapped=scrapped,
    )


def make_fault(fault_id, fault_type, process, t_start, t_end=None, t_cleared=None):
    return FaultRecord(
        fault_id=fault_id,
        process=process,
        equipment="X",
        fault_type=fault_type,
        params={},
        t_start=t_start,
        t_end=t_end,
        t_cleared=t_cleared,
    )


@pytest.fixture
def store():
    with TraceStore() as s:
        yield s


def test_pipeline_latency():
    assert PIPELINE_LATENCY_S == 5400.0


def test_physical_fault_types_match_sim():
    assert PHYSICAL_FAULT_TYPES == frozenset(k for k, v in FAULT_DEFECT_LINKS.items() if v)


def test_empty_store_returns_nan(store):
    assert math.isnan(fpy(store))
    assert math.isnan(scrap_rate(store))
    result = fpr(store)
    assert math.isnan(result.fpr)
    assert math.isnan(result.cross_process_ratio)
    assert result.n_faults == 0


def test_fpy_and_scrap_rate(store):
    store.record_panel(make_panel("L0001-P01"))
    store.record_panel(make_panel("L0001-P02", defects=[("residue", "etch")]))
    store.record_panel(make_panel("L0001-P03", defects=[("open", "etch")], scrapped=True))
    store.record_panel(make_panel("L0001-P04"))
    assert fpy(store) == pytest.approx(0.5)
    assert scrap_rate(store) == pytest.approx(0.25)


def test_fpr_counts_physical_faults_and_propagation(store):
    store.record_fault(make_fault("F1", "nozzle_clog", "etch", 1800.0))
    store.record_fault(make_fault("F2", "drill_abnormal_wear", "drill", 1800.0, t_end=3600.0))
    store.record_fault(make_fault("F3", "sensor_bias", "plating", 1800.0))
    store.record_panel(make_panel("L0001-P01", t_aoi=7200.0, defects=[("open", "etch")], root="etch"))
    result = fpr(store)
    assert isinstance(result, FprResult)
    assert result.n_faults == 2
    assert result.fpr == pytest.approx(0.5)


def test_fpr_ignores_defects_after_cleared_plus_latency(store):
    store.record_fault(make_fault("F1", "nozzle_clog", "etch", 0.0, t_end=36000.0, t_cleared=1800.0))
    store.record_panel(
        make_panel("L0005-P01", t_aoi=1800.0 + PIPELINE_LATENCY_S + 1.0, defects=[("open", "etch")], root="etch")
    )
    assert fpr(store).fpr == 0.0
    store.record_panel(
        make_panel("L0004-P01", t_aoi=1800.0 + PIPELINE_LATENCY_S, defects=[("open", "etch")], root="etch")
    )
    assert fpr(store).fpr == 1.0


def test_fpr_falls_back_to_t_end(store):
    store.record_fault(make_fault("F1", "nozzle_clog", "etch", 0.0, t_end=3600.0))
    store.record_panel(
        make_panel("L0005-P01", t_aoi=3600.0 + PIPELINE_LATENCY_S + 1.0, defects=[("open", "etch")], root="etch")
    )
    assert fpr(store).fpr == 0.0
    store.record_panel(
        make_panel("L0004-P01", t_aoi=3600.0 + PIPELINE_LATENCY_S, defects=[("open", "etch")], root="etch")
    )
    assert fpr(store).fpr == 1.0


def test_fpr_ignores_defects_before_fault_start(store):
    store.record_fault(make_fault("F1", "nozzle_clog", "etch", 3600.0))
    store.record_panel(make_panel("L0001-P01", t_aoi=1800.0, defects=[("open", "etch")], root="etch"))
    assert fpr(store).fpr == 0.0


def test_fpr_only_sensor_faults_is_nan(store):
    store.record_fault(make_fault("F1", "sensor_drift", "etch", 0.0))
    result = fpr(store)
    assert result.n_faults == 0
    assert math.isnan(result.fpr)


def test_cross_process_ratio(store):
    store.record_panel(
        make_panel("L0001-P01", defects=[("thin_copper", "plating"), ("open", "etch")], root="plating")
    )
    store.record_panel(make_panel("L0001-P02", defects=[("open", "etch")], root="etch"))
    store.record_panel(make_panel("L0001-P03", defects=[("residue", "etch")]))
    assert fpr(store).cross_process_ratio == pytest.approx(1 / 3)


def test_cross_process_ratio_uses_per_defect_cause(store):
    panel = make_panel("L0001-P01", defects=[("width_under", "etch"), ("residue", "etch")], root="plating")
    panel.defects[0]["cause"] = "plating"
    panel.defects[1]["cause"] = "none"
    store.record_panel(panel)
    store.record_panel(make_panel("L0002-P01", defects=[("open", "etch")], root="etch"))
    assert fpr(store).cross_process_ratio == pytest.approx(1 / 2)


def test_mape_perfect_prediction_is_zero():
    y = np.array([10.0, 20.0, 30.0])
    assert mape(y, y) == 0.0


def test_mape_skips_near_zero_targets():
    y = np.array([10.0, 1e-12, 20.0])
    yhat = np.array([11.0, 100.0, 22.0])
    assert mape(y, yhat) == pytest.approx(10.0)


def test_coverage90_all_inside_interval_is_one():
    y = np.array([1.0, 2.0, 3.0])
    assert coverage90(y, y - 1.0, y + 1.0) == 1.0


def test_coverage90_partial_hits():
    y = np.array([0.0, 5.0, 10.0])
    q05 = np.array([-1.0, 6.0, 9.0])
    q95 = np.array([1.0, 7.0, 11.0])
    assert coverage90(y, q05, q95) == pytest.approx(2 / 3)


def test_root_cause_top1_perfect_and_partial():
    assert root_cause_top1(["etch", "plating"], ["etch", "plating"]) == 1.0
    assert root_cause_top1(["etch", "drill"], ["etch", "plating"]) == pytest.approx(0.5)
    assert math.isnan(root_cause_top1([], []))
    assert math.isnan(root_cause_top1(["etch"], ["etch", "plating"]))


def test_action_accept_rate():
    assert action_accept_rate(
        ["clean_nozzle", "dose_additive"],
        [{"clean_nozzle", "set_conveyor_speed"}, ["dose_additive"]],
    ) == 1.0
    assert action_accept_rate(
        ["stop", "dose_additive"],
        [["clean_nozzle"], ["dose_additive"]],
    ) == pytest.approx(0.5)
    assert math.isnan(action_accept_rate([], []))


def test_llm_usage_sums_detail_fields():
    usage = llm_usage(
        [
            {"llm_calls": 2, "llm_tokens": 100},
            {"llm_calls": 1, "tokens": 40},
            {},
        ]
    )
    assert usage == {"llm_calls": 3.0, "llm_tokens": 140.0}
