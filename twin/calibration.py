class RlsGain:
    def __init__(self, theta: float = 1.0, p: float = 1.0, lam: float = 0.98):
        self.theta = float(theta)
        self._p = float(p)
        self._lam = float(lam)

    def update(self, x: float, y: float) -> float:
        x = float(x)
        y = float(y)
        denom = self._lam + x * self._p * x
        self.theta += self._p * x * (y - x * self.theta) / denom
        self._p = (self._p - self._p * x * x * self._p / denom) / self._lam
        return self.theta


def twin_confidence(coverages: list[bool], window: int = 20) -> float:
    if len(coverages) < 8:
        return 0.5
    recent = coverages[-window:]
    return max(0.0, min(1.0, sum(recent) / len(recent)))
