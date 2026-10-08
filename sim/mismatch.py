MISMATCH_SCALE: dict[str, float] = {"low": 0.5, "mid": 1.0, "high": 2.0}


def mismatch_scale(level: str) -> float:
    if level not in MISMATCH_SCALE:
        raise ValueError(f"unknown mismatch level {level!r}, expected one of {sorted(MISMATCH_SCALE)}")
    return MISMATCH_SCALE[level]
