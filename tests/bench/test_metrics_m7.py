import math

import pytest

from bench.metrics import PIPELINE_LATENCY_S, fpr, mttd, mttc
from bench.schema import EpisodeRecord, FaultRecord, PanelRecord, TraceStore


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
