import json
import os
import subprocess
import sys
import time
from functools import cache
from pathlib import Path

import pytest

from bench.schema import TraceStore
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from sim.faults import FAULT_PROCESS, load_scenario
from common.config import load_ablation
from sim.runner import RunSummary, run

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "bench" / "scenarios"
UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
PHYSICAL = [
    "drill_wear", "additive_depletion", "rectifier_low", "plating_overplate", "sg_drift", "nozzle_clog", "sensor_spoof",
]


@cache
def result(name: str) -> tuple[RunSummary, TraceStore]:
    store = TraceStore()
    return run(load_scenario(SCENARIOS / f"{name}.yaml"), store), store


def test_scenario_files_follow_conventions():
    names = {p.stem for p in SCENARIOS.glob("*.yaml")}
    assert names == {"nominal", "network_outage", *PHYSICAL}
    for name in names:
        s = load_scenario(SCENARIOS / f"{name}.yaml")
        assert (s.name, s.seed, s.n_ticks, s.model_mismatch) == (name, 42, 96, "mid")


def test_nominal_fpy():
    summary, _ = result("nominal")
    assert summary.scenario == "nominal" and summary.seed == 42
    assert summary.n_panels > 1000
    assert summary.fpy >= 0.95


@pytest.mark.parametrize("name", PHYSICAL)
def test_physical_faults_lower_fpy_with_correct_root_cause(name):
    summary, store = result(name)
    nominal, _ = result("nominal")
    assert summary.fpy <= nominal.fpy - 0.05
    expected = {FAULT_PROCESS[f.type] for f in load_scenario(SCENARIOS / f"{name}.yaml").faults if f.type in FAULT_PROCESS}
    causes = [p.root_cause_truth for p in store.panels() if p.root_cause_truth != "none"]
    assert causes
    assert sum(c in expected for c in causes) / len(causes) >= 0.9


def test_plating_overplate_cross_process():
    summary, _ = result("plating_overplate")
    assert summary.cross_process_ratio > 0.5


def test_additive_depletion_has_no_cross_process_defects():
    summary, _ = result("additive_depletion")
    assert summary.cross_process_ratio == 0


def test_network_outage_runs():
    summary, store = result("network_outage")
    assert summary.n_panels > 1000
    assert store.faults()[0].t_end is not None


def test_network_outage_blocks_edge_telemetry():
    bus = InMemoryBus()
    ticks: set[int] = set()
    bus.subscribe(topics.telemetry("drill"), lambda t, p: ticks.add(p["tick"]), "edge-drill")
    run(load_scenario(SCENARIOS / "network_outage.yaml"), TraceStore(), bus=bus, n_ticks=45)
    assert ticks == set(range(45)) - set(range(20, 40))


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.yaml")))
def test_dump_reproducible(name):
    scenario = load_scenario(SCENARIOS / f"{name}.yaml")
    a, b = TraceStore(), TraceStore()
    run(scenario, a, n_ticks=24)
    run(scenario, b, n_ticks=24)
    assert a.dump() == b.dump()


def test_nominal_runtime():
    start = time.perf_counter()
    run(load_scenario(SCENARIOS / "nominal.yaml"), TraceStore())
    assert time.perf_counter() - start < 10


def test_last_tick_command_is_recorded():
    bus = InMemoryBus()

    class Stopper:
        def on_tick(self, clock: SimClock) -> None:
            if clock.tick == 3:
                bus.publish(
                    topics.command("drill"),
                    {
                        "command": "stop",
                        "params": {},
                        "source": "edge",
                        "reason": "末拍",
                        "guarded": True,
                        "guard_id": "guard",
                    },
                    "edge-drill",
                )

    store = TraceStore()
    run(load_scenario(SCENARIOS / "nominal.yaml"), store, bus=bus, controllers=[Stopper()], n_ticks=3)
    [action] = store.actions()
    assert (action.command, action.t, action.accepted, action.reason) == ("stop", 5400.0, True, "末拍")


def test_controllers_called_each_tick():
    seen = []

    class Spy:
        def on_tick(self, clock: SimClock) -> None:
            seen.append(clock.tick)

    run(load_scenario(SCENARIOS / "nominal.yaml"), TraceStore(), controllers=[Spy()], n_ticks=3)
    assert seen == [1, 2, 3]


def test_edge_only_improves_nozzle_clog_fpy():
    scenario = load_scenario(SCENARIOS / "nozzle_clog.yaml")
    ablation = load_ablation(ROOT / "bench" / "configs" / "edge_only.yaml")
    baseline, treated = TraceStore(), TraceStore()
    raw = run(scenario, baseline)
    with_edge = run(scenario, treated, ablation=ablation)
    assert with_edge.fpy > raw.fpy


def test_baseline_rule_commands_are_human():
    scenario = load_scenario(SCENARIOS / "sg_drift.yaml")
    ablation = load_ablation(ROOT / "bench" / "configs" / "baseline_rule.yaml")
    store = TraceStore()
    run(scenario, store, ablation=ablation, n_ticks=20)
    human = [a for a in store.actions() if a.source == "human"]
    assert human
    assert store.episodes()
    assert human[0].t - min(e.t_detect for e in store.episodes()) >= 3600


def test_cli_prints_json(tmp_path):
    db = tmp_path / "trace.db"
    proc = subprocess.run(
        [sys.executable, "-m", "sim.runner", str(SCENARIOS / "nominal.yaml"), "--ticks", "8", "--db", str(db)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=UTF8_ENV,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["scenario"] == "nominal" and out["n_panels"] == 60
    assert out["fpr"] is None
    assert db.exists()


def test_cli_refuses_existing_db_unless_overwrite(tmp_path):
    db = tmp_path / "trace.db"
    db.write_text("old", encoding="utf-8")
    cmd = [sys.executable, "-m", "sim.runner", str(SCENARIOS / "nominal.yaml"), "--ticks", "4", "--db", str(db)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=UTF8_ENV)
    assert proc.returncode != 0 and "--overwrite" in proc.stderr and proc.stdout == ""
    assert db.read_text(encoding="utf-8") == "old"
    proc = subprocess.run([*cmd, "--overwrite"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=UTF8_ENV)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["n_panels"] == 12
