from collections import defaultdict
from dataclasses import dataclass, field
from math import sqrt

LAMBDA = 0.2
BASELINE_TICKS = 8
SIGMA_MIN = 1e-6


def western_electric(xs: list[float], mu: float, sigma: float) -> str | None:
    s = max(sigma, SIGMA_MIN)
    n = len(xs)
    if n >= 1 and abs(xs[-1] - mu) > 3 * s:
        return "R1"
    if n >= 3:
        last = xs[-3:]
        for sign in (1, -1):
            if sum((x - mu) * sign > 2 * s for x in last) >= 2:
                return "R2"
    if n >= 5:
        last = xs[-5:]
        for sign in (1, -1):
            if sum((x - mu) * sign > s for x in last) >= 4:
                return "R3"
    if n >= 8:
        last = xs[-8:]
        if all(x > mu for x in last) or all(x < mu for x in last):
            return "R4"
    return None


class Ewma:
    def __init__(self, mu: float, sigma: float, lam: float = LAMBDA):
        self.mu = mu
        self.sigma = max(sigma, SIGMA_MIN)
        self.lam = lam
        self.value = mu
        self.alarm = False

    def update(self, x: float) -> float:
        self.value = self.lam * x + (1 - self.lam) * self.value
        denom = self.sigma * sqrt(self.lam / (2 - self.lam))
        z = (self.value - self.mu) / denom
        self.alarm = abs(z) > 3
        return z


@dataclass
class ChannelStats:
    values: list[float] = field(default_factory=list)

    def update(self, x: float) -> None:
        self.values.append(float(x))

    @property
    def ready(self) -> bool:
        return len(self.values) >= BASELINE_TICKS

    @property
    def mu(self) -> float:
        xs = self.values[:BASELINE_TICKS]
        return sum(xs) / len(xs)

    @property
    def sigma(self) -> float:
        xs = self.values[:BASELINE_TICKS]
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / len(xs)
        return max(sqrt(var), SIGMA_MIN)


class TickAggregator:
    def __init__(self) -> None:
        self._points: dict[int, list[dict[str, float]]] = defaultdict(list)

    def add(self, tick: int, values: dict[str, float]) -> None:
        self._points[tick].append(values)

    def mean_of(self, tick: int) -> dict[str, float] | None:
        rows = self._points.get(tick)
        if not rows:
            return None
        keys = rows[0].keys()
        return {k: sum(r[k] for r in rows) / len(rows) for k in keys}

    def variance_of(self, tick: int, key: str) -> float | None:
        rows = self._points.get(tick)
        if not rows:
            return None
        xs = [r[key] for r in rows]
        mu = sum(xs) / len(xs)
        return sum((x - mu) ** 2 for x in xs) / len(xs)
