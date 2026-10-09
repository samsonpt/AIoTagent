import os

import pytest

from cloud.llm import DeepSeekLLM
from cloud.schemas import SupervisorDecision

pytestmark = pytest.mark.live


def test_live_deepseek_supervisor_decision():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY not set")

    llm = DeepSeekLLM(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": (
                "Respond with JSON only. Schema: "
                '{"route": "quality_rca"|"process_tuner"|"maint"|"end", "reason": string}'
            ),
        },
        {
            "role": "user",
            "content": "AOI defect rate spike on etch line, lot L123.",
        },
    ]
    out = llm.complete(messages, response_model=SupervisorDecision)
    assert isinstance(out, SupervisorDecision)
    assert out.route in ("quality_rca", "process_tuner", "maint", "end")
    assert out.reason
