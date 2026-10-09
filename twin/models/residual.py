import numpy as np
from sklearn.ensemble import GradientBoostingRegressor


class ResidualQuantiles:
    def __init__(self, seed: int) -> None:
        self._seed = seed
        self.ready = False
        self._models: dict[float, GradientBoostingRegressor] | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        if len(y) < 8:
            self.ready = False
            return

        self._models = {}
        for alpha in (0.05, 0.5, 0.95):
            model = GradientBoostingRegressor(
                loss="quantile",
                alpha=alpha,
                n_estimators=40,
                max_depth=2,
                random_state=self._seed,
            )
            model.fit(X, y)
            self._models[alpha] = model
        self.ready = True

    def predict(
        self, X: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self.ready:
            raise RuntimeError("ResidualQuantiles is not ready")

        assert self._models is not None
        q05 = self._models[0.05].predict(X)
        q50 = self._models[0.5].predict(X)
        q95 = self._models[0.95].predict(X)
        return q05, q50, q95
