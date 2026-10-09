import math
from typing import Literal

import numpy as np

from common import topics
from twin.calibration import RlsGain
from twin.models import drill, etch, plating
from twin.models.residual import ResidualQuantiles
from twin.state import ProcessState, ScalarFilter
from twin.types import CounterfactualResult, Prediction, TwinObservation

Kind = Literal["thickness", "width", "roughness"]

_KINDS = ("thickness", "width", "roughness")
_SPEC_BY_KIND = {
    "thickness": "copper_thickness_um",
    "width": "line_width_um",
    "roughness": "hole_roughness_um",
}
_SIGMA_MECH = {
    "thickness": 0.4,
    "width": 0.7,
    "roughness": 0.5,
}
_Z90 = 1.645
_SIGMA_FROM_90 = 3.29
_TELEMETRY_KEYS = {
    "sg": "sg",
    "bath_temp_c": "bath_temp_c",
    "current_density_asd": "current_density_asd",
    "spindle_current_a": "spindle_current_a",
    "bit_hits": "bit_hits",
    "conveyor_speed_m_min": "conveyor_speed_m_min",
    "etch_temp_c": "etch_temp_c",
    "spray_pressure_z1": "spray",
}
_FEATURE_KEYS = (
    "additive_ml_l",
    "bath_temp_c",
    "bit_hits",
    "conveyor_speed_m_min",
    "current_density_asd",
    "etch_temp_c",
    "sg",
    "spindle_current_a",
    "spray",
)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _oos_prob(mean: float, q05: float, q95: float, spec) -> float:
    sigma = abs(q95 - q05) / _SIGMA_FROM_90
    if sigma <= 1e-12:
        return 0.0 if spec.contains(mean) else 1.0
    p = 0.0
    if spec.min is not None:
        p += _norm_cdf((spec.min - mean) / sigma)
    if spec.max is not None:
        p += 1.0 - _norm_cdf((spec.max - mean) / sigma)
    return min(1.0, max(0.0, p))


class TwinService:
    client_id = "twin"

    def __init__(self, bus, recipe, clock, *, fidelity: str = "hybrid", seed: int = 0):
        self.bus = bus
        self.recipe = recipe
        self.clock = clock
        self.fidelity = fidelity
        self.seed = seed
        self.client_id = "twin"
        self.state = ProcessState()
        self.predictions: list[TwinObservation] = []
        self._rls = {
            "thickness": RlsGain(lam=0.93),
            "width": RlsGain(),
            "roughness": RlsGain(),
        }
        self._residual = {kind: ResidualQuantiles(seed=seed) for kind in _KINDS}
        self._resid_x: dict[str, list[list[float]]] = {kind: [] for kind in _KINDS}
        self._resid_y: dict[str, list[float]] = {kind: [] for kind in _KINDS}
        self._last_thickness_y: float | None = None
        for process in ("plating", "etch", "drill"):
            bus.subscribe(topics.telemetry(process), self._on_telemetry, self.client_id)
        bus.subscribe(topics.lab_assay(), self._on_assay, self.client_id)
        bus.subscribe(topics.measurement("plating"), self._on_plating_measurement, self.client_id)
        bus.subscribe(topics.measurement("etch"), self._on_etch_measurement, self.client_id)

    def on_tick(self, clock) -> None:
        self.clock = clock
        for filt in self.state.filters.values():
            filt.predict(u=0.0)

    def simulate(self, params: dict, *, kind: Kind) -> Prediction:
        if self.fidelity == "none":
            return Prediction(
                mean=None,
                q05=None,
                q95=None,
                yield_prob=0.0,
                oos_prob=1.0,
                detail={"kind": kind},
            )
        return self._predict(params, kind)

    def compare(self, candidates: list[dict]) -> list[Prediction]:
        preds: list[Prediction] = []
        for candidate in candidates:
            kind = candidate["kind"]
            params = {key: value for key, value in candidate.items() if key != "kind"}
            preds.append(self.simulate(params, kind=kind))
        return preds

    def counterfactual(
        self, params: dict, hypothesis: dict, *, kind: Kind
    ) -> CounterfactualResult:
        before = self.simulate(params, kind=kind)
        after = self.simulate({**params, **hypothesis}, kind=kind)
        return CounterfactualResult(
            hypothesis=hypothesis,
            before=before,
            after=after,
            defect_cleared=before.oos_prob >= 0.5 and after.oos_prob < 0.5,
        )

    def observe_thickness(self, y: float, params: dict) -> None:
        self._observe(float(y), params, "thickness")

    def observe_width(self, y: float, params: dict) -> None:
        self._observe(float(y), params, "width")

    def _on_assay(self, topic: str, payload: dict) -> None:
        if payload.get("process") != "plating":
            return
        values = payload.get("values") or {}
        if "additive_ml_l" not in values:
            return
        self._upsert_filter("additive_ml_l", float(values["additive_ml_l"]))

    def _on_telemetry(self, topic: str, payload: dict) -> None:
        values = payload.get("values") or {}
        for src, dest in _TELEMETRY_KEYS.items():
            if src in values:
                self._upsert_filter(dest, float(values[src]))

    def _on_plating_measurement(self, topic: str, payload: dict) -> None:
        y = float(np.mean(payload["zones"]))
        self._last_thickness_y = y
        self.observe_thickness(y, self._thickness_params(payload))

    def _on_etch_measurement(self, topic: str, payload: dict) -> None:
        y = float(np.mean(payload["zones"]))
        self.observe_width(y, self._width_params(payload))

    def _upsert_filter(self, key: str, z: float) -> None:
        if key not in self.state.filters:
            self.state.set_filter(key, ScalarFilter(x0=float(z)))
            return
        self.state.filters[key].update(float(z))

    def _state_or(self, key: str, default: float) -> float:
        if key in self.state.filters:
            return self.state.get(key)
        return float(default)

    def _thickness_params(self, payload: dict) -> dict:
        return {
            "asd": self._state_or(
                "current_density_asd",
                self.recipe.window("plating", "current_density_asd").target,
            ),
            "time_min": float(self.recipe.constants["plating_time_min"]),
            "additive_ml_l": self._state_or(
                "additive_ml_l",
                self.recipe.window("plating", "additive_ml_l").target,
            ),
            "lot_id": payload.get("lot_id"),
        }

    def _width_params(self, payload: dict) -> dict:
        thickness = 25.0 if self._last_thickness_y is None else self._last_thickness_y
        return {
            "sg": self._state_or("sg", self.recipe.window("etch", "sg").target),
            "temp_c": self._state_or(
                "etch_temp_c", self.recipe.window("etch", "etch_temp_c").target
            ),
            "spray_bar": self._state_or(
                "spray", self.recipe.window("etch", "spray_pressure_bar").target
            ),
            "speed_m_min": self._state_or(
                "conveyor_speed_m_min",
                self.recipe.window("etch", "conveyor_speed_m_min").target,
            ),
            "thickness_um": thickness,
            "lot_id": payload.get("lot_id"),
        }

    def _predict(self, params: dict, kind: str) -> Prediction:
        mech = self._mechanistic(params, kind)
        theta = self._rls[kind].theta
        mean_scaled = mech * theta
        residual = self._residual[kind]
        if self.fidelity == "hybrid" and residual.ready:
            features = np.asarray([self._features(mech)], dtype=float)
            q05_r, q50_r, q95_r = residual.predict(features)
            mean = mean_scaled + float(q50_r[0])
            q05 = mean_scaled + float(q05_r[0])
            q95 = mean_scaled + float(q95_r[0])
        else:
            mean = mean_scaled
            half = _Z90 * _SIGMA_MECH[kind]
            q05 = mean - half
            q95 = mean + half
        spec = self.recipe.specs[_SPEC_BY_KIND[kind]]
        oos = _oos_prob(mean, q05, q95, spec)
        return Prediction(
            mean=mean,
            q05=q05,
            q95=q95,
            yield_prob=1.0 - oos,
            oos_prob=oos,
            detail={"kind": kind, "mean_mech": mech, "theta": theta},
        )

    def _observe(self, y: float, params: dict, kind: str) -> None:
        pred = self._predict(params, kind)
        mech = float(pred.detail["mean_mech"])
        self._rls[kind].update(mech, y)
        residual_target = y - mech * self._rls[kind].theta
        self._resid_x[kind].append(self._features(mech))
        self._resid_y[kind].append(residual_target)
        if len(self._resid_y[kind]) >= 8:
            self._residual[kind].fit(
                np.asarray(self._resid_x[kind], dtype=float),
                np.asarray(self._resid_y[kind], dtype=float),
            )
        self.predictions.append(
            TwinObservation(
                t=self.clock.now,
                tick=self.clock.tick,
                kind=kind,
                y=y,
                yhat=float(pred.mean),
                q05=float(pred.q05),
                q95=float(pred.q95),
                lot_id=params.get("lot_id"),
            )
        )

    def _mechanistic(self, params: dict, kind: str) -> float:
        if kind == "thickness":
            return plating.thickness_um(
                asd=float(params["asd"]),
                time_min=float(
                    params.get("time_min", self.recipe.constants["plating_time_min"])
                ),
                additive_ml_l=float(params["additive_ml_l"]),
            )
        if kind == "width":
            return etch.width_um(
                sg=float(params["sg"]),
                temp_c=float(params["temp_c"]),
                spray_bar=float(params["spray_bar"]),
                speed_m_min=float(params["speed_m_min"]),
                thickness_um=float(params["thickness_um"]),
                artwork_um=float(self.recipe.constants["artwork_width_um"]),
                chamber_m=float(self.recipe.constants["etch_chamber_length_m"]),
                etch_factor=float(self.recipe.constants["etch_factor"]),
            )
        if kind == "roughness":
            return drill.roughness_um(
                bit_hits=float(params["bit_hits"]),
                rated=float(self.recipe.constants["bit_rated_life_hits"]),
            )
        raise ValueError(f"unknown kind: {kind}")

    def _features(self, mech: float) -> list[float]:
        return [float(mech), *[self._state_or(key, 0.0) for key in _FEATURE_KEYS]]
