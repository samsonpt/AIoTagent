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


def _command_scrap_only(panel) -> bool:
    defects = panel.defects or []
    return bool(defects) and all(d.get("type") == "scrapped_by_command" for d in defects)


def _lot_rows(store: TraceStore) -> list[dict]:
    grouped: dict[str, list] = {}
    for panel in store.panels():
        grouped.setdefault(panel.lot_id, []).append(panel)
    rows = []
    for lot_id, panels in grouped.items():
        if all(_command_scrap_only(p) for p in panels):
            continue
        t_aoi_ref = max(p.t_aoi for p in panels)
        defective = sum(1 for p in panels if p.defects)
        rows.append(
            {
                "lot_id": lot_id,
                "t_aoi_ref": float(t_aoi_ref),
                "rate": defective / len(panels),
                "panels": panels,
            }
        )
    rows.sort(key=lambda row: (row["t_aoi_ref"], row["lot_id"]))
    return rows


def _physical_faults(store: TraceStore):
    return [f for f in store.faults() if f.fault_type in PHYSICAL_FAULT_TYPES]


def lot_defect_rate(store: TraceStore) -> list[tuple[str, float, float]]:
    return [(row["lot_id"], row["t_aoi_ref"], row["rate"]) for row in _lot_rows(store)]


def baseline_defect_rate(store: TraceStore) -> float:
    rows = _lot_rows(store)
    faults = store.faults()
    if faults:
        t_min = min(f.t_start for f in faults)
        warmup = [row["rate"] for row in rows if row["t_aoi_ref"] < t_min]
    else:
        warmup = [row["rate"] for row in rows]
    if not warmup:
        return float("nan")
    return float(sum(warmup) / len(warmup))


def recovery_threshold(b: float, lot_size: int = 12) -> float:
    if math.isnan(b):
        return float("nan")
    return max(1.2 * b, b + 1.0 / lot_size)


def _next_process_fault_start(fault, faults) -> float:
    later = [
        other.t_start
        for other in faults
        if other.fault_id != fault.fault_id and other.process == fault.process and other.t_start > fault.t_start
    ]
    return min(later) if later else math.inf


def _lot_affected(row: dict, fault, t_next: float) -> bool:
    if not (fault.t_start <= row["t_aoi_ref"] < t_next):
        return False
    return any(
        p.root_cause_truth == fault.process and fault.t_start <= p.t_aoi < t_next for p in row["panels"]
    )


def _mttc_one(fault, rows: list[dict], faults, theta: float) -> tuple[str, float | None]:
    """返回 (recovered|no_impact|censored, 时延秒)。删失时延为 None。"""
    t_next = _next_process_fault_start(fault, faults)
    affected = [row for row in rows if _lot_affected(row, fault, t_next)]
    if not affected:
        return "no_impact", 0.0
    t_s = max(affected[-1]["t_aoi_ref"], fault.t_start + PIPELINE_LATENCY_S)
    tail = [row for row in rows if row["t_aoi_ref"] > t_s]
    if not math.isnan(theta):
        for i in range(len(tail) - 4):
            window = tail[i : i + 5]
            if all(row["rate"] <= theta for row in window):
                return "recovered", window[-1]["t_aoi_ref"] - fault.t_start
    return "censored", None


def _t_correct(fault, rows: list[dict], faults, theta: float) -> float:
    kind, delay = _mttc_one(fault, rows, faults, theta)
    if kind == "recovered" and delay is not None:
        return fault.t_start + delay
    return next((t for t in (fault.t_cleared, fault.t_end) if t is not None), math.inf)


def mttd(store: TraceStore) -> dict[str, float | int]:
    episodes = store.episodes()
    delays: list[float] = []
    n_undetected = 0
    for fault in _physical_faults(store):
        candidates = [
            ep.t_detect for ep in episodes if ep.process == fault.process and ep.t_detect >= fault.t_start
        ]
        if not candidates:
            n_undetected += 1
            continue
        delays.append(min(candidates) - fault.t_start)
    n = len(delays)
    mean = float(sum(delays) / n) if n else float("nan")
    return {"mean": mean, "n": n, "n_undetected": n_undetected}


def mttc(store: TraceStore) -> dict[str, float | int]:
    rows = _lot_rows(store)
    faults = _physical_faults(store)
    theta = recovery_threshold(baseline_defect_rate(store))
    values: list[float] = []
    n_no_impact = 0
    n_censored = 0
    for fault in faults:
        kind, delay = _mttc_one(fault, rows, faults, theta)
        if kind == "no_impact":
            n_no_impact += 1
            values.append(0.0)
        elif kind == "censored":
            n_censored += 1
        elif delay is not None:
            values.append(delay)
    n = len(values)
    mean = float(sum(values) / n) if n else float("nan")
    return {"mean": mean, "n": n, "n_no_impact": n_no_impact, "n_censored": n_censored}


def fpr(store: TraceStore) -> FprResult:
    panels = store.panels()
    faults = _physical_faults(store)
    rows = _lot_rows(store)
    theta = recovery_threshold(baseline_defect_rate(store))
    propagated = 0
    for f in faults:
        t_correct = _t_correct(f, rows, faults, theta)
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


def _as_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out):
        return None
    return out


def _latest_telemetry(store: TraceStore, process: str, equipment: str, key: str, t_max: float) -> float | None:
    best_t: float | None = None
    best: float | None = None
    for t, _proc, equip, _key, value in store.telemetry(process=process, key=key):
        if equip != equipment or t > t_max:
            continue
        number = _as_float(value)
        if number is None:
            continue
        if best_t is None or t >= best_t:
            best_t = t
            best = number
    return best


def _bit_used_rated(action, store: TraceStore) -> tuple[float | None, float | None]:
    params = action.params or {}
    used = _as_float(params.get("bit_hits"))
    rated = _as_float(params.get("bit_rated_life_hits"))
    if rated is None:
        rated = _as_float(params.get("rated_life"))
    if used is None:
        used = _latest_telemetry(store, action.process, action.equipment, "bit_hits", action.t)
    if rated is None:
        rated = _latest_telemetry(store, action.process, action.equipment, "bit_rated_life_hits", action.t)
    if rated is None:
        rated = _latest_telemetry(store, action.process, action.equipment, "rated_life", action.t)
    return used, rated


def bit_life_utilization(store: TraceStore) -> float:
    ratios: list[float] = []
    for action in store.actions():
        if not action.accepted or action.category != "bit_change":
            continue
        used, rated = _bit_used_rated(action, store)
        if used is None or rated is None or rated <= 0:
            continue
        ratios.append(used / rated)
    if not ratios:
        return float("nan")
    return float(sum(ratios) / len(ratios))


def drill_break_count(store: TraceStore) -> float:
    return float(sum(1 for fault in store.faults() if fault.fault_type == "drill_break"))


def unplanned_downtime_s(store: TraceStore) -> float:
    total = 0.0
    for action in store.actions():
        if not action.accepted or action.category != "line_stop":
            continue
        raw = (action.params or {}).get("duration_s", 1800)
        duration = 1800.0 if raw is None else _as_float(raw)
        if duration is None:
            duration = 1800.0
        total += duration
    return total


def dosing_consumption(store: TraceStore) -> float:
    total = 0.0
    for action in store.actions():
        if not action.accepted or action.category != "dosing":
            continue
        amount = _as_float((action.params or {}).get("amount"))
        if amount is not None:
            total += amount
    return total


def envelope_violations(store: TraceStore) -> float:
    return float(sum(1 for episode in store.episodes() if episode.violated))


def _fault_active_until(fault) -> float:
    stops = [stamp for stamp in (fault.t_end, fault.t_cleared) if stamp is not None]
    return min(stops) if stops else math.inf


def _preventive_action(action) -> bool:
    reason = action.reason or ""
    return "预防" in reason or "preventive" in reason.lower()


def false_action_count(store: TraceStore) -> float:
    faults = store.faults()
    count = 0
    for action in store.actions():
        if not action.accepted or _preventive_action(action):
            continue
        active = any(
            fault.process == action.process
            and fault.fault_type in PHYSICAL_FAULT_TYPES
            and fault.t_start <= action.t < _fault_active_until(fault)
            for fault in faults
        )
        if not active:
            count += 1
    return float(count)


def decision_latency(store: TraceStore) -> dict[str, float]:
    delays = [ep.t_decide - ep.t_detect for ep in store.episodes() if ep.t_decide is not None]
    if not delays:
        return {"p50": float("nan"), "p95": float("nan")}
    arr = np.asarray(delays, dtype=float)
    return {"p50": float(np.percentile(arr, 50)), "p95": float(np.percentile(arr, 95))}


def _panel_fpy(panels) -> float:
    if not panels:
        return float("nan")
    return sum(1 for panel in panels if not panel.defects) / len(panels)


def _in_outage(t_aoi: float, faults) -> bool:
    for fault in faults:
        if fault.fault_type != "network_outage":
            continue
        hi = math.inf if fault.t_end is None else fault.t_end + PIPELINE_LATENCY_S
        if fault.t_start <= t_aoi <= hi:
            return True
    return False


def outage_fpy_retention(store: TraceStore) -> float:
    outages = [fault for fault in store.faults() if fault.fault_type == "network_outage"]
    if not outages:
        return float("nan")
    inside = [panel for panel in store.panels() if _in_outage(panel.t_aoi, outages)]
    outside = [panel for panel in store.panels() if not _in_outage(panel.t_aoi, outages)]
    numerator = _panel_fpy(inside)
    denominator = _panel_fpy(outside)
    if math.isnan(numerator) or math.isnan(denominator) or denominator == 0:
        return float("nan")
    return float(numerator / denominator)
