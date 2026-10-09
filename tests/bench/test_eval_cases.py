from pathlib import Path

import pytest

from bench.eval_runner import (
    CASE_SCRIPTS,
    load_all_eval_cases,
    load_eval_case,
    list_eval_case_paths,
    score_eval_case,
    score_eval_suite,
)
from cloud.llm import FakeLLM

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "bench" / "eval_cases"
REQUIRED_SCENARIOS = {
    "nozzle_clog",
    "additive_depletion",
    "sg_drift",
    "drill_wear",
    "rectifier_low",
}


def test_eval_cases_cover_required_scenarios():
    cases = load_all_eval_cases(EVAL_DIR)
    assert len(cases) >= 8
    scenarios = {c.scenario for c in cases}
    assert REQUIRED_SCENARIOS <= scenarios
    for case in cases:
        assert case.id in CASE_SCRIPTS
        assert case.root_cause_process in {"drill", "plating", "etch"}
        assert case.acceptable_actions
        assert case.tick_window
        assert case.context


def test_load_eval_case_fields():
    path = EVAL_DIR / "nc-01.yaml"
    case = load_eval_case(path)
    assert case.id == "nc-01"
    assert case.scenario == "nozzle_clog"
    assert case.root_cause_process == "etch"
    assert "set_conveyor_speed" in case.acceptable_actions


def test_score_eval_case_graph_path_hit():
    case = load_eval_case(EVAL_DIR / "nc-01.yaml")
    result = score_eval_case(case, llm=FakeLLM(CASE_SCRIPTS[case.id]))
    assert result["hit"] is True
    assert result["action_ok"] is True
    assert result["predicted_process"] == "etch"
    assert "set_conveyor_speed" in result["actions"]
    assert result["llm_calls"] >= 2


def test_fake_llm_suite_meets_thresholds():
    suite = score_eval_suite(directory=EVAL_DIR)
    assert suite["n"] >= 8
    assert suite["top1"] >= 0.75
    assert suite["action_accept_rate"] >= 0.75


def test_list_eval_case_paths_sorted():
    paths = list_eval_case_paths(EVAL_DIR)
    assert paths == sorted(paths)
    assert all(p.suffix == ".yaml" for p in paths)
