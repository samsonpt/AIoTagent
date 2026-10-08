from common.recipe import Recipe
from edge.envelope import bound_command


def commands_for(process: str, key: str, value: float, recipe: Recipe) -> list[tuple[str, dict, str]]:
    if process == "drill" and key in {"vibration_g", "spindle_current_a"}:
        return [("change_bit", {}, f"SPC {key}")]
    if process == "plating" and key.startswith("rect_current"):
        return [("repair_rectifier", {}, f"SPC {key}")]
    if process == "plating" and key == "additive_ml_l":
        window = recipe.window("plating", "additive_ml_l")
        dose = min(3.0, window.target - value)
        if dose <= 0:
            return []
        return [("dose_additive", {"ml_l": dose}, "化验低于窗口")]
    if process == "etch" and key == "sg":
        window = recipe.window("etch", "sg")
        cmds: list[tuple[str, dict, str]] = [("repair_regenerator", {}, "SPC sg")]
        bound = bound_command(recipe, "etch", "adjust_sg", {"delta": window.target - value}, current={"sg": value})
        if bound is not None:
            cmds.append((bound[0], bound[1], "比重拉回窗口"))
        return cmds
    if process == "etch" and key.startswith("width_col_"):
        zone = int(key.rsplit("_", 1)[1])
        return [("clean_nozzle", {"zone": zone}, f"线宽列 {zone} 偏低")]
    return []
