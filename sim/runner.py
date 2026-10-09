import argparse
import dataclasses
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from bench.metrics import fpr, fpy, scrap_rate
from bench.schema import TraceStore
from common.bus import Bus, InMemoryBus
from common.clock import SimClock
from common.config import AblationConfig, load_ablation
from common.env import load_env
from common.recipe import load_recipe
from cloud.orchestrator import CloudOrchestrator
from edge.factory import make_controllers
from guard.action_guard import ActionGuard
from sim.faults import Scenario, load_scenario
from sim.plant import Plant
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[1]


class Controller(Protocol):
    def on_tick(self, clock: SimClock) -> None: ...


@dataclass(frozen=True)
class RunSummary:
    scenario: str
    seed: int
    n_panels: int
    fpy: float
    scrap_rate: float
    fpr: float
    cross_process_ratio: float
    downtime_ticks: int


def run(
    scenario: Scenario,
    store: TraceStore,
    bus: Bus | None = None,
    controllers: Sequence[Controller] = (),
    n_ticks: int | None = None,
    ablation: AblationConfig | None = None,
    auto_approve: bool = True,
) -> RunSummary:
    clock = SimClock()
    bus = bus if bus is not None else InMemoryBus()
    plant = Plant(scenario, bus, store, clock)
    extra = list(controllers)
    if ablation is not None:
        recipe_path = Path(scenario.recipe_path)
        recipe = load_recipe(recipe_path if recipe_path.is_absolute() else ROOT / recipe_path)
        twin = None
        if ablation.use_twin_lookahead and ablation.twin_fidelity != "none":
            twin = TwinService(
                bus,
                recipe,
                clock,
                fidelity=ablation.twin_fidelity,
                seed=scenario.seed,
            )
        guard = ActionGuard(bus, store, recipe, clock, twin, scenario.seed, ablation)
        extra = [
            *make_controllers(
                ablation,
                bus,
                recipe,
                clock,
                store,
                scenario.seed,
                guard=guard,
                auto_approve=auto_approve,
            ),
            *extra,
        ]
        if twin is not None:
            extra.append(twin)
        if ablation.use_cloud:
            extra.append(CloudOrchestrator.from_ablation(ablation, bus, recipe, clock, store, twin))
        extra.append(guard)
    for _ in range(scenario.n_ticks if n_ticks is None else n_ticks):
        plant.step_tick()
        for controller in extra:
            controller.on_tick(clock)
    plant.flush_commands()
    fpr_result = fpr(store)
    return RunSummary(
        scenario=scenario.name,
        seed=scenario.seed,
        n_panels=len(store.panels()),
        fpy=fpy(store),
        scrap_rate=scrap_rate(store),
        fpr=fpr_result.fpr,
        cross_process_ratio=fpr_result.cross_process_ratio,
        downtime_ticks=plant.downtime_ticks,
    )


def summary_json(summary: RunSummary) -> str:
    data = {
        k: None if isinstance(v, float) and math.isnan(v) else v for k, v in dataclasses.asdict(summary).items()
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def main(argv: Sequence[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m sim.runner", description="运行仿真场景并输出 RunSummary")
    parser.add_argument("scenario", help="场景 YAML 路径")
    parser.add_argument("--db", default=":memory:", help="TraceStore SQLite 路径")
    parser.add_argument("--ticks", type=int, default=None, help="覆盖场景的 n_ticks")
    parser.add_argument("--overwrite", action="store_true", help="删除已存在的 --db 文件后再运行")
    parser.add_argument("--ablation", default=None, help="消融配置 YAML 路径")
    args = parser.parse_args(argv)
    if args.db != ":memory:":
        db = Path(args.db)
        if db.exists() and not args.overwrite:
            parser.error(f"数据库文件已存在: {db}（如需覆盖请加 --overwrite）")
        for path in (db, Path(f"{db}-wal"), Path(f"{db}-shm")):
            path.unlink(missing_ok=True)
    with TraceStore(args.db) as store:
        ablation = load_ablation(args.ablation) if args.ablation else None
        print(summary_json(run(load_scenario(args.scenario), store, n_ticks=args.ticks, ablation=ablation)))


if __name__ == "__main__":
    main()
