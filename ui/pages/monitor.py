"""监视页：telemetry 时间序列。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore


def build_telemetry_frame(rows: list[tuple]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["t"])
    long = pd.DataFrame(rows, columns=["t", "process", "equipment", "key", "value"])
    wide = long.pivot_table(index="t", columns="key", values="value", aggfunc="last")
    wide = wide.reset_index()
    wide.columns.name = None
    return wide


def render(store: DashboardStore) -> None:
    import streamlit as st

    st.subheader("运行监视")
    all_rows = store.telemetry(limit=2000)
    processes = sorted({row[1] for row in all_rows})
    process = st.selectbox("工序", ["全部", *processes], index=0)
    filt = None if process == "全部" else process
    rows = store.telemetry(process=filt, limit=500)
    frame = build_telemetry_frame(rows)
    if frame.empty or len(frame.columns) <= 1:
        st.info("暂无 telemetry 数据")
        return
    st.dataframe(frame, use_container_width=True)
    chart_cols = [c for c in frame.columns if c != "t"]
    if chart_cols:
        st.line_chart(frame.set_index("t")[chart_cols])
