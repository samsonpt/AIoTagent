from common.recipe import Recipe

PASSTHROUGH = {
    "change_bit",
    "repair_rectifier",
    "repair_regenerator",
    "clean_nozzle",
    "stop",
    "resume",
    "hold_lot",
    "scrap_lot",
}

_WINDOW_KEYS = {
    ("drill", "set_rpm"): ("spindle_rpm", "spindle_rpm"),
    ("drill", "set_feed"): ("feed_rate_m_min", "feed_rate_m_min"),
    ("plating", "set_current_density"): ("current_density_asd", "asd"),
    ("plating", "set_bath_temp"): ("bath_temp_c", "c"),
    ("etch", "set_etch_temp"): ("etch_temp_c", "c"),
    ("etch", "set_conveyor_speed"): ("conveyor_speed_m_min", "m_min"),
    ("etch", "set_spray_pressure"): ("spray_pressure_bar", "bar"),
}


def bound_command(
    recipe: Recipe, process: str, command: str, params: dict, current: dict | None = None
) -> tuple[str, dict] | None:
    if command in PASSTHROUGH:
        return command, dict(params)
    if command == "dose_additive":
        ml = min(max(float(params["ml_l"]), 0.0), 3.0)
        if ml <= 0:
            return None
        return command, {**params, "ml_l": ml}
    if command == "adjust_sg":
        sg_now = float((current or {}).get("sg", recipe.window(process, "sg").target))
        desired = sg_now + float(params["delta"])
        clamped = recipe.window(process, "sg").clamp(desired)
        delta = clamped - sg_now
        if abs(delta) < 1e-9:
            return None
        return command, {**params, "delta": delta}
    mapping = _WINDOW_KEYS.get((process, command))
    if mapping is None:
        return None
    window_name, param_key = mapping
    window = recipe.window(process, window_name)
    value = window.clamp(float(params[param_key]))
    return command, {**params, param_key: value}
