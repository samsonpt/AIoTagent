from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bench.schema import PanelRecord, TraceStore
from cloud.graph import build_cloud_graph, run_cloud_graph
from cloud.llm import FakeLLM
from cloud.rag import KnowledgeBase
from cloud.tools import CloudTools
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from twin.types import CounterfactualResult, Prediction

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _panel(lot_id: str = "L0001", *, t_aoi: float = 5400.0) -> PanelRecord:
    return PanelRecord(
        panel_id=f"{lot_id}-P01",
        lot_id=lot_id,
        part_no="PN-4L-001",
        t_release=0.0,
        t_aoi=t_aoi,
        drill={"bit_hits": 1200},
        plating={"thickness_zones": [25.0, 25.1]},
        etch={"line_width_zones": [0.10, 0.11], "conveyor_speed_m_min": 1.8},
        defects=[{"type": "open", "zone": [0, 1], "stage": "etch"}],
        root_cause_truth="nozzle_clog",
        scrapped=False,
    )


def _tools(*, store: TraceStore | None = None, twin=None) -> CloudTools:
    return CloudTools(
        InMemoryBus(),
        store or TraceStore(),
        RECIPE,
        SimClock(),
        twin,
        KnowledgeBase(ROOT / "cloud" / "kb", ROOT / "runs" / "test_chroma", enabled=False),
    )


def _aoi_event(lot_id: str = "L0001") -> dict:
    return {
        "type": "aoi_threshold",
        "lot_id": lot_id,
        "process": "etch",
        "defect_rate": 0.25,
        "episode_id": "cloud-1-aoi",
    }


def _etch_success_scripts() -> dict:
    return {
        "SupervisorDecision": {"route": "quality_rca", "reason": "aoi defect rate high"},
        "RootCauseHypothesis": {
            "process": "etch",
            "confidence": 0.92,
            "rationale": "喷嘴堵塞导致线宽异常",
            "hypothesis_params": {"conveyor_speed_m_min": 1.5},
        },
        "TunePlan": {
            "candidates": [
                {
                    "kind": "width",
                    "params": {"m_min": 1.5},
                    "rationale": "降速加宽线宽",
                },
                {
                    "kind": "width",
                    "params": {"m_min": 1.7},
                    "rationale": "略降速",
                },
            ]
        },
    }


def test_aoi_event_routes_to_rca_and_publishes_tune_intent():
    store = TraceStore()
    store.record_panel(_panel())
    tools = _tools(store=store, twin=MagicMock())
    llm = FakeLLM(_etch_success_scripts())

    cf = CounterfactualResult(
        hypothesis={"conveyor_speed_m_min": 1.5},
        before=Prediction(None, None, None, 0.2, 0.8, {}),
        after=Prediction(None, None, None, 0.9, 0.1, {}),
        defect_cleared=True,
    )
    tools.twin_counterfactual = MagicMock(return_value=cf)
    tools.twin_compare = MagicMock(
        return_value=[
            Prediction(None, None, None, 0.85, 0.15, {}),
            Prediction(None, None, None, 0.75, 0.25, {}),
        ]
    )
    received: list[dict] = []
    tools.bus.subscribe("plant/intents/etch", lambda t, p: received.append(p), "spy")

    graph = build_cloud_graph(llm, tools, use_rag=False, use_counterfactual_rca=True)
    state = run_cloud_graph(graph, _aoi_event())

    assert state["route"] == "quality_rca"
    assert state["accepted_cause"] == "etch"
    assert state["handler"] == "cloud"
    assert state["hypothesis"]["process"] == "etch"
    assert len(state["intents"]) == 1
    assert state["intents"][0]["intent"] == "set_conveyor_speed"
    assert state["intents"][0]["source"] == "cloud"
    assert state["intents"][0]["policy_version"] == "m4"
    assert len(received) == 1
    assert received[0]["intent"] == "set_conveyor_speed"


def test_counterfactual_failure_sets_human_handler_and_no_intents():
    store = TraceStore()
    store.record_panel(_panel())
    tools = _tools(store=store, twin=MagicMock())
    llm = FakeLLM(_etch_success_scripts())

    cf = CounterfactualResult(
        hypothesis={"conveyor_speed_m_min": 1.5},
        before=Prediction(None, None, None, 0.2, 0.8, {}),
        after=Prediction(None, None, None, 0.3, 0.7, {}),
        defect_cleared=False,
    )
    tools.twin_counterfactual = MagicMock(return_value=cf)
    tools.twin_compare = MagicMock()
    received: list[dict] = []
    tools.bus.subscribe("plant/intents/etch", lambda t, p: received.append(p), "spy")

    graph = build_cloud_graph(llm, tools, use_rag=False, use_counterfactual_rca=True)
    state = run_cloud_graph(graph, _aoi_event())

    assert state["accepted_cause"] is None
    assert state["handler"] == "human"
    assert state["intents"] == []
    assert len(received) == 0
    tools.twin_compare.assert_not_called()


def test_maint_route_publishes_maint_intent():
    tools = _tools(twin=None)
    llm = FakeLLM(
        {
            "SupervisorDecision": {"route": "maint", "reason": "drill vibration"},
            "MaintPlan": {
                "intent": "change_bit",
                "target_process": "drill",
                "params": {},
                "rationale": "钻针寿命到期",
            },
        }
    )
    received: list[dict] = []
    tools.bus.subscribe("plant/intents/drill", lambda t, p: received.append(p), "spy")

    graph = build_cloud_graph(llm, tools, use_rag=False, use_counterfactual_rca=False)
    state = run_cloud_graph(
        graph,
        {"type": "event", "process": "drill", "kind": "vibration", "episode_id": "cloud-2"},
    )

    assert state["route"] == "maint"
    assert len(state["intents"]) == 1
    assert state["intents"][0]["intent"] == "change_bit"
    assert len(received) == 1


def test_supervisor_end_route_skips_agents():
    tools = _tools()
    llm = FakeLLM({"SupervisorDecision": {"route": "end", "reason": "unknown"}})

    graph = build_cloud_graph(llm, tools, use_rag=False, use_counterfactual_rca=False)
    state = run_cloud_graph(graph, {"type": "heartbeat", "episode_id": "cloud-3"})

    assert state["route"] == "end"
    assert state["handler"] == "human"
    assert state["intents"] == []


def test_rca_without_counterfactual_accepts_hypothesis():
    store = TraceStore()
    store.record_panel(_panel())
    tools = _tools(store=store, twin=None)
    llm = FakeLLM(
        {
            "SupervisorDecision": {"route": "quality_rca", "reason": "aoi"},
            "RootCauseHypothesis": {
                "process": "etch",
                "confidence": 0.8,
                "rationale": "蚀刻异常",
                "hypothesis_params": {},
            },
            "TunePlan": {
                "candidates": [
                    {"kind": "width", "params": {"m_min": 1.6}, "rationale": "调速"},
                ]
            },
        }
    )
    twin = MagicMock()
    twin.compare = MagicMock(
        return_value=[Prediction(None, None, None, 0.9, 0.1, {})]
    )
    tools.twin = twin
    tools.twin_compare = MagicMock(
        return_value=[Prediction(None, None, None, 0.9, 0.1, {})]
    )

    graph = build_cloud_graph(llm, tools, use_rag=False, use_counterfactual_rca=False)
    state = run_cloud_graph(graph, _aoi_event())

    assert state["accepted_cause"] == "etch"
    assert state["handler"] == "cloud"
    assert len(state["intents"]) == 1
