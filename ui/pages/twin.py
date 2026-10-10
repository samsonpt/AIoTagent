"""孪生对比页：预测 vs 实测、残差、滚动 MAPE、前瞻门控。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore

_OBS_CHART_COLS = ["t", "y", "yhat", "q05", "q95"]
_GATE_COLS = [
    "t",
    "tick",
    "process",
    "command",
    "kind",
    "confidence",
    "yield_prob",
    "oos_prob",
    "passed",
    "reason",
    "lot_id",
]


def _as_mapping(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if is_dataclass(row):
        return asdict(row)
    return dict(row)


def _numeric(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def filter_observations(
    rows: list,
    *,
    kind: str | None = None,
    lot_id: str | None = None,
) -> list:
    out: list = []
    for row in rows:
        data = _as_mapping(row)
        if kind is not None and data.get("kind") != kind:
            continue
        if lot_id is not None and data.get("lot_id") != lot_id:
            continue
        out.append(row)
    return out


def build_observation_chart_frame(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_OBS_CHART_COLS)
    records = []
    for row in rows:
        data = _as_mapping(row)
        records.append({col: data.get(col) for col in _OBS_CHART_COLS})
    return pd.DataFrame(records)


def build_residual_frame(rows: list) -> pd.DataFrame:
    points = residual_series(rows)
    if not points:
        return pd.DataFrame(columns=["t", "residual"])
    return pd.DataFrame(points, columns=["t", "residual"])


def build_gate_table(gates: list) -> pd.DataFrame:
    if not gates:
        return pd.DataFrame(columns=_GATE_COLS)
    rows = []
    for gate in gates:
        data = _as_mapping(gate)
        rows.append({col: data.get(col) for col in _GATE_COLS})
    return pd.DataFrame(rows)


def residual_series(rows: list) -> list[tuple[float, float]]:
    """(t, y - yhat) for each row with numeric y/yhat."""
    out: list[tuple[float, float]] = []
    for row in rows:
        y = _numeric(getattr(row, "y", None) if not isinstance(row, dict) else row.get("y"))
        yhat = _numeric(getattr(row, "yhat", None) if not isinstance(row, dict) else row.get("yhat"))
        t = _numeric(getattr(row, "t", None) if not isinstance(row, dict) else row.get("t"))
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
        y = _numeric(getattr(row, "y", None) if not isinstance(row, dict) else row.get("y"))
        yhat = _numeric(getattr(row, "yhat", None) if not isinstance(row, dict) else row.get("yhat"))
        if y is None or yhat is None or abs(y) < 1e-9:
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
        y = _numeric(getattr(row, "y", None) if not isinstance(row, dict) else row.get("y"))
        q05 = _numeric(getattr(row, "q05", None) if not isinstance(row, dict) else row.get("q05"))
        q95 = _numeric(getattr(row, "q95", None) if not isinstance(row, dict) else row.get("q95"))
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
    all_obs = store.twin_observations(limit=2000)
    all_gates = store.twin_gates(limit=500)
    if not all_obs and not all_gates:
        st.info(
            "暂无孪生观测或前瞻门控记录。请先运行 demo_live 生成数据，"
            "例如：python scripts/demo_live.py --db runs/demo_twin.db --ticks 24 --overwrite --no-cloud"
        )
        return

    kinds = sorted({getattr(r, "kind", None) or _as_mapping(r).get("kind") for r in all_obs} - {None})
    kind_options = ["全部", *kinds]
    kind_sel = st.selectbox("指标 kind", kind_options, index=0)
    kind_filt = None if kind_sel == "全部" else kind_sel

    lot_ids = sorted(
        {
            lid
            for r in all_obs
            if (lid := getattr(r, "lot_id", None) or _as_mapping(r).get("lot_id"))
        }
    )
    lot_sel = st.selectbox("lot_id", ["全部", *lot_ids], index=0)
    lot_filt = None if lot_sel == "全部" else lot_sel

    obs = filter_observations(all_obs, kind=kind_filt, lot_id=lot_filt)

    if obs:
        chart_frame = build_observation_chart_frame(obs)
        st.line_chart(chart_frame.set_index("t"))

        residual_frame = build_residual_frame(obs)
        st.caption("残差 (y − yhat)")
        st.dataframe(residual_frame, use_container_width=True, hide_index=True)
        if not residual_frame.empty:
            st.line_chart(residual_frame.set_index("t"))

        window = st.slider("滚动窗口 N", min_value=1, max_value=max(len(obs), 1), value=min(20, len(obs)))
        mape = rolling_mape(obs, window)
        coverage = rolling_coverage(obs, window)
        mape_text = f"{mape:.2f}%" if pd.notna(mape) else "—"
        cov_text = f"{coverage:.2%}" if pd.notna(coverage) else "—"
        st.metric("滚动 MAPE", mape_text)
        st.metric("滚动覆盖率（置信度估计）", cov_text, help="由落库 observation 滚动估计；Guard 阈值 0.5")
        st.caption("覆盖率阈值参考：0.5（低于此值表示区间校准偏弱）")
    else:
        st.info("当前筛选条件下暂无孪生观测")

    st.markdown("#### 前瞻门控")
    failed_only = st.checkbox("仅未通过", value=False)
    gate_passed = False if failed_only else None
    gates = store.twin_gates(passed=gate_passed, limit=500)
    gate_frame = build_gate_table(gates)
    if gate_frame.empty:
        st.info("暂无前瞻门控记录")
    else:
        st.dataframe(gate_frame, use_container_width=True, hide_index=True)
