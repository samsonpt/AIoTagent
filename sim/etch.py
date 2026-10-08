from dataclasses import dataclass

import numpy as np

from common.recipe import Recipe
from sim.limits import require, require_int
from sim.mismatch import mismatch_scale


@dataclass(frozen=True)
class EtchResult:
    lot_id: str
    width_um: np.ndarray
    overetch_um: np.ndarray
    params: dict[str, float]


class EtchStation:
    def __init__(self, recipe: Recipe, mismatch: str, rng: np.random.Generator):
        self._rng = rng
        self._m = mismatch_scale(mismatch)
        self._artwork_width_um = recipe.constants["artwork_width_um"]
        self._chamber_length_m = recipe.constants["etch_chamber_length_m"]
        self._etch_factor = recipe.constants["etch_factor"]
        self.sg = 1.28
        self.etch_temp_c = 50.0
        self.spray_pressure_bar = 2.0
        self.conveyor_speed_m_min = 2.0
        self.sg_drift_per_tick = 0.0
        self.clog_factor = [1.0, 1.0, 1.0]
        self.stopped = False

    def tick_update(self) -> None:
        self.sg += self.sg_drift_per_tick

    def etch_rates(self) -> np.ndarray:
        dsg = (self.sg - 1.28) / 0.01
        dT = self.etch_temp_c - 50
        chem = 0.5 * (1 + 0.08 * dsg + 0.03 * dT + 0.02 * self._m * dsg * dT)
        return chem * np.sqrt(self.spray_pressure_bar * np.array(self.clog_factor) / 2.0)

    def dwell_s(self) -> float:
        return 60 * self._chamber_length_m / self.conveyor_speed_m_min

    def params(self) -> dict[str, float]:
        return {
            "sg": self.sg,
            "etch_temp_c": self.etch_temp_c,
            "spray_pressure_bar": self.spray_pressure_bar,
            "conveyor_speed_m_min": self.conveyor_speed_m_min,
        }

    def process_lot(self, lot_id: str, thickness_um: np.ndarray) -> EtchResult:
        capacity = self.etch_rates() * self.dwell_s()
        overetch = capacity - thickness_um
        width = (
            self._artwork_width_um
            - 2 * (thickness_um / self._etch_factor + np.maximum(overetch, 0) * 0.5)
            + self._rng.normal(0, 0.5, size=thickness_um.shape)
        )
        return EtchResult(lot_id=lot_id, width_um=width, overetch_um=overetch, params=self.params())

    def telemetry(self) -> dict[str, float]:
        return {
            "sg": self.sg,
            "etch_temp_c": self.etch_temp_c,
            "spray_pressure_z1": self.spray_pressure_bar,
            "spray_pressure_z2": self.spray_pressure_bar,
            "spray_pressure_z3": self.spray_pressure_bar,
            "conveyor_speed_m_min": self.conveyor_speed_m_min,
        }

    def apply(self, command: str, params: dict) -> None:
        match command:
            case "set_conveyor_speed":
                self.conveyor_speed_m_min = require(params, "m_min", 1.0, 3.0)
            case "adjust_sg":
                self.sg += require(params, "delta", -0.05, 0.05)
            case "set_etch_temp":
                self.etch_temp_c = require(params, "c", 40.0, 60.0)
            case "set_spray_pressure":
                self.spray_pressure_bar = require(params, "bar", 1.0, 3.0)
            case "clean_nozzle":
                self.clog_factor[require_int(params, "zone", 0, 2)] = 1.0
            case "repair_regenerator":
                self.sg_drift_per_tick = 0.0
            case "stop":
                self.stopped = True
            case "resume":
                self.stopped = False
            case _:
                raise ValueError(f"unknown etch command {command!r}")
