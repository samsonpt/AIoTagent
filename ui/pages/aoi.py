"""AOI 骨架页：面板缺陷摘要。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from ui.db import DashboardStore

_COLS = [
    "panel_id",
    "lot_id",
    "part_no",
    "t_aoi",
    "n_defects",
    "defect_types",
    "root_cause_truth",
    "scrapped",
]


def _as_mapping(row: Any) -> dict:
    if isinstance(row, dict):
        return row
    if is_dataclass(row):
        return asdict(row)
    return dict(row)


def build_defect_summary(panels: list) -> pd.DataFrame:
    rows = []
    for panel in panels:
        data = _as_mapping(panel)
        defects = data.get("defects") or []
        if not isinstance(defects, list):
            defects = []
        types = sorted(
            {
                str(d.get("type") or d.get("defect_type") or d.get("kind") or "")
                for d in defects
                if isinstance(d, dict)
            }
            - {""}
        )
        rows.append(
            {
                "panel_id": data.get("panel_id"),
                "lot_id": data.get("lot_id"),
                "part_no": data.get("part_no"),
                "t_aoi": data.get("t_aoi"),
                "n_defects": len(defects),
                "defect_types": ",".join(types),
                "root_cause_truth": data.get("root_cause_truth"),
                "scrapped": data.get("scrapped"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows)[_COLS]


def render(store: DashboardStore) -> None:
    import streamlit as st

    st.subheader("AOI")
    st.caption("骨架视图：panel 缺陷摘要（只读）")
    frame = build_defect_summary(store.panels())
    if frame.empty:
        st.info("暂无 panel / AOI 数据")
        return
    st.dataframe(frame, use_container_width=True)
