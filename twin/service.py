import math
from typing import Literal

import numpy as np

from twin.calibration import RlsGain
from twin.models import drill, etch, plating
from twin.models.residual import ResidualQuantiles
from twin.state import ProcessState
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
        self._rls = {kind: RlsGain() for kind in _KINDS}
        self._residual = {kind: ResidualQuantiles(seed=seed) for kind in _KINDS}
        self._resid_x: dict[str, list[list[float]]] = {kind: [] for kind in _KINDS}
        self._resid_y: dict[str, list[float]] = {kind: [] for kind in _KINDS}

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
        scalars = [self.state.filters[key].x for key in sorted(self.state.filters)]
        return [float(mech), *scalars]
