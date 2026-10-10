import math

import pytest

pytest.importorskip("pandas")

from bench.schema import TraceStore, TwinGateRecord, TwinObservationRecord
from ui.db import DashboardStore
from ui.pages.twin import (
    build_gate_table,
    build_observation_chart_frame,
    build_residual_frame,
    filter_observations,
)


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


def test_filter_observations_by_kind_and_lot():
    rows = [
        TwinObservationRecord(
            t=0.0, tick=0, kind="thickness", y=1.0, yhat=1.0, q05=0.0, q95=2.0, lot_id="L1"
        ),
        TwinObservationRecord(
            t=1.0, tick=1, kind="width", y=2.0, yhat=2.0, q05=1.0, q95=3.0, lot_id="L2"
        ),
    ]
    assert len(filter_observations(rows, kind="thickness")) == 1
    assert filter_observations(rows, lot_id="L2")[0].kind == "width"
    assert len(filter_observations(rows, kind="width", lot_id="L1")) == 0


def test_build_observation_and_residual_frames():
    rows = [
        TwinObservationRecord(
            t=0.0, tick=0, kind="k", y=10.0, yhat=9.0, q05=8.0, q95=12.0, lot_id="L1"
        )
    ]
    chart = build_observation_chart_frame(rows)
    assert list(chart.columns) == ["t", "y", "yhat", "q05", "q95"]
    assert float(chart.iloc[0]["y"]) == 10.0
    residual = build_residual_frame(rows)
    assert float(residual.iloc[0]["residual"]) == 1.0


def test_build_gate_table():
    gates = [
        TwinGateRecord(
            t=1.0,
            tick=1,
            process="plating",
            command="dosing",
            kind="thickness",
            confidence=0.8,
            yield_prob=0.9,
            oos_prob=0.1,
            passed=False,
            reason="confidence_low",
            lot_id="L1",
        )
    ]
    df = build_gate_table(gates)
    assert df.iloc[0]["reason"] == "confidence_low"
    assert df.iloc[0]["passed"] == False


def test_twin_page_importable():
    from ui.pages.twin import render

    assert callable(render)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "twin.db"
