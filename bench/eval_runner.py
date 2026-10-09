from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bench.metrics import action_accept_rate, root_cause_top1, root_cause_top3
from bench.schema import PanelRecord, TraceStore
from cloud.graph import build_cloud_graph, run_cloud_graph
from cloud.llm import FakeLLM
from cloud.rag import KnowledgeBase
from cloud.tools import CloudTools
from common.bus import InMemoryBus
from common.clock import SimClock
from common.recipe import load_recipe
from twin.types import Prediction

ROOT = Path(__file__).resolve().parents[1]
EVAL_CASES_DIR = ROOT / "bench" / "eval_cases"
RECIPE_PATH = ROOT / "bench" / "recipes" / "PN-4L-001.yaml"


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    scenario: str
    seed: int
    tick_window: list[int]
    context: dict[str, Any]
    root_cause_process: str
    acceptable_actions: list[str]
    notes: str = ""


# 证据充分性、工艺知识一致性、动作理由、风险说明。评分模型与被测 LLM 分离。
RUBRIC_KEYS = ("evidence", "consistency", "rationale", "risk")
RUBRIC_CRITERIA = {
    "evidence": "证据充分性",
    "consistency": "与工艺知识的一致性",
    "rationale": "动作理由",
    "risk": "风险说明",
}


class RubricScores(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: int = Field(ge=1, le=5)
    consistency: int = Field(ge=1, le=5)
    rationale: int = Field(ge=1, le=5)
    risk: int = Field(ge=1, le=5)


class JudgeLLM:
    """包装另一 ChatLLM / FakeLLM。不得与被测智能体共用同一客户端实例。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    def complete(self, messages: list[dict], *, response_model: type[BaseModel]) -> BaseModel:
        return self._client.complete(messages, response_model=response_model)


# FakeLLM 脚本按 case.id 注册；score_eval_case 走 run_cloud_graph(case.context)，
# 不跑完整 Plant（见各 case notes）。
CASE_SCRIPTS: dict[str, dict[str, Any]] = {
    "nc-01": {
        "SupervisorDecision": {"route": "quality_rca", "reason": "aoi etch opens"},
        "RootCauseHypothesis": {
            "process": "etch",
            "confidence": 0.91,
            "rationale": "喷嘴堵塞导致局部过蚀线宽异常",
            "hypothesis_params": {"conveyor_speed_m_min": 1.7},
        },
        "TunePlan": {
            "candidates": [
                {"kind": "width", "params": {"m_min": 1.7}, "rationale": "降速补偿"},
            ]
        },
    },
    "nc-02": {
        "SupervisorDecision": {"route": "maint", "reason": "nozzle clog pattern"},
        "MaintPlan": {
            "intent": "clean_nozzle",
            "target_process": "etch",
            "params": {"zone": 1},
            "rationale": "清洗堵塞喷嘴",
        },
    },
    "ad-01": {
        "SupervisorDecision": {"route": "quality_rca", "reason": "thin copper aoi"},
        "RootCauseHypothesis": {
            "process": "plating",
            "confidence": 0.9,
            "rationale": "添加剂耗尽导致镀铜偏薄",
            "hypothesis_params": {"asd": 2.2},
        },
        "TunePlan": {
            "candidates": [
                {"kind": "thickness", "params": {"asd": 2.2}, "rationale": "提高电流密度"},
            ]
        },
    },
    "ad-02": {
        "SupervisorDecision": {"route": "maint", "reason": "additive assay low"},
        "MaintPlan": {
            "intent": "dose_additive",
            "target_process": "plating",
            "params": {"ml_l": 1.5},
            "rationale": "补加光亮剂",
        },
    },
    "sg-01": {
        "SupervisorDecision": {"route": "maint", "reason": "sg drift"},
        "MaintPlan": {
            "intent": "adjust_sg",
            "target_process": "etch",
            "params": {"delta": -0.02},
            "rationale": "回调蚀刻液比重",
        },
    },
    "sg-02": {
        "SupervisorDecision": {"route": "quality_rca", "reason": "width under from sg"},
        "RootCauseHypothesis": {
            "process": "etch",
            "confidence": 0.88,
            "rationale": "比重漂移导致线宽偏窄",
            "hypothesis_params": {"conveyor_speed_m_min": 1.8},
        },
        "TunePlan": {
            "candidates": [
                {"kind": "width", "params": {"m_min": 1.8}, "rationale": "降速补偿比重偏高"},
            ]
        },
    },
    "dw-01": {
        "SupervisorDecision": {"route": "maint", "reason": "drill wear"},
        "MaintPlan": {
            "intent": "change_bit",
            "target_process": "drill",
            "params": {},
            "rationale": "异常磨损换针",
        },
    },
    "dw-02": {
        "SupervisorDecision": {"route": "quality_rca", "reason": "roughness aoi"},
        "RootCauseHypothesis": {
            "process": "drill",
            "confidence": 0.87,
            "rationale": "钻针磨损导致孔壁粗糙",
            "hypothesis_params": {"feed_rate_m_min": 1.8},
        },
        "TunePlan": {
            "candidates": [
                {
                    "kind": "roughness",
                    "params": {"feed_rate_m_min": 1.8},
                    "rationale": "降低进给",
                },
            ]
        },
    },
    "rl-01": {
        "SupervisorDecision": {"route": "maint", "reason": "rectifier low"},
        "MaintPlan": {
            "intent": "repair_rectifier",
            "target_process": "plating",
            "params": {},
            "rationale": "修复低电流整流器",
        },
    },
}


def load_eval_case(path: str | Path) -> EvalCase:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvalCase.model_validate(data)


def list_eval_case_paths(directory: str | Path | None = None) -> list[Path]:
    root = Path(directory) if directory is not None else EVAL_CASES_DIR
    return sorted(root.glob("*.yaml"))


def load_all_eval_cases(directory: str | Path | None = None) -> list[EvalCase]:
    return [load_eval_case(p) for p in list_eval_case_paths(directory)]


def scripts_for_case(case_id: str) -> dict[str, Any]:
    if case_id not in CASE_SCRIPTS:
        raise KeyError(case_id)
    return CASE_SCRIPTS[case_id]


def fake_llm_for_case(case_id: str) -> FakeLLM:
    return FakeLLM(scripts_for_case(case_id))


def _seed_store(case: EvalCase) -> TraceStore:
    store = TraceStore()
    process = case.root_cause_process
    lot_id = case.context.get("lot_id", "L0001")
    panel = PanelRecord(
        panel_id=f"{lot_id}-P01",
        lot_id=lot_id,
        part_no="PN-4L-001",
        t_release=0.0,
        t_aoi=5400.0,
        drill={"bit_hits": 5000, "spindle_rpm": 120000, "feed_rate_m_min": 2.0},
        plating={
            "thickness_zones": [22.0, 22.1],
            "asd": 2.0,
            "time_min": 60.0,
            "additive_ml_l": 2.5,
        },
        etch={
            "line_width_zones": [0.09, 0.09],
            "conveyor_speed_m_min": 2.0,
            "etch_temp_c": 50.0,
            "sg": 1.32,
        },
        defects=[{"type": "defect", "zone": [0, 0], "stage": process, "cause": process}],
        root_cause_truth=process,
        scrapped=False,
    )
    store.record_panel(panel)
    return store


def _make_tools(case: EvalCase) -> CloudTools:
    twin = MagicMock()
    twin.compare = MagicMock(
        return_value=[Prediction(None, None, None, 0.9, 0.1, {})]
    )
    tools = CloudTools(
        InMemoryBus(),
        _seed_store(case),
        load_recipe(RECIPE_PATH),
        SimClock(),
        twin,
        KnowledgeBase(ROOT / "cloud" / "kb", ROOT / "runs" / "eval_chroma", enabled=False),
    )
    tools.twin_compare = MagicMock(
        return_value=[Prediction(None, None, None, 0.9, 0.1, {})]
    )
    return tools


def _ranked_processes(state: dict) -> list[str]:
    """主预测在前，其余假设按出现顺序去重。Top-3 只看前三名。"""
    ranked: list[str] = []
    detail = state.get("detail") or {}
    for key in ("hypotheses", "root_causes"):
        items = detail.get(key) or []
        if not isinstance(items, list):
            continue
        for item in items:
            process = item.get("process") if isinstance(item, dict) else None
            if process and str(process) not in ranked:
                ranked.append(str(process))
    primary = _predicted_process(state)
    if primary:
        if primary in ranked:
            ranked.remove(primary)
        ranked.insert(0, primary)
    return ranked


def _predicted_process(state: dict) -> str:
    if state.get("accepted_cause"):
        return str(state["accepted_cause"])
    hypothesis = state.get("hypothesis") or {}
    if hypothesis.get("process"):
        return str(hypothesis["process"])
    maint = (state.get("detail") or {}).get("maint_plan") or {}
    if maint.get("target_process"):
        return str(maint["target_process"])
    intents = state.get("intents") or []
    if intents and intents[0].get("target_process"):
        return str(intents[0]["target_process"])
    return ""


def _actions(state: dict) -> list[str]:
    return [str(i["intent"]) for i in state.get("intents") or [] if i.get("intent")]


def score_eval_case(case: EvalCase, *, llm: FakeLLM | None = None) -> dict:
    """对单条 eval_case 跑云端图打分（注入 case.context，不跑 Plant）。"""
    client = llm if llm is not None else fake_llm_for_case(case.id)
    tools = _make_tools(case)
    graph = build_cloud_graph(
        client,
        tools,
        use_rag=False,
        use_counterfactual_rca=False,
    )
    state = run_cloud_graph(graph, dict(case.context))
    predicted = _predicted_process(state)
    predicted_processes = _ranked_processes(state)
    actions = _actions(state)
    primary = actions[0] if actions else ""
    hit = predicted == case.root_cause_process
    action_ok = any(a in set(case.acceptable_actions) for a in actions) if actions else False
    detail = state.get("detail") or {}
    return {
        "hit": hit,
        "action_ok": action_ok,
        "predicted_process": predicted,
        "predicted_processes": predicted_processes,
        "actions": actions,
        "primary_action": primary,
        "route": state.get("route", ""),
        "detail": detail,
        "llm_calls": getattr(client, "call_count", 0),
    }


def score_eval_suite(
    cases: list[EvalCase] | None = None,
    *,
    directory: str | Path | None = None,
) -> dict:
    cases = cases if cases is not None else load_all_eval_cases(directory)
    results = [score_eval_case(c, llm=fake_llm_for_case(c.id)) for c in cases]
    predicted = [r["predicted_process"] for r in results]
    predicted_lists = [
        r.get("predicted_processes") or ([r["predicted_process"]] if r["predicted_process"] else [])
        for r in results
    ]
    truth = [c.root_cause_process for c in cases]
    actions = [r["primary_action"] for r in results]
    # 若某案多动作命中，用第一个可接受动作作为主动作以便聚合指标
    scored_actions: list[str] = []
    for r, c in zip(results, cases):
        ok_set = set(c.acceptable_actions)
        chosen = next((a for a in r["actions"] if a in ok_set), r["primary_action"])
        scored_actions.append(chosen)
    acceptable = [c.acceptable_actions for c in cases]
    return {
        "n": len(cases),
        "top1": root_cause_top1(predicted, truth),
        "top3": root_cause_top3(predicted_lists, truth),
        "action_accept_rate": action_accept_rate(scored_actions, acceptable),
        "results": results,
        "cases": cases,
    }


def score_rubric(judge_llm, case: EvalCase, agent_output: dict) -> dict[str, float]:
    """用独立评分模型给四项 1–5 分。judge_llm 不得是被测智能体的 LLM。"""
    payload = {
        "criteria": {key: RUBRIC_CRITERIA[key] for key in RUBRIC_KEYS},
        "scale": "1-5",
        "case": {
            "id": case.id,
            "scenario": case.scenario,
            "root_cause_process": case.root_cause_process,
            "acceptable_actions": list(case.acceptable_actions),
            "context": case.context,
            "notes": case.notes,
        },
        "agent_output": agent_output,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是独立评审，与被测智能体不是同一个模型。"
                "按 1 到 5 分给出 evidence（证据充分性）、consistency（与工艺知识的一致性）、"
                "rationale（动作理由）、risk（风险说明）。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        },
    ]
    scores = judge_llm.complete(messages, response_model=RubricScores)
    return {key: float(getattr(scores, key)) for key in RUBRIC_KEYS}


def cohens_quadratic_kappa(
    y_true: list[int],
    y_pred: list[int],
    n_classes: int = 5,
) -> float:
    """二次加权 Cohen's κ。w_ij = (i-j)^2 / (n_classes-1)^2。空或不等长为 nan。"""
    if not y_true or not y_pred or len(y_true) != len(y_pred):
        return float("nan")
    labels = range(1, n_classes + 1)
    if any(int(y) not in labels for y in (*y_true, *y_pred)):
        return float("nan")
    width = (n_classes - 1) ** 2
    counts = [[0.0 for _ in range(n_classes)] for _ in range(n_classes)]
    for truth, pred in zip(y_true, y_pred):
        counts[int(truth) - 1][int(pred) - 1] += 1.0
    row = [sum(counts[i]) for i in range(n_classes)]
    col = [sum(counts[i][j] for i in range(n_classes)) for j in range(n_classes)]
    n = float(len(y_true))
    observed = 0.0
    expected = 0.0
    for i in range(n_classes):
        for j in range(n_classes):
            weight = (i - j) ** 2 / width
            observed += weight * counts[i][j]
            expected += weight * (row[i] * col[j] / n)
    if expected == 0.0:
        return 1.0 if observed == 0.0 else float("nan")
    return float(1.0 - observed / expected)


def run_eval_suite(
    cases: list[EvalCase] | None = None,
    *,
    directory: str | Path | None = None,
    judge_llm=None,
    human_scores: list[dict] | None = None,
) -> dict:
    """汇总 Top-1/Top-3、动作可接受率与四项评审均分。κ 仅在提供 human_scores 时计算。"""
    summary = score_eval_suite(cases, directory=directory)
    scored_cases = summary["cases"]
    results = summary["results"]
    rubrics: list[dict[str, float]] | None = None
    if judge_llm is not None:
        rubrics = [score_rubric(judge_llm, case, result) for case, result in zip(scored_cases, results)]
    if rubrics:
        rubric_mean = {key: float(sum(row[key] for row in rubrics) / len(rubrics)) for key in RUBRIC_KEYS}
    else:
        rubric_mean = {key: float("nan") for key in RUBRIC_KEYS}
    out = {**summary, "rubric_mean": rubric_mean}
    if rubrics is not None:
        out["rubrics"] = rubrics
    if human_scores is not None:
        kappa: dict[str, float] = {}
        aligned = rubrics is not None and len(human_scores) == len(rubrics)
        for key in RUBRIC_KEYS:
            if not aligned:
                kappa[key] = float("nan")
                continue
            y_true = [int(row[key]) for row in human_scores]
            y_pred = [int(row[key]) for row in rubrics]
            kappa[key] = cohens_quadratic_kappa(y_true, y_pred)
        out["kappa"] = kappa
    return out


def _main() -> None:
    """评测套件入口。默认不算 κ；矩阵不必调用本路径。"""
    suite = run_eval_suite()
    payload = {
        "n": suite["n"],
        "top1": suite["top1"],
        "top3": suite["top3"],
        "action_accept_rate": suite["action_accept_rate"],
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    _main()
