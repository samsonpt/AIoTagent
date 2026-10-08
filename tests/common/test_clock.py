from common.clock import SimClock


def test_defaults():
    clock = SimClock()
    assert clock.tick_seconds == 1800.0
    assert clock.substeps == 30
    assert clock.substep_seconds == 60.0
    assert clock.now == 0.0


def test_thirty_substeps_roll_over_to_next_tick():
    clock = SimClock()
    for _ in range(30):
        clock.advance_substep()
    assert clock.tick == 1
    assert clock.substep == 0
    assert clock.now == 1800.0


def test_now_includes_substep_offset():
    clock = SimClock()
    clock.advance_substep()
    clock.advance_substep()
    assert clock.now == 120.0


def test_advance_tick_resets_substep():
    clock = SimClock()
    clock.advance_substep()
    clock.advance_tick()
    assert clock.tick == 1
    assert clock.substep == 0
    assert clock.now == 1800.0
