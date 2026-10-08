from pathlib import Path

from bench.schema import TraceStore
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
    for i in range(30):
        noisy = {**values, "sg": sg + i * 1e-4}
        bus.publish(topics.telemetry("etch"), {"t": tick * 1800.0, "tick": tick, "values": noisy}, "plant")


def test_sg_ocap_records_episode_and_both_commands():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock, store=store)
    commands = []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    for tick in range(8):
        tel(bus, tick, 1.28)
        clock.advance_tick()
        agent.on_tick(clock)
    tel(bus, 8, 1.40)
    clock.advance_tick()
    agent.on_tick(clock)
    assert [c["command"] for c in commands] == ["repair_regenerator", "adjust_sg"]
    [episode] = store.episodes()
    assert episode.episode_id == "etch-8-sg"
    assert episode.handler == "edge"
    assert episode.trigger == "R1"
    assert episode.t_detect == clock.now


def test_emit_commands_false_marks_human_handler():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    agent = ProcessEdgeAgent("etch", bus, RECIPE, clock, store=store, emit_commands=False)
    for tick in range(8):
        tel(bus, tick, 1.28)
        clock.advance_tick()
        agent.on_tick(clock)
    tel(bus, 8, 1.40)
    clock.advance_tick()
    agent.on_tick(clock)
    [episode] = store.episodes()
    assert episode.handler == "human"
    assert agent.emit_commands is False
