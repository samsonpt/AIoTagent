from dataclasses import dataclass

import numpy as np

from common.recipe import Recipe
from sim.limits import require
from sim.mismatch import MISMATCH_SCALE

NONUNIFORMITY = np.array([[0.03, 0.0, 0.03], [0.0, -0.03, 0.0], [0.03, 0.0, 0.03]])
PUMP_REFILL_ML_L = 0.3


@dataclass(frozen=True)
class PlatingResult:
    lot_id: str
    thickness_um: np.ndarray
    params: dict[str, float]


class PlatingStation:
    def __init__(self, recipe: Recipe, mismatch: str, rng: np.random.Generator):
        self._rng = rng
        self._m = MISMATCH_SCALE[mismatch]
        self._lot_size = recipe.lot_size
        self._plating_time_min = recipe.constants["plating_time_min"]
        self._panel_area_dm2 = recipe.constants["panel_area_dm2"]
        self.additive_ml_l = 4.5
        self.cu_g_l = 60.0
        self.bath_temp_c = recipe.window("plating", "bath_temp_c").target
        self.current_density_asd = recipe.window("plating", "current_density_asd").target
        self.rect_factor = [1.0, 1.0, 1.0]
        self.consumption_multiplier = 1.0
        self.stopped = False

    def eta_ratio(self) -> float:
        deficit = max(0.0, (4.5 - self.additive_ml_l) / 4.5)
        return 1 - 0.6 * deficit**2 * (1 + 0.5 * self._m)

    def params(self) -> dict[str, float]:
        return {
            "current_density_asd": self.current_density_asd,
            "bath_temp_c": self.bath_temp_c,
            "additive_ml_l": self.additive_ml_l,
        }

    def process_lot(self, lot_id: str) -> PlatingResult:
        asd = self.current_density_asd
        rows = np.array(self.rect_factor)[:, None]
        zone = (
            25.0 * (asd / 2.0) * (self._plating_time_min / 60) * self.eta_ratio()
            * (1 + self._m * NONUNIFORMITY) * rows
        )
        thickness = zone + self._rng.normal(0, 0.3, size=(self._lot_size, 3, 3))
        result = PlatingResult(lot_id=lot_id, thickness_um=thickness, params=self.params())
        self.additive_ml_l -= 0.3 * (asd / 2.0) * self.consumption_multiplier
        self.additive_ml_l = max(0.0, self.additive_ml_l + PUMP_REFILL_ML_L)
        return result

    def telemetry(self) -> dict[str, float]:
        base = self.current_density_asd * self._panel_area_dm2
        return {
            "bath_temp_c": self.bath_temp_c,
            "current_density_asd": self.current_density_asd,
            "rect_current_r1": base * self.rect_factor[0],
            "rect_current_r2": base * self.rect_factor[1],
            "rect_current_r3": base * self.rect_factor[2],
        }

    def assay(self) -> dict[str, float]:
        return {"additive_ml_l": self.additive_ml_l, "cu_g_l": self.cu_g_l}

    def apply(self, command: str, params: dict) -> None:
        match command:
            case "dose_additive":
                ml_l = require(params, "ml_l", 0.0, 3.0)
                if ml_l == 0.0:
                    raise ValueError("ml_l must be > 0")
                self.additive_ml_l += ml_l
            case "set_current_density":
                self.current_density_asd = require(params, "asd", 0.5, 4.0)
            case "set_bath_temp":
                self.bath_temp_c = require(params, "c", 15.0, 35.0)
            case "repair_rectifier":
                self.rect_factor = [1.0, 1.0, 1.0]
            case "stop":
                self.stopped = True
            case "resume":
                self.stopped = False
            case _:
                raise ValueError(f"unknown plating command {command!r}")
