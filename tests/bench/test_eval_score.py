import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from bench.eval_runner import (
    RUBRIC_KEYS,
    JudgeLLM,
    load_eval_case,
    run_eval_suite,
    score_rubric,
)
from bench.metrics import root_cause_top3
from cloud.llm import FakeLLM

EVAL_DIR_CASE = Path(__file__).resolve().parents[2] / "bench" / "eval_cases" / "nc-01.yaml"


def _rubric(evidence=5, consistency=4, rationale=3, risk=2):
    return {
        "evidence": evidence,
        "consistency": consistency,
        "rationale": rationale,
        "risk": risk,
    }


def test_root_cause_top3_counts_truth_in_first_three():
    predicted = [
        ["etch", "plating", "drill"],
        ["plating", "drill", "etch"],
        ["plating", "drill", "drill", "etch"],
        ["etch"],
    ]
    truth = ["etch", "etch", "etch", "etch"]
    assert root_cause_top3(predicted, truth) == pytest.approx(0.75)


def test_root_cause_top3_empty_or_length_mismatch_is_nan():
    assert math.isnan(root_cause_top3([], []))
    assert math.isnan(root_cause_top3([["etch"]], []))
    assert math.isnan(root_cause_top3([["etch"]], ["etch", "plating"]))


def test_score_rubric_uses_separate_judge_not_agent_llm():
    agent = FakeLLM({"SupervisorDecision": {"route": "end", "reason": "unused"}})
    judge = JudgeLLM(FakeLLM({"RubricScores": _rubric()}))
    case = load_eval_case(EVAL_DIR_CASE)
    scores = score_rubric(
        judge,
        case,
        {"predicted_process": "etch", "actions": ["set_conveyor_speed"], "detail": {}},
    )
    assert scores == {
        "evidence": 5.0,
        "consistency": 4.0,
        "rationale": 3.0,
        "risk": 2.0,
    }
    assert set(scores) == set(RUBRIC_KEYS)
    assert agent.call_count == 0


def test_score_rubric_rejects_scores_outside_one_to_five():
    judge = FakeLLM({"RubricScores": _rubric(evidence=6)})
    case = load_eval_case(EVAL_DIR_CASE)
    with pytest.raises(ValidationError):
        score_rubric(judge, case, {"predicted_process": "etch"})


def test_cohens_quadratic_kappa_perfect_and_hand_computed():
    from bench.eval_runner import cohens_quadratic_kappa

    assert cohens_quadratic_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert cohens_quadratic_kappa([3, 3, 3], [3, 3, 3]) == pytest.approx(1.0)
    # 三类各一条：1=1、2=2、3→4。二次权重后 κ = 6/7。
    assert cohens_quadratic_kappa([1, 2, 3], [1, 2, 4]) == pytest.approx(6 / 7)
    assert math.isnan(cohens_quadratic_kappa([], []))
    assert math.isnan(cohens_quadratic_kappa([1], [1, 2]))


def test_quadratic_kappa_penalizes_far_disagreement_more():
    from bench.eval_runner import cohens_quadratic_kappa

    near = cohens_quadratic_kappa([1, 2, 3, 4, 5], [2, 3, 4, 5, 5])
    far = cohens_quadratic_kappa([1, 2, 3, 4, 5], [5, 5, 5, 1, 1])
    assert near > far


def test_run_eval_suite_separates_judge_and_skips_kappa_by_default():
    case = load_eval_case(EVAL_DIR_CASE)
    client = FakeLLM({"RubricScores": _rubric()})
    judge = JudgeLLM(client)
    suite = run_eval_suite([case], judge_llm=judge)
    assert "kappa" not in suite
    assert suite["n"] == 1
    assert suite["top1"] == pytest.approx(1.0)
    assert suite["top3"] == pytest.approx(1.0)
    assert suite["action_accept_rate"] == pytest.approx(1.0)
    assert suite["rubric_mean"] == {
        "evidence": 5.0,
        "consistency": 4.0,
        "rationale": 3.0,
        "risk": 2.0,
    }
    assert client.call_count == 1


def test_run_eval_suite_kappa_only_with_human_scores():
    from bench.eval_runner import cohens_quadratic_kappa

    case = load_eval_case(EVAL_DIR_CASE)
    judge = JudgeLLM(FakeLLM({"RubricScores": _rubric(risk=2)}))
    human = [_rubric(risk=1)]
    suite = run_eval_suite([case], judge_llm=judge, human_scores=human)
    assert set(suite["kappa"]) == set(RUBRIC_KEYS)
    assert suite["kappa"]["evidence"] == pytest.approx(1.0)
    assert suite["kappa"]["risk"] == pytest.approx(
        cohens_quadratic_kappa([1], [2])
    )


def test_run_eval_suite_without_judge_leaves_rubric_nan():
    case = load_eval_case(EVAL_DIR_CASE)
    suite = run_eval_suite([case])
    assert "kappa" not in suite
    assert all(math.isnan(suite["rubric_mean"][key]) for key in RUBRIC_KEYS)
