import pytest

from common import topics


def test_process_topics():
    assert topics.telemetry("etch") == "plant/etch/ETC-01/telemetry"
    assert topics.command("drill") == "plant/drill/DRL-01/command"
    assert topics.measurement("plating") == "plant/plating/PLT-01/measurement"
    assert topics.events("etch") == "plant/events/etch"
    assert topics.intents("plating") == "plant/intents/plating"


def test_fixed_topics():
    assert topics.aoi_result() == "plant/aoi/AOI-01/result"
    assert topics.lab_assay() == "plant/lab/LAB-01/assay"
    assert topics.line_command() == "plant/line/command"


def test_constants():
    assert topics.PROCESSES == ("drill", "plating", "etch")
    assert topics.EQUIPMENT["lab"] == "LAB-01"


@pytest.mark.parametrize(
    "fn", [topics.telemetry, topics.command, topics.measurement, topics.events, topics.intents]
)
def test_unknown_process_raises_key_error(fn):
    with pytest.raises(KeyError):
        fn("x")
