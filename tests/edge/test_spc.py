import math

from edge.spc import Ewma, TickAggregator, western_electric


def test_western_electric_rules():
    mu, sigma = 0.0, 1.0
    assert western_electric([0.0] * 7 + [3.1], mu, sigma) == "R1"
    assert western_electric([0.0, 2.1, 2.2], mu, sigma) == "R2"
    xs = [0.0, 1.2, 1.3, 1.4, 1.5]
    assert western_electric(xs, mu, sigma) == "R3"
    assert western_electric([0.2] * 8, mu, sigma) == "R4"
    assert western_electric([0.0] * 8, mu, sigma) is None


def test_ewma_alarms_on_shift():
    ewma = Ewma(mu=0.0, sigma=1.0)
    zs = [ewma.update(0.0) for _ in range(5)]
    assert max(abs(z) for z in zs) < 3
    alarmed = False
    for _ in range(40):
        ewma.update(5.0)
        if ewma.alarm:
            alarmed = True
            break
    assert alarmed


def test_tick_aggregator_means():
    agg = TickAggregator()
    agg.add(1, {"sg": 1.0})
    agg.add(1, {"sg": 3.0})
    agg.add(2, {"sg": 5.0})
    assert agg.mean_of(1) == {"sg": 2.0}
    assert agg.mean_of(3) is None
    assert math.isclose(agg.variance_of(1, "sg"), 1.0)
