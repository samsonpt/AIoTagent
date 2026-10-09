from common.recipe import Recipe
from twin.calibration import twin_confidence
from twin.types import Prediction

FAST_PATH: frozenset[str] = frozenset({"stop", "resume", "change_bit"})
HIGH_RISK_COMMANDS: frozenset[str] = frozenset({"hold_lot", "scrap_lot"})
PARAM_TUNE_REL_THRESHOLD: float = 0.25

_MAINTENANCE_PREFIXES = ("clean_nozzle", "repair_")

_PARAM_TUNE_KEYS: dict[tuple[str, str], tuple[str, str]] = {
    ("drill", "set_rpm"): ("spindle_rpm", "spindle_rpm"),
    ("drill", "set_feed"): ("feed_rate_m_min", "feed_rate_m_min"),
    ("plating", "set_current_density"): ("current_density_asd", "asd"),
    ("plating", "set_bath_temp"): ("bath_temp_c", "c"),
    ("etch", "set_etch_temp"): ("etch_temp_c", "c"),
    ("etch", "set_conveyor_speed"): ("conveyor_speed_m_min", "m_min"),
    ("etch", "set_spray_pressure"): ("spray_pressure_bar", "bar"),
}


def twin_thresholds(confidence: float) -> tuple[float, float]:
    y_min = round(0.95 - 0.05 * confidence, 12)
    oos_max = round(0.05 + 0.15 * confidence, 12)
    return y_min, oos_max


def passes_twin_gate(pred: Prediction, confidence: float) -> bool:
    y_min, oos_max = twin_thresholds(confidence)
    return pred.yield_prob >= y_min and pred.oos_prob <= oos_max


def confidence_from_twin(twin) -> float:
    if twin is None:
        return 0.5
    predictions = getattr(twin, "predictions", None)
    if not predictions:
        return 0.5
    coverages = [o.q05 <= o.y <= o.q95 for o in predictions]
    return twin_confidence(coverages)


def _default_thickness_params(recipe: Recipe) -> dict:
    return {
        "asd": recipe.window("plating", "current_density_asd").target,
        "time_min": float(recipe.constants["plating_time_min"]),
        "additive_ml_l": recipe.window("plating", "additive_ml_l").target,
    }


def _default_width_params(recipe: Recipe) -> dict:
    return {
        "sg": recipe.window("etch", "sg").target,
        "temp_c": recipe.window("etch", "etch_temp_c").target,
        "spray_bar": recipe.window("etch", "spray_pressure_bar").target,
        "speed_m_min": recipe.window("etch", "conveyor_speed_m_min").target,
        "thickness_um": 25.0,
    }


def _is_maintenance(command: str) -> bool:
    return command == "clean_nozzle" or command.startswith("repair_")


def _relative_deviation(recipe: Recipe, process: str, window_name: str, value: float) -> float:
    window = recipe.window(process, window_name)
    span = max(window.max - window.min, 1e-9)
    return abs(value - window.target) / span


def is_high_risk(recipe: Recipe, process: str, command: str, params: dict) -> bool:
    if command in HIGH_RISK_COMMANDS:
        return True
    mapping = _PARAM_TUNE_KEYS.get((process, command))
    if mapping is None:
        return False
    window_name, param_key = mapping
    if param_key not in params:
        return False
    return (
        _relative_deviation(recipe, process, window_name, float(params[param_key]))
        > PARAM_TUNE_REL_THRESHOLD
    )


def map_command_to_twin(
    recipe: Recipe, process: str, command: str, params: dict
) -> tuple[str, dict] | None:
    if _is_maintenance(command):
        return None
    if command == "set_current_density" and process == "plating":
        twin_params = _default_thickness_params(recipe)
        twin_params["asd"] = float(params["asd"])
        return "thickness", twin_params
    if command == "set_bath_temp" and process == "plating":
        return None
    if command == "dose_additive" and process == "plating":
        twin_params = _default_thickness_params(recipe)
        twin_params["additive_ml_l"] = float(params.get("ml_l", params.get("additive_ml_l", 0.0)))
        return "thickness", twin_params
    if command in {"set_rpm", "set_feed"} and process == "drill":
        return None
    if command == "set_etch_temp" and process == "etch":
        twin_params = _default_width_params(recipe)
        twin_params["temp_c"] = float(params["c"])
        return "width", twin_params
    if command == "set_conveyor_speed" and process == "etch":
        twin_params = _default_width_params(recipe)
        twin_params["speed_m_min"] = float(params["m_min"])
        return "width", twin_params
    if command == "set_spray_pressure" and process == "etch":
        twin_params = _default_width_params(recipe)
        twin_params["spray_bar"] = float(params["bar"])
        return "width", twin_params
    if command == "adjust_sg" and process == "etch":
        twin_params = _default_width_params(recipe)
        if "sg" in params:
            twin_params["sg"] = float(params["sg"])
        else:
            twin_params["sg"] = recipe.window("etch", "sg").target + float(params.get("delta", 0.0))
        return "width", twin_params
    return None
