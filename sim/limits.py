def _lookup(params: object, key: str) -> object:
    if not isinstance(params, dict):
        raise ValueError(f"params must be a dict, got {type(params).__name__}")
    if key not in params:
        raise ValueError(f"missing parameter {key!r}")
    value = params[key]
    if isinstance(value, (bool, str)):
        raise ValueError(f"{key}={value!r} must be a number")
    return value


def _check_range(key: str, value: float, lo: float, hi: float) -> None:
    if not lo <= value <= hi:
        raise ValueError(f"{key}={value} outside physical limit [{lo}, {hi}]")


def require(params: object, key: str, lo: float, hi: float) -> float:
    raw = _lookup(params, key)
    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key}={raw!r} must be a number") from e
    _check_range(key, value, lo, hi)
    return value


def require_int(params: object, key: str, lo: int, hi: int) -> int:
    value = _lookup(params, key)
    if not isinstance(value, int):
        raise ValueError(f"{key}={value!r} must be an int")
    _check_range(key, value, lo, hi)
    return value
