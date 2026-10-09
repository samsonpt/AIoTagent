import math


def roughness_um(bit_hits: float, rated: float) -> float:
    wear = bit_hits / rated
    return 12 + 9 * wear**2


def break_prob(bit_hits: float, rated: float) -> float:
    wear = bit_hits / rated
    return 1 / (1 + math.exp(-(wear - 1.2) * 12))
