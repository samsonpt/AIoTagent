import numpy as np

from bench.schema import TraceStore
from common import topics
from common.bus import Bus
from common.rng import make_rng
from common.topics import EQUIPMENT
from sim.aoi import PanelInspection
from sim.faults import FaultSpec, Scenario

SENDER = "plant"
NOISELESS_KEYS = {"bit_hits"}


def _sensor_fault(spec: FaultSpec, value: float, tick: int) -> float:
    p = spec.params
    match spec.type:
        case "sensor_bias":
            return value + p["offset"]
        case "sensor_drift":
            return value + p["per_tick"] * (tick - spec.start_tick)
        case "sensor_spoof":
            return float(p["value"])
    raise ValueError(f"非传感器故障: {spec.type}")


class Measurement:
    def __init__(self, scenario: Scenario, bus: Bus, store: TraceStore, sensor_faults: dict):
        self._bus = bus
        self._store = store
        self._sensor_faults = sensor_faults
        self._delay = scenario.assay_delay_ticks
        self._rng = make_rng(scenario.seed, "measurement")
        self._pending: list[tuple[int, dict]] = []

    def publish_telemetry(self, t: float, tick: int, process: str, true_values: dict[str, float]) -> None:
        observed = {}
        for key, value in true_values.items():
            v = float(value)
            if key not in NOISELESS_KEYS:
                v += self._rng.normal(0, 0.01 * abs(v))
            spec = self._sensor_faults.get((process, key))
            if spec is not None:
                v = _sensor_fault(spec, v, tick)
            observed[key] = v
        self._bus.publish(topics.telemetry(process), {"t": t, "values": observed}, SENDER)
        self._store.record_telemetry(t, process, EQUIPMENT[process], observed)

    def schedule_assay(self, t_sample: float, tick: int, values: dict) -> None:
        noisy = {k: float(v) + self._rng.normal(0, 0.02 * abs(v)) for k, v in values.items()}
        self._pending.append((tick + self._delay, {"t_sample": t_sample, "process": "plating", "values": noisy}))

    def deliver_assays(self, t: float, tick: int) -> None:
        due = [payload for due_tick, payload in self._pending if due_tick <= tick]
        self._pending = [(d, p) for d, p in self._pending if d > tick]
        for payload in due:
            self._bus.publish(topics.lab_assay(), {**payload, "t_report": t}, SENDER)

    def publish_lot_measurements(
        self, t: float, lot_id: str, panel_id: str, thickness_zones: np.ndarray, width_zones: np.ndarray
    ) -> None:
        for process, kind, zones, sigma in (
            ("plating", "thickness_um", thickness_zones, 0.3),
            ("etch", "line_width_um", width_zones, 0.5),
        ):
            noisy = np.asarray(zones, dtype=float) + self._rng.normal(0, sigma, size=np.shape(zones))
            payload = {"t": t, "kind": kind, "lot_id": lot_id, "panel_id": panel_id, "zones": noisy.tolist()}
            self._bus.publish(topics.measurement(process), payload, SENDER)

    def publish_aoi(self, t: float, lot_id: str, inspections: list[PanelInspection]) -> None:
        panels = [
            {
                "panel_id": insp.panel_id,
                "defects": [{"type": d.type, "zone": list(d.zone), "stage": d.stage} for d in insp.defects],
            }
            for insp in inspections
        ]
        self._bus.publish(topics.aoi_result(), {"t": t, "lot_id": lot_id, "panels": panels}, SENDER)
