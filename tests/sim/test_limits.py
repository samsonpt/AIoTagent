from pathlib import Path

import pytest

from common.recipe import load_recipe
from common.rng import make_rng
from sim.drill import DrillStation
from sim.etch import EtchStation
from sim.limits import require, require_int
from sim.mismatch import mismatch_scale
from sim.plating import PlatingStation

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def test_require_accepts_numbers_in_range():
    assert require({"x": 2}, "x", 1.0, 3.0) == 2.0
    assert require({"x": 1.5}, "x", 1.0, 3.0) == 1.5


@pytest.mark.parametrize(
    "params",
    [None, [("x", 2.0)], {}, {"x": None}, {"x": [2.0]}, {"x": True}, {"x": "1"}, {"x": 5.0}],
)
def test_require_rejects_bad_input(params):
    with pytest.raises(ValueError):
        require(params, "x", 1.0, 3.0)


def test_require_int_accepts_int():
    assert require_int({"zone": 2}, "zone", 0, 2) == 2


@pytest.mark.parametrize(
    "params",
    [None, {}, {"zone": None}, {"zone": [1]}, {"zone": True}, {"zone": "1"}, {"zone": 1.0}, {"zone": 3}],
)
def test_require_int_rejects_bad_input(params):
    with pytest.raises(ValueError):
        require_int(params, "zone", 0, 2)


@pytest.mark.parametrize("station", [DrillStation, PlatingStation, EtchStation])
def test_unknown_mismatch_level_raises_value_error(station):
    with pytest.raises(ValueError):
        station(RECIPE, "extreme", make_rng(0, "x"))


def test_mismatch_scale_lookup():
    assert mismatch_scale("high") == 2.0
    with pytest.raises(ValueError):
        mismatch_scale("MID")


@pytest.mark.parametrize(
    "station,command,params",
    [
        (DrillStation, "set_rpm", None),
        (DrillStation, "set_rpm", {"spindle_rpm": "120000"}),
        (PlatingStation, "dose_additive", {"ml_l": True}),
        (PlatingStation, "set_bath_temp", {"c": None}),
        (EtchStation, "set_etch_temp", {"c": [50.0]}),
        (EtchStation, "clean_nozzle", {"zone": 2.0}),
        (EtchStation, "clean_nozzle", None),
    ],
)
def test_station_commands_reject_malformed_params(station, command, params):
    with pytest.raises(ValueError):
        station(RECIPE, "mid", make_rng(0, "x")).apply(command, params)
