from common import topics
from common.bus import Bus
from common.clock import SimClock
from common.recipe import Recipe
from common.rng import make_rng
from edge.envelope import bound_command
from edge.ocap import commands_for


class HumanModel:
    def __init__(
        self,
        bus: Bus,
        recipe: Recipe,
        clock: SimClock,
        delay_ticks: int = 2,
        *,
        store=None,
        guard=None,
        seed: int = 0,
        ocap: bool = True,
        approval_delay_s: float = 900,
    ):
        self.bus = bus
        self.recipe = recipe
        self.clock = clock
        self.delay_ticks = delay_ticks
        self.store = store
        self.guard = guard
        self.seed = seed
        self.ocap = ocap
        self.approval_delay_s = approval_delay_s
        self.client_id = "human"
        self._pending: list[tuple[int, dict]] = []
        self._rng = make_rng(seed, "human_approval")
        if ocap:
            bus.subscribe("plant/events/+", self._on_event, self.client_id)

    def _on_event(self, topic: str, payload: dict) -> None:
        self._pending.append((self.clock.tick + self.delay_ticks, payload))

    def on_tick(self, clock: SimClock) -> None:
        self.clock = clock
        if self.ocap:
            self._emit_due_ocap(clock)
        if self.store is not None and self.guard is not None:
            self._process_approvals(clock)

    def _emit_due_ocap(self, clock: SimClock) -> None:
        due, later = [], []
        for when, event in self._pending:
            (due if when <= clock.tick else later).append((when, event))
        self._pending = later
        for _, event in due:
            for command, params, reason in commands_for(
                event["process"], event["key"], event["value"], self.recipe
            ):
                bound = bound_command(
                    self.recipe,
                    event["process"],
                    command,
                    params,
                    current={event["key"]: event["value"]},
                )
                if bound is None:
                    continue
                command, params = bound
                self.bus.publish(
                    topics.command(event["process"]),
                    {"command": command, "params": params, "source": "human", "reason": reason},
                    self.client_id,
                )

    def _process_approvals(self, clock: SimClock) -> None:
        for row in list(self.store.list_approvals("pending")):
            if clock.now < float(row["t_submit"]) + self.approval_delay_s:
                continue
            approved = self._decide(row["process"], clock.now)
            status = "approved" if approved else "rejected"
            reason = f"human_{status}"
            self.store.update_approval(
                row["request_id"],
                status=status,
                t_decide=clock.now,
                decider="human_model",
                reason=reason,
            )
            self.guard.resolve_approval(row["request_id"], approved, reason)

    def _decide(self, process: str, now: float) -> bool:
        matched = self._matches_active_fault(process, now)
        draw = self._rng.random()
        if matched:
            return draw < 0.9
        return not (draw < 0.9)

    def _matches_active_fault(self, process: str, now: float) -> bool:
        for fault in self.store.faults():
            if fault.t_cleared is not None:
                continue
            if fault.t_start <= now and fault.process == process:
                return True
        return False
