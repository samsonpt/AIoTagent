from pathlib import Path

import numpy as np
import pytest

from common.recipe import load_recipe
from common.rng import make_rng
from sim.plating import PlatingResult, PlatingStation

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def make_station(seed: int = 7, mismatch: str = "mid") -> PlatingStation:
    return PlatingStation(RECIPE, mismatch, make_rng(seed, "plating"))


def test_initial_state():
    st = make_station()
    assert st.additive_ml_l == 4.5
    assert st.cu_g_l == 60.0
    assert st.params() == {"current_density_asd": 2.0, "bath_temp_c": 25.0, "additive_ml_l": 4.5}
    assert st.rect_factor == [1.0, 1.0, 1.0]
    assert not st.stopped


def test_target_thickness_mean():
    r = make_station().process_lot("L0001")
    assert isinstance(r, PlatingResult)
    assert r.lot_id == "L0001"
    assert r.thickness_um.shape == (12, 3, 3)
    assert abs(r.thickness_um.mean() - 25.0) <= 1.0


def test_nonuniform_pattern_scales_with_mismatch():
    u = np.array([[0.03, 0.0, 0.03], [0.0, -0.03, 0.0], [0.03, 0.0, 0.03]])
    for mismatch, m in [("low", 0.5), ("high", 2.0)]:
        zone_mean = make_station(5, mismatch).process_lot("L0001").thickness_um.mean(axis=0)
        np.testing.assert_allclose(zone_mean, 25.0 * (1 + m * u), atol=0.3)


def test_nominal_additive_is_balanced_by_pump():
    st = make_station()
    for k in range(1, 11):
        st.process_lot(f"L{k:04d}")
    assert st.additive_ml_l == pytest.approx(4.5)


def test_double_consumption_depletes_additive_and_thins_copper():
    st = make_station()
    st.consumption_multiplier = 2.0
    results = [st.process_lot(f"L{k:04d}") for k in range(1, 11)]
    assert st.additive_ml_l < 2.0
    assert results[-1].thickness_um.mean() < 22


def test_eta_ratio_formula():
    st = make_station(mismatch="high")
    st.additive_ml_l = 2.25
    eta = 1 - 0.6 * 0.5**2 * (1 + 0.5 * 2.0)
    r = st.process_lot("L0001")
    assert r.thickness_um[:, 1, 1].mean() == pytest.approx(25.0 * eta * (1 - 2.0 * 0.03), abs=0.3)


def test_additive_clamped_at_zero():
    st = make_station()
    st.additive_ml_l = 0.1
    st.consumption_multiplier = 10.0
    st.process_lot("L0001")
    assert st.additive_ml_l == 0.0


def test_rectifier_fault_thins_row_and_repair_restores():
    st = make_station()
    st.rect_factor[0] = 0.8
    rows = st.process_lot("L0001").thickness_um.mean(axis=(0, 2))
    assert rows[0] / rows[1:].mean() == pytest.approx(0.8, abs=0.03)
    assert st.telemetry()["rect_current_r1"] == pytest.approx(2.0 * 30.0 * 0.8)
    st.apply("repair_rectifier", {})
    assert st.rect_factor == [1.0, 1.0, 1.0]
    rows = st.process_lot("L0002").thickness_um.mean(axis=(0, 2))
    assert rows[0] / rows[1:].mean() == pytest.approx(1.0, abs=0.03)


def test_telemetry_and_assay():
    st = make_station()
    tel = st.telemetry()
    assert tel == {
        "bath_temp_c": 25.0,
        "current_density_asd": 2.0,
        "rect_current_r1": 60.0,
        "rect_current_r2": 60.0,
        "rect_current_r3": 60.0,
    }
    assert st.assay() == {"additive_ml_l": 4.5, "cu_g_l": 60.0}


def test_commands():
    st = make_station()
    st.apply("dose_additive", {"ml_l": 1.5})
    assert st.additive_ml_l == pytest.approx(6.0)
    st.apply("set_current_density", {"asd": 2.2})
    st.apply("set_bath_temp", {"c": 26.0})
    assert st.current_density_asd == 2.2
    assert st.bath_temp_c == 26.0
    st.apply("stop", {})
    assert st.stopped
    st.apply("resume", {})
    assert not st.stopped


def test_higher_current_density_thickens():
    st = make_station()
    st.apply("set_current_density", {"asd": 2.4})
    assert st.process_lot("L0001").thickness_um.mean() == pytest.approx(25.25 * 1.2, abs=0.3)


@pytest.mark.parametrize(
    "command,params",
    [
        ("dose_additive", {"ml_l": 0.0}),
        ("dose_additive", {"ml_l": 3.1}),
        ("dose_additive", {"ml_l": -1.0}),
        ("set_current_density", {"asd": 0.4}),
        ("set_current_density", {"asd": 4.1}),
        ("set_bath_temp", {"c": 14.9}),
        ("set_bath_temp", {"c": 35.1}),
        ("dose_additive", {}),
        ("fly", {}),
    ],
)
def test_rejects_physical_limits_and_unknown(command, params):
    st = make_station()
    with pytest.raises(ValueError):
        st.apply(command, params)
    assert st.params() == {"current_density_asd": 2.0, "bath_temp_c": 25.0, "additive_ml_l": 4.5}


def test_same_seed_is_deterministic():
    a = make_station(3).process_lot("L0001").thickness_um
    b = make_station(3).process_lot("L0001").thickness_um
    c = make_station(4).process_lot("L0001").thickness_um
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)
