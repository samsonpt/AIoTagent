from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bench.schema import TraceStore
from cloud.llm import FakeLLM
from cloud.orchestrator import CloudOrchestrator
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _orchestrator(
    *,
    bus=None,
    clock=None,
    store=None,
    llm=None,
    use_event_trigger=True,
    twin=None,
):
    bus = bus or InMemoryBus()
    clock = clock or SimClock()
    store = store or TraceStore()
    llm = llm or FakeLLM({"SupervisorDecision": {"route": "end", "reason": "noop"}})
    return CloudOrchestrator(
        bus,
        RECIPE,
        clock,
        store,
        twin,
        llm,
        use_event_trigger=use_event_trigger,
        use_rag=False,
        use_counterfactual_rca=False,
        persist_dir=ROOT / "runs" / "test_orchestrator_chroma",
    )


def test_no_events_does_not_invoke_graph():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    with patch("cloud.orchestrator.run_cloud_graph") as run_graph:
        orch = _orchestrator(bus=bus, clock=clock, store=store)
        orch.on_tick(clock)
        run_graph.assert_not_called()
        assert store.episodes() == []


def test_plant_event_triggers_graph_and_records_episode():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    llm = FakeLLM({"SupervisorDecision": {"route": "end", "reason": "edge event"}})
    orch = _orchestrator(bus=bus, clock=clock, store=store, llm=llm)
    bus.publish(
        topics.events("etch"),
        {
            "t": 1800.0,
            "tick": 1,
            "process": "etch",
            "key": "sg",
            "rule": "R1",
            "value": 1.4,
            "episode_id": "cloud-etch-1",
        },
        "edge-etch",
    )
    orch.on_tick(clock)
    episodes = store.episodes()
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.episode_id == "cloud-etch-1"
    assert ep.process == "etch"
    assert ep.trigger == "R1"
    assert ep.handler == "human"
    assert ep.t_detect == clock.now


def test_periodic_wake_when_event_trigger_disabled():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    with patch("cloud.orchestrator.run_cloud_graph") as run_graph:
        run_graph.return_value = {
            "event": {"type": "periodic"},
            "route": "end",
            "hypothesis": None,
            "accepted_cause": None,
            "intents": [],
            "episode_id": "cloud-periodic",
            "handler": "human",
            "detail": {},
            "messages": [],
        }
        orch = _orchestrator(bus=bus, clock=clock, store=store, use_event_trigger=False)
        orch.on_tick(clock)
        run_graph.assert_not_called()
        clock.advance_substep()
        for _ in range(29):
            clock.advance_substep()
        assert clock.now >= 60.0
        orch.on_tick(clock)
        run_graph.assert_called_once()


def test_from_ablation_uses_fake_llm_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    from common.config import AblationConfig

    ablation = AblationConfig(name="test", use_rag=False)
    orch = CloudOrchestrator.from_ablation(ablation, bus, RECIPE, clock, store, None)
    assert orch.client_id == "cloud"
    assert isinstance(orch._llm, FakeLLM)


def test_record_episode_includes_llm_calls_from_state():
    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    state = {
        "event": {"process": "etch", "rule": "R1", "episode_id": "cloud-llm-1"},
        "route": "quality_rca",
        "hypothesis": {"process": "etch"},
        "accepted_cause": "etch",
        "intents": [],
        "episode_id": "cloud-llm-1",
        "handler": "cloud",
        "detail": {"supervisor_reason": "aoi"},
        "llm_calls": 3,
        "messages": [],
    }
    with patch("cloud.orchestrator.run_cloud_graph", return_value=state):
        orch = _orchestrator(bus=bus, clock=clock, store=store)
        bus.publish(
            topics.events("etch"),
            {"process": "etch", "rule": "R1", "episode_id": "cloud-llm-1"},
            "edge-etch",
        )
        orch.on_tick(clock)
    [ep] = store.episodes()
    assert ep.detail["llm_calls"] == 3
    assert ep.detail["supervisor_reason"] == "aoi"
    assert ep.handler == "cloud"


def test_aoi_threshold_triggers_synthetic_event():
    from bench.schema import FaultRecord

    bus, clock, store = InMemoryBus(), SimClock(), TraceStore()
    store.record_fault(
        FaultRecord(
            fault_id="F1",
            process="etch",
            equipment="ETC-01",
            fault_type="nozzle_clog",
            params={},
            t_start=7200.0,
        )
    )
    llm = FakeLLM({"SupervisorDecision": {"route": "end", "reason": "aoi spike"}})
    orch = _orchestrator(bus=bus, clock=clock, store=store, llm=llm)
    for i in range(3):
        bus.publish(
            topics.aoi_result(),
            {
                "t": float(i * 1800),
                "tick": i,
                "lot_id": f"W{i:04d}",
                "panels": [
                    {"panel_id": f"W{i:04d}-P01", "defects": []},
                    {"panel_id": f"W{i:04d}-P02", "defects": []},
                ],
            },
            "plant",
        )
    for i in range(5):
        bus.publish(
            topics.aoi_result(),
            {
                "t": 7200.0 + float(i * 1800),
                "tick": 4 + i,
                "lot_id": f"D{i:04d}",
                "panels": [
                    {"panel_id": f"D{i:04d}-P01", "defects": [{"type": "open", "zone": [0, 1], "stage": "etch"}]},
                    {"panel_id": f"D{i:04d}-P02", "defects": []},
                ],
            },
            "plant",
        )
    orch.on_tick(clock)
    episodes = store.episodes()
    assert len(episodes) == 1
    assert episodes[0].trigger == "aoi_threshold"
