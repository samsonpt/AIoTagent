import csv
import math
from pathlib import Path

import pytest

from bench.report import (
    bootstrap_ci,
    holm_correct,
    main,
    mann_whitney_u,
    write_reports,
)

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_reproducible():
    a = bootstrap_ci([1.0, 2.0, 3.0], seed=0)
    b = bootstrap_ci([1.0, 2.0, 3.0], seed=0)
    assert a == b


def test_bootstrap_filters_nan_and_contains_mean():
    mean, lo, hi = bootstrap_ci([1.0, float("nan"), 2.0, 3.0], n_boot=200, seed=1)
    assert mean == pytest.approx(2.0)
    assert lo <= mean <= hi


def test_bootstrap_empty_is_nan():
    mean, lo, hi = bootstrap_ci([float("nan")], n_boot=10, seed=0)
    assert math.isnan(mean) and math.isnan(lo) and math.isnan(hi)


def test_holm_monotone():
    raw = [0.01, 0.04, 0.03]
    corrected = holm_correct(raw)
    assert len(corrected) == len(raw)
    for p, p2 in zip(raw, corrected):
        assert p2 >= p - 1e-12


def test_holm_known_values_keep_input_order():
    assert holm_correct([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    # 校正值按原下标返回；这一例升序后的序列与原序不同
    ordered = holm_correct([0.04, 0.01])
    assert ordered == pytest.approx([0.04, 0.02])
    assert ordered != sorted(ordered)
    assert holm_correct([0.5, 0.5]) == pytest.approx([1.0, 1.0])


def test_mann_whitney_known_values():
    # scipy.stats.mannwhitneyu(..., alternative="two-sided")，U 属于第一个样本
    u, p = mann_whitney_u([1, 2, 3], [4, 5, 6])
    assert u == pytest.approx(0.0)
    assert p == pytest.approx(0.1)
    u_tie, p_tie = mann_whitney_u([1, 2, 3, 4], [1, 2, 3, 4])
    assert u_tie == pytest.approx(8.0)
    assert p_tie == pytest.approx(1.0)


def test_mann_whitney_ignores_nan():
    full = mann_whitney_u([1, 2, 3], [4, 5, 6])
    with_nan = mann_whitney_u([1, float("nan"), 2, 3], [4, 5, 6])
    assert with_nan[0] == pytest.approx(full[0])
    assert with_nan[1] == pytest.approx(full[1])


def _mini_results(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "cell_id,scenario,config,seed,status,error,db,fpy,fpr,arg,caf,es",
                "ad__baseline_rule__s1,additive_depletion,baseline_rule,1,ok,,a.db,1,0.4,0.5,0.2,3",
                "ad__baseline_rule__s2,additive_depletion,baseline_rule,2,ok,,b.db,3,0.2,0.3,0.4,1",
                "ad__baseline_rule__bad,additive_depletion,baseline_rule,3,error,boom,c.db,0,0,0,0,0",
                "ad__baseline_rule__nan,additive_depletion,baseline_rule,4,ok,,d.db,nan,0.1,0.1,0.1,nan",
                "ad__edge_only__s1,additive_depletion,edge_only,1,ok,,e.db,2,0.2,0.2,0.2,2",
                "ad__edge_only__s2,additive_depletion,edge_only,2,ok,,f.db,2,0.2,0.2,0.2,2",
                "ad__full__s1,additive_depletion,full,1,ok,,g.db,4,0.0,0.1,0.1,0.5",
                "ad__full__s2,additive_depletion,full,2,ok,,h.db,6,0.0,0.1,0.1,0.5",
                "ad__full_minus_use_rag__s1,additive_depletion,full_minus_use_rag,1,ok,,i.db,1,0.3,0.4,0.3,2",
                "ad__full_twin_none__s1,additive_depletion,full_twin_none,1,ok,,j.db,1,0.5,0.6,0.4,4",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_write_reports_outputs_ci_holm_and_table_iv(tmp_path):
    results = tmp_path / "results.csv"
    _mini_results(results)
    out = tmp_path / "out"
    notes = "自动放行，approval_delay_s=900"
    write_reports(results, out, human_model_notes=notes)
    write_reports(results, out, human_model_notes=notes)

    summary = _rows(out / "summary_by_config.csv")
    by_scenario = _rows(out / "summary_by_scenario_config.csv")
    comparisons = _rows(out / "comparisons.csv")
    report = (out / "report.md").read_text(encoding="utf-8")

    assert "表 IV" in report
    assert notes in report

    fpy = next(row for row in summary if row["config"] == "baseline_rule" and row["metric"] == "fpy")
    assert int(fpy["n"]) == 2
    assert float(fpy["mean"]) == pytest.approx(2.0)
    assert float(fpy["ci_lo"]) <= float(fpy["mean"]) <= float(fpy["ci_hi"])

    scenario_fpy = next(
        row
        for row in by_scenario
        if row["scenario"] == "additive_depletion"
        and row["config"] == "baseline_rule"
        and row["metric"] == "fpy"
    )
    assert int(scenario_fpy["n"]) == 2
    assert float(scenario_fpy["mean"]) == pytest.approx(2.0)

    main_fpy = next(
        row
        for row in comparisons
        if row["group"] == "main"
        and row["metric"] == "fpy"
        and row["config_a"] == "baseline_rule"
        and row["config_b"] == "full"
    )
    assert main_fpy["direction"] == "lower"
    assert float(main_fpy["p_holm"]) > float(main_fpy["p"])
    u, p = mann_whitney_u([1.0, 3.0], [4.0, 6.0])
    assert float(main_fpy["u"]) == pytest.approx(u)
    assert float(main_fpy["p"]) == pytest.approx(p)
    for row in comparisons:
        assert float(row["p_holm"]) >= float(row["p"]) - 1e-12

    ablation = {
        (row["config_a"], row["config_b"])
        for row in comparisons
        if row["group"] == "ablation" and row["metric"] == "fpy"
    }
    assert ablation == {("full", "full_minus_use_rag"), ("full", "full_twin_none")}

    again = (out / "summary_by_config.csv").read_text(encoding="utf-8")
    write_reports(results, out, human_model_notes=notes)
    assert (out / "summary_by_config.csv").read_text(encoding="utf-8") == again


def test_report_cli(tmp_path):
    results = tmp_path / "results.csv"
    _mini_results(results)
    out = tmp_path / "cli-out"
    code = main(["--results", str(results), "--out", str(out), "--human-notes", "CLI备注"])
    assert code == 0
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "表 IV" in text
    assert "CLI备注" in text


def test_run_matrix_writes_report_when_results_exist(tmp_path, monkeypatch):
    from bench.matrix_runner import run_matrix

    def fake_run_cell(cell, out_dir, *, llm, n_ticks=None):
        return {
            "cell_id": cell.cell_id,
            "scenario": cell.scenario.stem,
            "config": cell.config.stem,
            "seed": cell.seed,
            "status": "ok",
            "error": "",
            "db": f"{cell.cell_id}.db",
            "fpy": 0.9,
            "fpr": 0.1,
            "arg": 0.2,
            "caf": 0.3,
            "es": 1.0,
        }

    monkeypatch.setattr("bench.matrix_runner.run_cell", fake_run_cell)
    run_matrix(
        tmp_path,
        scenarios=[ROOT / "bench" / "scenarios" / "nominal.yaml"],
        configs=[ROOT / "bench" / "configs" / "baseline_rule.yaml"],
        seeds=[42],
        llm="fake",
    )
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "表 IV" in report
    assert (tmp_path / "summary_by_config.csv").is_file()
    assert (tmp_path / "comparisons.csv").is_file()
