from __future__ import annotations

import copy
import logging

from bench.schema import SOURCES, ActionRecord
from common import topics
from common.config import AblationConfig
from common.topics import EQUIPMENT, PROCESSES
from edge.envelope import bound_command
from guard.policy import (
    FAST_PATH,
    confidence_from_twin,
    is_high_risk,
    map_command_to_twin,
    passes_twin_gate,
)

logger = logging.getLogger(__name__)

# Duplicated from Plant — guard must not import sim.
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

_LINE_COMMANDS = frozenset({"hold_lot", "scrap_lot"})


class ActionGuard:
    client_id = "guard"

    def __init__(self, bus, store, recipe, clock, twin, seed: int, ablation: AblationConfig):
        self._bus = bus
        self._store = store
        self._recipe = recipe
        self._clock = clock
        self._twin = twin
        self._seed = seed
        self._ablation = ablation
        self.auto_actions_enabled = True
        bus.subscribe("plant/+/+/command", self._on_command, self.client_id)
        bus.subscribe(topics.line_command(), self._on_command, self.client_id)

    def on_tick(self, clock) -> None:
        return None

    def resolve_approval(self, request_id: str, approved: bool, reason: str) -> None:
        rows = [r for r in self._store.list_approvals() if r["request_id"] == request_id]
        if not rows:
            raise KeyError(request_id)
        row = rows[0]
        topic = row["topic"]
        payload = {
            "command": row["command"],
            "params": dict(row["params"]),
            "source": row["source"],
            "reason": reason,
        }
        if approved:
            self._stamp_forward(topic, payload, reason)
        else:
            self._reject(
                process=row["process"],
                equipment=row["equipment"],
                command=row["command"],
                params=dict(row["params"]),
                source=row["source"],
                reason=reason,
                lot_id=row["lot_id"],
            )

    def _on_command(self, topic: str, payload: dict) -> None:
        if payload.get("guarded") is True:
            return

        process, equipment = self._parse_topic(topic)
        command = str(payload.get("command", ""))
        params = dict(payload.get("params") or {})
        source = str(payload.get("source", "edge"))
        lot_id = str(params.get("lot_id") or "default")

        if command in FAST_PATH:
            if not self._topic_ok(process, equipment):
                self._reject(process, equipment, command, params, source, "invalid_target", lot_id)
                return
            self._stamp_forward(topic, payload, "fast_path")
            return

        if not self.auto_actions_enabled:
            self._reject(
                process, equipment, command, params, source, "chain_invalid_auto_disabled", lot_id
            )
            return

        # Gate 1 — envelope / line allow-list
        if process == "line":
            if command not in _LINE_COMMANDS:
                self._reject(
                    process, equipment, command, params, source, "line_command_not_allowed", lot_id
                )
                return
        else:
            if not self._topic_ok(process, equipment):
                self._reject(process, equipment, command, params, source, "invalid_target", lot_id)
                return
            bounded = bound_command(self._recipe, process, command, params)
            if bounded is None:
                self._reject(process, equipment, command, params, source, "envelope_reject", lot_id)
                return
            command, params = bounded
            payload = {**payload, "command": command, "params": params}

        needs_approval = False

        # Gate 2 — twin lookahead (§4.3: no twin / disabled → no auto-stamp)
        if not self._ablation.use_twin_lookahead or self._twin is None:
            twin_reason = (
                "twin_disabled" if not self._ablation.use_twin_lookahead else "no_twin"
            )
            if self._ablation.use_human_gate:
                self._enqueue(topic, process, equipment, command, params, source, lot_id)
                return
            self._reject(process, equipment, command, params, source, twin_reason, lot_id)
            return

        mapped = map_command_to_twin(self._recipe, process, command, params)
        if mapped is None:
            logger.warning("twin map skip command=%s process=%s", command, process)
        else:
            kind, sim_params = mapped
            if self._ablation.use_twin_confidence_gate:
                confidence = confidence_from_twin(self._twin)
                if confidence < 0.5:
                    needs_approval = True
                else:
                    pred = self._twin.simulate(sim_params, kind=kind)
                    if not passes_twin_gate(pred, confidence):
                        needs_approval = True
            else:
                confidence = 1.0
                pred = self._twin.simulate(sim_params, kind=kind)
                if not passes_twin_gate(pred, confidence):
                    needs_approval = True

        # Gate 3 — human / high-risk
        if needs_approval or is_high_risk(self._recipe, process, command, params):
            if self._ablation.use_human_gate:
                self._enqueue(topic, process, equipment, command, params, source, lot_id)
                return
            self._stamp_forward(topic, payload, "high_risk_auto")
            return

        self._stamp_forward(topic, payload, "passed_gates")

    def _stamp_forward(self, topic: str, payload: dict, reason: str) -> None:
        stamped = copy.deepcopy(payload)
        stamped["guarded"] = True
        stamped["guard_id"] = "guard"
        stamped["guard_reason"] = reason
        self._bus.publish(topic, stamped, self.client_id)

        process, equipment = self._parse_topic(topic)
        command = str(stamped.get("command", ""))
        params = dict(stamped.get("params") or {})
        source = str(stamped.get("source", "edge"))
        lot_id = str(params.get("lot_id") or "default")
        action_id = self._record(process, equipment, command, params, source, True, reason)
        self._store.append_chain(
            lot_id=lot_id,
            kind="act",
            ref=str(action_id),
            t=self._clock.now,
            payload={
                "command": command,
                "accepted": True,
                "reason": reason,
                "process": process,
                "equipment": equipment,
            },
        )

    def _reject(
        self,
        process: str,
        equipment: str,
        command: str,
        params: dict,
        source: str,
        reason: str,
        lot_id: str,
    ) -> None:
        full_reason = f"rejected_by=guard;{reason}"
        action_id = self._record(process, equipment, command, params, source, False, full_reason)
        self._store.append_chain(
            lot_id=lot_id,
            kind="act",
            ref=str(action_id),
            t=self._clock.now,
            payload={
                "command": command,
                "accepted": False,
                "reason": full_reason,
                "process": process,
                "equipment": equipment,
            },
        )

    def _enqueue(
        self,
        topic: str,
        process: str,
        equipment: str,
        command: str,
        params: dict,
        source: str,
        lot_id: str,
    ) -> None:
        self._store.enqueue_approval(
            t_submit=self._clock.now,
            process=process,
            equipment=equipment,
            command=command,
            params=params,
            source=source,
            lot_id=lot_id,
            topic=topic,
        )

    def _record(
        self,
        process: str,
        equipment: str,
        command: str,
        params: dict,
        source: str,
        accepted: bool,
        reason: str,
    ) -> int:
        if source not in SOURCES:
            source = "rule"
        return self._store.record_action(
            ActionRecord(
                t=self._clock.now,
                process=process,
                equipment=equipment,
                command=command,
                params=params,
                source=source,
                category=COMMAND_CATEGORY.get(command, "param_tune"),
                affected_panels=self._recipe.lot_size,
                accepted=accepted,
                reason=reason,
            )
        )

    @staticmethod
    def _parse_topic(topic: str) -> tuple[str, str]:
        levels = topic.split("/")
        process = levels[1]
        equipment = "line" if process == "line" else levels[2]
        return process, equipment

    @staticmethod
    def _topic_ok(process: str, equipment: str) -> bool:
        if process == "line":
            return equipment == "line"
        return process in PROCESSES and EQUIPMENT.get(process) == equipment
