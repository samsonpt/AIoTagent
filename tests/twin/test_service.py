import time
from pathlib import Path

from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")

NOMINAL_THICKNESS = {
    "asd": 2.0,
    "time_min": 60.0,
    "additive_ml_l": 4.5,
}


def _service(**kwargs) -> TwinService:
    return TwinService(InMemoryBus(), RECIPE, SimClock(), **kwargs)


def test_nominal_simulate_thickness_mean():
    pred = _service().simulate(NOMINAL_THICKNESS, kind="thickness")
    assert pred.mean is not None
    assert 24.0 <= pred.mean <= 28.0


def test_compare_twenty_candidates_under_two_seconds():
    svc = _service()
    candidates = [
        {**NOMINAL_THICKNESS, "kind": "thickness", "asd": 1.5 + i * 0.05}
        for i in range(20)
    ]
    t0 = time.perf_counter()
    preds = svc.compare(candidates)
    elapsed = time.perf_counter() - t0
    assert len(preds) == 20
    assert elapsed < 2.0


def test_raising_asd_increases_oos_prob():
    svc = _service()
    nominal = svc.simulate(NOMINAL_THICKNESS, kind="thickness")
    high = svc.simulate({**NOMINAL_THICKNESS, "asd": 3.5}, kind="thickness")
    assert high.oos_prob > nominal.oos_prob


def test_fidelity_none_returns_empty_mean():
    pred = _service(fidelity="none").simulate(NOMINAL_THICKNESS, kind="thickness")
    assert pred.mean is None
    assert pred.q05 is None
    assert pred.q95 is None
    assert pred.yield_prob == 0
    assert pred.oos_prob == 1


def test_observe_thickness_appends_prediction():
    svc = _service()
    assert svc.predictions == []
    svc.observe_thickness(25.0, NOMINAL_THICKNESS)
    assert svc.predictions
    assert svc.predictions[0].kind == "thickness"
    assert svc.predictions[0].y == 25.0
