import argparse
import dataclasses
import json
import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from bench.metrics import fpr, fpy, scrap_rate
from bench.schema import TraceStore
from common.bus import Bus, InMemoryBus
from common.clock import SimClock
from sim.faults import Scenario, load_scenario
from sim.plant import Plant


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
) -> RunSummary:
    clock = SimClock()
    plant = Plant(scenario, bus if bus is not None else InMemoryBus(), store, clock)
    for _ in range(scenario.n_ticks if n_ticks is None else n_ticks):
        plant.step_tick()
        for controller in controllers:
            controller.on_tick(clock)
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
    parser = argparse.ArgumentParser(prog="python -m sim.runner", description="运行仿真场景并输出 RunSummary")
    parser.add_argument("scenario", help="场景 YAML 路径")
    parser.add_argument("--db", default=":memory:", help="TraceStore SQLite 路径")
    parser.add_argument("--ticks", type=int, default=None, help="覆盖场景的 n_ticks")
    args = parser.parse_args(argv)
    with TraceStore(args.db) as store:
        print(summary_json(run(load_scenario(args.scenario), store, n_ticks=args.ticks)))


if __name__ == "__main__":
    main()
