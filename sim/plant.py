from dataclasses import dataclass, field
from pathlib import Path

from bench.schema import SOURCES, ActionRecord, TraceStore
from common import topics
from common.bus import Bus
from common.clock import SimClock
from common.recipe import load_recipe
from common.rng import make_rng
from common.topics import EQUIPMENT, PROCESSES
from sim.aoi import inspect_lot
from sim.drill import DrillResult, DrillStation
from sim.etch import EtchResult, EtchStation
from sim.faults import REMEDIES, FaultInjector, FaultSpec, Scenario
from sim.measurement import Measurement
from sim.mes import Lot, Mes
from sim.plating import PlatingResult, PlatingStation

ROOT = Path(__file__).resolve().parents[1]

COMMAND_CATEGORY: dict[str, str] = {
    "change_bit": "bit_change",
    "set_rpm": "param_tune",
    "set_feed": "param_tune",
    "set_current_density": "param_tune",
    "set_bath_temp": "param_tune",
    "set_conveyor_speed": "param_tune",
    "set_etch_temp": "param_tune",
    "set_spray_pressure": "param_tune",
    "dose_additive": "dosing",
    "adjust_sg": "dosing",
    "clean_nozzle": "maintenance",
    "repair_rectifier": "maintenance",
    "repair_regenerator": "maintenance",
    "stop": "line_stop",
    "resume": "line_stop",
    "hold_lot": "lot_hold",
    "scrap_lot": "scrap",
}
LINE_COMMANDS = ("hold_lot", "scrap_lot")


@dataclass
class _InFlight:
    lot: Lot
    active_faults: dict[str, list[FaultSpec]] = field(default_factory=dict)
    drill: DrillResult | None = None
    plating: PlatingResult | None = None
    etch: EtchResult | None = None


class Plant:
    def __init__(self, scenario: Scenario, bus: Bus, store: TraceStore, clock: SimClock):
        recipe_path = Path(scenario.recipe_path)
        self._recipe = load_recipe(recipe_path if recipe_path.is_absolute() else ROOT / recipe_path)
        self._scenario = scenario
        self._bus = bus
        self._store = store
        self._clock = clock
        seed, m = scenario.seed, scenario.model_mismatch
        self.stations = {
            "drill": DrillStation(self._recipe, m, make_rng(seed, "drill")),
            "plating": PlatingStation(self._recipe, m, make_rng(seed, "plating")),
            "etch": EtchStation(self._recipe, m, make_rng(seed, "etch")),
        }
        self._aoi_rng = make_rng(seed, "aoi")
        self._rated_life = int(self._recipe.constants["bit_rated_life_hits"])
        self._mes = Mes(self._recipe)
        self._injector = FaultInjector(scenario, store)
        self._sensor_faults: dict = {}
        self._measurement = Measurement(scenario, bus, store, self._sensor_faults)
        self._lots: dict[str, _InFlight] = {}
        self._stage: dict[str, _InFlight | None] = {"drill": None, "plating": None, "etch": None}
        self._applied_tick = -1
        self.downtime_ticks = 0
        bus.subscribe("plant/+/+/command", self._on_command, "plant")
        bus.subscribe(topics.line_command(), self._on_command, "plant")

    def step_tick(self) -> None:
        clock = self._clock
        tick = clock.tick
        self._injector.apply(tick, clock.now, self.stations, self._bus, self._sensor_faults)
        self._applied_tick = tick
        self.stations["etch"].tick_update()

        sums = {p: {} for p in PROCESSES}
        for _ in range(clock.substeps):
            for process in PROCESSES:
                values = self.stations[process].telemetry()
                self._measurement.publish_telemetry(clock.now, tick, process, values)
                acc = sums[process]
                for k, v in values.items():
                    acc[k] = acc.get(k, 0.0) + v
            clock.advance_substep()
        means = {p: {k: v / clock.substeps for k, v in acc.items()} for p, acc in sums.items()}

        if any(s.stopped for s in self.stations.values()):
            self.downtime_ticks += 1
        else:
            self._advance_pipeline(tick, means)

        if tick % self._scenario.assay_every_ticks == 0:
            self._measurement.schedule_assay(clock.now, tick, self.stations["plating"].assay())
        self._measurement.deliver_assays(clock.now, tick)

    def _take(self, stage: str) -> _InFlight | None:
        item = self._stage[stage]
        self._stage[stage] = None
        if item is None or item.lot.lot_id not in self._lots or self._mes.is_held(item.lot.lot_id):
            return None
        return item

    def _record(self, item: _InFlight, process: str, tick: int, params: dict, means: dict) -> None:
        item.active_faults[process] = self._injector.active(process, tick)
        self._mes.record_step(item.lot.lot_id, process, {**means[process], **params})

    def _advance_pipeline(self, tick: int, means: dict) -> None:
        t = self._clock.now
        item = self._take("etch")
        if item is not None:
            inspections = inspect_lot(
                item.lot.panel_ids, item.drill, item.plating, item.etch, self._recipe, self._aoi_rng, item.active_faults
            )
            self._mes.finish(item.lot, t, inspections, self._store)
            self._measurement.publish_lot_measurements(
                t, item.lot.lot_id, item.lot.panel_ids[0], item.plating.thickness_um[0], item.etch.width_um[0]
            )
            self._measurement.publish_aoi(t, item.lot.lot_id, inspections)
            del self._lots[item.lot.lot_id]

        item = self._take("plating")
        if item is not None:
            item.etch = self.stations["etch"].process_lot(item.lot.lot_id, item.plating.thickness_um)
            self._record(item, "etch", tick, item.etch.params, means)
            self._stage["etch"] = item

        item = self._take("drill")
        if item is not None:
            item.plating = self.stations["plating"].process_lot(item.lot.lot_id)
            self._record(item, "plating", tick, item.plating.params, means)
            self._stage["plating"] = item

        item = _InFlight(self._mes.release_lot(t))
        self._lots[item.lot.lot_id] = item
        item.drill = self.stations["drill"].process_lot(item.lot.lot_id)
        self._record(item, "drill", tick, item.drill.params, means)
        self._stage["drill"] = item
        if item.drill.params["bit_hits"] >= self._rated_life:
            self._clear_active("drill", "drill_break", "machine")

    def _clear_active(self, process: str, fault_type: str, cleared_by: str, zone: int | None = None) -> None:
        for spec in self._injector.active(process, self._applied_tick):
            if spec.type == fault_type and (zone is None or spec.params["zone"] == zone):
                self._injector.clear(spec.fault_id, self._clock.now, cleared_by)

    def _on_command(self, topic: str, payload: dict) -> None:
        levels = topic.split("/")
        process = levels[1]
        equipment = "line" if process == "line" else levels[2]
        command = str(payload.get("command", ""))
        params = payload.get("params", {})
        source = payload.get("source")
        category = COMMAND_CATEGORY.get(command, "param_tune")
        affected = self._recipe.lot_size
        accepted, reason = False, str(payload.get("reason", ""))

        if source not in SOURCES:
            source, reason = "rule", f"非法指令来源 {source!r}"
        elif command not in COMMAND_CATEGORY:
            reason = f"未知指令 {command!r}"
        elif process == "line":
            if command not in LINE_COMMANDS:
                reason = f"整线主题不支持指令 {command!r}"
            else:
                try:
                    affected = self._lot_command(command, params)
                    accepted = True
                except (KeyError, ValueError) as e:
                    reason = f"批次指令失败: {e}"
        elif process not in self.stations or EQUIPMENT[process] != equipment:
            reason = f"未知设备 {process}/{equipment}"
        else:
            try:
                self.stations[process].apply(command, params)
                accepted = True
            except (KeyError, ValueError) as e:
                reason = str(e)
            if accepted and command in REMEDIES:
                zone = params["zone"] if command == "clean_nozzle" else None
                self._clear_active(process, REMEDIES[command], source, zone)

        self._store.record_action(
            ActionRecord(
                t=self._clock.now,
                process=process,
                equipment=equipment,
                command=command,
                params=params if isinstance(params, dict) else {},
                source=source,
                category=category,
                affected_panels=affected,
                accepted=accepted,
                reason=reason,
            )
        )

    def _lot_command(self, command: str, params: object) -> int:
        lot_id = params.get("lot_id") if isinstance(params, dict) else None
        item = self._lots.get(lot_id) if isinstance(lot_id, str) else None
        if item is None:
            raise KeyError(f"批次 {lot_id!r} 不存在或已完成")
        if command == "hold_lot":
            self._mes.hold(lot_id)
        else:
            self._mes.scrap(item.lot, self._clock.now, self._store)
            del self._lots[lot_id]
        return len(item.lot.panel_ids)
