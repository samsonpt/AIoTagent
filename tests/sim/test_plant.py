import threading

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


def steps(plant, n=1):
    for _ in range(n):
        plant.step_tick()


def test_command_category_covers_all_station_commands():
    assert COMMAND_CATEGORY["change_bit"] == "bit_change"
    assert COMMAND_CATEGORY["set_spray_pressure"] == "param_tune"
    assert COMMAND_CATEGORY["adjust_sg"] == "dosing"
    assert COMMAND_CATEGORY["repair_regenerator"] == "maintenance"
    assert COMMAND_CATEGORY["resume"] == "line_stop"
    assert COMMAND_CATEGORY["hold_lot"] == "lot_hold"
    assert COMMAND_CATEGORY["scrap_lot"] == "scrap"


def test_command_queued_until_next_tick():
    plant, bus, store, _ = make()
    steps(plant)
    send(bus, "plating", "set_current_density", {"asd": 2.2}, reason="补偿")
    assert plant.stations["plating"].current_density_asd == 2.0
    assert store.actions() == []
    steps(plant)
    assert plant.stations["plating"].current_density_asd == 2.2
    [a] = store.actions()
    assert (a.t, a.process, a.equipment, a.command, a.params) == (1800.0, "plating", "PLT-01", "set_current_density", {"asd": 2.2})
    assert (a.source, a.category, a.affected_panels, a.accepted, a.reason) == ("edge", "param_tune", 12, True, "补偿")


def test_command_from_other_thread_executes_on_step():
    plant, bus, store, _ = make()
    worker = threading.Thread(target=send, args=(bus, "etch", "set_etch_temp", {"c": 51.0}))
    worker.start()
    worker.join()
    assert store.actions() == []
    steps(plant)
    assert plant.stations["etch"].etch_temp_c == 51.0


def test_command_issued_mid_tick_takes_effect_next_tick():
    plant, bus, _, _ = make()
    issued = []

    def react(topic, payload):
        if not issued:
            issued.append(payload["tick"])
            send(bus, "drill", "stop")

    bus.subscribe(topics.telemetry("drill"), react, "edge-drill")
    steps(plant)
    assert issued == [0]
    assert not plant.stations["drill"].stopped and plant.downtime_ticks == 0
    steps(plant)
    assert plant.stations["drill"].stopped and plant.downtime_ticks == 1


def test_invalid_commands_rejected():
    plant, bus, store, _ = make()
    send(bus, "etch", "set_etch_temp", {"c": 99.0})
    send(bus, "etch", "levitate")
    send(bus, "drill", "set_rpm", {"spindle_rpm": 130000}, source="hacker")
    send(bus, "line", "hold_lot", {"lot_id": "L0099"})
    send(bus, "line", "stop")
    steps(plant)
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
    steps(plant)
    send(bus, "etch", "clean_nozzle", {"zone": 1})
    steps(plant)
    assert store.faults()[0].t_cleared is None
    send(bus, "etch", "clean_nozzle", {"zone": 2}, source="cloud")
    steps(plant)
    [f] = store.faults()
    assert f.t_cleared == 3600.0 and f.cleared_by == "cloud"
    assert plant.stations["etch"].clog_factor == [1.0, 1.0, 1.0]


def test_repair_rectifier_clears_rectifier_high():
    plant, bus, store, _ = make({"fault_id": "F1", "type": "rectifier_high", "process": "plating", "start_tick": 0})
    steps(plant)
    assert plant.stations["plating"].rect_factor == [1.25, 1.0, 1.0]
    send(bus, "plating", "repair_rectifier")
    steps(plant)
    assert store.faults()[0].cleared_by == "edge"
    assert plant.stations["plating"].rect_factor == [1.0, 1.0, 1.0]


def test_queued_command_runs_after_fault_injection():
    plant, bus, store, _ = make({"fault_id": "F1", "type": "nozzle_clog", "process": "etch", "start_tick": 1})
    steps(plant)
    send(bus, "etch", "clean_nozzle", {"zone": 2})
    steps(plant)
    [f] = store.faults()
    assert (f.t_start, f.t_cleared) == (1800.0, 1800.0)
    assert plant.stations["etch"].clog_factor == [1.0, 1.0, 1.0]


def test_stop_halts_pipeline_and_counts_downtime():
    plant, bus, store, _ = make()
    send(bus, "drill", "stop")
    steps(plant, 5)
    assert plant.downtime_ticks == 5
    assert store.panels() == []
    assert store.actions()[0].category == "line_stop"
    send(bus, "drill", "resume")
    steps(plant, 4)
    assert plant.downtime_ticks == 5
    assert {p.lot_id for p in store.panels()} == {"L0001"}


def test_pipeline_lineage_and_timing():
    plant, _, store, _ = make()
    steps(plant, 4)
    panels = store.panels()
    assert [p.panel_id for p in panels] == [f"L0001-P{k:02d}" for k in range(1, 13)]
    p = panels[0]
    assert (p.t_release, p.t_aoi) == (1800.0, 7200.0)
    assert p.drill["bit_hits"] == 600 and "spindle_current_a" in p.drill
    assert "additive_ml_l" in p.plating and "rect_current_r1" in p.plating
    assert "sg" in p.etch and "spray_pressure_z1" in p.etch


def test_lot_step_and_measurement_timing():
    plant, bus, _, _ = make()
    seen: list[tuple[str, dict]] = []
    for pattern in ("plant/mes/+/lot_step", topics.measurement("plating"), topics.measurement("etch"), topics.aoi_result()):
        bus.subscribe(pattern, lambda t, p: seen.append((t, p)), "spy")
    steps(plant, 4)
    l1 = [(t, p) for t, p in seen if p["lot_id"] == "L0001"]
    assert [(t, p["tick"]) for t, p in l1] == [
        (topics.lot_step("drill"), 0),
        (topics.lot_step("plating"), 1),
        (topics.measurement("plating"), 1),
        (topics.lot_step("etch"), 2),
        (topics.measurement("etch"), 2),
        (topics.aoi_result(), 3),
    ]
    assert l1[0][1] == {"t": 1800.0, "tick": 0, "lot_id": "L0001", "process": "drill"}
    assert l1[2][1]["kind"] == "thickness_um" and l1[2][1]["panel_id"] == "L0001-P01"
    assert l1[4][1]["kind"] == "line_width_um"


def test_hold_lot_leaves_pipeline_and_rejects_second_hold():
    plant, bus, store, _ = make()
    steps(plant)
    send(bus, "line", "hold_lot", {"lot_id": "L0001"}, source="human")
    steps(plant)
    send(bus, "line", "hold_lot", {"lot_id": "L0001"}, source="human")
    steps(plant, 4)
    lots = {p.lot_id for p in store.panels()}
    assert "L0001" not in lots and "L0002" in lots
    first, second = store.actions()
    assert (first.process, first.category, first.affected_panels, first.accepted) == ("line", "lot_hold", 12, True)
    assert not second.accepted and "扣留" in second.reason


def test_scrap_lot_and_reject_finished_lot():
    plant, bus, store, _ = make()
    steps(plant, 2)
    send(bus, "line", "scrap_lot", {"lot_id": "L0002"})
    steps(plant, 3)
    send(bus, "line", "hold_lot", {"lot_id": "L0001"})
    steps(plant)
    by_lot = {}
    for p in store.panels():
        by_lot.setdefault(p.lot_id, []).append(p)
    assert all(p.scrapped and p.defects[0]["type"] == "scrapped_by_command" for p in by_lot["L0002"])
    assert len(by_lot["L0002"]) == 12
    assert [a.accepted for a in store.actions()] == [True, False]


def test_auto_bit_change_clears_drill_break():
    plant, _, store, _ = make({"fault_id": "B1", "type": "drill_break", "process": "drill", "start_tick": 2})
    steps(plant, 10)
    [f] = store.faults()
    assert f.cleared_by == "machine" and f.t_cleared is not None
    assert [p for p in store.panels() if p.root_cause_truth == "drill"]


def test_station_params_never_on_bus():
    plant, bus, _, _ = make(assay_every_ticks=2, assay_delay_ticks=1)
    seen: list[tuple[str, dict]] = []
    bus.subscribe("#", lambda t, p: seen.append((t, p)), "spy")
    steps(plant, 6)
    assays = [p for t, p in seen if t == topics.lab_assay()]
    assert [a["tick"] for a in assays] == [1, 3, 5]
    assert all("additive_ml_l" not in str(p) for t, p in seen if t != topics.lab_assay())
