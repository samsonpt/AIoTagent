from pathlib import Path

import pytest

from bench.eval_runner import (
    CASE_SCRIPTS,
    EvalCase,
    load_all_eval_cases,
    load_eval_case,
    score_eval_case,
    score_eval_suite,
)
from bench.metrics import action_accept_rate, root_cause_top1
from cloud.llm import FakeLLM

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "bench" / "eval_cases"
REQUIRED_SCENARIOS = {
    "nozzle_clog",
    "additive_depletion",
    "sg_drift",
    "drill_wear",
    "rectifier_low",
}


def test_eval_cases_cover_scenarios_and_fields():
    cases = load_all_eval_cases(CASES_DIR)
    assert len(cases) >= 8
    scenarios = {c.scenario for c in cases}
    assert REQUIRED_SCENARIOS <= scenarios
    for case in cases:
        assert case.id
        assert case.scenario
        assert isinstance(case.seed, int)
        assert len(case.tick_window) == 2
        assert isinstance(case.context, dict)
        assert case.root_cause_process in {"drill", "plating", "etch"}
        assert case.acceptable_actions
        assert case.notes
        assert case.id in CASE_SCRIPTS


def test_load_eval_case_nc01():
    case = load_eval_case(CASES_DIR / "nc-01.yaml")
    assert isinstance(case, EvalCase)
    assert case.id == "nc-01"
    assert case.scenario == "nozzle_clog"
    assert case.root_cause_process == "etch"


def test_score_eval_case_hit_and_action():
    case = load_eval_case(CASES_DIR / "nc-01.yaml")
    llm = FakeLLM(CASE_SCRIPTS[case.id])
    result = score_eval_case(case, llm=llm)
    assert result["hit"] is True
    assert result["action_ok"] is True
    assert result["predicted_process"] == "etch"
    assert "set_conveyor_speed" in result["actions"]
    assert result["llm_calls"] >= 2


def test_fake_llm_suite_meets_thresholds():
    summary = score_eval_suite(directory=CASES_DIR)
    assert summary["n"] >= 8
    assert summary["top1"] >= 0.75
    assert summary["action_accept_rate"] >= 0.75
    predicted = [r["predicted_process"] for r in summary["results"]]
    truth = [c.root_cause_process for c in summary["cases"]]
    assert root_cause_top1(predicted, truth) == pytest.approx(summary["top1"])
    actions = []
    acceptable = []
    for r, c in zip(summary["results"], summary["cases"]):
        ok = set(c.acceptable_actions)
        actions.append(next((a for a in r["actions"] if a in ok), r["primary_action"]))
        acceptable.append(c.acceptable_actions)
    assert action_accept_rate(actions, acceptable) == pytest.approx(summary["action_accept_rate"])
