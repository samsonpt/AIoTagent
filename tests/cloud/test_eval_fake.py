from pathlib import Path

import pytest

from bench.eval_runner import fake_llm_for_case, load_all_eval_cases, score_eval_case, score_eval_suite
from bench.metrics import action_accept_rate, root_cause_top1

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "bench" / "eval_cases"


def test_fakellm_eval_suite_top1_and_action_accept():
    summary = score_eval_suite(directory=CASES_DIR)
    assert summary["top1"] >= 0.75
    assert summary["action_accept_rate"] >= 0.75


@pytest.mark.parametrize("case", load_all_eval_cases(CASES_DIR), ids=lambda c: c.id)
def test_each_case_scores_with_registered_scripts(case):
    llm = fake_llm_for_case(case.id)
    result = score_eval_case(case, llm=llm)
    assert result["predicted_process"] == case.root_cause_process
    assert result["action_ok"] is True
    assert result["llm_calls"] >= 1


def test_aggregate_metrics_from_suite_results():
    cases = load_all_eval_cases(CASES_DIR)
    results = [score_eval_case(c, llm=fake_llm_for_case(c.id)) for c in cases]
    top1 = root_cause_top1(
        [r["predicted_process"] for r in results],
        [c.root_cause_process for c in cases],
    )
    actions = []
    acceptable = []
    for r, c in zip(results, cases):
        ok = set(c.acceptable_actions)
        actions.append(next((a for a in r["actions"] if a in ok), r["primary_action"]))
        acceptable.append(c.acceptable_actions)
    accept = action_accept_rate(actions, acceptable)
    assert top1 >= 0.75
    assert accept >= 0.75
