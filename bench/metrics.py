import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from bench.schema import TraceStore

PIPELINE_LATENCY_S = 3 * 1800.0

# 与 sim.faults.FAULT_DEFECT_LINKS 中非空键保持一致（bench 不依赖 sim，由测试校验）
PHYSICAL_FAULT_TYPES = frozenset(
    {
        "drill_abnormal_wear",
        "drill_break",
        "additive_depletion",
        "rectifier_low",
        "rectifier_high",
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


def mape(y: np.ndarray, yhat: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    mask = np.abs(y) >= 1e-9
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs(yhat[mask] - y[mask]) / np.abs(y[mask])) * 100.0)


def coverage90(y: np.ndarray, q05: np.ndarray, q95: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    q05 = np.asarray(q05, dtype=float)
    q95 = np.asarray(q95, dtype=float)
    if y.size == 0:
        return float("nan")
    return float(np.mean((y >= q05) & (y <= q95)))


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
    caused = [(d["stage"], d.get("cause", p.root_cause_truth)) for p in panels for d in p.defects]
    caused = [(stage, cause) for stage, cause in caused if cause != "none"]
    return FprResult(
        fpr=_ratio(propagated, len(faults)),
        cross_process_ratio=_ratio(sum(1 for stage, cause in caused if stage != cause), len(caused)),
        n_faults=len(faults),
    )


def root_cause_top1(predicted: list[str], truth: list[str]) -> float:
    if not predicted or not truth or len(predicted) != len(truth):
        return float("nan")
    hits = sum(1 for p, t in zip(predicted, truth) if p == t)
    return float(hits / len(truth))


def action_accept_rate(
    actions: list[str],
    acceptable: list[set[str] | list[str]],
) -> float:
    if not actions or not acceptable or len(actions) != len(acceptable):
        return float("nan")
    hits = sum(1 for action, ok in zip(actions, acceptable) if action in set(ok))
    return float(hits / len(actions))


def llm_usage(details: Iterable[dict]) -> dict[str, float]:
    calls = 0.0
    tokens = 0.0
    for detail in details:
        calls += float(detail.get("llm_calls", 0) or 0)
        token_val = detail.get("llm_tokens", detail.get("tokens", detail.get("token_usage", 0)))
        if isinstance(token_val, dict):
            tokens += float(token_val.get("total", 0) or 0)
        else:
            tokens += float(token_val or 0)
    return {"llm_calls": calls, "llm_tokens": tokens}
