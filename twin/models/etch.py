import math


def width_um(
    *,
    sg: float,
    temp_c: float,
    spray_bar: float,
    speed_m_min: float,
    thickness_um: float,
    artwork_um: float,
    chamber_m: float,
    etch_factor: float,
) -> float:
    rate = (
        0.48
        * (1 + 0.07 * ((sg - 1.28) / 0.01) + 0.025 * (temp_c - 50))
        * math.sqrt(spray_bar / 2)
    )
    dwell = 60 * chamber_m / speed_m_min
    over = rate * dwell - thickness_um
    side = thickness_um / etch_factor + max(over, 0.0) * 0.45
    return artwork_um - 2 * side
