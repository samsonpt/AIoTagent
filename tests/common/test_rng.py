import numpy as np

from common.rng import make_rng


def test_same_seed_and_stream_give_identical_sequence():
    a = make_rng(42, "etch").random(100)
    b = make_rng(42, "etch").random(100)
    assert np.array_equal(a, b)


def test_different_stream_gives_different_sequence():
    a = make_rng(42, "etch").random(100)
    b = make_rng(42, "drill").random(100)
    assert not np.array_equal(a, b)


def test_different_seed_gives_different_sequence():
    a = make_rng(1, "etch").random(100)
    b = make_rng(2, "etch").random(100)
    assert not np.array_equal(a, b)
