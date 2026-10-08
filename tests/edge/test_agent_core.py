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
    for _ in range(30):
        bus.publish(topics.telemetry("etch"), {"t": tick * 1800.0, "tick": tick, "values": values}, "plant")


def test_spc_emits_event_and_ocap_commands():
    bus, clock = InMemoryBus(), SimClock()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock)
    commands, events = [], []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    bus.subscribe(topics.events("etch"), lambda t, p: events.append(p), "spy")
    for tick in range(8):
        tel(bus, tick, 1.28)
        clock.advance_tick()
        agent.on_tick(clock)
    assert commands == [] and events == []
    tel(bus, 8, 1.40)
    clock.advance_tick()
    agent.on_tick(clock)
    assert [c["command"] for c in commands] == ["repair_regenerator", "adjust_sg"]
    assert commands[0]["source"] == "edge"
    assert events[0]["rule"] == "R1" and events[0]["key"] == "sg" and events[0]["replayed"] is False
