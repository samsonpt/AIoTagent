"""场景 × 配置 × seed 串行消融矩阵，支持按 manifest 续跑。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from bench.metrics import COMPUTE_ALL_KEYS, compute_all
from bench.schema import TraceStore
from common.config import load_ablation
from common.env import load_env
from sim.faults import load_scenario
from sim.runner import run

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "bench" / "scenarios"
CONFIG_DIR = ROOT / "bench" / "configs"
DEFAULT_SEEDS = (42, 43, 44, 45, 46)

BASE_COLUMNS = (
    "cell_id",
    "scenario",
    "config",
    "seed",
    "status",
    "error",
    "db",
)

LlmMode = Literal["deepseek", "fake"]


@dataclass(frozen=True)
class Cell:
    scenario: Path
    config: Path
    seed: int
    cell_id: str


def expand_cells(scenarios: list[Path], configs: list[Path], seeds: list[int]) -> list[Cell]:
    cells: list[Cell] = []
    for scenario in scenarios:
        for config in configs:
            for seed in seeds:
                cell_id = f"{scenario.stem}__{config.stem}__s{seed}"
                cells.append(Cell(scenario=scenario, config=config, seed=seed, cell_id=cell_id))
    return cells


def run_cell(
    cell: Cell,
    out_dir: Path,
    *,
    llm: LlmMode,
    n_ticks: int | None = None,
) -> dict:
    """跑一格：写 sqlite、`auto_approve=True`、用 cell.seed 覆盖场景种子，返回 CSV 行。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    db_name = f"{cell.cell_id}.db"
    if llm == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY"):
        return _row(cell, status="error", error="DEEPSEEK_API_KEY is required", db=db_name)

    scenario = load_scenario(cell.scenario).model_copy(update={"seed": cell.seed})
    ablation = load_ablation(cell.config)
    db_path = out_dir / db_name
    _reset_db(db_path)
    try:
        with _llm_env(llm):
            with TraceStore(db_path) as store:
                run(
                    scenario,
                    store,
                    ablation=ablation,
                    auto_approve=True,
                    n_ticks=n_ticks,
                )
                metrics = compute_all(store)
    except Exception as exc:
        return _row(cell, status="error", error=str(exc), db=db_name)

    row = _row(cell, status="ok", error="", db=db_name)
    for key in COMPUTE_ALL_KEYS:
        row[key] = metrics.get(key, float("nan"))
    return row


def run_matrix(
    out_dir: Path,
    scenarios: list[Path],
    configs: list[Path],
    seeds: list[int],
    *,
    llm: LlmMode = "deepseek",
    resume: bool = True,
    n_ticks: int | None = None,
) -> Path:
    """串行跑矩阵。`resume=True` 时跳过 manifest 里 `status==ok` 的格，失败格重试。

    写出 results.csv 后调用 write_reports，生成汇总 CSV 与 report.md。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    results_path = out_dir / "results.csv"
    manifest = _load_manifest(manifest_path)
    manifest["llm"] = llm
    rows_by_id = _read_results(results_path)
    cells = expand_cells(list(scenarios), list(configs), list(seeds))

    for cell in cells:
        previous = manifest["cells"].get(cell.cell_id)
        if resume and previous and previous.get("status") == "ok":
            rows_by_id.setdefault(cell.cell_id, dict(previous))
            continue
        try:
            row = run_cell(cell, out_dir, llm=llm, n_ticks=n_ticks)
        except Exception as exc:
            row = _row(cell, status="error", error=str(exc), db=f"{cell.cell_id}.db")
        rows_by_id[cell.cell_id] = row
        manifest["cells"][cell.cell_id] = _manifest_entry(row)
        _write_results(results_path, _ordered_rows(cells, rows_by_id))
        _write_manifest(manifest_path, manifest)

    if not results_path.exists():
        _write_results(results_path, _ordered_rows(cells, rows_by_id))
        _write_manifest(manifest_path, manifest)
    if results_path.is_file():
        from bench.report import write_reports

        write_reports(results_path, out_dir)
    return results_path


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(
        prog="python scripts/run_matrix.py",
        description="串行运行消融矩阵，写出 results.csv 与 manifest.json",
    )
    parser.add_argument("--out", required=True, help="输出目录（results.csv / manifest.json / 每格 .db）")
    parser.add_argument("--llm", choices=("deepseek", "fake"), default="deepseek")
    parser.add_argument(
        "--seeds",
        default=",".join(str(seed) for seed in DEFAULT_SEEDS),
        help="逗号分隔种子，默认 42,43,44,45,46",
    )
    parser.add_argument("--scenarios", default=None, help="逗号分隔场景名或 YAML 路径；默认全部")
    parser.add_argument("--configs", default=None, help="逗号分隔配置名或 YAML 路径；默认全部")
    parser.add_argument("--ticks", type=int, default=None, help="覆盖场景 n_ticks")
    parser.add_argument("--no-resume", action="store_true", help="忽略已成功的格子，全部重跑")
    parser.add_argument("--jobs", type=int, default=1, help="预留并行度；当前仅支持 1")
    args = parser.parse_args(argv)
    if args.jobs != 1:
        parser.error("当前仅支持串行（--jobs 1）")
    try:
        scenarios = _resolve_entries(args.scenarios, SCENARIO_DIR)
        configs = _resolve_entries(args.configs, CONFIG_DIR)
        seeds = _parse_seeds(args.seeds)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    run_matrix(
        Path(args.out),
        scenarios,
        configs,
        seeds,
        llm=args.llm,
        resume=not args.no_resume,
        n_ticks=args.ticks,
    )
    return 0


def _row(cell: Cell, *, status: str, error: str, db: str) -> dict:
    return {
        "cell_id": cell.cell_id,
        "scenario": cell.scenario.stem,
        "config": cell.config.stem,
        "seed": cell.seed,
        "status": status,
        "error": error,
        "db": db,
    }


def _manifest_entry(row: dict) -> dict:
    return {key: row.get(key, "") for key in BASE_COLUMNS}


def _ordered_rows(cells: list[Cell], rows_by_id: dict[str, dict]) -> list[dict]:
    ordered = [rows_by_id[cell.cell_id] for cell in cells if cell.cell_id in rows_by_id]
    current = {cell.cell_id for cell in cells}
    for cell_id, row in rows_by_id.items():
        if cell_id not in current:
            ordered.append(row)
    return ordered


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"cells": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"cells": {}}
    cells = data.get("cells")
    if not isinstance(cells, dict):
        data["cells"] = {}
    return data


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_results(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: dict[str, dict] = {}
        for row in reader:
            cell_id = (row.get("cell_id") or "").strip()
            if cell_id:
                rows[cell_id] = dict(row)
        return rows


def _write_results(path: Path, rows: list[dict]) -> None:
    fieldnames = _fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fieldnames})


def _fieldnames(rows: list[dict]) -> list[str]:
    names = list(BASE_COLUMNS)
    seen = set(names)
    for key in COMPUTE_ALL_KEYS:
        if any(key in row for row in rows):
            names.append(key)
            seen.add(key)
    for row in rows:
        for key in row:
            if key not in seen:
                names.append(key)
                seen.add(key)
    return names


def _csv_value(value: object) -> object:
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return value


def _reset_db(path: Path) -> None:
    for item in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        item.unlink(missing_ok=True)


@contextmanager
def _llm_env(llm: LlmMode) -> Iterator[None]:
    """`--llm fake` 时临时去掉 DEEPSEEK_API_KEY，让 from_ablation 走 FakeLLM。"""
    if llm != "fake":
        yield
        return
    saved = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        yield
    finally:
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved


def _resolve_entries(raw: str | None, directory: Path) -> list[Path]:
    if raw is None or not str(raw).strip():
        paths = sorted(directory.glob("*.yaml"))
        if not paths:
            raise FileNotFoundError(f"目录中没有 YAML: {directory}")
        return paths
    resolved: list[Path] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        path = Path(token)
        if path.suffix == ".yaml" or "/" in token or "\\" in token:
            if not path.is_absolute():
                path = ROOT / path
        else:
            path = directory / f"{token}.yaml"
        if not path.is_file():
            raise FileNotFoundError(path)
        resolved.append(path)
    if not resolved:
        raise FileNotFoundError("未给出有效条目")
    return resolved


def _parse_seeds(raw: str) -> list[int]:
    seeds: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        seeds.append(int(token))
    if not seeds:
        raise ValueError("至少提供一个 seed")
    return seeds
