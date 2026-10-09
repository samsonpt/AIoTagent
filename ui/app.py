"""Streamlit 看板入口：侧栏选页 + --db + 顶栏链告警。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ui.db import DashboardStore
from ui.pages import approvals, monitor, trace

_PAGES = ("审批", "追溯", "监视", "AOI", "推理链", "孪生")
_DEFAULT_DB = "runs/demo.db"


def parse_db_arg(argv: list[str] | None = None) -> str | None:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" in args:
        args = args[args.index("--") + 1 :]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=None)
    known, _ = parser.parse_known_args(args)
    return known.db


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="AIoT PCB 看板", layout="wide")

    cli_db = parse_db_arg()
    with st.sidebar:
        st.title("AIoT 看板")
        db_path = st.text_input("SQLite 路径", value=cli_db or _DEFAULT_DB)
        page = st.radio("页面", _PAGES, index=0)
        t_decide_raw = st.number_input(
            "t_decide（空则用 telemetry.max）",
            value=float("nan"),
            format="%.3f",
            help="NaN 表示未注入，回退库内仿真时间",
        )
        if st.button("刷新"):
            st.rerun()

    path = Path(db_path)
    if not path.exists():
        st.warning(f"数据库不存在: {path}（可先运行 demo_live 创建）")
        return

    with DashboardStore(path) as store:
        ok, reason = store.verify_chain()
        if ok:
            st.success("链完整性: OK")
        else:
            st.error(f"链完整性告警: {reason}")

        t_decide = None if t_decide_raw != t_decide_raw else float(t_decide_raw)

        if page == "审批":
            approvals.render(store, t_decide=t_decide)
        elif page == "追溯":
            trace.render(store)
        elif page == "监视":
            monitor.render(store)
        elif page == "AOI":
            st.subheader("AOI")
            st.info("骨架页：Task 5 实现缺陷面板视图")
        elif page == "推理链":
            st.subheader("推理链")
            st.info("骨架页：Task 5 实现 episode 视图")
        else:
            st.subheader("孪生")
            st.info("骨架页：Task 5 实现孪生对比视图")


if __name__ == "__main__":
    main()
