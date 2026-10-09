from pathlib import Path

import numpy as np

from bench.metrics import coverage90, mape
from bench.schema import TraceStore
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from sim.faults import load_scenario
from sim.runner import run
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]


def test_mid_mismatch_thickness_width_meet_thresholds():
    scenario = load_scenario(ROOT / "bench" / "scenarios" / "nominal.yaml")
    recipe = load_recipe(ROOT / scenario.recipe_path)
    bus = InMemoryBus()
    twin = TwinService(bus, recipe, SimClock(), fidelity="hybrid", seed=scenario.seed)
    run(scenario, TraceStore(), bus=bus, controllers=[twin], n_ticks=48)

    thickness = [obs for obs in twin.predictions if obs.kind == "thickness"][16:]
    width = [obs for obs in twin.predictions if obs.kind == "width"][16:]

    yt = np.array([obs.y for obs in thickness], dtype=float)
    yp = np.array([obs.yhat for obs in thickness], dtype=float)
    t05 = np.array([obs.q05 for obs in thickness], dtype=float)
    t95 = np.array([obs.q95 for obs in thickness], dtype=float)
    yw = np.array([obs.y for obs in width], dtype=float)
    ywp = np.array([obs.yhat for obs in width], dtype=float)
    w05 = np.array([obs.q05 for obs in width], dtype=float)
    w95 = np.array([obs.q95 for obs in width], dtype=float)

    assert mape(yt, yp) <= 5.0
    assert mape(yw, ywp) <= 5.0
    assert coverage90(yt, t05, t95) >= 0.85
    assert coverage90(yw, w05, w95) >= 0.85
