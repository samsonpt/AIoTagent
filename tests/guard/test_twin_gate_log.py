from pathlib import Path

from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from guard.action_guard import ActionGuard
from sim.faults import Scenario
from sim.plant import Plant
from twin.types import Prediction, TwinObservation

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def make_guarded(*, ablation: AblationConfig | None = None, twin=None):
    scenario = Scenario(name="t", seed=5, n_ticks=20)
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    plant = Plant(scenario, bus, store, clock)
    ablation = ablation or AblationConfig()
    guard = ActionGuard(
        bus, store, RECIPE, clock, twin, seed=5, ablation=ablation
    )
    return plant, bus, store, clock, guard


def publish(bus, process, command, params=None, source="edge", reason="test"):
    topic = topics.line_command() if process == "line" else topics.command(process)
    bus.publish(
        topic,
        {
            "command": command,
            "params": params or {},
            "source": source,
            "reason": reason,
        },
        "edge",
    )
    return topic


def steps(plant, n=1):
    for _ in range(n):
        plant.step_tick()


class _HighConfTwin:
    predictions = [
        TwinObservation(
            t=0.0, tick=0, kind="thickness", y=25.0, yhat=25.0, q05=24.0, q95=26.0
        )
        for _ in range(10)
    ]

    def __init__(self, pred: Prediction):
        self._pred = pred

    def simulate(self, _params, kind=None):
        return self._pred


class _LowConfTwin:
    predictions = [
        TwinObservation(
            t=0.0, tick=0, kind="thickness", y=30.0, yhat=25.0, q05=24.0, q95=26.0
        )
        for _ in range(10)
    ]

    def simulate(self, _params, kind=None):
        raise AssertionError("simulate must not run when confidence is low")


def test_map_skip_logs_gate():
    ablation = AblationConfig(use_human_gate=False)
    twin = object()
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=twin)
    publish(bus, "etch", "clean_nozzle", {"zone": 1})
    steps(plant)
    gates = store.twin_gates()
    assert len(gates) >= 1
    row = gates[-1]
    assert row.reason == "map_skip"
    assert row.process == "etch"
    assert row.command == "clean_nozzle"
    assert row.passed is False
    assert row.kind is None
    assert row.confidence is None
    assert row.yield_prob is None
    assert row.oos_prob is None


def test_no_twin_logs_gate():
    ablation = AblationConfig(use_human_gate=True, use_twin_lookahead=True)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=None)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    gates = store.twin_gates()
    assert len(gates) == 1
    assert gates[0].reason == "no_twin"
    assert gates[0].passed is False
    assert gates[0].kind is None


def test_twin_disabled_logs_gate():
    ablation = AblationConfig(use_human_gate=True, use_twin_lookahead=False)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=None)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    gates = store.twin_gates()
    assert len(gates) == 1
    assert gates[0].reason == "twin_disabled"
    assert gates[0].passed is False


def test_confidence_low_logs_gate():
    ablation = AblationConfig(use_human_gate=True, use_twin_confidence_gate=True)
    twin = _LowConfTwin()
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=twin)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    gates = store.twin_gates()
    assert len(gates) == 1
    row = gates[0]
    assert row.reason == "confidence_low"
    assert row.passed is False
    assert row.confidence is not None
    assert row.confidence < 0.5
    assert row.yield_prob is None
    assert row.oos_prob is None
    assert row.kind == "thickness"


def test_simulate_passed_logs_gate():
    pred = Prediction(
        mean=25.0, q05=24.0, q95=26.0, yield_prob=0.95, oos_prob=0.05
    )
    ablation = AblationConfig(use_human_gate=False, use_twin_confidence_gate=True)
    twin = _HighConfTwin(pred)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=twin)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    gates = store.twin_gates(passed=True)
    assert len(gates) == 1
    row = gates[0]
    assert row.reason == "passed"
    assert row.passed is True
    assert row.yield_prob == 0.95
    assert row.oos_prob == 0.05
    assert row.kind == "thickness"


def test_simulate_gate_fail_logs_gate():
    pred = Prediction(
        mean=25.0, q05=24.0, q95=26.0, yield_prob=0.80, oos_prob=0.30
    )
    ablation = AblationConfig(use_human_gate=False, use_twin_confidence_gate=True)
    twin = _HighConfTwin(pred)
    plant, bus, store, _, _ = make_guarded(ablation=ablation, twin=twin)
    publish(bus, "plating", "set_current_density", {"asd": 2.0})
    steps(plant)
    gates = store.twin_gates(passed=False)
    assert len(gates) == 1
    row = gates[0]
    assert row.reason == "gate_fail"
    assert row.passed is False
    assert row.yield_prob == 0.80
    assert row.oos_prob == 0.30
