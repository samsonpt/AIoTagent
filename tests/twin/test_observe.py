from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import load_recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")

THICKNESS_ZONES = [
    [25.0, 25.0, 25.0],
    [26.0, 26.0, 26.0],
    [24.0, 24.0, 24.0],
]
WIDTH_ZONES = [[100.0, 101.0, 99.0]] * 3


def _service(bus: InMemoryBus | None = None, clock: SimClock | None = None) -> TwinService:
    return TwinService(bus or InMemoryBus(), RECIPE, clock or SimClock(), seed=0)


def test_assay_and_thickness_measurement_records_observation():
    bus = InMemoryBus()
    clock = SimClock()
    clock.tick = 4
    svc = _service(bus, clock)

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
    assert "additive_ml_l" in svc.state.filters
    assert svc.state.get("additive_ml_l") == 4.2

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
    obs = svc.predictions[0]
    assert obs.kind == "thickness"
    assert obs.y == 25.0
    assert obs.lot_id == "L0001"
    assert obs.tick == 4


def test_width_measurement_after_thickness_records_observation():
    bus = InMemoryBus()
    clock = SimClock()
    clock.tick = 5
    svc = _service(bus, clock)

    bus.publish(
        topics.measurement("plating"),
        {
            "t": 9000.0,
            "tick": 5,
            "kind": "thickness_um",
            "lot_id": "L0001",
            "panel_id": "L0001-P01",
            "zones": THICKNESS_ZONES,
        },
        "plant",
    )
    bus.publish(
        topics.measurement("etch"),
        {
            "t": 9000.0,
            "tick": 5,
            "kind": "line_width_um",
            "lot_id": "L0001",
            "panel_id": "L0001-P01",
            "zones": WIDTH_ZONES,
        },
        "plant",
    )

    kinds = [obs.kind for obs in svc.predictions]
    assert kinds == ["thickness", "width"]
    assert svc.predictions[1].y == 100.0
    assert svc.predictions[1].lot_id == "L0001"


def test_telemetry_updates_known_filter_keys():
    bus = InMemoryBus()
    svc = _service(bus)

    bus.publish(
        topics.telemetry("etch"),
        {
            "t": 0.0,
            "tick": 1,
            "values": {
                "sg": 1.29,
                "etch_temp_c": 51.0,
                "spray_pressure_z1": 2.1,
                "conveyor_speed_m_min": 1.8,
                "unknown_channel": 9.0,
            },
        },
        "plant",
    )
    bus.publish(
        topics.telemetry("plating"),
        {
            "t": 0.0,
            "tick": 1,
            "values": {"bath_temp_c": 26.0, "current_density_asd": 2.2},
        },
        "plant",
    )
    bus.publish(
        topics.telemetry("drill"),
        {
            "t": 0.0,
            "tick": 1,
            "values": {"spindle_current_a": 2.4, "bit_hits": 600.0},
        },
        "plant",
    )

    assert svc.state.get("sg") == 1.29
    assert svc.state.get("etch_temp_c") == 51.0
    assert svc.state.get("spray") == 2.1
    assert svc.state.get("conveyor_speed_m_min") == 1.8
    assert svc.state.get("bath_temp_c") == 26.0
    assert svc.state.get("current_density_asd") == 2.2
    assert svc.state.get("spindle_current_a") == 2.4
    assert svc.state.get("bit_hits") == 600.0
    assert "unknown_channel" not in svc.state.filters


def test_eight_thickness_obs_fit_despite_growing_telemetry():
    bus = InMemoryBus()
    svc = _service(bus)
    for i in range(8):
        values = {"sg": 1.28}
        if i >= 2:
            values["etch_temp_c"] = 50.0
        if i >= 4:
            values["spray_pressure_z1"] = 2.0
        bus.publish(
            topics.telemetry("etch"),
            {"t": 0.0, "tick": i, "values": values},
            "plant",
        )
        bus.publish(
            topics.measurement("plating"),
            {
                "t": 0.0,
                "tick": i,
                "kind": "thickness_um",
                "lot_id": "L0001",
                "panel_id": "P01",
                "zones": THICKNESS_ZONES,
            },
            "plant",
        )
    assert len(svc.predictions) == 8
    assert svc._residual["thickness"].ready


def test_on_tick_predicts_existing_filters():
    svc = _service()
    from twin.state import ScalarFilter

    filt = ScalarFilter(x0=4.5)
    svc.state.set_filter("additive_ml_l", filt)
    before_p = filt.p
    svc.on_tick(svc.clock)
    assert filt.x == 4.5
    assert filt.p > before_p


def test_runner_appends_twin_when_lookahead_enabled(monkeypatch):
    created: list[TwinService] = []
    real = TwinService

    def tracking(*args, **kwargs):
        svc = real(*args, **kwargs)
        created.append(svc)
        return svc

    monkeypatch.setattr("sim.runner.TwinService", tracking)
    from bench.schema import TraceStore
    from sim.faults import load_scenario
    from sim.runner import run

    ablation = AblationConfig(
        name="twin_on",
        use_edge_agent=False,
        use_cloud=False,
        use_twin_lookahead=True,
        twin_fidelity="mechanistic",
    )
    run(
        load_scenario(ROOT / "bench" / "scenarios" / "nominal.yaml"),
        TraceStore(),
        ablation=ablation,
        n_ticks=1,
    )
    assert len(created) == 1
    assert created[0].fidelity == "mechanistic"
    assert created[0].client_id == "twin"


def test_runner_skips_twin_when_fidelity_none(monkeypatch):
    created: list[TwinService] = []

    def tracking(*args, **kwargs):
        created.append(real := TwinService(*args, **kwargs))
        return real

    monkeypatch.setattr("sim.runner.TwinService", tracking)
    from bench.schema import TraceStore
    from sim.faults import load_scenario
    from sim.runner import run

    ablation = AblationConfig(
        name="twin_off",
        use_edge_agent=False,
        use_cloud=False,
        use_twin_lookahead=True,
        twin_fidelity="none",
    )
    run(
        load_scenario(ROOT / "bench" / "scenarios" / "nominal.yaml"),
        TraceStore(),
        ablation=ablation,
        n_ticks=1,
    )
    assert created == []
