import math

import pytest

from bench.schema import TraceStore, TwinGateRecord, TwinObservationRecord
from ui.db import DashboardStore


def test_rolling_mape_and_coverage():
    from ui.pages.twin import rolling_coverage, rolling_mape, residual_series

    rows = [
        TwinObservationRecord(
            t=float(i), tick=i, kind="thickness", y=10.0, yhat=9.0, q05=8.0, q95=12.0
        )
        for i in range(5)
    ]
    assert residual_series(rows)[0] == (0.0, 1.0)
    assert rolling_mape(rows, 5) == pytest.approx(10.0)
    assert rolling_coverage(rows, 5) == pytest.approx(1.0)


def test_rolling_mape_nan_when_no_valid_points():
    from ui.pages.twin import rolling_mape

    rows = [TwinObservationRecord(t=0.0, tick=0, kind="k", y=0.0, yhat=1.0, q05=0.0, q95=1.0)]
    assert math.isnan(rolling_mape(rows, 1))


def test_rolling_mape_skips_near_zero_y():
    from ui.pages.twin import rolling_mape

    rows = [
        TwinObservationRecord(t=0.0, tick=0, kind="k", y=1e-10, yhat=1.0, q05=0.0, q95=1.0),
        TwinObservationRecord(t=1.0, tick=1, kind="k", y=10.0, yhat=9.0, q05=8.0, q95=12.0),
    ]
    assert rolling_mape(rows, 2) == pytest.approx(10.0)


def test_twin_observations_limit(db_path):
    with TraceStore(db_path) as store:
        for i in range(10):
            store.record_twin_observation(
                TwinObservationRecord(
                    t=float(i),
                    tick=i,
                    kind="thickness",
                    y=10.0,
                    yhat=9.0,
                    q05=8.0,
                    q95=12.0,
                    lot_id="L1",
                )
            )
    with DashboardStore(db_path) as dash:
        rows = dash.twin_observations(kind="thickness", limit=3)
        assert len(rows) == 3
        assert rows[-1].t == 9.0


def test_twin_gates_filter_and_limit(db_path):
    with TraceStore(db_path) as store:
        for i in range(6):
            store.record_twin_gate(
                TwinGateRecord(
                    t=float(i),
                    tick=i,
                    process="plating",
                    command="dosing",
                    kind="thickness",
                    confidence=0.9,
                    yield_prob=0.95,
                    oos_prob=0.05,
                    passed=i % 2 == 0,
                    reason="ok" if i % 2 == 0 else "confidence_low",
                    lot_id="L1",
                )
            )
    with DashboardStore(db_path) as dash:
        passed = dash.twin_gates(passed=True, limit=2)
        assert len(passed) == 2
        assert all(r.passed for r in passed)
        assert passed[-1].t == 4.0


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "twin.db"
