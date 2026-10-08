from dataclasses import dataclass


@dataclass
class SimClock:
    tick_seconds: float = 1800.0
    substeps: int = 30
    tick: int = 0
    substep: int = 0

    @property
    def substep_seconds(self) -> float:
        return self.tick_seconds / self.substeps

    @property
    def now(self) -> float:
        return self.tick * self.tick_seconds + self.substep * self.substep_seconds

    def advance_substep(self) -> None:
        self.substep += 1
        if self.substep >= self.substeps:
            self.substep = 0
            self.tick += 1

    def advance_tick(self) -> None:
        self.tick += 1
        self.substep = 0
