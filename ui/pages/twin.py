"""孪生对比骨架页：占位说明。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.db import DashboardStore


def _numeric(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def residual_series(rows: list) -> list[tuple[float, float]]:
    """(t, y - yhat) for each row with numeric y/yhat."""
    out: list[tuple[float, float]] = []
    for row in rows:
        y = _numeric(getattr(row, "y", None))
        yhat = _numeric(getattr(row, "yhat", None))
        t = _numeric(getattr(row, "t", None))
        if y is None or yhat is None or t is None:
            continue
        out.append((t, y - yhat))
    return out


def rolling_mape(rows: list, window: int) -> float:
    """MAPE% over last `window` points; nan if none valid."""
    if window <= 0:
        return float("nan")
    slice_rows = rows[-window:]
    errors: list[float] = []
    for row in slice_rows:
        y = _numeric(getattr(row, "y", None))
        yhat = _numeric(getattr(row, "yhat", None))
        if y is None or yhat is None or y == 0.0:
            continue
        errors.append(abs(y - yhat) / abs(y))
    if not errors:
        return float("nan")
    return sum(errors) / len(errors) * 100.0


def rolling_coverage(rows: list, window: int) -> float:
    """Fraction with q05 <= y <= q95 over last window; nan if empty."""
    if window <= 0:
        return float("nan")
    slice_rows = rows[-window:]
    if not slice_rows:
        return float("nan")
    covered = 0
    valid = 0
    for row in slice_rows:
        y = _numeric(getattr(row, "y", None))
        q05 = _numeric(getattr(row, "q05", None))
        q95 = _numeric(getattr(row, "q95", None))
        if y is None or q05 is None or q95 is None:
            continue
        valid += 1
        if q05 <= y <= q95:
            covered += 1
    if valid == 0:
        return float("nan")
    return covered / valid


def render(store: DashboardStore) -> None:
    import streamlit as st

    st.subheader("孪生对比")
    st.markdown(
        """
骨架页：孪生预测 vs 实测对比尚未接入完整可视化。

后续可展示：
- 前瞻预测摘要（lookahead）
- 关键 vs 孪生残差
- 置信度门控结果

当前请使用「监视」查看 telemetry，「追溯」查看链与动作。
"""
    )
    _ = store  # 保持与其它页一致的 render(store) 签名
