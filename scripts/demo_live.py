"""演示联跑：写入共享 SQLite，供 Streamlit 看板边跑边看（关闭 HumanModel 自动审批）。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.schema import TraceStore
from common.config import AblationConfig
from common.env import load_env
from sim.faults import load_scenario
from sim.runner import run, summary_json

_DEFAULT_DB = "runs/demo.db"
_DEFAULT_SCENARIO = "bench/scenarios/nominal.yaml"


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(
        prog="python scripts/demo_live.py",
        description="运行演示场景并写入共享 SQLite（auto_approve=False）",
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="TraceStore SQLite 路径")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="删除已存在的 --db 后再运行",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=48,
        help="仿真 tick 数（默认 48；传 0 使用场景 n_ticks）",
    )
    parser.add_argument(
        "--scenario",
        default=_DEFAULT_SCENARIO,
        help="场景 YAML 路径",
    )
    parser.add_argument(
        "--no-cloud",
        action="store_true",
        help="关闭云端编排（无 DeepSeek 调用，演示更快）",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)
    elif db_path.exists():
        parser.error(f"数据库文件已存在: {db_path}（如需覆盖请加 --overwrite）")

    scenario_path = Path(args.scenario)
    if not scenario_path.is_absolute():
        scenario_path = ROOT / scenario_path

    scenario = load_scenario(scenario_path)
    n_ticks = scenario.n_ticks if args.ticks == 0 else args.ticks
    ablation = AblationConfig(use_cloud=not args.no_cloud)

    cloud_mode = "off"
    if ablation.use_cloud:
        cloud_mode = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "fake-llm"
    print(
        f"demo_live: ticks={n_ticks} db={db_path} cloud={cloud_mode} auto_approve=False",
        flush=True,
    )
    if cloud_mode == "deepseek":
        print("提示：含 DeepSeek 调用时 48 tick 约需 2–3 分钟，请等待末尾 JSON 摘要。", flush=True)

    with TraceStore(db_path) as store:
        summary = run(
            scenario,
            store,
            ablation=ablation,
            auto_approve=False,
            n_ticks=n_ticks,
        )
    print(summary_json(summary), flush=True)


if __name__ == "__main__":
    main()
