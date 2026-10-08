from pathlib import Path

from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from edge.agent import ProcessEdgeAgent

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def test_plating_publishes_intent_and_etch_sets_speed():
    bus, clock = InMemoryBus(), SimClock()
    plating = ProcessEdgeAgent("plating", bus, RECIPE, clock)
    etch = ProcessEdgeAgent("etch", bus, RECIPE, clock)
    intents, commands = [], []
    bus.subscribe(topics.intents("etch"), lambda t, p: intents.append(p), "spy")
    bus.subscribe(topics.command("etch"), lambda t, p: commands.append(p), "spy")
    zones = [[31.0] * 3] * 3
    bus.publish(
        topics.measurement("plating"),
        {"t": 0, "tick": 1, "kind": "thickness_um", "lot_id": "L0001", "panel_id": "L0001-P01", "zones": zones},
        "plant",
    )
    assert intents[0]["intent"] == "copper_thickness"
    assert intents[0]["source"] == "peer"
    assert commands[0]["command"] == "set_conveyor_speed"
    assert commands[0]["source"] == "peer"
    assert commands[0]["params"]["m_min"] < 2.0


def test_feedforward_can_be_disabled():
    bus, clock = InMemoryBus(), SimClock()
    ProcessEdgeAgent("plating", bus, RECIPE, clock, feedforward=False)
    intents = []
    bus.subscribe(topics.intents("etch"), lambda t, p: intents.append(p), "spy")
    bus.publish(
        topics.measurement("plating"),
        {"t": 0, "tick": 1, "kind": "thickness_um", "lot_id": "L0001", "panel_id": "P", "zones": [[25.0] * 3] * 3},
        "plant",
    )
    assert intents == []
