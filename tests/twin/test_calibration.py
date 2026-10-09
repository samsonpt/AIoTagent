from twin.calibration import RlsGain, twin_confidence


def test_rls_converges_to_true_gain():
    rls = RlsGain()
    for i in range(1, 31):
        x = float(i)
        y = 0.8 * x
        rls.update(x, y)
    assert abs(rls.theta - 0.8) < 0.05


def test_rls_update_returns_theta():
    rls = RlsGain()
    theta = rls.update(1.0, 0.8)
    assert theta == rls.theta


def test_twin_confidence_insufficient_data():
    assert twin_confidence([True, False, True]) == 0.5
    assert twin_confidence([]) == 0.5
    assert twin_confidence([True] * 7) == 0.5


def test_twin_confidence_window_mean():
    coverages = [False] * 10 + [True] * 10
    assert twin_confidence(coverages, window=20) == 0.5

    coverages = [True] * 25
    assert twin_confidence(coverages, window=20) == 1.0

    coverages = [False] * 5 + [True] * 15
    assert twin_confidence(coverages, window=20) == 0.75


def test_twin_confidence_clamped():
    coverages = [True] * 20
    assert twin_confidence(coverages) == 1.0

    coverages = [False] * 20
    assert twin_confidence(coverages) == 0.0
