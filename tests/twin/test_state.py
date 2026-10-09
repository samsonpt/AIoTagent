from twin.state import ProcessState, ScalarFilter
from twin.types import CounterfactualResult, Prediction, TwinObservation


def test_filter_moves_toward_measurement():
    f = ScalarFilter(x0=4.5)
    f.predict(u=-0.1)
    f.update(4.2)
    assert 4.15 < f.x < 4.45


def test_types_construct():
    obs = TwinObservation(
        t=0.0,
        tick=1,
        kind="thickness",
        y=25.0,
        yhat=26.0,
        q05=24.0,
        q95=28.0,
    )
    assert obs.lot_id is None
    pred = Prediction(
        mean=25.0,
        q05=24.0,
        q95=26.0,
        yield_prob=0.9,
        oos_prob=0.1,
        detail={},
    )
    cf = CounterfactualResult(
        hypothesis={"asd": 3.0},
        before=pred,
        after=pred,
        defect_cleared=False,
    )
    assert cf.hypothesis == {"asd": 3.0}


def test_process_state_get_set():
    state = ProcessState()
    f = ScalarFilter(x0=1.28)
    state.set_filter("sg", f)
    assert state.get("sg") == 1.28
    assert state.filters["sg"] is f
