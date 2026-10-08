import math
from dataclasses import dataclass

import numpy as np

from common.recipe import Recipe
from sim.limits import require
from sim.mismatch import mismatch_scale


@dataclass(frozen=True)
class DrillResult:
    lot_id: str
    roughness_um: float
    broken: bool
    params: dict[str, float]


class DrillStation:
    def __init__(self, recipe: Recipe, mismatch: str, rng: np.random.Generator):
        self._rng = rng
        self._m = mismatch_scale(mismatch)
        self._hits_per_lot = int(recipe.constants["hits_per_lot"])
        self._rated_life = int(recipe.constants["bit_rated_life_hits"])
        self._h = float(rng.uniform(-1, 1))
        self._life_true = self._rated_life * (1 + 0.1 * self._m * self._h)
        self.bit_hits = 0
        self.effective_hits = 0.0
        self.broken = False
        self.wear_multiplier = 1.0
        self.spindle_rpm = recipe.window("drill", "spindle_rpm").target
        self.feed_rate_m_min = recipe.window("drill", "feed_rate_m_min").target
        self.stopped = False

    def wear(self) -> float:
        return self.effective_hits / self._life_true

    def params(self) -> dict[str, float]:
        return {"spindle_rpm": self.spindle_rpm, "feed_rate_m_min": self.feed_rate_m_min}

    def _change_bit(self) -> None:
        self.bit_hits = 0
        self.effective_hits = 0.0
        self.broken = False

    def process_lot(self, lot_id: str) -> DrillResult:
        self.bit_hits += self._hits_per_lot
        self.effective_hits += self._hits_per_lot * self.wear_multiplier
        w = self.wear()
        p_break = 1 / (1 + math.exp(-(w - 1.15) * 15))
        if self._rng.random() < p_break:
            self.broken = True
        roughness = 12 + 10 * w**2 * (1 + 0.2 * self._m * self._h) + self._rng.normal(0, 0.5)
        result = DrillResult(
            lot_id=lot_id,
            roughness_um=float(roughness),
            broken=self.broken,
            params={**self.params(), "bit_hits": self.bit_hits},
        )
        if self.bit_hits >= self._rated_life:
            self._change_bit()
        return result

    def telemetry(self) -> dict[str, float]:
        w = self.wear()
        if self.broken:
            current, vibration = 1.2, 0.2
        else:
            current = 2.0 + 0.8 * w + 0.05 * (self.spindle_rpm / 120000 - 1)
            vibration = 0.5 + 0.6 * w**2
        return {
            "spindle_current_a": current,
            "vibration_g": vibration,
            "spindle_rpm": self.spindle_rpm,
            "feed_rate_m_min": self.feed_rate_m_min,
            "bit_hits": float(self.bit_hits),
        }

    def apply(self, command: str, params: dict) -> None:
        match command:
            case "change_bit":
                self._change_bit()
            case "set_rpm":
                self.spindle_rpm = require(params, "spindle_rpm", 50000, 200000)
            case "set_feed":
                self.feed_rate_m_min = require(params, "feed_rate_m_min", 0.5, 4.0)
            case "stop":
                self.stopped = True
            case "resume":
                self.stopped = False
            case _:
                raise ValueError(f"unknown drill command {command!r}")
