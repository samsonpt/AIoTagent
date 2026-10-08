from pydantic import ValidationError
import pytest

from common.intents import Intent


def test_intent_roundtrip():
    raw = Intent(
        intent="copper_thickness",
        target_process="etch",
        lot_id="L0001",
        params={"thickness_mean": 31.2},
        rationale="过镀前馈",
        source="peer",
    )
    again = Intent.model_validate_json(raw.model_dump_json())
    assert again == raw
    assert again.policy_version == "m2"


def test_intent_rejects_unknown_process():
    with pytest.raises(ValidationError):
        Intent(intent="x", target_process="aoi", params={})
