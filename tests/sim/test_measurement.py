import numpy as np
import pytest

from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from sim.aoi import Defect, PanelInspection
from sim.faults import FaultSpec, Scenario
from sim.measurement import Measurement


def make(sensor_faults: dict | None = None, **scenario_kwargs):
    scenario = Scenario(name="t", seed=3, n_ticks=10, **scenario_kwargs)
    bus, store = InMemoryBus(), TraceStore()
    got: list[tuple[str, dict]] = []
    bus.subscribe("#", lambda topic, payload: got.append((topic, payload)), "spy")
    return Measurement(scenario, bus, store, sensor_faults if sensor_faults is not None else {}), store, got


def test_telemetry_noise_and_store():
    m, store, got = make()
    true = {"sg": 1.28, "etch_temp_c": 50.0}
    for k in range(200):
        m.publish_telemetry(float(k), k, "etch", true)
    sg = np.array([p["values"]["sg"] for _, p in got])
    assert {t for t, _ in got} == {topics.telemetry("etch")}
    assert (got[5][1]["t"], got[5][1]["tick"]) == (5.0, 5)
    assert abs(sg.mean() - 1.28) < 0.01 * 1.28 * 0.3
    assert 0.5 * 0.0128 < sg.std() < 1.5 * 0.0128
    rows = store.telemetry("etch", "sg")
    assert len(rows) == 200
    assert rows[0] == (0.0, "etch", "ETC-01", "sg", got[0][1]["values"]["sg"])


def test_bit_hits_not_noised():
    m, _, got = make()
    m.publish_telemetry(0.0, 0, "drill", {"bit_hits": 600.0, "vibration_g": 0.5})
    assert got[0][1]["values"]["bit_hits"] == 600.0
    assert got[0][1]["values"]["vibration_g"] != 0.5


@pytest.mark.parametrize(
    ("ftype", "params", "expected"),
    [
        ("sensor_bias", {"key": "sg", "offset": 0.5}, lambda v: v + 0.5),
        ("sensor_drift", {"key": "sg", "per_tick": 0.1}, lambda v: v + 0.1 * (7 - 4)),
        ("sensor_spoof", {"key": "sg", "value": 9.0}, lambda v: 9.0),
    ],
)
def test_sensor_fault_transforms(ftype, params, expected):
    spec = FaultSpec(fault_id="S1", type=ftype, process="etch", start_tick=4, params=params)
    clean, _, got_clean = make()
    faulty, _, got_faulty = make({("etch", "sg"): spec})
    values = {"sg": 1.28, "etch_temp_c": 50.0}
    clean.publish_telemetry(0.0, 7, "etch", values)
    faulty.publish_telemetry(0.0, 7, "etch", values)
    a, b = got_clean[0][1]["values"], got_faulty[0][1]["values"]
    assert b["sg"] == pytest.approx(expected(a["sg"]))
    assert b["etch_temp_c"] == a["etch_temp_c"]


def test_assay_delivered_after_delay():
    m, _, got = make(assay_delay_ticks=2)
    m.schedule_assay(1800.0, 1, {"additive_ml_l": 4.5, "cu_g_l": 60.0})
    m.deliver_assays(3600.0, 2)
    assert got == []
    m.deliver_assays(5400.0, 3)
    assert len(got) == 1
    topic, payload = got[0]
    assert topic == topics.lab_assay()
    assert (payload["t_sample"], payload["t_report"], payload["tick"]) == (1800.0, 5400.0, 3)
    assert payload["process"] == "plating"
    assert payload["values"]["additive_ml_l"] == pytest.approx(4.5, abs=0.5)
    assert payload["values"]["additive_ml_l"] != 4.5
    m.deliver_assays(7200.0, 4)
    assert len(got) == 1


def test_thickness_and_width_measurements():
    m, _, got = make()
    m.publish_thickness(10.0, 4, "L0001", "L0001-P01", np.full((3, 3), 25.0))
    m.publish_width(12.0, 5, "L0001", "L0001-P01", np.full((3, 3), 100.0))
    (t_th, th), (t_wd, wd) = got
    assert (t_th, t_wd) == (topics.measurement("plating"), topics.measurement("etch"))
    assert (th["kind"], th["lot_id"], th["panel_id"], th["t"], th["tick"]) == ("thickness_um", "L0001", "L0001-P01", 10.0, 4)
    assert (wd["kind"], wd["t"], wd["tick"]) == ("line_width_um", 12.0, 5)
    assert np.array(th["zones"]).shape == (3, 3) and np.array(wd["zones"]).shape == (3, 3)
    assert np.abs(np.array(th["zones"]) - 25.0).max() < 2.0
    assert not np.array_equal(np.array(wd["zones"]), np.full((3, 3), 100.0))


def test_aoi_payload_hides_truth():
    m, _, got = make()
    insp = [
        PanelInspection("L0001-P01", [Defect("open", (1, 2), "etch", "etch")], "etch", True),
        PanelInspection("L0001-P02", [], "none", False),
    ]
    m.publish_aoi(9.0, 3, "L0001", insp)
    topic, payload = got[0]
    assert topic == topics.aoi_result()
    assert payload == {
        "t": 9.0,
        "tick": 3,
        "lot_id": "L0001",
        "panels": [
            {"panel_id": "L0001-P01", "defects": [{"type": "open", "zone": [1, 2], "stage": "etch"}]},
            {"panel_id": "L0001-P02", "defects": []},
        ],
    }
