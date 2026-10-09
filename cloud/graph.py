from __future__ import annotations

import json
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from cloud.llm import LlmClient
from cloud.schemas import MaintPlan, RootCauseHypothesis, SupervisorDecision, TunePlan
from cloud.tools import CloudTools
from common.intents import Intent
from edge.envelope import bound_command

_PROCESS_TO_KIND: dict[str, Literal["thickness", "width", "roughness"]] = {
    "plating": "thickness",
    "etch": "width",
    "drill": "roughness",
}

_INTENT_BY_PROCESS_KIND: dict[tuple[str, str], str] = {
    ("etch", "width"): "set_conveyor_speed",
    ("plating", "thickness"): "set_current_density",
    ("drill", "roughness"): "set_feed",
}


class CloudState(TypedDict):
    event: dict
    route: str
    hypothesis: dict | None
    accepted_cause: str | None
    intents: list[dict]
    episode_id: str
    handler: str
    detail: dict
    messages: list


def _lot_params(history: dict, process: str, recipe) -> dict:
    params = dict(history.get(process) or {})
    if process == "plating":
        zones = params.get("thickness_zones")
        if zones:
            params.setdefault("asd", recipe.window("plating", "current_density_asd").target)
            params.setdefault("time_min", 60.0)
            params.setdefault("additive_ml_l", 4.5)
    elif process == "etch":
        params.setdefault("conveyor_speed_m_min", recipe.window("etch", "conveyor_speed_m_min").target)
        params.setdefault("etch_temp_c", recipe.window("etch", "etch_temp_c").target)
        params.setdefault("sg", recipe.window("etch", "sg").target)
    elif process == "drill":
        params.setdefault("bit_hits", 0)
        params.setdefault("spindle_rpm", recipe.window("drill", "spindle_rpm").target)
        params.setdefault("feed_rate_m_min", recipe.window("drill", "feed_rate_m_min").target)
    return params


def _pick_best_candidate(candidates: list[dict], predictions) -> dict:
    best_idx = 0
    best = (predictions[0].yield_prob, -predictions[0].oos_prob)
    for idx in range(1, len(candidates)):
        score = (predictions[idx].yield_prob, -predictions[idx].oos_prob)
        if score > best:
            best = score
            best_idx = idx
    return candidates[best_idx]


def build_cloud_graph(
    llm: LlmClient,
    tools: CloudTools,
    *,
    use_rag: bool,
    use_counterfactual_rca: bool,
):
    def supervisor(state: CloudState) -> CloudState:
        event = state["event"]
        messages = [
            {
                "role": "user",
                "content": json.dumps({"event": event}, sort_keys=True, ensure_ascii=False),
            }
        ]
        decision = llm.complete(messages, response_model=SupervisorDecision)
        handler = "human" if decision.route == "end" else state.get("handler", "")
        return {
            **state,
            "route": decision.route,
            "handler": handler,
            "detail": {**state.get("detail", {}), "supervisor_reason": decision.reason},
            "messages": state.get("messages", []) + messages,
        }

    def quality_rca(state: CloudState) -> CloudState:
        event = state["event"]
        lot_id = event.get("lot_id")
        history = tools.query_lot_history(lot_id)
        rag_hits: list[dict] = []
        if use_rag:
            query = event.get("rag_query") or f"{event.get('type', '')} {event.get('process', '')}"
            rag_hits = tools.rag_search(query, k=4)
        context = {
            "event": event,
            "lot_history": history,
            "rag": rag_hits,
        }
        messages = [
            {
                "role": "user",
                "content": json.dumps(context, sort_keys=True, ensure_ascii=False),
            }
        ]
        hypothesis = llm.complete(messages, response_model=RootCauseHypothesis)
        hypothesis_dict = hypothesis.model_dump()
        accepted_cause: str | None = None
        handler = "human"
        detail = {
            **state.get("detail", {}),
            "hypothesis": hypothesis_dict,
        }

        if use_counterfactual_rca and tools.twin is not None:
            kind = _PROCESS_TO_KIND[hypothesis.process]
            params = _lot_params(history, hypothesis.process, tools.recipe)
            cf = tools.twin_counterfactual(
                params,
                hypothesis.hypothesis_params,
                kind=kind,
            )
            detail["counterfactual"] = {
                "defect_cleared": cf.defect_cleared,
                "before_oos": cf.before.oos_prob,
                "after_oos": cf.after.oos_prob,
            }
            if cf.defect_cleared:
                accepted_cause = hypothesis.process
                handler = "cloud"
        elif not use_counterfactual_rca:
            accepted_cause = hypothesis.process
            handler = "cloud"

        return {
            **state,
            "hypothesis": hypothesis_dict,
            "accepted_cause": accepted_cause,
            "handler": handler,
            "detail": detail,
            "messages": state.get("messages", []) + messages,
        }

    def process_tuner(state: CloudState) -> CloudState:
        if not state.get("accepted_cause"):
            return state
        process = state["accepted_cause"]
        context = {
            "event": state["event"],
            "accepted_cause": process,
            "hypothesis": state.get("hypothesis"),
        }
        messages = [
            {
                "role": "user",
                "content": json.dumps(context, sort_keys=True, ensure_ascii=False),
            }
        ]
        plan = llm.complete(messages, response_model=TunePlan)
        compare_candidates: list[dict] = []
        for candidate in plan.candidates:
            compare_candidates.append({"kind": candidate.kind, **candidate.params})
        predictions = tools.twin_compare(compare_candidates)
        best = _pick_best_candidate(compare_candidates, predictions)
        kind = best["kind"]
        params = {key: value for key, value in best.items() if key != "kind"}
        intent_name = _INTENT_BY_PROCESS_KIND[(process, kind)]
        bound = bound_command(tools.recipe, process, intent_name, params)
        if bound is None:
            return {
                **state,
                "handler": "human",
                "detail": {**state.get("detail", {}), "tuner_error": "bound_command rejected"},
                "messages": state.get("messages", []) + messages,
            }
        command, bounded_params = bound
        intent = Intent(
            intent=command,
            target_process=process,
            params=bounded_params,
            lot_id=state["event"].get("lot_id"),
            source="cloud",
            policy_version="m4",
            rationale=next(
                (c.rationale for c in plan.candidates if c.kind == kind and c.params == params),
                "",
            ),
        )
        tools.publish_intent(intent)
        intent_dict = intent.model_dump()
        return {
            **state,
            "intents": state.get("intents", []) + [intent_dict],
            "detail": {**state.get("detail", {}), "tuner_best": best},
            "messages": state.get("messages", []) + messages,
        }

    def maint(state: CloudState) -> CloudState:
        event = state["event"]
        messages = [
            {
                "role": "user",
                "content": json.dumps({"event": event}, sort_keys=True, ensure_ascii=False),
            }
        ]
        plan = llm.complete(messages, response_model=MaintPlan)
        intent = Intent(
            intent=plan.intent,
            target_process=plan.target_process,
            params=plan.params,
            lot_id=event.get("lot_id"),
            source="cloud",
            policy_version="m4",
            rationale=plan.rationale,
        )
        tools.publish_intent(intent)
        intent_dict = intent.model_dump()
        return {
            **state,
            "handler": "cloud",
            "intents": state.get("intents", []) + [intent_dict],
            "detail": {**state.get("detail", {}), "maint_plan": plan.model_dump()},
            "messages": state.get("messages", []) + messages,
        }

    def route_after_supervisor(state: CloudState) -> str:
        route = state["route"]
        if route == "end":
            return END
        return route

    def route_after_rca(state: CloudState) -> str:
        if state.get("accepted_cause"):
            return "process_tuner"
        return END

    graph = StateGraph(CloudState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("quality_rca", quality_rca)
    graph.add_node("process_tuner", process_tuner)
    graph.add_node("maint", maint)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"quality_rca": "quality_rca", "process_tuner": "process_tuner", "maint": "maint", END: END},
    )
    graph.add_conditional_edges(
        "quality_rca",
        route_after_rca,
        {"process_tuner": "process_tuner", END: END},
    )
    graph.add_edge("process_tuner", END)
    graph.add_edge("maint", END)
    return graph.compile()


def run_cloud_graph(graph, event: dict) -> CloudState:
    initial_state: CloudState = {
        "event": event,
        "route": "",
        "hypothesis": None,
        "accepted_cause": None,
        "intents": [],
        "episode_id": event.get("episode_id", ""),
        "handler": "",
        "detail": {},
        "messages": [],
    }
    return graph.invoke(initial_state)
