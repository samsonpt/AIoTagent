from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from sim.faults import FaultSpec, Scenario
from sim.plant import COMMAND_CATEGORY, Plant


def make(*faults: dict, **kwargs):
    scenario = Scenario(name="t", seed=5, n_ticks=20, faults=[FaultSpec(**f) for f in faults], **kwargs)
    bus, store, clock = InMemoryBus(), TraceStore(), SimClock()
    return Plant(scenario, bus, store, clock), bus, store, clock


def send(bus, process, command, params=None, source="edge", reason="test"):
    topic = topics.line_command() if process == "line" else topics.command(process)
    bus.publish(topic, {"command": command, "params": params or {}, "source": source, "reason": reason}, "edge")


def test_command_category_covers_all_station_commands():
    assert COMMAND_CATEGORY["change_bit"] == "bit_change"
    assert COMMAND_CATEGORY["set_spray_pressure"] == "param_tune"
    assert COMMAND_CATEGORY["adjust_sg"] == "dosing"
    assert COMMAND_CATEGORY["repair_regenerator"] == "maintenance"
    assert COMMAND_CATEGORY["resume"] == "line_stop"
    assert COMMAND_CATEGORY["hold_lot"] == "lot_hold"
    assert COMMAND_CATEGORY["scrap_lot"] == "scrap"


def test_valid_command_changes_station_and_logs_action():
    plant, bus, store, clock = make()
    plant.step_tick()
    send(bus, "plating", "set_current_density", {"asd": 2.2}, reason="补偿")
    assert plant.stations["plating"].current_density_asd == 2.2
    [a] = store.actions()
    assert (a.t, a.process, a.equipment, a.command, a.params) == (1800.0, "plating", "PLT-01", "set_current_density", {"asd": 2.2})
    assert (a.source, a.category, a.affected_panels, a.accepted, a.reason) == ("edge", "param_tune", 12, True, "补偿")


def test_invalid_commands_rejected():
    plant, bus, store, _ = make()
    send(bus, "etch", "set_etch_temp", {"c": 99.0})
    send(bus, "etch", "levitate")
    send(bus, "drill", "set_rpm", {"spindle_rpm": 120000}, source="hacker")
    send(bus, "line", "hold_lot", {"lot_id": "L0099"})
    send(bus, "line", "stop")
    assert plant.stations["etch"].etch_temp_c == 50.0
    actions = store.actions()
    assert [a.accepted for a in actions] == [False] * 5
    assert "outside" in actions[0].reason
    assert actions[1].category == "param_tune"
    assert actions[2].source == "rule" and "hacker" in actions[2].reason
    assert plant.stations["drill"].spindle_rpm == 120000
    assert actions[3].category == "lot_hold"


def test_clean_nozzle_clears_matching_zone_only():
    plant, bus, store, _ = make({"fault_id": "F1", "type": "nozzle_clog", "process": "etch", "start_tick": 0})
    plant.step_tick()
    send(bus, "etch", "clean_nozzle", {"zone": 1})
    assert store.faults()[0].t_cleared is None
    send(bus, "etch", "clean_nozzle", {"zone": 2}, source="cloud")
    [f] = store.faults()
    assert f.t_cleared == 1800.0 and f.cleared_by == "cloud"
    assert plant.stations["etch"].clog_factor == [1.0, 1.0, 1.0]


def test_repair_rectifier_clears_rectifier_high():
    plant, bus, store, _ = make({"fault_id": "F1", "type": "rectifier_high", "process": "plating", "start_tick": 0})
    plant.step_tick()
    assert plant.stations["plating"].rect_factor == [1.25, 1.0, 1.0]
    send(bus, "plating", "repair_rectifier")
    assert store.faults()[0].cleared_by == "edge"
    assert plant.stations["plating"].rect_factor == [1.0, 1.0, 1.0]


def test_remedy_before_fault_activation_is_harmless():
    plant, bus, store, _ = make({"fault_id": "F1", "type": "nozzle_clog", "process": "etch", "start_tick": 1})
    plant.step_tick()
    send(bus, "etch", "clean_nozzle", {"zone": 2})
    assert store.actions()[0].accepted
    plant.step_tick()
    assert store.faults()[0].t_cleared is None


def test_stop_halts_pipeline_and_counts_downtime():
    plant, bus, store, _ = make()
    send(bus, "drill", "stop")
    for _ in range(5):
        plant.step_tick()
    assert plant.downtime_ticks == 5
    assert store.panels() == []
    assert store.actions()[0].category == "line_stop"
    send(bus, "drill", "resume")
    for _ in range(4):
        plant.step_tick()
    assert plant.downtime_ticks == 5
    assert {p.lot_id for p in store.panels()} == {"L0001"}


def test_pipeline_lineage_and_timing():
    plant, _, store, _ = make()
    for _ in range(4):
        plant.step_tick()
    panels = store.panels()
    assert [p.panel_id for p in panels] == [f"L0001-P{k:02d}" for k in range(1, 13)]
    p = panels[0]
    assert (p.t_release, p.t_aoi) == (1800.0, 7200.0)
    assert p.drill["bit_hits"] == 600 and "spindle_current_a" in p.drill
    assert "additive_ml_l" in p.plating and "rect_current_r1" in p.plating
    assert "sg" in p.etch and "spray_pressure_z1" in p.etch


def test_hold_lot_leaves_pipeline():
    plant, bus, store, _ = make()
    plant.step_tick()
    send(bus, "line", "hold_lot", {"lot_id": "L0001"}, source="human")
    for _ in range(5):
        plant.step_tick()
    lots = {p.lot_id for p in store.panels()}
    assert "L0001" not in lots and "L0002" in lots
    a = store.actions()[0]
    assert (a.process, a.category, a.affected_panels, a.accepted) == ("line", "lot_hold", 12, True)


def test_scrap_lot_and_reject_finished_lot():
    plant, bus, store, _ = make()
    for _ in range(2):
        plant.step_tick()
    send(bus, "line", "scrap_lot", {"lot_id": "L0002"})
    for _ in range(3):
        plant.step_tick()
    send(bus, "line", "hold_lot", {"lot_id": "L0001"})
    by_lot = {}
    for p in store.panels():
        by_lot.setdefault(p.lot_id, []).append(p)
    assert all(p.scrapped and p.defects[0]["type"] == "scrapped_by_command" for p in by_lot["L0002"])
    assert len(by_lot["L0002"]) == 12
    assert [a.accepted for a in store.actions()] == [True, False]


def test_auto_bit_change_clears_drill_break():
    plant, _, store, _ = make({"fault_id": "B1", "type": "drill_break", "process": "drill", "start_tick": 2})
    for _ in range(10):
        plant.step_tick()
    [f] = store.faults()
    assert f.cleared_by == "machine" and f.t_cleared is not None
    drill_defects = [p for p in store.panels() if p.root_cause_truth == "drill"]
    assert drill_defects


def test_station_params_never_on_bus():
    plant, bus, _, _ = make(assay_every_ticks=2, assay_delay_ticks=1)
    seen: list[tuple[str, dict]] = []
    bus.subscribe("#", lambda t, p: seen.append((t, p)), "spy")
    for _ in range(6):
        plant.step_tick()
    assays = [p for t, p in seen if t == topics.lab_assay()]
    assert len(assays) == 3
    assert all("additive_ml_l" not in str(p) for t, p in seen if t != topics.lab_assay())
