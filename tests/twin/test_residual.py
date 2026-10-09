import numpy as np
import pytest

from twin.models.residual import ResidualQuantiles


def _make_training_data(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, 3))
    y = X @ np.array([1.0, -0.5, 0.3]) + rng.normal(0, 0.1, n)
    return X, y


def test_not_ready_when_n_less_than_8():
    model = ResidualQuantiles(seed=42)
    X, y = _make_training_data(7, seed=0)
    model.fit(X, y)
    assert model.ready is False


def test_predict_raises_when_not_ready():
    model = ResidualQuantiles(seed=42)
    X, _ = _make_training_data(7, seed=0)
    with pytest.raises(RuntimeError):
        model.predict(X)


def test_fit_makes_ready_when_n_ge_8():
    model = ResidualQuantiles(seed=42)
    X, y = _make_training_data(8, seed=0)
    model.fit(X, y)
    assert model.ready is True


def test_predict_returns_three_ndarrays():
    model = ResidualQuantiles(seed=42)
    X_train, y_train = _make_training_data(20, seed=0)
    model.fit(X_train, y_train)
    X_test = np.array([[0.1, -0.2, 0.3], [1.0, 0.5, -0.1]])
    q05, q50, q95 = model.predict(X_test)
    assert isinstance(q05, np.ndarray)
    assert isinstance(q50, np.ndarray)
    assert isinstance(q95, np.ndarray)
    assert q05.shape == (2,)
    assert q50.shape == (2,)
    assert q95.shape == (2,)


def test_same_seed_bitwise_equal_predictions():
    X_train, y_train = _make_training_data(30, seed=1)
    X_test = np.array([[0.2, 0.1, -0.3], [0.5, -0.4, 0.2]])

    model_a = ResidualQuantiles(seed=99)
    model_a.fit(X_train, y_train)
    pred_a = model_a.predict(X_test)

    model_b = ResidualQuantiles(seed=99)
    model_b.fit(X_train, y_train)
    pred_b = model_b.predict(X_test)

    for arr_a, arr_b in zip(pred_a, pred_b):
        np.testing.assert_array_equal(arr_a, arr_b)
