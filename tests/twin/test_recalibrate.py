from pathlib import Path

import numpy as np

from bench.metrics import mape
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _params(asd: float = 2.0) -> dict:
    return {"asd": asd, "time_min": 60.0, "additive_ml_l": 4.5}


def _batch_mape(twin: TwinService, asds: np.ndarray, scale: float) -> float:
    ys, yhats = [], []
    for asd in asds:
        pred = twin.simulate(_params(float(asd)), kind="thickness")
        ys.append(scale * float(pred.detail["mean_mech"]))
        yhats.append(float(pred.mean))
    return mape(np.array(ys), np.array(yhats))


def test_rls_recalibrates_after_scale_drift():
    twin = TwinService(InMemoryBus(), RECIPE, SimClock(), fidelity="mechanistic", seed=0)
    recal_ticks = 0
    asds = np.linspace(1.5, 2.5, 15)

    for asd in asds:
        pred = twin.simulate(_params(float(asd)), kind="thickness")
        twin.observe_thickness(float(pred.detail["mean_mech"]), _params(float(asd)))
        recal_ticks += 1
    assert _batch_mape(twin, asds, scale=1.0) < 2.0

    drift_asds = np.linspace(1.5, 2.5, 10)
    drift_ys, drift_yhats = [], []
    for asd in drift_asds:
        pred = twin.simulate(_params(float(asd)), kind="thickness")
        y = 1.3 * float(pred.detail["mean_mech"])
        drift_ys.append(y)
        drift_yhats.append(float(pred.mean))
        twin.observe_thickness(y, _params(float(asd)))
        recal_ticks += 1
    assert mape(np.array(drift_ys), np.array(drift_yhats)) > 5.0

    for asd in np.linspace(1.5, 2.5, 20):
        pred = twin.simulate(_params(float(asd)), kind="thickness")
        twin.observe_thickness(1.3 * float(pred.detail["mean_mech"]), _params(float(asd)))
        recal_ticks += 1
    assert _batch_mape(twin, asds, scale=1.3) < 3.0
    assert recal_ticks == 45
