import math
from dataclasses import dataclass

from bench.schema import TraceStore

PIPELINE_LATENCY_S = 3 * 1800.0

# 与 sim.faults.FAULT_DEFECT_LINKS 中非空键保持一致（bench 不依赖 sim，由测试校验）
PHYSICAL_FAULT_TYPES = frozenset(
    {
        "drill_abnormal_wear",
        "drill_break",
        "additive_depletion",
        "rectifier_low",
        "etch_sg_drift",
        "nozzle_clog",
    }
)


@dataclass(frozen=True)
class FprResult:
    fpr: float
    cross_process_ratio: float
    n_faults: int


def _ratio(num: int, den: int) -> float:
    return num / den if den else float("nan")


def fpy(store: TraceStore) -> float:
    panels = store.panels()
    return _ratio(sum(1 for p in panels if not p.defects), len(panels))


def scrap_rate(store: TraceStore) -> float:
    panels = store.panels()
    return _ratio(sum(1 for p in panels if p.scrapped), len(panels))


def fpr(store: TraceStore) -> FprResult:
    panels = store.panels()
    faults = [f for f in store.faults() if f.fault_type in PHYSICAL_FAULT_TYPES]
    propagated = 0
    for f in faults:
        t_correct = next((t for t in (f.t_cleared, f.t_end) if t is not None), math.inf)
        if any(
            p.root_cause_truth == f.process and f.t_start <= p.t_aoi <= t_correct + PIPELINE_LATENCY_S
            for p in panels
        ):
            propagated += 1
    stages = [(d["stage"], p.root_cause_truth) for p in panels if p.root_cause_truth != "none" for d in p.defects]
    return FprResult(
        fpr=_ratio(propagated, len(faults)),
        cross_process_ratio=_ratio(sum(1 for stage, root in stages if stage != root), len(stages)),
        n_faults=len(faults),
    )
