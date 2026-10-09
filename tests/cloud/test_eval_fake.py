from pathlib import Path

from bench.eval_runner import fake_llm_for_case, load_all_eval_cases, score_eval_suite
from bench.metrics import action_accept_rate, root_cause_top1

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "bench" / "eval_cases"


def test_fake_llm_registered_per_case_id():
    cases = load_all_eval_cases(EVAL_DIR)
    for case in cases:
        llm = fake_llm_for_case(case.id)
        assert llm.call_count == 0


def test_fake_llm_aggregate_thresholds():
    suite = score_eval_suite(directory=EVAL_DIR)
    predicted = [r["predicted_process"] for r in suite["results"]]
    truth = [c.root_cause_process for c in suite["cases"]]
    actions = []
    acceptable = []
    for result, case in zip(suite["results"], suite["cases"]):
        ok = set(case.acceptable_actions)
        chosen = next((a for a in result["actions"] if a in ok), result["primary_action"])
        actions.append(chosen)
        acceptable.append(case.acceptable_actions)

    top1 = root_cause_top1(predicted, truth)
    accept = action_accept_rate(actions, acceptable)
    assert top1 >= 0.75
    assert accept >= 0.75
    assert suite["top1"] == top1
    assert suite["action_accept_rate"] == accept
