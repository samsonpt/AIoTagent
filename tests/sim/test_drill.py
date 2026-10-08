from pathlib import Path

import pytest

from common.recipe import load_recipe
from common.rng import make_rng
from sim.drill import DrillResult, DrillStation
from sim.mismatch import MISMATCH_SCALE

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def make_station(seed: int = 7, mismatch: str = "mid") -> DrillStation:
    return DrillStation(RECIPE, mismatch, make_rng(seed, "drill"))


def test_mismatch_scale():
    assert MISMATCH_SCALE == {"low": 0.5, "mid": 1.0, "high": 2.0}


def test_initial_state_uses_recipe_targets():
    st = make_station()
    assert st.params() == {"spindle_rpm": 120000.0, "feed_rate_m_min": 2.0}
    assert st.bit_hits == 0
    assert st.effective_hits == 0.0
    assert not st.broken
    assert not st.stopped


@pytest.mark.parametrize("seed", range(5))
def test_nominal_wear_keeps_roughness_in_spec_for_nine_lots(seed):
    st = make_station(seed)
    results = [st.process_lot(f"L{k:04d}") for k in range(1, 10)]
    assert all(r.roughness_um <= 25 for r in results)


@pytest.mark.parametrize("seed", range(5))
def test_accelerated_wear_exceeds_roughness_before_rated_change(seed):
    st = make_station(seed)
    st.wear_multiplier = 1.6
    results = [st.process_lot(f"L{k:04d}") for k in range(1, 11)]
    assert any(r.roughness_um > 25 for r in results)


def test_machine_changes_bit_at_rated_life():
    st = make_station()
    for k in range(1, 10):
        st.process_lot(f"L{k:04d}")
    assert st.bit_hits == 5400
    r = st.process_lot("L0010")
    assert r.params["bit_hits"] == 6000
    assert st.bit_hits == 0
    assert st.effective_hits == 0.0
    assert st.wear() == 0.0
    assert not st.broken


def test_result_carries_params_and_hits():
    st = make_station()
    r = st.process_lot("L0001")
    assert isinstance(r, DrillResult)
    assert r.lot_id == "L0001"
    assert r.params == {"spindle_rpm": 120000.0, "feed_rate_m_min": 2.0, "bit_hits": 600}


def test_wear_formula_and_hidden_life():
    st = make_station()
    st.wear_multiplier = 1.5
    st.process_lot("L0001")
    assert st.bit_hits == 600
    assert st.effective_hits == pytest.approx(900.0)
    life = 900.0 / st.wear()
    assert 6000 * 0.9 <= life <= 6000 * 1.1


def test_hidden_life_spread_scales_with_mismatch():
    def life(mismatch):
        st = make_station(3, mismatch)
        st.process_lot("L0001")
        return st.effective_hits / st.wear()

    assert life("high") - 6000 == pytest.approx(4 * (life("low") - 6000))


def test_telemetry_formulas():
    st = make_station()
    st.process_lot("L0001")
    w = st.wear()
    tel = st.telemetry()
    assert tel["spindle_current_a"] == pytest.approx(2.0 + 0.8 * w)
    assert tel["vibration_g"] == pytest.approx(0.5 + 0.6 * w**2)
    assert tel["spindle_rpm"] == 120000.0
    assert tel["feed_rate_m_min"] == 2.0
    assert tel["bit_hits"] == 600
    st.apply("set_rpm", {"spindle_rpm": 150000})
    assert st.telemetry()["spindle_current_a"] == pytest.approx(2.0 + 0.8 * w + 0.05 * 0.25)


def test_broken_bit_telemetry_and_sticky_state():
    st = make_station()
    st.wear_multiplier = 3.0
    results = [st.process_lot(f"L{k:04d}") for k in range(1, 6)]
    assert results[-1].broken
    first = next(k for k, r in enumerate(results) if r.broken)
    assert all(r.broken for r in results[first:])
    tel = st.telemetry()
    assert tel["spindle_current_a"] == 1.2
    assert tel["vibration_g"] == 0.2


def test_change_bit_resets():
    st = make_station()
    st.wear_multiplier = 3.0
    for k in range(1, 6):
        st.process_lot(f"L{k:04d}")
    st.apply("change_bit", {})
    assert st.bit_hits == 0
    assert st.effective_hits == 0.0
    assert st.wear() == 0.0
    assert not st.broken


def test_set_rpm_and_feed():
    st = make_station()
    st.apply("set_rpm", {"spindle_rpm": 140000})
    st.apply("set_feed", {"feed_rate_m_min": 2.5})
    assert st.params() == {"spindle_rpm": 140000.0, "feed_rate_m_min": 2.5}


@pytest.mark.parametrize(
    "command,params",
    [
        ("set_rpm", {"spindle_rpm": 49999}),
        ("set_rpm", {"spindle_rpm": 200001}),
        ("set_feed", {"feed_rate_m_min": 0.4}),
        ("set_feed", {"feed_rate_m_min": 4.1}),
        ("set_rpm", {}),
        ("fly", {}),
    ],
)
def test_rejects_physical_limits_and_unknown(command, params):
    st = make_station()
    with pytest.raises(ValueError):
        st.apply(command, params)
    assert st.params() == {"spindle_rpm": 120000.0, "feed_rate_m_min": 2.0}


def test_stop_resume():
    st = make_station()
    st.apply("stop", {})
    assert st.stopped
    st.apply("resume", {})
    assert not st.stopped


def test_same_seed_is_deterministic():
    def run(seed):
        st = make_station(seed)
        st.wear_multiplier = 1.6
        return [(r.roughness_um, r.broken) for r in (st.process_lot(f"L{k:04d}") for k in range(1, 15))]

    assert run(11) == run(11)
    assert run(11) != run(12)
