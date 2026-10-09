from pathlib import Path

from common.recipe import load_recipe
from guard.policy import (
    FAST_PATH,
    HIGH_RISK_COMMANDS,
    PARAM_TUNE_REL_THRESHOLD,
    confidence_from_twin,
    is_high_risk,
    map_command_to_twin,
    passes_twin_gate,
    twin_thresholds,
)
from twin.types import Prediction, TwinObservation

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_thresholds_tighten_when_confidence_low():
    y_hi, oos_hi = twin_thresholds(1.0)
    y_lo, oos_lo = twin_thresholds(0.0)
    assert y_hi == 0.90 and oos_hi == 0.20
    assert y_lo == 0.95 and oos_lo == 0.05
    pred = Prediction(mean=1.0, q05=0.9, q95=1.1, yield_prob=0.92, oos_prob=0.08)
    assert passes_twin_gate(pred, 1.0) is True
    assert passes_twin_gate(pred, 0.0) is False


def test_fast_path_and_high_risk_constants():
    assert FAST_PATH == frozenset({"stop", "resume", "change_bit"})
    assert HIGH_RISK_COMMANDS == frozenset({"hold_lot", "scrap_lot"})
    assert PARAM_TUNE_REL_THRESHOLD == 0.25


def test_map_dose_additive_ml_l_uses_target_plus_dose():
    target = RECIPE.window("plating", "additive_ml_l").target
    kind, params = map_command_to_twin(
        RECIPE, "plating", "dose_additive", {"ml_l": 0.5}
    )
    assert kind == "thickness"
    assert params["additive_ml_l"] == target + 0.5


def test_map_set_current_density_to_thickness():
    mapped = map_command_to_twin(
        RECIPE, "plating", "set_current_density", {"asd": 2.2}
    )
    assert mapped is not None
    kind, params = mapped
    assert kind == "thickness"
    assert params["asd"] == 2.2
    assert params["time_min"] == RECIPE.constants["plating_time_min"]
    assert params["additive_ml_l"] == RECIPE.window("plating", "additive_ml_l").target


def test_map_clean_nozzle_returns_none():
    assert map_command_to_twin(RECIPE, "etch", "clean_nozzle", {"zone": 1}) is None


def test_map_set_bath_temp_skips_gate():
    assert map_command_to_twin(RECIPE, "plating", "set_bath_temp", {"c": 26.0}) is None


def test_map_set_rpm_skips_when_only_hits_needed():
    assert map_command_to_twin(RECIPE, "drill", "set_rpm", {"spindle_rpm": 130000}) is None


def test_map_width_commands():
    kind, params = map_command_to_twin(
        RECIPE, "etch", "set_conveyor_speed", {"m_min": 2.1}
    )
    assert kind == "width"
    assert params["speed_m_min"] == 2.1
    assert "sg" in params and "temp_c" in params and "spray_bar" in params


def test_is_high_risk_hold_lot():
    assert is_high_risk(RECIPE, "line", "hold_lot", {"lot_id": "L1"}) is True
    assert is_high_risk(RECIPE, "line", "scrap_lot", {"lot_id": "L1"}) is True


def test_is_high_risk_param_tune_relative_threshold():
    # window [1.5, 2.5], span=1.0; |2.4-2.0|/1.0 = 0.4 > 0.25
    assert is_high_risk(RECIPE, "plating", "set_current_density", {"asd": 2.4}) is True
    # |2.1-2.0|/1.0 = 0.1 <= 0.25
    assert is_high_risk(RECIPE, "plating", "set_current_density", {"asd": 2.1}) is False


def test_is_high_risk_maintenance_not_by_threshold():
    assert is_high_risk(RECIPE, "etch", "clean_nozzle", {"zone": 1}) is False
    assert is_high_risk(RECIPE, "drill", "change_bit", {}) is False


def test_confidence_from_twin_none_is_half():
    assert confidence_from_twin(None) == 0.5


def test_confidence_from_twin_uses_coverages():
    class FakeTwin:
        predictions = [
            TwinObservation(
                t=0.0, tick=0, kind="thickness", y=25.0, yhat=25.0, q05=24.0, q95=26.0
            )
            for _ in range(10)
        ]

    # 10 coverages all True → twin_confidence returns 1.0 (>=8)
    assert confidence_from_twin(FakeTwin()) == 1.0


def test_confidence_from_twin_empty_predictions():
    class EmptyTwin:
        predictions = []

    assert confidence_from_twin(EmptyTwin()) == 0.5
