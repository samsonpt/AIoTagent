from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.intents import Intent
from common.recipe import load_recipe
from edge.agent import ProcessEdgeAgent

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def _etch_values(sg: float) -> dict[str, float]:
    return {
        "sg": sg,
        "etch_temp_c": 50.0,
        "spray_pressure_z1": 2.0,
        "spray_pressure_z2": 2.0,
        "spray_pressure_z3": 2.0,
        "conveyor_speed_m_min": 2.0,
    }


def _plating_values(temp: float) -> dict[str, float]:
    return {
        "bath_temp_c": temp,
        "current_density_asd": 2.0,
        "rect_current_r1": 60.0,
        "rect_current_r2": 60.0,
        "rect_current_r3": 60.0,
    }


def publish_varied(bus, topic, tick, base: dict, key: str, jitter: float, n: int = 30) -> None:
    for i in range(n):
        values = {**base, key: base[key] + i * jitter}
        bus.publish(topic, {"t": tick * 1800.0, "tick": tick, "values": values}, "plant")


def publish_frozen(bus, topic, tick, values: dict, n: int = 30) -> None:
    for _ in range(n):
        bus.publish(topic, {"t": tick * 1800.0, "tick": tick, "values": values}, "plant")


def test_spoof_blocks_set_keeps_repair():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock)
    commands = []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    for tick in range(8):
        publish_varied(bus, topics.telemetry("etch"), tick, _etch_values(1.28), "sg", 1e-4)
        clock.advance_tick()
        agent.on_tick(clock)
    publish_frozen(bus, topics.telemetry("etch"), 8, _etch_values(1.40))
    clock.advance_tick()
    agent.on_tick(clock)
    assert agent._confidence == "low"
    assert "repair_regenerator" in [c["command"] for c in commands]
    assert all(not c["command"].startswith("set_") for c in commands)
    bus.publish(
        topics.intents("etch"),
        Intent(
            intent="copper_thickness",
            target_process="etch",
            params={"thickness_mean": 31.0},
            source="peer",
        ).model_dump(),
        "edge-plating",
    )
    assert all(c["command"] != "set_conveyor_speed" for c in commands)


def test_isolation_forest_flags_outlier_vector():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock)
    for tick in range(8):
        publish_varied(bus, topics.telemetry("etch"), tick, _etch_values(1.28), "sg", 1e-4)
        clock.advance_tick()
        agent.on_tick(clock)
    for i in range(30):
        values = _etch_values(1.28)
        values["etch_temp_c"] = 90.0 + i * 0.05
        bus.publish(topics.telemetry("etch"), {"t": 14400.0, "tick": 8, "values": values}, "plant")
    clock.advance_tick()
    agent.on_tick(clock)
    assert agent._confidence == "low"


def test_confidence_off_keeps_feedforward_under_spoof():
    bus, clock = InMemoryBus(), SimClock()
    ProcessEdgeAgent("plating", bus, RECIPE, clock, confidence_modulation=False)
    intents = []
    bus.subscribe(topics.intents("etch"), lambda t, p: intents.append(p), "spy")
    for tick in range(8):
        publish_frozen(bus, topics.telemetry("plating"), tick, _plating_values(45.0))
        clock.advance_tick()
    bus.publish(
        topics.measurement("plating"),
        {"t": 0, "tick": 8, "kind": "thickness_um", "lot_id": "L0001", "panel_id": "P", "zones": [[31.0] * 3] * 3},
        "plant",
    )
    assert intents[0]["intent"] == "copper_thickness"


def test_spoof_blocks_plating_feedforward():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("plating", bus, RECIPE, clock)
    intents = []
    bus.subscribe(topics.intents("etch"), lambda t, p: intents.append(p), "spy")
    for tick in range(8):
        publish_varied(bus, topics.telemetry("plating"), tick, _plating_values(45.0), "bath_temp_c", 0.02)
        clock.advance_tick()
        agent.on_tick(clock)
    publish_frozen(bus, topics.telemetry("plating"), 8, _plating_values(45.0))
    clock.advance_tick()
    agent.on_tick(clock)
    bus.publish(
        topics.measurement("plating"),
        {"t": 0, "tick": 8, "kind": "thickness_um", "lot_id": "L0001", "panel_id": "P", "zones": [[31.0] * 3] * 3},
        "plant",
    )
    assert intents == []
