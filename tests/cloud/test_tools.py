from pathlib import Path

import pytest

from bench.schema import PanelRecord, TraceStore
from cloud.rag import KnowledgeBase
from cloud.tools import CloudTools
from common import topics
from common.bus import InMemoryBus
from common.clock import SimClock
from common.intents import Intent
from common.recipe import load_recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[2]
RECIPE = load_recipe(ROOT / "bench" / "recipes" / "PN-4L-001.yaml")


def _panel(
    panel_id: str,
    lot_id: str,
    *,
    t_aoi: float = 5400.0,
    drill: dict | None = None,
    plating: dict | None = None,
    etch: dict | None = None,
    defects: list | None = None,
) -> PanelRecord:
    return PanelRecord(
        panel_id=panel_id,
        lot_id=lot_id,
        part_no="PN-4L-001",
        t_release=0.0,
        t_aoi=t_aoi,
        drill=drill or {"bit_hits": 1200},
        plating=plating or {"thickness_zones": [25.0, 25.1]},
        etch=etch or {"line_width_zones": [0.10, 0.11]},
        defects=defects or [{"type": "open", "zone": [0, 1], "stage": "etch"}],
        root_cause_truth="nozzle_clog",
        scrapped=False,
    )


def _tools(
    *,
    store: TraceStore | None = None,
    twin: TwinService | None = None,
    kb: KnowledgeBase | None = None,
    clock: SimClock | None = None,
) -> CloudTools:
    return CloudTools(
        InMemoryBus(),
        store or TraceStore(),
        RECIPE,
        clock or SimClock(),
        twin,
        kb or KnowledgeBase(ROOT / "cloud" / "kb", ROOT / "runs" / "test_chroma", enabled=False),
    )


def test_publish_intent_on_bus():
    bus = InMemoryBus()
    store = TraceStore()
    tools = CloudTools(
        bus,
        store,
        RECIPE,
        SimClock(),
        None,
        KnowledgeBase(ROOT / "cloud" / "kb", ROOT / "runs" / "test_chroma", enabled=False),
    )
    received: list[dict] = []
    bus.subscribe(topics.intents("etch"), lambda t, p: received.append(p), "spy")
    intent = Intent(
        intent="clean_nozzle",
        target_process="etch",
        params={"zone": 1},
        source="cloud",
        policy_version="m4",
    )
    tools.publish_intent(intent)
    assert len(received) == 1
    assert received[0]["source"] == "cloud"
    assert received[0]["intent"] == "clean_nozzle"


def test_query_lot_history_from_store():
    store = TraceStore()
    store.record_panel(_panel("L0001-P01", "L0001"))
    store.record_panel(_panel("L0001-P02", "L0001", defects=[{"type": "short"}]))
    tools = _tools(store=store)
    hist = tools.query_lot_history("L0001")
    assert hist["lot_id"] == "L0001"
    assert hist["panels_n"] == 2
    assert len(hist["defects"]) == 2
    assert hist["drill"] == {"bit_hits": 1200}
    assert hist["plating"] == {"thickness_zones": [25.0, 25.1]}
    assert hist["etch"] == {"line_width_zones": [0.10, 0.11]}


def test_query_lot_history_latest_lot():
    store = TraceStore()
    store.record_panel(_panel("L0001-P01", "L0001", t_aoi=1000.0))
    store.record_panel(_panel("L0002-P01", "L0002", t_aoi=9000.0, drill={"bit_hits": 5000}))
    tools = _tools(store=store)
    hist = tools.query_lot_history()
    assert hist["lot_id"] == "L0002"
    assert hist["panels_n"] == 1
    assert hist["drill"] == {"bit_hits": 5000}


def test_rag_search_delegates_to_kb(tmp_path):
    kb = KnowledgeBase(ROOT / "cloud" / "kb", tmp_path / "chroma", enabled=True)
    tools = _tools(kb=kb)
    hits = tools.rag_search("蚀刻喷嘴堵塞", k=2)
    assert hits and all("text" in h and "source" in h for h in hits)


def test_twin_counterfactual_raises_without_twin():
    tools = _tools(twin=None)
    with pytest.raises(RuntimeError):
        tools.twin_counterfactual({"asd": 2.0}, {"asd": 1.5}, kind="thickness")


def test_twin_counterfactual_with_twin():
    twin = TwinService(InMemoryBus(), RECIPE, SimClock())
    tools = _tools(twin=twin)
    params = {"asd": 3.5, "time_min": 60.0, "additive_ml_l": 4.5}
    result = tools.twin_counterfactual(params, {"asd": 2.0}, kind="thickness")
    assert result.before.oos_prob >= result.after.oos_prob


def test_twin_compare_with_twin():
    twin = TwinService(InMemoryBus(), RECIPE, SimClock())
    tools = _tools(twin=twin)
    candidates = [
        {"kind": "thickness", "asd": 2.0, "time_min": 60.0, "additive_ml_l": 4.5},
        {"kind": "thickness", "asd": 3.5, "time_min": 60.0, "additive_ml_l": 4.5},
    ]
    preds = tools.twin_compare(candidates)
    assert len(preds) == 2
    assert preds[1].oos_prob > preds[0].oos_prob


def test_record_episode_writes_store():
    store = TraceStore()
    clock = SimClock()
    clock.advance_tick()
    tools = _tools(store=store, clock=clock)
    tools.record_episode(
        episode_id="cloud-1-aoi",
        process="etch",
        trigger="aoi_threshold",
        handler="cloud",
        detail={"route": "quality_rca"},
    )
    [episode] = store.episodes()
    assert episode.episode_id == "cloud-1-aoi"
    assert episode.handler == "cloud"
    assert episode.t_detect == clock.now
    assert episode.detail == {"route": "quality_rca"}
