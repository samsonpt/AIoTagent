"""推理链骨架页：episode_log 表。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore

_COLS = [
    "episode_id",
    "process",
    "trigger",
    "t_detect",
    "handler",
    "t_decide",
    "t_execute",
    "t_recover",
    "violated",
]


def _as_mapping(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if is_dataclass(row):
        return asdict(row)
    return dict(row)


def build_episode_table(episodes: list) -> pd.DataFrame:
    if not episodes:
        return pd.DataFrame(columns=_COLS)
    rows = [_as_mapping(ep) for ep in episodes]
    frame = pd.DataFrame(rows)
    for col in _COLS:
        if col not in frame.columns:
            frame[col] = None
    return frame[_COLS]


def render(store: DashboardStore) -> None:
    import streamlit as st

    st.subheader("推理链")
    st.caption("骨架视图：episode_log（只读）")
    frame = build_episode_table(store.episodes())
    if frame.empty:
        st.info("暂无 episode 数据")
        return
    st.dataframe(frame, use_container_width=True)
