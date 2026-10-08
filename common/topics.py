PROCESSES = ("drill", "plating", "etch")
EQUIPMENT = {"drill": "DRL-01", "plating": "PLT-01", "etch": "ETC-01", "aoi": "AOI-01", "lab": "LAB-01"}


def _equipment_topic(process: str, kind: str) -> str:
    return f"plant/{process}/{EQUIPMENT[process]}/{kind}"


def _check(process: str) -> str:
    if process not in EQUIPMENT:
        raise KeyError(process)
    return process


def telemetry(process: str) -> str:
    return _equipment_topic(process, "telemetry")


def command(process: str) -> str:
    return _equipment_topic(process, "command")


def measurement(process: str) -> str:
    return _equipment_topic(process, "measurement")


def aoi_result() -> str:
    return _equipment_topic("aoi", "result")


def lab_assay() -> str:
    return _equipment_topic("lab", "assay")


def line_command() -> str:
    return "plant/line/command"


def events(process: str) -> str:
    return f"plant/events/{_check(process)}"


def intents(target_process: str) -> str:
    return f"plant/intents/{_check(target_process)}"
