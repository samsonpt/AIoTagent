import pytest
from pydantic import ValidationError

from cloud.llm import DeepSeekLLM, FakeLLM, default_fake_llm
from cloud.schemas import (
    MaintPlan,
    RootCauseHypothesis,
    SupervisorDecision,
    TuneCandidate,
    TunePlan,
)


def test_default_fake_llm_supervisor_noop_many_times():
    llm = default_fake_llm(supervisor_cycles=4)
    for _ in range(4):
        out = llm.complete([], response_model=SupervisorDecision)
        assert out.route == "end"
    with pytest.raises(KeyError, match="SupervisorDecision"):
        llm.complete([], response_model=SupervisorDecision)


def test_fake_llm_returns_scripted_model():
    llm = FakeLLM({"SupervisorDecision": {"route": "quality_rca", "reason": "aoi"}})
    out = llm.complete([{"role": "user", "content": "x"}], response_model=SupervisorDecision)
    assert out.route == "quality_rca"
    assert out.reason == "aoi"


def test_fake_llm_queue_pops_in_order():
    llm = FakeLLM(
        {
            "SupervisorDecision": [
                {"route": "quality_rca", "reason": "first"},
                {"route": "maint", "reason": "second"},
            ]
        }
    )
    first = llm.complete([], response_model=SupervisorDecision)
    second = llm.complete([], response_model=SupervisorDecision)
    assert first.route == "quality_rca"
    assert second.route == "maint"


def test_fake_llm_missing_key_raises():
    llm = FakeLLM({})
    with pytest.raises(KeyError):
        llm.complete([], response_model=SupervisorDecision)


def test_fake_llm_empty_queue_raises():
    llm = FakeLLM({"SupervisorDecision": []})
    with pytest.raises(KeyError):
        llm.complete([], response_model=SupervisorDecision)


def test_supervisor_decision_frozen():
    model = SupervisorDecision(route="end", reason="done")
    with pytest.raises(ValidationError):
        model.route = "maint"


def test_tune_plan_rejects_more_than_five_candidates():
    candidates = [
        TuneCandidate(kind="thickness", params={"x": i}) for i in range(6)
    ]
    with pytest.raises(ValidationError):
        TunePlan(candidates=candidates)


def test_root_cause_hypothesis_defaults():
    h = RootCauseHypothesis(process="etch", confidence=0.9, rationale="nozzle")
    assert h.hypothesis_params == {}


def test_maint_plan_roundtrip():
    plan = MaintPlan(
        intent="clean_nozzle",
        target_process="etch",
        params={"duration_s": 120},
        rationale="FMEA",
    )
    again = MaintPlan.model_validate_json(plan.model_dump_json())
    assert again == plan


def test_deepseek_llm_construct_without_key():
    llm = DeepSeekLLM(api_key=None)
    assert llm is not None


def test_deepseek_llm_complete_without_key_raises():
    llm = DeepSeekLLM(api_key=None)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        llm.complete([], response_model=SupervisorDecision)


def test_with_json_instruction_includes_json_word():
    from cloud.llm import _with_json_instruction

    out = _with_json_instruction(
        [{"role": "user", "content": "route this event"}],
        SupervisorDecision,
    )
    assert out[0]["role"] == "system"
    assert "json" in out[0]["content"].lower()
    assert "SupervisorDecision" in out[0]["content"]
    assert len(out) == 2


def test_deepseek_complete_sends_json_in_prompt(monkeypatch):
    from unittest.mock import MagicMock

    llm = DeepSeekLLM(api_key="sk-test")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"route":"end","reason":"ok"}'))]
    )
    llm._client = mock_client
    out = llm.complete([{"role": "user", "content": "x"}], response_model=SupervisorDecision)
    assert out.route == "end"
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    blob = " ".join(m["content"] for m in kwargs["messages"]).lower()
    assert "json" in blob
    assert kwargs["response_format"] == {"type": "json_object"}
