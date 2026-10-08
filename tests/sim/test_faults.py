from pathlib import Path

import pytest
from pydantic import ValidationError

from bench.schema import TraceStore
from common.bus import InMemoryBus
from common.recipe import load_recipe
from common.rng import make_rng
from sim.drill import DrillStation
from sim.etch import EtchStation
from sim.faults import (
    FAULT_DEFAULTS,
    FAULT_DEFECT_LINKS,
    REMEDIES,
    FaultInjector,
    FaultSpec,
    Scenario,
    load_scenario,
)
from sim.plating import PlatingStation

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def make_stations() -> dict[str, object]:
    return {
        "drill": DrillStation(RECIPE, "mid", make_rng(1, "drill")),
        "plating": PlatingStation(RECIPE, "mid", make_rng(1, "plating")),
        "etch": EtchStation(RECIPE, "mid", make_rng(1, "etch")),
    }


def make_scenario(*faults: dict) -> Scenario:
    return Scenario(name="t", seed=1, n_ticks=20, faults=[FaultSpec(**f) for f in faults])


def test_fault_spec_fills_default_params():
    spec = FaultSpec(fault_id="F1", type="nozzle_clog", process="etch", start_tick=3, params={"factor": 0.3})
    assert spec.params == {"zone": 2, "factor": 0.3}
    assert spec.end_tick is None
    assert FaultSpec(fault_id="F2", type="drill_break", process="drill", start_tick=0).params == {}


def test_fault_spec_rejects_unknown_type():
    with pytest.raises(ValidationError):
        FaultSpec(fault_id="F1", type="meteor", process="etch", start_tick=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "nozzle_clog", "process": "plating"},
        {"type": "drill_break", "process": "etch"},
        {"type": "rectifier_low", "process": "drill"},
        {"type": "rectifier_high", "process": "etch"},
        {"type": "rectifier_high", "process": "plating", "params": {"gain": 2}},
        {"type": "nozzle_clog", "process": "etch", "end_tick": 3},
        {"type": "nozzle_clog", "process": "etch", "end_tick": 2},
        {"type": "nozzle_clog", "process": "etch", "params": {"zonee": 1}},
        {"type": "drill_break", "process": "drill", "params": {"x": 1}},
    ],
)
def test_fault_spec_rejects_invalid_definitions(kwargs):
    with pytest.raises(ValidationError):
        FaultSpec(fault_id="F1", start_tick=3, **kwargs)


def test_fault_spec_physical_flag_and_free_process_for_sensor_and_network():
    assert FaultSpec(fault_id="F1", type="nozzle_clog", process="etch", start_tick=0).physical
    assert not FaultSpec(fault_id="S1", type="sensor_bias", process="plating", start_tick=0).physical
    assert not FaultSpec(fault_id="N1", type="network_outage", process="drill", start_tick=0).physical


def test_fault_spec_defaults_not_shared():
    a = FaultSpec(fault_id="N1", type="network_outage", process="etch", start_tick=0)
    a.params["clients"].append("edge-etch")
    assert FAULT_DEFAULTS["network_outage"] == {"clients": []}
    assert FaultSpec(fault_id="N2", type="network_outage", process="etch", start_tick=0).params == {"clients": []}


def test_defaults_and_links_cover_same_types():
    assert set(FAULT_DEFAULTS) == set(FAULT_DEFECT_LINKS)
    assert FAULT_DEFECT_LINKS["drill_break"] == {"hole_wall", "hole_missing"}
    assert FAULT_DEFECT_LINKS["sensor_bias"] == set()
    assert FAULT_DEFECT_LINKS["network_outage"] == set()
    assert "thin_copper" in FAULT_DEFECT_LINKS["rectifier_low"]
    assert "thin_copper" not in FAULT_DEFECT_LINKS["nozzle_clog"]
    assert FAULT_DEFECT_LINKS["rectifier_high"] == {"width_under", "width_over", "open", "residue", "short"}
    assert FAULT_DEFAULTS["rectifier_high"] == {"row": 0, "factor": 1.25}
    assert REMEDIES == {
        "clean_nozzle": ("nozzle_clog",),
        "repair_rectifier": ("rectifier_low", "rectifier_high"),
        "repair_regenerator": ("etch_sg_drift",),
        "change_bit": ("drill_break",),
    }


def test_scenario_defaults():
    s = Scenario(name="t", seed=1, n_ticks=10)
    assert s.model_mismatch == "mid"
    assert s.recipe_path == "bench/recipes/PN-4L-001.yaml"
    assert s.faults == []
    assert (s.assay_every_ticks, s.assay_delay_ticks) == (8, 2)
    with pytest.raises(ValidationError):
        Scenario(name="t", seed=1, n_ticks=10, model_mismatch="extreme")
    dup = {"fault_id": "F1", "type": "drill_break", "process": "drill", "start_tick": 0}
    with pytest.raises(ValidationError):
        Scenario(name="t", seed=1, n_ticks=10, faults=[dup, dup])


def test_load_scenario(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "name: 喷嘴堵塞\nseed: 42\nn_ticks: 48\nmodel_mismatch: high\n"
        "faults:\n  - {fault_id: F1, type: nozzle_clog, process: etch, start_tick: 5, params: {zone: 1}}\n",
        encoding="utf-8",
    )
    s = load_scenario(path)
    assert (s.name, s.seed, s.n_ticks, s.model_mismatch) == ("喷嘴堵塞", 42, 48, "high")
    assert s.faults[0].params == {"zone": 1, "factor": 0.5}


@pytest.mark.parametrize(
    "fault,check",
    [
        ({"type": "drill_abnormal_wear", "process": "drill"}, lambda st: st["drill"].wear_multiplier == 1.6),
        ({"type": "drill_break", "process": "drill"}, lambda st: st["drill"].broken is True),
        ({"type": "additive_depletion", "process": "plating"}, lambda st: st["plating"].consumption_multiplier == 2.0),
        ({"type": "rectifier_low", "process": "plating", "params": {"row": 1}}, lambda st: st["plating"].rect_factor == [1.0, 0.8, 1.0]),
        ({"type": "rectifier_high", "process": "plating"}, lambda st: st["plating"].rect_factor == [1.25, 1.0, 1.0]),
        ({"type": "etch_sg_drift", "process": "etch"}, lambda st: st["etch"].sg_drift_per_tick == 0.004),
        ({"type": "nozzle_clog", "process": "etch"}, lambda st: st["etch"].clog_factor == [1.0, 1.0, 0.5]),
    ],
)
def test_physical_fault_activates_and_restores(fault, check):
    stations = make_stations()
    nominal = {
        "wear": stations["drill"].wear_multiplier,
        "broken": stations["drill"].broken,
        "cons": stations["plating"].consumption_multiplier,
        "rect": list(stations["plating"].rect_factor),
        "drift": stations["etch"].sg_drift_per_tick,
        "clog": list(stations["etch"].clog_factor),
    }
    store = TraceStore()
    inj = FaultInjector(make_scenario({"fault_id": "F1", "start_tick": 3, "end_tick": 6, **fault}), store)
    bus = InMemoryBus()
    sensor_faults: dict = {}
    inj.apply(2, 3600.0, stations, bus, sensor_faults)
    assert not check(stations)
    assert inj.active(fault["process"], 2) == []
    inj.apply(3, 5400.0, stations, bus, sensor_faults)
    assert check(stations)
    assert [f.fault_id for f in inj.active(fault["process"], 3)] == ["F1"]
    assert [f.fault_id for f in inj.active(fault["process"], 5)] == ["F1"]
    assert inj.active("other", 3) == []
    inj.apply(6, 10800.0, stations, bus, sensor_faults)
    assert inj.active(fault["process"], 6) == []
    assert stations["drill"].wear_multiplier == nominal["wear"]
    assert stations["drill"].broken == nominal["broken"]
    assert stations["plating"].consumption_multiplier == nominal["cons"]
    assert stations["plating"].rect_factor == nominal["rect"]
    assert stations["etch"].sg_drift_per_tick == nominal["drift"]
    assert stations["etch"].clog_factor == nominal["clog"]
    [rec] = store.faults()
    assert (rec.fault_id, rec.process, rec.fault_type) == ("F1", fault["process"], fault["type"])
    assert rec.equipment == {"drill": "DRL-01", "plating": "PLT-01", "etch": "ETC-01"}[fault["process"]]
    assert rec.params == FaultSpec(fault_id="F1", start_tick=3, **fault).params
    assert (rec.t_start, rec.t_end, rec.t_cleared, rec.cleared_by) == (5400.0, 10800.0, None, None)


def test_fault_without_end_stays_active():
    stations = make_stations()
    inj = FaultInjector(make_scenario({"fault_id": "F1", "type": "nozzle_clog", "process": "etch", "start_tick": 0}), TraceStore())
    inj.apply(0, 0.0, stations, InMemoryBus(), {})
    assert [f.fault_id for f in inj.active("etch", 10_000)] == ["F1"]


def test_clear_marks_inactive_and_records():
    stations = make_stations()
    store = TraceStore()
    inj = FaultInjector(
        make_scenario({"fault_id": "F1", "type": "nozzle_clog", "process": "etch", "start_tick": 1, "end_tick": 9}),
        store,
    )
    bus = InMemoryBus()
    inj.apply(1, 1800.0, stations, bus, {})
    stations["etch"].apply("clean_nozzle", {"zone": 2})
    inj.clear("F1", 3600.0, "edge")
    assert inj.active("etch", 2) == []
    stations["etch"].clog_factor[2] = 0.7
    inj.apply(9, 16200.0, stations, bus, {})
    assert stations["etch"].clog_factor == [1.0, 1.0, 0.7]
    [rec] = store.faults()
    assert (rec.t_end, rec.t_cleared, rec.cleared_by) == (16200.0, 3600.0, "edge")
    inj.clear("F1", 5400.0, "machine")
    assert store.faults()[0].t_cleared == 3600.0
    with pytest.raises(KeyError):
        inj.clear("F9", 0.0, "edge")


def test_sensor_fault_registered_and_removed():
    spec = {"fault_id": "S1", "type": "sensor_bias", "process": "etch", "start_tick": 2, "end_tick": 4,
            "params": {"key": "sg", "offset": -0.01}}
    store = TraceStore()
    inj = FaultInjector(make_scenario(spec), store)
    stations = make_stations()
    sensor_faults: dict = {}
    inj.apply(2, 3600.0, stations, InMemoryBus(), sensor_faults)
    assert list(sensor_faults) == [("etch", "sg")]
    assert sensor_faults[("etch", "sg")].fault_id == "S1"
    assert inj.active("etch", 2) == []
    assert stations["etch"].params()["sg"] == 1.28
    inj.apply(4, 7200.0, stations, InMemoryBus(), sensor_faults)
    assert sensor_faults == {}
    [rec] = store.faults()
    assert (rec.equipment, rec.fault_type, rec.t_end) == ("ETC-01", "sensor_bias", 7200.0)


def test_network_outage_cuts_and_restores_links():
    spec = {"fault_id": "N1", "type": "network_outage", "process": "etch", "start_tick": 1, "end_tick": 3,
            "params": {"clients": ["edge-etch", "cloud"]}}
    store = TraceStore()
    inj = FaultInjector(make_scenario(spec), store)
    bus = InMemoryBus()
    inj.apply(1, 1800.0, make_stations(), bus, {})
    assert not bus.is_link_up("edge-etch")
    assert not bus.is_link_up("cloud")
    assert bus.is_link_up("edge-drill")
    assert inj.active("etch", 1) == []
    inj.apply(3, 5400.0, make_stations(), bus, {})
    assert bus.is_link_up("edge-etch")
    assert bus.is_link_up("cloud")
    [rec] = store.faults()
    assert (rec.equipment, rec.t_start, rec.t_end) == ("network", 1800.0, 5400.0)
