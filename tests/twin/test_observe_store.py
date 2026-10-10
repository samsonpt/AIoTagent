from pathlib import Path

from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")

THICKNESS_ZONES = [
    [25.0, 25.0, 25.0],
    [26.0, 26.0, 26.0],
    [24.0, 24.0, 24.0],
]


def _service(
    bus: InMemoryBus | None = None,
    clock: SimClock | None = None,
    store: TraceStore | None = None,
) -> TwinService:
    return TwinService(
        bus or InMemoryBus(), RECIPE, clock or SimClock(), seed=0, store=store
    )


def test_observe_persists_to_trace_store():
    bus = InMemoryBus()
    clock = SimClock()
    clock.tick = 4
    with TraceStore() as store:
        svc = _service(bus, clock, store)
        bus.publish(
            topics.lab_assay(),
            {
                "t_sample": 0.0,
                "t_report": 7200.0,
                "tick": 4,
                "process": "plating",
                "values": {"additive_ml_l": 4.2, "cu_g_l": 60.0},
            },
            "plant",
        )
        bus.publish(
            topics.measurement("plating"),
            {
                "t": 7200.0,
                "tick": 4,
                "kind": "thickness_um",
                "lot_id": "L0001",
                "panel_id": "L0001-P01",
                "zones": THICKNESS_ZONES,
            },
            "plant",
        )
        assert len(svc.predictions) == 1
        obs = svc.predictions[-1]
        rows = store.twin_observations()
        assert len(rows) == 1
        rec = rows[0]
        assert rec.t == obs.t
        assert rec.tick == obs.tick
        assert rec.kind == obs.kind
        assert rec.y == obs.y
        assert rec.yhat == obs.yhat
        assert rec.q05 == obs.q05
        assert rec.q95 == obs.q95
        assert rec.lot_id == obs.lot_id


def test_observe_without_store_does_not_error():
    bus = InMemoryBus()
    clock = SimClock()
    clock.tick = 4
    svc = _service(bus, clock, store=None)
    bus.publish(
        topics.measurement("plating"),
        {
            "t": 7200.0,
            "tick": 4,
            "kind": "thickness_um",
            "lot_id": "L0001",
            "panel_id": "L0001-P01",
            "zones": THICKNESS_ZONES,
        },
        "plant",
    )
    assert len(svc.predictions) == 1
