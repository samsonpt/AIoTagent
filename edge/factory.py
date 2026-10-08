from bench.schema import TraceStore
from common.bus import Bus
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import Recipe
from common.topics import PROCESSES
from edge.agent import ProcessEdgeAgent
from bench.human_model import HumanModel


def make_controllers(
    ablation: AblationConfig,
    bus: Bus,
    recipe: Recipe,
    clock: SimClock,
    store: TraceStore | None,
    seed: int,
) -> list:
    shared = dict(
        bus=bus,
        recipe=recipe,
        clock=clock,
        store=store,
        feedforward=ablation.use_peer_feedforward,
        confidence_modulation=ablation.use_confidence_modulation,
        seed=seed,
    )
    if ablation.use_edge_agent:
        return [ProcessEdgeAgent(process, emit_commands=True, **shared) for process in PROCESSES]
    agents = [
        ProcessEdgeAgent(process, emit_commands=False, **{**shared, "feedforward": False})
        for process in PROCESSES
    ]
    return [*agents, HumanModel(bus, recipe, clock)]
