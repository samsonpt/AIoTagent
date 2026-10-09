"""追溯页：按 lot 查看链与 verify_chain。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore

_CHAIN_COLS = [
    "seq",
    "lot_id",
    "kind",
    "ref",
    "t",
    "payload",
    "prev_hash",
    "entry_hash",
]


def build_chain_table(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_CHAIN_COLS)
    frame = pd.DataFrame(rows)
    for col in _CHAIN_COLS:
        if col not in frame.columns:
            frame[col] = None
    out = frame[_CHAIN_COLS].copy()
    out["payload"] = out["payload"].map(
        lambda p: p if isinstance(p, str) else str(p)
    )
    return out


def render(store: DashboardStore) -> None:
    import streamlit as st

    st.subheader("追溯")
    lots = store.lot_ids()
    if not lots:
        st.info("暂无 trace_chain 记录")
        ok, reason = store.verify_chain()
        if ok:
            st.success("verify_chain: OK（空链）")
        else:
            st.error(f"verify_chain 失败: {reason}")
        return

    lot_id = st.selectbox("lot_id", lots)
    ok, reason = store.verify_chain(lot_id)
    if ok:
        st.success(f"verify_chain({lot_id}): OK")
    else:
        st.error(f"verify_chain({lot_id}) 失败: {reason}")

    rows = store.chain_rows(lot_id)
    st.dataframe(build_chain_table(rows), use_container_width=True)
