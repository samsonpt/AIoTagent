import math

import pytest

from bench.metrics import PHYSICAL_FAULT_TYPES, PIPELINE_LATENCY_S, FprResult, fpr, fpy, scrap_rate
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
