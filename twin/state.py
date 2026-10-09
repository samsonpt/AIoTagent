from dataclasses import dataclass, field


class ScalarFilter:
    def __init__(self, x0: float, q: float = 1e-4, r: float = 1e-2):
        self.x = float(x0)
        self.p = 1.0
        self._q = q
        self._r = r

    def predict(self, u: float = 0.0) -> float:
        self.x += u
        self.p += self._q
        return self.x

    def update(self, z: float) -> float:
        k = self.p / (self.p + self._r)
        self.x += k * (z - self.x)
        self.p = (1.0 - k) * self.p + self._q
        return self.x


@dataclass
class ProcessState:
    filters: dict[str, ScalarFilter] = field(default_factory=dict)

    def get(self, key: str) -> float:
        return self.filters[key].x

    def set_filter(self, key: str, filter: ScalarFilter) -> None:
        self.filters[key] = filter
