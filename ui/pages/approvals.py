"""审批页：pending 列表与批准/驳回写回。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore

_TABLE_COLS = [
    "request_id",
    "t_submit",
    "process",
    "equipment",
    "command",
    "lot_id",
    "status",
    "source",
    "applied",
]


def build_approval_table(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_TABLE_COLS)
    frame = pd.DataFrame(rows)
    for col in _TABLE_COLS:
        if col not in frame.columns:
            frame[col] = None
    return frame[_TABLE_COLS]


def resolve_t_decide(store: DashboardStore | None, t_decide: float | None = None) -> float:
    if t_decide is not None:
        return float(t_decide)
    if store is None:
        return 0.0
    rows = store.telemetry(limit=None)
    if not rows:
        return 0.0
    return float(max(row[0] for row in rows))


def render(store: DashboardStore, *, t_decide: float | None = None) -> None:
    import streamlit as st

    st.subheader("审批")
    status = st.selectbox("状态筛选", ["pending", "approved", "rejected", "全部"], index=0)
    filter_status = None if status == "全部" else status
    rows = store.list_approvals(status=filter_status)
    st.dataframe(build_approval_table(rows), use_container_width=True)

    pending = store.list_approvals(status="pending")
    if not pending:
        st.info("当前无待审批请求")
        return

    options = {
        f"{r['request_id']} | {r.get('command')} | lot={r.get('lot_id')}": r["request_id"]
        for r in pending
    }
    label = st.selectbox("待审批请求", list(options.keys()))
    request_id = options[label]
    reason = st.text_input("原因", value="")
    decided_t = resolve_t_decide(store, t_decide=t_decide)

    col_a, col_r = st.columns(2)
    if col_a.button("批准", type="primary"):
        store.decide_approval(request_id, True, t_decide=decided_t, reason=reason or "approved")
        st.success(f"已批准 {request_id}")
        st.rerun()
    if col_r.button("驳回"):
        store.decide_approval(request_id, False, t_decide=decided_t, reason=reason or "rejected")
        st.warning(f"已驳回 {request_id}")
        st.rerun()
