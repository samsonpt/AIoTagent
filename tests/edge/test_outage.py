from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from edge.agent import ProcessEdgeAgent

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def tel(bus, tick, sg):
    values = {
        "sg": sg,
        "etch_temp_c": 50.0,
        "spray_pressure_z1": 2.0,
        "spray_pressure_z2": 2.0,
        "spray_pressure_z3": 2.0,
        "conveyor_speed_m_min": 2.0,
    }
    bus.publish(topics.telemetry("etch"), {"t": tick * 1800.0, "tick": tick, "values": values}, "plant")


def test_events_replay_after_link_restored():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock)
    events = []
    bus.subscribe(topics.events("etch"), lambda t, p: events.append(p), "spy")
    for tick in range(8):
        tel(bus, tick, 1.28)
        clock.advance_tick()
        agent.on_tick(clock)
    bus.set_link("edge-etch", False)
    agent._on_telemetry(
        topics.telemetry("etch"),
        {
            "t": 14400.0,
            "tick": 8,
            "values": {
                "sg": 1.40,
                "etch_temp_c": 50.0,
                "spray_pressure_z1": 2.0,
                "spray_pressure_z2": 2.0,
                "spray_pressure_z3": 2.0,
                "conveyor_speed_m_min": 2.0,
            },
        },
    )
    clock.advance_tick()
    agent.on_tick(clock)
    assert events == []
    bus.set_link("edge-etch", True)
    clock.advance_tick()
    agent.on_tick(clock)
    assert events[0]["replayed"] is True and events[0]["key"] == "sg"
