from __future__ import annotations

from bench.schema import EpisodeRecord
from cloud.rag import KnowledgeBase
from common import topics
from common.intents import Intent
from twin.service import TwinService
from twin.types import CounterfactualResult, Prediction


class CloudTools:
    def __init__(
        self,
        bus,
        store,
        recipe,
        clock,
        twin: TwinService | None,
        kb: KnowledgeBase,
        *,
        client_id: str = "cloud",
    ) -> None:
        self.bus = bus
        self.store = store
        self.recipe = recipe
        self.clock = clock
        self.twin = twin
        self.kb = kb
        self.client_id = client_id

    def query_lot_history(self, lot_id: str | None = None) -> dict:
        panels = self.store.panels()
        if not panels:
            return {
                "lot_id": lot_id,
                "panels_n": 0,
                "defects": [],
                "drill": {},
                "plating": {},
                "etch": {},
            }
        if lot_id is None:
            latest = max(panels, key=lambda p: p.t_aoi)
            lot_id = latest.lot_id
        lot_panels = [p for p in panels if p.lot_id == lot_id]
        latest_panel = max(lot_panels, key=lambda p: p.t_aoi)
        defects: list = []
        for panel in lot_panels:
            defects.extend(panel.defects)
        return {
            "lot_id": lot_id,
            "panels_n": len(lot_panels),
            "defects": defects,
            "drill": latest_panel.drill,
            "plating": latest_panel.plating,
            "etch": latest_panel.etch,
        }

    def rag_search(self, query: str, k: int = 4) -> list[dict]:
        return self.kb.search(query, k=k)

    def twin_counterfactual(
        self, params: dict, hypothesis: dict, *, kind: str
    ) -> CounterfactualResult:
        if self.twin is None:
            raise RuntimeError("twin service not available")
        return self.twin.counterfactual(params, hypothesis, kind=kind)

    def twin_compare(self, candidates: list[dict]) -> list[Prediction]:
        if self.twin is None:
            raise RuntimeError("twin service not available")
        return self.twin.compare(candidates)

    def publish_intent(self, intent: Intent) -> None:
        validated = Intent.model_validate(intent.model_dump())
        self.bus.publish(
            topics.intents(validated.target_process),
            validated.model_dump(),
            self.client_id,
        )

    def record_episode(
        self,
        *,
        episode_id: str,
        process: str,
        trigger: str,
        handler: str,
        detail: dict | None = None,
    ) -> None:
        self.store.record_episode(
            EpisodeRecord(
                episode_id=episode_id,
                process=process,
                trigger=trigger,
                t_detect=self.clock.now,
                handler=handler,
                detail=detail or {},
            )
        )
