import json
import os
from pathlib import Path

import pytest

from bench.matrix_runner import (
    expand_cells,
    main,
    run_cell,
    run_matrix,
)
from bench.metrics import COMPUTE_ALL_KEYS

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "bench" / "scenarios"
CONFIGS = ROOT / "bench" / "configs"


def _ok_row(cell, **extra):
    row = {
        "cell_id": cell.cell_id,
        "scenario": cell.scenario.stem,
        "config": cell.config.stem,
        "seed": cell.seed,
        "status": "ok",
        "error": "",
        "db": f"{cell.cell_id}.db",
        "fpy": 0.9,
    }
    row.update(extra)
    return row


def test_expand_cells_ids():
    cells = expand_cells(
        [SCENARIOS / "additive_depletion.yaml", SCENARIOS / "nominal.yaml"],
        [CONFIGS / "full.yaml"],
        [42, 43],
    )
    assert [cell.cell_id for cell in cells] == [
        "additive_depletion__full__s42",
        "additive_depletion__full__s43",
        "nominal__full__s42",
        "nominal__full__s43",
    ]
    assert cells[0].scenario == SCENARIOS / "additive_depletion.yaml"
    assert cells[0].config == CONFIGS / "full.yaml"
    assert cells[0].seed == 42


def test_expand_and_resume(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_run_cell(cell, out_dir, *, llm, n_ticks=None):
        calls.append(cell.cell_id)
        assert llm == "fake"
        return _ok_row(cell)

    monkeypatch.setattr("bench.matrix_runner.run_cell", fake_run_cell)
    scenarios = [SCENARIOS / "additive_depletion.yaml"]
    configs = [CONFIGS / "baseline_rule.yaml", CONFIGS / "edge_only.yaml"]
    path = run_matrix(
        tmp_path,
        scenarios=scenarios,
        configs=configs,
        seeds=[42],
        llm="fake",
        resume=True,
    )
    assert path == tmp_path / "results.csv"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "additive_depletion" in text
    assert "baseline_rule" in text
    assert "edge_only" in text
    assert len(calls) == 2
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["cells"]) == set(calls)
    assert all(item["status"] == "ok" for item in manifest["cells"].values())

    run_matrix(
        tmp_path,
        scenarios=scenarios,
        configs=configs,
        seeds=[42],
        llm="fake",
        resume=True,
    )
    assert len(calls) == 2


def test_resume_retries_error_cells(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_run_cell(cell, out_dir, *, llm, n_ticks=None):
        calls.append(cell.cell_id)
        status = "error" if len(calls) == 1 else "ok"
        return _ok_row(cell, status=status, error="boom" if status == "error" else "")

    monkeypatch.setattr("bench.matrix_runner.run_cell", fake_run_cell)
    scenarios = [SCENARIOS / "nominal.yaml"]
    configs = [CONFIGS / "baseline_rule.yaml"]
    run_matrix(tmp_path, scenarios=scenarios, configs=configs, seeds=[42], llm="fake")
    assert len(calls) == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cells"]["nominal__baseline_rule__s42"]["status"] == "error"

    run_matrix(tmp_path, scenarios=scenarios, configs=configs, seeds=[42], llm="fake", resume=True)
    assert len(calls) == 2
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cells"]["nominal__baseline_rule__s42"]["status"] == "ok"
    rows = (tmp_path / "results.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2
    assert rows[-1].split(",")[0] == "nominal__baseline_rule__s42" or "ok" in rows[-1]


def test_matrix_continues_when_cell_raises(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_run_cell(cell, out_dir, *, llm, n_ticks=None):
        calls.append(cell.cell_id)
        if cell.config.stem == "baseline_rule":
            raise RuntimeError("cell crashed")
        return _ok_row(cell)

    monkeypatch.setattr("bench.matrix_runner.run_cell", fake_run_cell)
    path = run_matrix(
        tmp_path,
        scenarios=[SCENARIOS / "nominal.yaml"],
        configs=[CONFIGS / "baseline_rule.yaml", CONFIGS / "full.yaml"],
        seeds=[42],
        llm="fake",
    )
    assert len(calls) == 2
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cells"]["nominal__baseline_rule__s42"]["status"] == "error"
    assert "cell crashed" in manifest["cells"]["nominal__baseline_rule__s42"]["error"]
    assert manifest["cells"]["nominal__full__s42"]["status"] == "ok"
    assert path.exists()


def test_no_resume_reruns_ok_cells(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_run_cell(cell, out_dir, *, llm, n_ticks=None):
        calls.append(cell.cell_id)
        return _ok_row(cell)

    monkeypatch.setattr("bench.matrix_runner.run_cell", fake_run_cell)
    kwargs = dict(
        scenarios=[SCENARIOS / "nominal.yaml"],
        configs=[CONFIGS / "baseline_rule.yaml"],
        seeds=[42],
        llm="fake",
        resume=False,
    )
    run_matrix(tmp_path, **kwargs)
    run_matrix(tmp_path, **kwargs)
    assert len(calls) == 2


def test_run_cell_overrides_seed_auto_approve_and_metrics(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run(scenario, store, *args, **kwargs):
        captured["seed"] = scenario.seed
        captured["auto_approve"] = kwargs.get("auto_approve")
        captured["ablation"] = kwargs.get("ablation")
        captured["n_ticks"] = kwargs.get("n_ticks")
        captured["store"] = store

    monkeypatch.setattr("bench.matrix_runner.run", fake_run)
    monkeypatch.setattr(
        "bench.matrix_runner.compute_all",
        lambda store: {key: 1.0 for key in COMPUTE_ALL_KEYS},
    )
    cell = expand_cells(
        [SCENARIOS / "additive_depletion.yaml"],
        [CONFIGS / "baseline_rule.yaml"],
        [99],
    )[0]
    row = run_cell(cell, tmp_path, llm="fake", n_ticks=3)
    assert captured["seed"] == 99
    assert captured["auto_approve"] is True
    assert captured["ablation"].name == "baseline_rule"
    assert captured["n_ticks"] == 3
    assert row["status"] == "ok"
    assert row["scenario"] == "additive_depletion"
    assert row["config"] == "baseline_rule"
    assert row["seed"] == 99
    assert row["cell_id"] == "additive_depletion__baseline_rule__s99"
    assert (tmp_path / "additive_depletion__baseline_rule__s99.db").exists()
    for key in COMPUTE_ALL_KEYS:
        assert row[key] == 1.0


def test_run_cell_fake_ignores_api_key_then_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    seen: dict = {}

    def fake_run(scenario, store, *args, **kwargs):
        seen["during"] = os.environ.get("DEEPSEEK_API_KEY")

    monkeypatch.setattr("bench.matrix_runner.run", fake_run)
    monkeypatch.setattr("bench.matrix_runner.compute_all", lambda store: {"fpy": 1.0})
    cell = expand_cells(
        [SCENARIOS / "nominal.yaml"],
        [CONFIGS / "baseline_rule.yaml"],
        [42],
    )[0]
    row = run_cell(cell, tmp_path, llm="fake")
    assert row["status"] == "ok"
    assert seen["during"] is None
    assert os.environ.get("DEEPSEEK_API_KEY") == "secret"


def test_run_cell_deepseek_missing_key_is_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def fake_run(*args, **kwargs):
        raise AssertionError("should not run")

    monkeypatch.setattr("bench.matrix_runner.run", fake_run)
    cell = expand_cells(
        [SCENARIOS / "nominal.yaml"],
        [CONFIGS / "full.yaml"],
        [42],
    )[0]
    row = run_cell(cell, tmp_path, llm="deepseek")
    assert row["status"] == "error"
    assert "DEEPSEEK_API_KEY" in row["error"]
    assert not (tmp_path / row["db"]).exists()


def test_run_cell_exception_becomes_error_row(tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise RuntimeError("sim blew up")

    monkeypatch.setattr("bench.matrix_runner.run", fake_run)
    cell = expand_cells(
        [SCENARIOS / "nominal.yaml"],
        [CONFIGS / "baseline_rule.yaml"],
        [42],
    )[0]
    row = run_cell(cell, tmp_path, llm="fake")
    assert row["status"] == "error"
    assert "sim blew up" in row["error"]


def test_cli_resolves_subset(tmp_path, monkeypatch):
    flags: dict = {}

    def fake_load_env():
        flags["loaded"] = True

    def fake_run_matrix(out_dir, scenarios, configs, seeds, *, llm, resume=True, n_ticks=None):
        flags["out"] = Path(out_dir)
        flags["scenarios"] = [path.stem for path in scenarios]
        flags["configs"] = [path.stem for path in configs]
        flags["seeds"] = list(seeds)
        flags["llm"] = llm
        flags["resume"] = resume
        flags["n_ticks"] = n_ticks
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        path = Path(out_dir) / "results.csv"
        path.write_text("status\nok\n", encoding="utf-8")
        return path

    monkeypatch.setattr("bench.matrix_runner.load_env", fake_load_env)
    monkeypatch.setattr("bench.matrix_runner.run_matrix", fake_run_matrix)
    code = main(
        [
            "--out",
            str(tmp_path),
            "--llm",
            "fake",
            "--scenarios",
            "additive_depletion",
            "--configs",
            "baseline_rule,edge_only,full",
            "--seeds",
            "42",
            "--ticks",
            "3",
        ]
    )
    assert code == 0
    assert flags["loaded"] is True
    assert flags["out"] == tmp_path
    assert flags["scenarios"] == ["additive_depletion"]
    assert flags["configs"] == ["baseline_rule", "edge_only", "full"]
    assert flags["seeds"] == [42]
    assert flags["llm"] == "fake"
    assert flags["resume"] is True
    assert flags["n_ticks"] == 3


def test_cli_no_resume_flag(tmp_path, monkeypatch):
    flags: dict = {}

    def fake_run_matrix(out_dir, scenarios, configs, seeds, *, llm, resume=True, n_ticks=None):
        flags["resume"] = resume
        flags["n_scenarios"] = len(scenarios)
        flags["n_configs"] = len(configs)
        flags["seeds"] = list(seeds)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return Path(out_dir) / "results.csv"

    monkeypatch.setattr("bench.matrix_runner.load_env", lambda: None)
    monkeypatch.setattr("bench.matrix_runner.run_matrix", fake_run_matrix)
    main(["--out", str(tmp_path), "--llm", "fake", "--no-resume", "--seeds", "42,43"])
    assert flags["resume"] is False
    assert flags["seeds"] == [42, 43]
    assert flags["n_scenarios"] == len(list(SCENARIOS.glob("*.yaml")))
    assert flags["n_configs"] == len(list(CONFIGS.glob("*.yaml")))


def test_cli_rejects_unknown_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr("bench.matrix_runner.load_env", lambda: None)
    with pytest.raises(SystemExit):
        main(["--out", str(tmp_path), "--scenarios", "does_not_exist", "--llm", "fake"])
