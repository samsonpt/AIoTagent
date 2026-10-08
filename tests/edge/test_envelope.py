from pathlib import Path

from common.recipe import load_recipe
from edge.envelope import bound_command

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_envelope_clamps_conveyor():
    assert bound_command(RECIPE, "etch", "set_conveyor_speed", {"m_min": 3.0}) == (
        "set_conveyor_speed",
        {"m_min": 2.4},
    )


def test_envelope_clamps_current_density_key():
    cmd, params = bound_command(RECIPE, "plating", "set_current_density", {"asd": 0.1})
    assert cmd == "set_current_density" and params["asd"] == 1.5


def test_envelope_adjust_sg_towards_window():
    # current sg assumed 1.28; delta +0.05 → 1.33, clamp to 1.30 → delta 0.02
    cmd, params = bound_command(RECIPE, "etch", "adjust_sg", {"delta": 0.05}, current={"sg": 1.28})
    assert cmd == "adjust_sg"
    assert abs(params["delta"] - 0.02) < 1e-9


def test_envelope_rejects_zero_adjust():
    assert bound_command(RECIPE, "etch", "adjust_sg", {"delta": 0.0}, current={"sg": 1.28}) is None


def test_envelope_dose_and_passthrough():
    assert bound_command(RECIPE, "plating", "dose_additive", {"ml_l": 9.0})[1]["ml_l"] == 3.0
    assert bound_command(RECIPE, "drill", "change_bit", {}) == ("change_bit", {})
    assert bound_command(RECIPE, "etch", "clean_nozzle", {"zone": 2}) == ("clean_nozzle", {"zone": 2})
