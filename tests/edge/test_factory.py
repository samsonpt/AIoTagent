from pathlib import Path

from common.bus import InMemoryBus
from common.clock import SimClock
from common.config import load_ablation
from common.recipe import load_recipe
from edge.agent import ProcessEdgeAgent
from edge.factory import make_controllers
from bench.human_model import HumanModel

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def test_edge_only_has_three_agents():
    ablation = load_ablation(ROOT / "bench" / "configs" / "edge_only.yaml")
    ctrls = make_controllers(ablation, InMemoryBus(), RECIPE, SimClock(), None, 0)
    assert len(ctrls) == 3
    assert all(isinstance(c, ProcessEdgeAgent) and c.emit_commands for c in ctrls)


def test_baseline_rule_adds_human():
    ablation = load_ablation(ROOT / "bench" / "configs" / "baseline_rule.yaml")
    ctrls = make_controllers(ablation, InMemoryBus(), RECIPE, SimClock(), None, 0)
    assert isinstance(ctrls[-1], HumanModel)
    assert all(not c.emit_commands for c in ctrls[:-1])
