import math

from twin.models import drill, etch, plating


def test_plating_faraday_not_simulator_constant():
    thickness = plating.thickness_um(asd=2, time_min=60, additive_ml_l=4.5)
    assert thickness == 26.4
    assert thickness != 25.0


def test_etch_width_differs_from_sim_formula():
    sg = 1.28
    temp_c = 50
    spray_bar = 2
    speed_m_min = 2
    thickness_um = 25
    artwork_um = 121.7
    chamber_m = 2
    etch_factor = 3

    twin_width = etch.width_um(
        sg=sg,
        temp_c=temp_c,
        spray_bar=spray_bar,
        speed_m_min=speed_m_min,
        thickness_um=thickness_um,
        artwork_um=artwork_um,
        chamber_m=chamber_m,
        etch_factor=etch_factor,
    )

    dsg = (sg - 1.28) / 0.01
    dT = temp_c - 50
    chem = 0.5 * (1 + 0.08 * dsg + 0.03 * dT)
    rate = chem * math.sqrt(spray_bar / 2)
    dwell = 60 * chamber_m / speed_m_min
    over = rate * dwell - thickness_um
    width_sim = artwork_um - 2 * (
        thickness_um / etch_factor + max(over, 0) * 0.5
    )

    assert abs(twin_width - width_sim) > 0.3


def test_drill_roughness_at_zero_hits():
    assert drill.roughness_um(bit_hits=0, rated=100_000) == 12


def test_drill_break_prob_increases_with_wear():
    rated = 100_000
    low = drill.break_prob(bit_hits=50_000, rated=rated)
    high = drill.break_prob(bit_hits=150_000, rated=rated)
    assert high > low
