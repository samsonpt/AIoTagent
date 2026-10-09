"""孪生对比骨架页：占位说明。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.db import DashboardStore


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
