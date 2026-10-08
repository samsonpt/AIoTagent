from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from bench.human_model import HumanModel

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_human_ocap_delayed_two_ticks():
    bus, clock = InMemoryBus(), SimClock()
    human = HumanModel(bus, RECIPE, clock, delay_ticks=2)
    commands = []
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    clock.advance_tick()
    clock.advance_tick()
    bus.publish(
        topics.events("etch"),
        {"t": 3600.0, "tick": 1, "process": "etch", "key": "sg", "rule": "R1", "value": 1.40, "replayed": False},
        "edge-etch",
    )
    human.on_tick(clock)
    assert commands == []
    clock.advance_tick()
    human.on_tick(clock)
    clock.advance_tick()
    human.on_tick(clock)
    assert [c["source"] for c in commands] == ["human", "human"]
    assert {c["command"] for c in commands} == {"repair_regenerator", "adjust_sg"}
