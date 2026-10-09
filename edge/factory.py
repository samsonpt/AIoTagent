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
    guard=None,
    auto_approve: bool = True,
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
        agents = [ProcessEdgeAgent(process, emit_commands=True, **shared) for process in PROCESSES]
        if ablation.use_human_gate and store is not None and guard is not None:
            agents.append(
                HumanModel(
                    bus,
                    recipe,
                    clock,
                    store=store,
                    guard=guard,
                    seed=seed,
                    ocap=False,
                    auto_approve=auto_approve,
                )
            )
        return agents
    agents = [
        ProcessEdgeAgent(process, emit_commands=False, **{**shared, "feedforward": False})
        for process in PROCESSES
    ]
    human_kwargs: dict = {"ocap": True, "auto_approve": auto_approve}
    if ablation.use_human_gate and store is not None and guard is not None:
        human_kwargs.update(store=store, guard=guard, seed=seed)
    return [*agents, HumanModel(bus, recipe, clock, **human_kwargs)]
