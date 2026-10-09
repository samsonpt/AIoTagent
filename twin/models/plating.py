FARADAY_UM_PER_ASD_H = 13.2
ADDITIVE_NOMINAL_ML_L = 4.5


def thickness_um(asd: float, time_min: float, additive_ml_l: float) -> float:
    deficit = max(0.0, (ADDITIVE_NOMINAL_ML_L - additive_ml_l) / ADDITIVE_NOMINAL_ML_L)
    eta = 1.0 - 0.45 * deficit**2
    return FARADAY_UM_PER_ASD_H * asd * (time_min / 60.0) * eta
