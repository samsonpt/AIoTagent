from __future__ import annotations

from twin.calibration import twin_confidence
from twin.types import Prediction

FAST_PATH: frozenset[str] = frozenset({"stop", "resume", "change_bit"})
HIGH_RISK_COMMANDS: frozenset[str] = frozenset({"hold_lot", "scrap_lot"})
PARAM_TUNE_REL_THRESHOLD: float = 0.25

# (process, command) → (window_name, command_param_key)
_PARAM_TUNE_WINDOWS: dict[tuple[str, str], tuple[str, str]] = {
    ("drill", "set_rpm"): ("spindle_rpm", "spindle_rpm"),
    ("drill", "set_feed"): ("feed_rate_m_min", "feed_rate_m_min"),
    ("plating", "set_current_density"): ("current_density_asd", "asd"),
    ("plating", "set_bath_temp"): ("bath_temp_c", "c"),
    ("etch", "set_etch_temp"): ("etch_temp_c", "c"),
    ("etch", "set_conveyor_speed"): ("conveyor_speed_m_min", "m_min"),
    ("etch", "set_spray_pressure"): ("spray_pressure_bar", "bar"),
}

_SKIP_TWIN: frozenset[str] = frozenset(
    {
        "clean_nozzle",
        "repair_rectifier",
        "repair_regenerator",
        "set_bath_temp",
        "set_rpm",
        "set_feed",
        "change_bit",
        "stop",
        "resume",
        "hold_lot",
        "scrap_lot",
    }
)


def twin_thresholds(confidence: float) -> tuple[float, float]:
    y_min = 0.90 + 0.05 * (1.0 - confidence)
    oos_max = 0.15 * confidence + 0.05
    return round(y_min, 10), round(oos_max, 10)


def passes_twin_gate(pred: Prediction, confidence: float) -> bool:
    y_min, oos_max = twin_thresholds(confidence)
    return pred.yield_prob >= y_min and pred.oos_prob <= oos_max


def confidence_from_twin(twin) -> float:
    if twin is None:
        return 0.5
    preds = getattr(twin, "predictions", None)
    if not preds:
        return 0.5
    coverages = [o.q05 <= o.y <= o.q95 for o in preds]
    return twin_confidence(coverages)


def _thickness_defaults(recipe) -> dict:
    return {
        "asd": recipe.window("plating", "current_density_asd").target,
        "time_min": float(recipe.constants["plating_time_min"]),
        "additive_ml_l": recipe.window("plating", "additive_ml_l").target,
    }


def _width_defaults(recipe) -> dict:
    return {
        "sg": recipe.window("etch", "sg").target,
        "temp_c": recipe.window("etch", "etch_temp_c").target,
        "spray_bar": recipe.window("etch", "spray_pressure_bar").target,
        "speed_m_min": recipe.window("etch", "conveyor_speed_m_min").target,
        "thickness_um": float(recipe.specs["copper_thickness_um"].target),
    }


def map_command_to_twin(
    recipe, process: str, command: str, params: dict
) -> tuple[str, dict] | None:
    if command in _SKIP_TWIN:
        return None

    if command == "set_current_density":
        out = _thickness_defaults(recipe)
        out["asd"] = float(params["asd"])
        return "thickness", out

    if command == "dose_additive":
        out = _thickness_defaults(recipe)
        if "additive_ml_l" in params:
            out["additive_ml_l"] = float(params["additive_ml_l"])
        elif "ml_l" in params:
            out["additive_ml_l"] = float(out["additive_ml_l"]) + float(params["ml_l"])
        return "thickness", out

    if command == "set_etch_temp":
        out = _width_defaults(recipe)
        out["temp_c"] = float(params.get("temp_c", params.get("c")))
        return "width", out

    if command == "set_conveyor_speed":
        out = _width_defaults(recipe)
        out["speed_m_min"] = float(params.get("speed_m_min", params.get("m_min")))
        return "width", out

    if command == "set_spray_pressure":
        out = _width_defaults(recipe)
        out["spray_bar"] = float(params.get("spray_bar", params.get("bar")))
        return "width", out

    if command == "adjust_sg":
        out = _width_defaults(recipe)
        if "sg" in params:
            out["sg"] = float(params["sg"])
        elif "delta" in params:
            out["sg"] = float(out["sg"]) + float(params["delta"])
        return "width", out

    return None


def is_high_risk(recipe, process: str, command: str, params: dict) -> bool:
    if command in HIGH_RISK_COMMANDS:
        return True
    mapping = _PARAM_TUNE_WINDOWS.get((process, command))
    if mapping is None:
        return False
    window_name, param_key = mapping
    if param_key not in params:
        return False
    window = recipe.window(process, window_name)
    span = max(window.max - window.min, 1e-9)
    rel = abs(float(params[param_key]) - window.target) / span
    return rel > PARAM_TUNE_REL_THRESHOLD
