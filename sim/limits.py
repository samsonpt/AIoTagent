def require(params: dict, key: str, lo: float, hi: float) -> float:
    if key not in params:
        raise ValueError(f"missing parameter {key!r}")
    value = float(params[key])
    if not lo <= value <= hi:
        raise ValueError(f"{key}={value} outside physical limit [{lo}, {hi}]")
    return value
