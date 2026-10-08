import math
from pathlib import Path

import numpy as np
import pytest

from common.recipe import load_recipe
from common.rng import make_rng
from sim.etch import EtchResult, EtchStation

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")
FLAT = np.full((12, 3, 3), 25.0)


def make_station(seed: int = 7, mismatch: str = "mid") -> EtchStation:
    return EtchStation(RECIPE, mismatch, make_rng(seed, "etch"))


def test_initial_state():
    st = make_station()
    assert st.params() == {
        "sg": 1.28,
        "etch_temp_c": 50.0,
        "spray_pressure_bar": 2.0,
        "conveyor_speed_m_min": 2.0,
    }
    assert st.sg_drift_per_tick == 0.0
    assert st.clog_factor == [1.0, 1.0, 1.0]
    assert not st.stopped


def test_nominal_line_width():
    r = make_station().process_lot("L0001", FLAT)
    assert isinstance(r, EtchResult)
    assert r.lot_id == "L0001"
    assert r.width_um.shape == FLAT.shape
    assert r.overetch_um.shape == FLAT.shape
    np.testing.assert_allclose(r.overetch_um, 5.0)
    assert abs(r.width_um.mean() - 100.0) <= 1.5
    assert r.params == st_params_nominal()


def st_params_nominal():
    return {"sg": 1.28, "etch_temp_c": 50.0, "spray_pressure_bar": 2.0, "conveyor_speed_m_min": 2.0}


def test_high_sg_narrows_lines():
    st = make_station()
    st.sg = 1.33
    assert st.process_lot("L0001", FLAT).width_um.mean() < 90


def test_interaction_term_scales_with_mismatch():
    for mismatch, m in [("low", 0.5), ("high", 2.0)]:
        st = make_station(mismatch=mismatch)
        st.sg = 1.29
        st.etch_temp_c = 52.0
        er = 0.5 * (1 + 0.08 + 0.06 + 0.02 * m * 2.0)
        np.testing.assert_allclose(st.process_lot("L0001", FLAT).overetch_um, er * 60 - 25.0)


def test_clogged_zone_underetches_and_clean_restores():
    st = make_station()
    st.clog_factor[2] = 0.5
    r = st.process_lot("L0001", FLAT)
    assert (r.overetch_um[:, :, 2] < -1).all()
    np.testing.assert_allclose(r.overetch_um[:, :, 2], 0.5 * math.sqrt(0.5) * 60 - 25.0)
    np.testing.assert_allclose(r.overetch_um[:, :, :2], 5.0)
    assert st.telemetry()["spray_pressure_z3"] == 2.0
    st.apply("clean_nozzle", {"zone": 2})
    assert st.clog_factor == [1.0, 1.0, 1.0]
    np.testing.assert_allclose(st.process_lot("L0002", FLAT).overetch_um, 5.0)


def test_slower_conveyor_narrows_lines():
    st = make_station()
    base = st.process_lot("L0001", FLAT).width_um.mean()
    st.apply("set_conveyor_speed", {"m_min": 1.6})
    r = st.process_lot("L0002", FLAT)
    np.testing.assert_allclose(r.overetch_um, 0.5 * 75 - 25.0)
    assert r.width_um.mean() < base


def test_width_formula_with_underetch():
    st = make_station()
    thick = np.full((12, 3, 3), 32.0)
    r = st.process_lot("L0001", thick)
    np.testing.assert_allclose(r.overetch_um, -2.0)
    assert r.width_um.mean() == pytest.approx(121.7 - 2 * 32.0 / 3.0, abs=0.5)


def test_tick_update_applies_drift_and_regenerator_repair():
    st = make_station()
    st.sg_drift_per_tick = 0.002
    for _ in range(5):
        st.tick_update()
    assert st.sg == pytest.approx(1.29)
    st.apply("repair_regenerator", {})
    assert st.sg_drift_per_tick == 0.0
    st.tick_update()
    assert st.sg == pytest.approx(1.29)


def test_telemetry():
    assert make_station().telemetry() == {
        "sg": 1.28,
        "etch_temp_c": 50.0,
        "spray_pressure_z1": 2.0,
        "spray_pressure_z2": 2.0,
        "spray_pressure_z3": 2.0,
        "conveyor_speed_m_min": 2.0,
    }


def test_commands():
    st = make_station()
    st.apply("adjust_sg", {"delta": -0.02})
    st.apply("set_etch_temp", {"c": 51.0})
    st.apply("set_spray_pressure", {"bar": 2.2})
    assert st.params() == {
        "sg": pytest.approx(1.26),
        "etch_temp_c": 51.0,
        "spray_pressure_bar": 2.2,
        "conveyor_speed_m_min": 2.0,
    }
    st.apply("stop", {})
    assert st.stopped
    st.apply("resume", {})
    assert not st.stopped


@pytest.mark.parametrize(
    "command,params",
    [
        ("set_conveyor_speed", {"m_min": 0.9}),
        ("set_conveyor_speed", {"m_min": 3.1}),
        ("adjust_sg", {"delta": 0.051}),
        ("adjust_sg", {"delta": -0.051}),
        ("set_etch_temp", {"c": 39.9}),
        ("set_etch_temp", {"c": 60.1}),
        ("set_spray_pressure", {"bar": 0.9}),
        ("set_spray_pressure", {"bar": 3.1}),
        ("clean_nozzle", {"zone": 3}),
        ("clean_nozzle", {"zone": -1}),
        ("clean_nozzle", {"zone": 0.5}),
        ("set_etch_temp", {}),
        ("fly", {}),
    ],
)
def test_rejects_physical_limits_and_unknown(command, params):
    st = make_station()
    with pytest.raises(ValueError):
        st.apply(command, params)
    assert st.params() == st_params_nominal()


def test_same_seed_is_deterministic():
    a = make_station(3).process_lot("L0001", FLAT).width_um
    b = make_station(3).process_lot("L0001", FLAT).width_um
    c = make_station(4).process_lot("L0001", FLAT).width_um
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)
