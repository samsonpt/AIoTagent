"""消融 results.csv 的 bootstrap 区间、Mann-Whitney / Holm 与 Markdown 报告。"""

from __future__ import annotations

import argparse
import csv
import math
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
from scipy.stats import mannwhitneyu

from common.rng import make_rng

META_COLUMNS = frozenset({"cell_id", "scenario", "config", "seed", "status", "error", "db"})
MAIN_CONFIGS = ("baseline_rule", "edge_only", "full")
TABLE_IV_METRICS = ("es", "arg", "fpr", "caf")
DEFAULT_HUMAN_MODEL_NOTES = (
    "矩阵以 auto_approve=True 运行，HumanModel 立即放行待审批动作；"
    "构造参数默认 delay_ticks=2、approval_delay_s=900，自动放行时不引入审批延迟。"
)

_SUMMARY_FIELDS = ("metric", "n", "mean", "ci_lo", "ci_hi")
_COMPARISON_FIELDS = (
    "group",
    "metric",
    "config_a",
    "config_b",
    "n_a",
    "n_b",
    "mean_a",
    "mean_b",
    "u",
    "p",
    "p_holm",
    "direction",
)


def bootstrap_ci(
    values: list[float],
    *,
    n_boot: int = 10000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float, float]:
    """样本均值与百分位法 bootstrap 置信区间。nan 不参与。"""
    clean = _finite(values)
    if not clean:
        nan = float("nan")
        return (nan, nan, nan)
    sample = np.asarray(clean, dtype=float)
    mean = float(sample.mean())
    if sample.size == 1 or n_boot <= 0:
        return (mean, mean, mean)
    rng = make_rng(seed, "bootstrap")
    index = rng.integers(0, sample.size, size=(n_boot, sample.size))
    means = sample[index].mean(axis=1)
    lo = float(np.quantile(means, alpha / 2.0))
    hi = float(np.quantile(means, 1.0 - alpha / 2.0))
    return (mean, lo, hi)


def mann_whitney_u(x, y) -> tuple[float, float]:
    """双侧 Mann-Whitney U。返回第一个样本的 U 与 p；nan 不参与。"""
    xs = _finite(x)
    ys = _finite(y)
    if not xs or not ys:
        return (float("nan"), float("nan"))
    result = mannwhitneyu(xs, ys, alternative="two-sided")
    return (float(result.statistic), float(result.pvalue))


def holm_correct(pvalues: list[float]) -> list[float]:
    """Holm 逐步校正，结果与输入逐项对应，并截断到 1。"""
    count = len(pvalues)
    adjusted = [0.0] * count
    order = sorted(range(count), key=lambda index: (pvalues[index], index))
    running = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (count - rank) * float(pvalues[index]))
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def write_reports(
    results_csv: Path,
    out_dir: Path,
    *,
    human_model_notes: str = DEFAULT_HUMAN_MODEL_NOTES,
) -> None:
    """写出按配置 / 场景汇总、comparisons.csv 与 report.md。"""
    results_csv = Path(results_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, metrics = _load_results(results_csv)
    by_config = _summarize(rows, metrics, ("config",))
    by_scenario = _summarize(rows, metrics, ("scenario", "config"))
    comparisons = _compare(rows, metrics)
    _write_csv(
        out_dir / "summary_by_config.csv",
        ("config", *_SUMMARY_FIELDS),
        by_config,
    )
    _write_csv(
        out_dir / "summary_by_scenario_config.csv",
        ("scenario", "config", *_SUMMARY_FIELDS),
        by_scenario,
    )
    _write_csv(out_dir / "comparisons.csv", _COMPARISON_FIELDS, comparisons)
    (out_dir / "report.md").write_text(
        _render_markdown(by_config, comparisons, human_model_notes),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.report",
        description="由 results.csv 生成消融汇总与 Markdown 报告",
    )
    parser.add_argument("--results", required=True, help="矩阵 results.csv")
    parser.add_argument("--out", required=True, help="汇总与 report.md 的输出目录")
    parser.add_argument("--human-notes", default=DEFAULT_HUMAN_MODEL_NOTES, help="写入报告的 HumanModel 说明")
    args = parser.parse_args(argv)
    write_reports(Path(args.results), Path(args.out), human_model_notes=args.human_notes)
    return 0


def _finite(values: Iterable) -> list[float]:
    clean: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(number):
            continue
        clean.append(number)
    return clean


def _load_results(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader if _is_ok(row)]
    metrics = [name for name in fieldnames if name not in META_COLUMNS and _column_is_numeric(rows, name)]
    return rows, metrics


def _is_ok(row: dict) -> bool:
    if "status" not in row:
        return True
    return (row.get("status") or "").strip().lower() == "ok"


def _column_is_numeric(rows: Sequence[dict], name: str) -> bool:
    for row in rows:
        if _parse_float(row.get(name)) is not None:
            return True
    return False


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _metric_values(rows: Sequence[dict], metric: str) -> list[float]:
    return _finite(_parse_float(row.get(metric)) for row in rows)


def _summarize(rows: Sequence[dict], metrics: Sequence[str], keys: Sequence[str]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        grouped.setdefault(tuple(row.get(key, "") for key in keys), []).append(row)
    summary: list[dict] = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        for metric in metrics:
            values = _metric_values(group_rows, metric)
            mean, lo, hi = bootstrap_ci(values)
            record = {column: key[index] for index, column in enumerate(keys)}
            record.update(metric=metric, n=len(values), mean=mean, ci_lo=lo, ci_hi=hi)
            summary.append(record)
    return summary


def _is_ablation(config: str) -> bool:
    return config.startswith("full_minus_") or config.startswith("full_twin_")


def _compare(rows: Sequence[dict], metrics: Sequence[str]) -> list[dict]:
    present = {row.get("config", "") for row in rows}
    main = [config for config in MAIN_CONFIGS if config in present]
    main_pairs = [(main[i], main[j]) for i in range(len(main)) for j in range(i + 1, len(main))]
    ablation_pairs = [("full", config) for config in sorted(present) if _is_ablation(config) and "full" in present]
    records: list[dict] = []
    for group, pairs in (("main", main_pairs), ("ablation", ablation_pairs)):
        drafted: list[dict] = []
        for metric in metrics:
            for config_a, config_b in pairs:
                values_a = _metric_values([row for row in rows if row.get("config") == config_a], metric)
                values_b = _metric_values([row for row in rows if row.get("config") == config_b], metric)
                if not values_a or not values_b:
                    continue
                statistic, pvalue = mann_whitney_u(values_a, values_b)
                mean_a = float(np.mean(values_a))
                mean_b = float(np.mean(values_b))
                drafted.append(
                    {
                        "group": group,
                        "metric": metric,
                        "config_a": config_a,
                        "config_b": config_b,
                        "n_a": len(values_a),
                        "n_b": len(values_b),
                        "mean_a": mean_a,
                        "mean_b": mean_b,
                        "u": statistic,
                        "p": pvalue,
                        "direction": _direction(mean_a, mean_b),
                    }
                )
        corrected = holm_correct([float(item["p"]) for item in drafted])
        for item, p_holm in zip(drafted, corrected):
            item["p_holm"] = p_holm
            records.append(item)
    return records


def _direction(mean_a: float, mean_b: float) -> str:
    if mean_a > mean_b:
        return "higher"
    if mean_a < mean_b:
        return "lower"
    return "equal"


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fieldnames})


def _csv_value(value: object) -> object:
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return value


def _render_markdown(summary: Sequence[dict], comparisons: Sequence[dict], human_model_notes: str) -> str:
    main_summary = [row for row in summary if row["config"] in MAIN_CONFIGS]
    table_iv = [row for row in summary if row["metric"] in TABLE_IV_METRICS]
    significant = [row for row in comparisons if float(row["p_holm"]) < 0.05]
    lines = [
        "# 消融报告",
        "",
        "## 设置",
        "",
        "分析单元是单次运行（场景 × 配置 × 种子）。指标先在运行内计算，再跨运行取均值；",
        "nan 不进入该指标，表中 n 为有效样本数。区间是 10000 次有放回 bootstrap 的 95% 百分位置信区间（seed=0）。",
        "配置之间用双侧 Mann-Whitney U（α=0.05）；同一张对比表内的 p 值用 Holm 法校正。",
        "主对比为 baseline_rule、edge_only、full；消融为每个 full_minus_* 与 full_twin_* 相对 full。",
        "",
        "## HumanModel",
        "",
        human_model_notes,
        "",
        "## 主对比",
        "",
        _markdown_table(("config", "metric", "n", "mean", "ci_lo", "ci_hi"), main_summary),
        "",
        "## 表 IV",
        "",
        "表 IV 维度为 ES、ARG、FPR、CAF（越低越好）。",
        "",
        _markdown_table(("config", "metric", "n", "mean", "ci_lo", "ci_hi"), table_iv),
        "",
        "## 显著结果",
        "",
    ]
    if not significant:
        lines.append("无 Holm 校正后达到 α=0.05 的比较。")
    else:
        lines.append(_markdown_table(("group", "metric", "config_a", "config_b", "p", "p_holm", "direction"), significant))
    lines.append("")
    return "\n".join(lines)


def _markdown_table(columns: Sequence[str], rows: Sequence[dict]) -> str:
    header = "| " + " | ".join(columns) + " |"
    rule = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(_fmt(row.get(column, "")) for column in columns) + " |" for row in rows]
    return "\n".join([header, rule, *body])


def _fmt(value: object) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "—"
        return f"{value:.6g}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
