from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from edge.agent import ProcessEdgeAgent

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_change_bit_when_near_life():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("drill", bus, RECIPE, clock)
    commands = []
    bus.subscribe(topics.command("drill"), lambda t, p: commands.append(p), "spy")
    values = {
        "spindle_current_a": 2.0,
        "vibration_g": 0.5,
        "spindle_rpm": 120000.0,
        "feed_rate_m_min": 2.0,
        "bit_hits": 5400.0,
    }
    bus.publish(topics.telemetry("drill"), {"t": 0, "tick": 0, "values": values}, "plant")
    clock.advance_tick()
    agent.on_tick(clock)
    assert commands[0]["command"] == "change_bit"
    commands.clear()
    bus.publish(topics.telemetry("drill"), {"t": 1800, "tick": 1, "values": values}, "plant")
    clock.advance_tick()
    agent.on_tick(clock)
    assert commands == []


def test_dose_additive_from_assay():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("plating", bus, RECIPE, clock)
    commands = []
    bus.subscribe(topics.command("plating"), lambda t, p: commands.append(p), "spy")
    bus.publish(
        topics.lab_assay(),
        {"t_sample": 0, "t_report": 0, "tick": 0, "process": "plating", "values": {"additive_ml_l": 2.0, "cu_g_l": 60.0}},
        "plant",
    )
    assert commands[0]["command"] == "dose_additive"
    assert commands[0]["params"]["ml_l"] == 2.5


def test_daily_dose_cap():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("plating", bus, RECIPE, clock)
    commands = []
    bus.subscribe(topics.command("plating"), lambda t, p: commands.append(p), "spy")
    assay = {"t_sample": 0, "t_report": 0, "process": "plating", "values": {"additive_ml_l": 2.0, "cu_g_l": 60.0}}
    for tick in (0, 3, 6, 9):
        bus.publish(topics.lab_assay(), {**assay, "tick": tick}, "plant")
        for _ in range(3):
            clock.advance_tick()
            agent.on_tick(clock)
    doses = [c["params"]["ml_l"] for c in commands if c["command"] == "dose_additive"]
    assert doses == [2.5, 2.5, 1.0]
