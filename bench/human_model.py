from common import topics
from common.bus import Bus
from common.clock import SimClock
from common.recipe import Recipe
from edge.envelope import bound_command
from edge.ocap import commands_for


class HumanModel:
    def __init__(self, bus: Bus, recipe: Recipe, clock: SimClock, delay_ticks: int = 2):
        self.bus = bus
        self.recipe = recipe
        self.clock = clock
        self.delay_ticks = delay_ticks
        self.client_id = "human"
        self._pending: list[tuple[int, dict]] = []
        bus.subscribe("plant/events/+", self._on_event, self.client_id)

    def _on_event(self, topic: str, payload: dict) -> None:
        self._pending.append((self.clock.tick + self.delay_ticks, payload))

    def on_tick(self, clock: SimClock) -> None:
        self.clock = clock
        due, later = [], []
        for when, event in self._pending:
            (due if when <= clock.tick else later).append((when, event))
        self._pending = later
        for _, event in due:
            for command, params, reason in commands_for(event["process"], event["key"], event["value"], self.recipe):
                bound = bound_command(self.recipe, event["process"], command, params, current={event["key"]: event["value"]})
                if bound is None:
                    continue
                command, params = bound
                self.bus.publish(
                    topics.command(event["process"]),
                    {"command": command, "params": params, "source": "human", "reason": reason},
                    self.client_id,
                )
