from dataclasses import dataclass, field


@dataclass(frozen=True)
class TwinObservation:
    t: float
    tick: int
    kind: str
    y: float
    yhat: float
    q05: float
    q95: float
    lot_id: str | None = None


@dataclass(frozen=True)
class Prediction:
    mean: float | None
    q05: float | None
    q95: float | None
    yield_prob: float
    oos_prob: float
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CounterfactualResult:
    hypothesis: dict
    before: Prediction
    after: Prediction
    defect_cleared: bool
