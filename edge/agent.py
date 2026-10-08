from collections import defaultdict

from bench.schema import EpisodeRecord, TraceStore
from common import topics
from common.bus import Bus
from common.clock import SimClock
from common.intents import Intent
from common.recipe import Recipe
from edge.envelope import bound_command
from edge.ocap import commands_for
from edge.spc import BASELINE_TICKS, ChannelStats, Ewma, TickAggregator, western_electric

SPOOF_VAR = 1e-12
COOLDOWN = 3


class ProcessEdgeAgent:
    def __init__(
        self,
        process: str,
        bus: Bus,
        recipe: Recipe,
        clock: SimClock,
        store: TraceStore | None = None,
        *,
        feedforward: bool = True,
        confidence_modulation: bool = True,
        emit_commands: bool = True,
        seed: int = 0,
    ):
        self.process = process
        self.bus = bus
        self.recipe = recipe
        self.clock = clock
        self.store = store
        self.feedforward = feedforward
        self.confidence_modulation = confidence_modulation
        self.emit_commands = emit_commands
        self.seed = seed
        self.client_id = f"edge-{process}"
        self._agg = TickAggregator()
        self._stats: dict[str, ChannelStats] = defaultdict(ChannelStats)
        self._ewma: dict[str, Ewma] = {}
        self._history: dict[str, list[float]] = defaultdict(list)
        self._cooldown: dict[str, int] = {}
        self._event_queue: list[dict] = []
        self._confidence = "high"
        self._daily_dose = 0.0
        self._dose_day = 0
        self._bit_armed = True
        bus.subscribe(topics.telemetry(process), self._on_telemetry, self.client_id)
        if process == "plating":
            bus.subscribe(topics.lab_assay(), self._on_assay, self.client_id)
            bus.subscribe(topics.measurement("plating"), self._on_thickness, self.client_id)
        if process == "etch":
            bus.subscribe(topics.measurement("etch"), self._on_width, self.client_id)
            bus.subscribe(topics.intents("etch"), self._on_intent, self.client_id)

    def _on_telemetry(self, topic: str, payload: dict) -> None:
        self._agg.add(int(payload["tick"]), payload["values"])

    def _on_assay(self, topic: str, payload: dict) -> None:
        if payload.get("process") != "plating":
            return
        values = payload["values"]
        additive = float(values["additive_ml_l"])
        window = self.recipe.window("plating", "additive_ml_l")
        if additive < window.min:
            self._handle_signal(int(payload["tick"]), "additive_ml_l", additive, "assay")

    def _on_thickness(self, topic: str, payload: dict) -> None:
        if not self.feedforward or self._confidence != "high":
            return
        zones = payload["zones"]
        flat = [v for row in zones for v in row] if zones and isinstance(zones[0], list) else list(zones)
        mean = sum(flat) / len(flat)
        intent = Intent(
            intent="copper_thickness",
            target_process="etch",
            lot_id=payload.get("lot_id"),
            params={"thickness_mean": mean},
            source="peer",
            rationale="电镀铜厚前馈",
        )
        self.bus.publish(topics.intents("etch"), intent.model_dump(), self.client_id)

    def _on_width(self, topic: str, payload: dict) -> None:
        zones = payload["zones"]
        lo = self.recipe.specs["line_width_um"].min or 0.0
        cols = list(zip(*zones)) if zones and isinstance(zones[0], list) else []
        for j, col in enumerate(cols):
            mean = sum(col) / len(col)
            if mean < lo:
                self._handle_signal(int(payload["tick"]), f"width_col_{j}", mean, "width")

    def _on_intent(self, topic: str, payload: dict) -> None:
        if not self.feedforward or self._confidence != "high":
            return
        intent = Intent.model_validate(payload)
        if intent.intent != "copper_thickness":
            return
        t_mean = float(intent.params["thickness_mean"])
        nom = self.recipe.specs["copper_thickness_um"].target
        target = self.recipe.window("etch", "conveyor_speed_m_min").target
        speed = target * (nom / max(t_mean, 1e-6))
        bound = bound_command(self.recipe, "etch", "set_conveyor_speed", {"m_min": speed})
        if bound:
            self._publish_command(bound[0], bound[1], "peer", "铜厚前馈")

    def _flush_replay(self) -> None:
        if not self.bus.is_link_up(self.client_id) or not self._event_queue:
            return
        queued, self._event_queue = self._event_queue, []
        for event in queued:
            event = {**event, "replayed": True}
            self.bus.publish(topics.events(self.process), event, self.client_id)

    def _emit_event(self, event: dict) -> None:
        if not self.bus.is_link_up(self.client_id):
            self._event_queue.append(event)
            return
        self.bus.publish(topics.events(self.process), event, self.client_id)

    def _publish_command(self, command: str, params: dict, source: str, reason: str) -> None:
        if not self.emit_commands:
            return
        bound = bound_command(self.recipe, self.process, command, params)
        if bound is None:
            return
        command, params = bound
        self.bus.publish(
            topics.command(self.process),
            {"command": command, "params": params, "source": source, "reason": reason},
            self.client_id,
        )

    def _handle_signal(self, tick: int, key: str, value: float, rule: str) -> None:
        if self._cooldown.get(key, 0) > 0:
            return
        event = {
            "t": self.clock.now,
            "tick": tick,
            "process": self.process,
            "key": key,
            "rule": rule,
            "value": value,
            "replayed": False,
        }
        self._emit_event(event)
        self._cooldown[key] = COOLDOWN
        allow_tune = self._confidence == "high"
        for command, params, reason in commands_for(self.process, key, value, self.recipe):
            if command.startswith("set_") and not allow_tune:
                continue
            source = "edge"
            self._publish_command(command, params, source, reason)
            if self.store is not None:
                self.store.record_episode(
                    EpisodeRecord(
                        episode_id=f"{self.process}-{tick}-{key}",
                        process=self.process,
                        trigger=rule,
                        t_detect=self.clock.now,
                        handler="edge" if self.emit_commands else "human",
                    )
                )

    def on_tick(self, clock: SimClock) -> None:
        self.clock = clock
        self._flush_replay()
        finished = clock.tick - 1
        if finished < 0:
            return
        means = self._agg.mean_of(finished)
        if means is None:
            return
        spoof = any(
            (var := self._agg.variance_of(finished, key)) is not None and var < SPOOF_VAR for key in means
        )
        if self.confidence_modulation and spoof:
            self._confidence = "low"
        rated = float(self.recipe.constants["bit_rated_life_hits"])
        if self.process == "drill" and means.get("bit_hits", 0) >= 0.9 * rated and self._bit_armed:
            self._publish_command("change_bit", {}, "edge", "钻针接近寿命")
            self._bit_armed = False
        if self.process == "drill" and means.get("bit_hits", rated) < 0.1 * rated:
            self._bit_armed = True
        day = finished // 48
        if day != self._dose_day:
            self._dose_day, self._daily_dose = day, 0.0
        for key, value in means.items():
            stats = self._stats[key]
            stats.update(value)
            self._history[key].append(value)
            if key in self._cooldown and self._cooldown[key] > 0:
                if stats.ready and abs(value - stats.mu) <= 2 * stats.sigma:
                    self._cooldown[key] -= 1
                continue
            if not stats.ready:
                continue
            if key not in self._ewma:
                self._ewma[key] = Ewma(stats.mu, stats.sigma)
            ewma = self._ewma[key]
            ewma.update(value)
            rule = western_electric(self._history[key], stats.mu, stats.sigma)
            if rule is None and ewma.alarm:
                rule = "EWMA"
            if rule:
                self._handle_signal(finished, key, value, rule)
