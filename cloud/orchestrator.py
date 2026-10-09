from __future__ import annotations

import os
import statistics
from pathlib import Path

from cloud.graph import build_cloud_graph, run_cloud_graph
from cloud.llm import DeepSeekLLM, FakeLLM, LlmClient
from cloud.rag import KnowledgeBase
from cloud.tools import CloudTools
from common import topics
from common.clock import SimClock
from common.config import AblationConfig
from common.recipe import Recipe
from twin.service import TwinService

ROOT = Path(__file__).resolve().parents[1]


class CloudOrchestrator:
    client_id = "cloud"
    WAKE_INTERVAL_S = 60.0
    AOI_WINDOW = 5
    MIN_WARMUP_BATCHES = 3

    def __init__(
        self,
        bus,
        recipe: Recipe,
        clock: SimClock,
        store,
        twin: TwinService | None,
        llm: LlmClient,
        *,
        use_event_trigger: bool = True,
        use_rag: bool = True,
        use_counterfactual_rca: bool = True,
        kb_dir: Path | None = None,
        persist_dir: Path | None = None,
        defect_rate_fallback: float = 0.15,
    ) -> None:
        self.bus = bus
        self.recipe = recipe
        self.clock = clock
        self.store = store
        self.twin = twin
        self._llm = llm
        self.use_event_trigger = use_event_trigger
        self.use_rag = use_rag
        self.use_counterfactual_rca = use_counterfactual_rca
        self.defect_rate_fallback = defect_rate_fallback

        kb_path = kb_dir or ROOT / "cloud" / "kb"
        chroma_path = persist_dir or ROOT / "runs" / "chroma"
        kb = KnowledgeBase(kb_path, chroma_path, enabled=use_rag)
        self._tools = CloudTools(
            bus, store, recipe, clock, twin, kb, client_id=self.client_id
        )
        self._graph = build_cloud_graph(
            llm,
            self._tools,
            use_rag=use_rag,
            use_counterfactual_rca=use_counterfactual_rca,
        )

        self._event_queue: list[dict] = []
        self._batch_rates: list[tuple[str, float, float]] = []
        self._last_wake_t = clock.now

        bus.subscribe("plant/events/+", self._on_event, self.client_id)
        bus.subscribe(topics.aoi_result(), self._on_aoi_result, self.client_id)

    @classmethod
    def from_ablation(
        cls,
        ablation: AblationConfig,
        bus,
        recipe: Recipe,
        clock: SimClock,
        store,
        twin: TwinService | None,
        llm: LlmClient | None = None,
    ) -> CloudOrchestrator:
        if llm is None:
            if os.environ.get("DEEPSEEK_API_KEY"):
                llm = DeepSeekLLM()
            else:
                llm = FakeLLM({})
        return cls(
            bus,
            recipe,
            clock,
            store,
            twin,
            llm,
            use_event_trigger=ablation.use_event_trigger,
            use_rag=ablation.use_rag,
            use_counterfactual_rca=ablation.use_counterfactual_rca,
        )

    def _on_event(self, topic: str, payload: dict) -> None:
        self._event_queue.append(payload)

    def _on_aoi_result(self, topic: str, payload: dict) -> None:
        panels = payload.get("panels") or []
        if not panels:
            return
        lot_id = payload["lot_id"]
        t = float(payload.get("t", self.clock.now))
        defective = any(panel.get("defects") for panel in panels)
        rate = 1.0 if defective else 0.0
        self._batch_rates.append((lot_id, t, rate))

    def _warmup_rates(self) -> list[float]:
        faults = self.store.faults()
        if not faults:
            return [rate for _, _, rate in self._batch_rates]
        cutoff = min(f.t_start for f in faults)
        return [rate for _, t, rate in self._batch_rates if t < cutoff]

    def _window_mean(self) -> float | None:
        if len(self._batch_rates) < self.AOI_WINDOW:
            return None
        window = self._batch_rates[-self.AOI_WINDOW :]
        return statistics.mean(rate for _, _, rate in window)

    def _aoi_threshold_exceeded(self) -> bool:
        window_mean = self._window_mean()
        if window_mean is None:
            return False
        warmup = self._warmup_rates()
        if len(warmup) < self.MIN_WARMUP_BATCHES:
            return window_mean > self.defect_rate_fallback
        mu = statistics.mean(warmup)
        sigma = statistics.pstdev(warmup) if len(warmup) > 1 else 0.0
        return window_mean > mu + 3 * sigma

    def _synthetic_aoi_event(self) -> dict:
        lot_id = self._batch_rates[-1][0] if self._batch_rates else ""
        window_mean = self._window_mean() or 0.0
        return {
            "type": "aoi_threshold",
            "lot_id": lot_id,
            "process": "etch",
            "defect_rate": window_mean,
            "t": self.clock.now,
            "episode_id": f"cloud-aoi-{int(self.clock.now)}",
        }

    def _periodic_event(self) -> dict:
        return {
            "type": "periodic",
            "t": self.clock.now,
            "episode_id": f"cloud-periodic-{int(self.clock.now)}",
        }

    def _should_wake(self) -> bool:
        if self.use_event_trigger:
            return bool(self._event_queue) or self._aoi_threshold_exceeded()
        return self.clock.now - self._last_wake_t >= self.WAKE_INTERVAL_S

    def _collect_events(self) -> list[dict]:
        events = list(self._event_queue)
        self._event_queue.clear()
        if events:
            return events
        if not self.use_event_trigger:
            return [self._periodic_event()]
        if self._aoi_threshold_exceeded():
            return [self._synthetic_aoi_event()]
        return []

    def _event_trigger(self, event: dict) -> str:
        if event.get("type") == "aoi_threshold":
            return "aoi_threshold"
        if event.get("type") == "periodic":
            return "periodic"
        return str(event.get("rule") or event.get("type") or "event")

    def _event_process(self, event: dict, state: dict) -> str:
        if event.get("process"):
            return event["process"]
        if state.get("accepted_cause"):
            return state["accepted_cause"]
        hypothesis = state.get("hypothesis") or {}
        if hypothesis.get("process"):
            return hypothesis["process"]
        return "etch"

    def _record_episode(self, event: dict, state: dict) -> None:
        detail = dict(state.get("detail") or {})
        if "llm_calls" in state:
            detail["llm_calls"] = state["llm_calls"]
        episode_id = state.get("episode_id") or event.get("episode_id") or f"cloud-{int(self.clock.now)}"
        self._tools.record_episode(
            episode_id=episode_id,
            process=self._event_process(event, state),
            trigger=self._event_trigger(event),
            handler=state.get("handler") or "human",
            detail=detail,
        )

    def _process_event(self, event: dict) -> None:
        if not event.get("episode_id"):
            process = event.get("process", "cloud")
            tick = event.get("tick", int(self.clock.tick))
            event = {
                **event,
                "episode_id": f"cloud-{process}-{tick}",
            }
        state = run_cloud_graph(self._graph, event)
        self._record_episode(event, state)

    def on_tick(self, clock: SimClock) -> None:
        self.clock = clock
        self._tools.clock = clock
        if not self._should_wake():
            return
        events = self._collect_events()
        for event in events:
            self._process_event(event)
        self._last_wake_t = clock.now
