import copy
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from bench.schema import FaultRecord, TraceStore
from common.bus import Bus
from common.topics import EQUIPMENT

FAULT_DEFAULTS: dict[str, dict] = {
    "drill_abnormal_wear": {"wear_multiplier": 1.6},
    "drill_break": {},
    "additive_depletion": {"consumption_multiplier": 2.0},
    "rectifier_low": {"row": 0, "factor": 0.8},
    "rectifier_high": {"row": 0, "factor": 1.25},
    "etch_sg_drift": {"sg_per_tick": 0.004},
    "nozzle_clog": {"zone": 2, "factor": 0.5},
    "sensor_drift": {"key": "", "per_tick": 0.0},
    "sensor_bias": {"key": "", "offset": 0.0},
    "sensor_spoof": {"key": "", "value": 0.0},
    "network_outage": {"clients": []},
}

_DRILL_DEFECTS = {"hole_wall", "hole_missing"}
_ETCH_DEFECTS = {"width_under", "width_over", "open", "residue", "short"}
_PLATING_DEFECTS = {"thin_copper"} | _ETCH_DEFECTS

FAULT_DEFECT_LINKS: dict[str, set[str]] = {
    "drill_abnormal_wear": _DRILL_DEFECTS,
    "drill_break": _DRILL_DEFECTS,
    "additive_depletion": _PLATING_DEFECTS,
    "rectifier_low": _PLATING_DEFECTS,
    "rectifier_high": _ETCH_DEFECTS,
    "etch_sg_drift": _ETCH_DEFECTS,
    "nozzle_clog": _ETCH_DEFECTS,
    "sensor_drift": set(),
    "sensor_bias": set(),
    "sensor_spoof": set(),
    "network_outage": set(),
}

FAULT_PROCESS = {
    "drill_abnormal_wear": "drill",
    "drill_break": "drill",
    "additive_depletion": "plating",
    "rectifier_low": "plating",
    "rectifier_high": "plating",
    "etch_sg_drift": "etch",
    "nozzle_clog": "etch",
}

SENSOR_FAULTS = ("sensor_drift", "sensor_bias", "sensor_spoof")

REMEDIES: dict[str, tuple[str, ...]] = {
    "clean_nozzle": ("nozzle_clog",),
    "repair_rectifier": ("rectifier_low", "rectifier_high"),
    "repair_regenerator": ("etch_sg_drift",),
    "change_bit": ("drill_break",),
}


class FaultSpec(BaseModel):
    fault_id: str
    type: str
    process: str
    start_tick: int
    end_tick: int | None = None
    params: dict = {}

    @model_validator(mode="after")
    def _validate(self) -> "FaultSpec":
        if self.type not in FAULT_DEFAULTS:
            raise ValueError(f"未知故障类型: {self.type}")
        expected = FAULT_PROCESS.get(self.type)
        if expected is not None and self.process != expected:
            raise ValueError(f"{self.type} 只能作用于 {expected}，实际为 {self.process}")
        if self.end_tick is not None and self.end_tick <= self.start_tick:
            raise ValueError(f"end_tick {self.end_tick} 必须大于 start_tick {self.start_tick}")
        unknown = set(self.params) - set(FAULT_DEFAULTS[self.type])
        if unknown:
            raise ValueError(f"{self.type} 不支持参数 {sorted(unknown)}")
        self.params = {**copy.deepcopy(FAULT_DEFAULTS[self.type]), **self.params}
        return self

    @property
    def physical(self) -> bool:
        return bool(FAULT_DEFECT_LINKS[self.type])


class Scenario(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    seed: int
    n_ticks: int
    model_mismatch: Literal["low", "mid", "high"] = "mid"
    recipe_path: str = "bench/recipes/PN-4L-001.yaml"
    faults: list[FaultSpec] = []
    assay_every_ticks: int = 8
    assay_delay_ticks: int = 2

    @model_validator(mode="after")
    def _unique_fault_ids(self) -> "Scenario":
        ids = [f.fault_id for f in self.faults]
        if len(ids) != len(set(ids)):
            raise ValueError("fault_id 重复")
        return self


def load_scenario(path: str | Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _physical_target(spec: FaultSpec) -> tuple[str, int | None, object]:
    p = spec.params
    match spec.type:
        case "drill_abnormal_wear":
            return "wear_multiplier", None, p["wear_multiplier"]
        case "drill_break":
            return "broken", None, True
        case "additive_depletion":
            return "consumption_multiplier", None, p["consumption_multiplier"]
        case "rectifier_low" | "rectifier_high":
            return "rect_factor", int(p["row"]), p["factor"]
        case "etch_sg_drift":
            return "sg_drift_per_tick", None, p["sg_per_tick"]
        case "nozzle_clog":
            return "clog_factor", int(p["zone"]), p["factor"]
    raise ValueError(f"非物理故障: {spec.type}")


def _get(station: object, attr: str, index: int | None):
    value = getattr(station, attr)
    return value if index is None else value[index]


def _set(station: object, attr: str, index: int | None, value) -> None:
    if index is None:
        setattr(station, attr, value)
    else:
        getattr(station, attr)[index] = value


class FaultInjector:
    def __init__(self, scenario: Scenario, store: TraceStore):
        self._specs = {f.fault_id: f for f in scenario.faults}
        self._store = store
        self._saved: dict[str, tuple[object, str, int | None, object]] = {}
        self._cleared: set[str] = set()

    def apply(self, tick: int, t: float, stations: dict[str, object], bus: Bus, sensor_faults: dict) -> None:
        for spec in self._specs.values():
            if tick == spec.start_tick:
                self._activate(spec, t, stations, bus, sensor_faults)
            if tick == spec.end_tick:
                self._deactivate(spec, t, bus, sensor_faults)

    def _activate(self, spec: FaultSpec, t: float, stations: dict[str, object], bus: Bus, sensor_faults: dict) -> None:
        if spec.type == "network_outage":
            for client in spec.params["clients"]:
                bus.set_link(client, False)
            equipment = "network"
        elif spec.type in SENSOR_FAULTS:
            sensor_faults[(spec.process, spec.params["key"])] = spec
            equipment = EQUIPMENT[spec.process]
        else:
            station = stations[spec.process]
            attr, index, value = _physical_target(spec)
            self._saved[spec.fault_id] = (station, attr, index, _get(station, attr, index))
            _set(station, attr, index, value)
            equipment = EQUIPMENT[spec.process]
        self._store.record_fault(
            FaultRecord(
                fault_id=spec.fault_id,
                process=spec.process,
                equipment=equipment,
                fault_type=spec.type,
                params=spec.params,
                t_start=t,
            )
        )

    def _deactivate(self, spec: FaultSpec, t: float, bus: Bus, sensor_faults: dict) -> None:
        if spec.type == "network_outage":
            for client in spec.params["clients"]:
                bus.set_link(client, True)
        elif spec.type in SENSOR_FAULTS:
            sensor_faults.pop((spec.process, spec.params["key"]), None)
        saved = self._saved.pop(spec.fault_id, None)
        if saved is not None and spec.fault_id not in self._cleared:
            station, attr, index, original = saved
            _set(station, attr, index, original)
        self._store.update_fault(spec.fault_id, t_end=t)

    def clear(self, fault_id: str, t: float, cleared_by: str) -> None:
        if fault_id not in self._specs:
            raise KeyError(fault_id)
        if fault_id in self._cleared:
            return
        self._store.update_fault(fault_id, t_cleared=t, cleared_by=cleared_by)
        self._cleared.add(fault_id)

    def active(self, process: str, tick: int) -> list[FaultSpec]:
        return [
            spec
            for spec in self._specs.values()
            if spec.physical
            and spec.process == process
            and spec.start_tick <= tick
            and (spec.end_tick is None or tick < spec.end_tick)
            and spec.fault_id not in self._cleared
        ]
