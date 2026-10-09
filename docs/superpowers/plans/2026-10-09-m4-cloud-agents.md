# M4 云端多智能体 + RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现事件/AOI 触发的云端 LangGraph 编排（Supervisor → RCA/调优/维护），接入 DeepSeek 与 FakeLLM、仓内 Chroma RAG、eval_cases 客观分，并在 FakeLLM 下达到 Top-1≥0.75、动作可接受率≥0.75。

**Architecture:** `CloudOrchestrator` 为 `Controller`，只经 `Bus` + `TraceStore` + 注入的 `TwinService` 工作，禁止 import `sim`。LLM 输出经 Pydantic schema；工具为纯 Python。默认测试走 FakeLLM；`@pytest.mark.live` 接 DeepSeek。

**Tech Stack:** Python 3.12, pydantic v2, langgraph, openai（DeepSeek 兼容）, chromadb, numpy, scikit-learn（HashingVectorizer 嵌入）, pytest。

**规格：** `docs/superpowers/specs/2026-10-09-m4-cloud-agents-design.md`；父规格 §4.4、§6、§7.2、§10 M4；ADR-002、ADR-004。

## Global Constraints

- Python 3.12；依赖用 `pyproject.toml` 管理；测试用 `pytest`。
- 所有随机过程接受 `seed`；同一 seed 必须得到逐位一致的结果。随机数一律通过 `common.rng.make_rng(seed, stream)` 获取。
- 消息总线抽象为 `Bus` 接口；单元测试不依赖 Mosquitto。
- 仿真时间由 `SimClock` 驱动。禁止使用 `time.time()` / `datetime.now()` 作为仿真时间。
- 代码标识符用英文；文档和日志说明用中文。
- 四类埋点字段只能新增，不能修改语义。
- `twin/`、`edge/`、`cloud/`、`guard/` 不允许 import `sim` 包的任何模块。
- JSON 序列化：`json.dumps(obj, sort_keys=True, ensure_ascii=False)`。
- 测试放在 `tests/<包名>/test_*.py`；`--import-mode=importlib`，`pythonpath = ["."]`。
- 不写解释“这行做什么”的注释。
- 密钥只从环境变量读取；仓库只提交 `.env.example`。
- LLM 输出视为不可信：必须先过 Pydantic 与 Intent/包络校验才能下发。

## File Structure

```
cloud/__init__.py
cloud/llm.py                 # FakeLLM / DeepSeekLLM / complete()
cloud/schemas.py             # SupervisorRoute, RootCauseHypothesis, TuneCandidates, MaintAction
cloud/rag.py                 # HashingEmbedder + ChromaKnowledgeBase
cloud/tools.py               # CloudTools
cloud/graph.py               # build_cloud_graph / invoke
cloud/orchestrator.py        # CloudOrchestrator Controller
cloud/kb/*.md                # 精简语料
bench/eval_cases/*.yaml
bench/eval_runner.py         # 加载用例、跑分
bench/metrics.py             # root_cause_top1 / action_accept_rate / llm_usage
bench/metrics_spec.md        # M4 指标标已实现
sim/runner.py                # use_cloud 挂载编排器
.env.example
pyproject.toml
tests/cloud/test_*.py
tests/bench/test_eval_cases.py
```

## 数值与语义约定

- DeepSeek 默认：`DEEPSEEK_BASE_URL=https://api.deepseek.com`，`DEEPSEEK_MODEL=deepseek-chat`；Key：`DEEPSEEK_API_KEY`。
- FakeLLM：构造时传入 `scripts: dict[str, dict]`，键为 `response_model.__name__`（同名多次调用用列表队列 pop）；缺键则抛 `KeyError`（测试显式失败，不静默编造）。
- AOI 缺陷率阈值：预热批次 = 故障注入前全部已完成批次（`fault_truth` 最早 `t_start` 之前 `t_aoi`）；`μ,σ` 在预热批次缺陷率上计算；不足 3 个预热批次时用绝对阈值 `defect_rate > 0.15`。窗口：最近 5 个已完成批次。
- 周期唤醒：`clock.now - last_wake_t >= 60.0`。
- 嵌入：`sklearn.feature_extraction.text.HashingVectorizer(n_features=256, alternate_sign=False, norm="l2")`，对中文按字符 bigram（`analyzer=lambda s: [s[i:i+2] for i in range(max(len(s)-1,0))]`）；确定性、无下载。
- Chroma：`PersistentClient(path=str(persist_dir))`，collection 名 `pcb_kb`；`persist_dir` 默认 `ROOT/runs/chroma`。
- RCA 假设 schema：`process: Literal["drill","plating","etch"]`，`confidence: float`，`rationale: str`，`hypothesis_params: dict`（供 counterfactual）。
- counterfactual：`kind` 由根因工序映射——`plating→thickness`，`etch→width`，`drill→roughness`；`params` 从 `query_lot_history` 最近批次拼出，缺省用配方 target。
- Tuner 候选 ≤5；每项含 `kind` 与参数；`compare` 后取 `yield_prob` 最大且 `oos_prob` 最小（先比 yield 再比 oos）。
- Intent：`source="cloud"`，`policy_version="m4"`；发布前 `Intent.model_validate`；调参类再经 `edge.envelope.bound_command` 对应参数裁剪（若 Intent.params 含 command 映射则裁剪后写回）。
- `client_id="cloud"`。
- eval_cases FakeLLM：按 `case.id` 注册脚本，使 Top-1 与可接受动作可稳定达标。

---

### Task 1: LLM 客户端与响应 schema

**Files:**
- Create: `cloud/__init__.py`, `cloud/llm.py`, `cloud/schemas.py`, `.env.example`
- Modify: `pyproject.toml`（加 `langgraph>=0.2`, `openai>=1.40`）
- Test: `tests/cloud/test_llm.py`

**Interfaces:**
- `class LlmClient(Protocol): def complete(self, messages: list[dict], *, response_model: type[BaseModel]) -> BaseModel: ...`
- `class FakeLLM`: `__init__(self, scripts: dict[str, list[dict] | dict])`；`complete` 按 `response_model.__name__` 取脚本，`model_validate` 后返回；队列空则 `KeyError`。
- `class DeepSeekLLM`: `__init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None)`；读环境变量缺省；用 `openai.OpenAI` chat.completions，`response_format` 尽量 JSON；解析后 `model_validate`；失败抛 `ValueError`（调用方转人工）。
- Schemas（均 frozen pydantic）：
  - `SupervisorDecision`: `route: Literal["quality_rca","process_tuner","maint","end"]`, `reason: str`
  - `RootCauseHypothesis`: `process: Literal["drill","plating","etch"]`, `confidence: float`, `rationale: str`, `hypothesis_params: dict = {}`
  - `TuneCandidate`: `kind: Literal["thickness","width","roughness"]`, `params: dict`, `rationale: str = ""`
  - `TunePlan`: `candidates: list[TuneCandidate]`（max 5，用 field validator）
  - `MaintPlan`: `intent: str`, `target_process: Literal["drill","plating","etch"]`, `params: dict`, `rationale: str`

- [ ] **Step 1: 失败测试**

```python
from cloud.llm import FakeLLM
from cloud.schemas import SupervisorDecision

def test_fake_llm_returns_scripted_model():
    llm = FakeLLM({"SupervisorDecision": {"route": "quality_rca", "reason": "aoi"}})
    out = llm.complete([{"role": "user", "content": "x"}], response_model=SupervisorDecision)
    assert out.route == "quality_rca"
```

- [ ] **Step 2:** `python -m pytest tests/cloud/test_llm.py -q` 失败（模块不存在）。
- [ ] **Step 3:** 实现 schema、FakeLLM、DeepSeekLLM 骨架（DeepSeek 在无 Key 时可构造但 complete 抛清晰错误）、`.env.example`、依赖。
- [ ] **Step 4:** 测试通过；另测 FakeLLM 队列两次调用与缺键 KeyError。
- [ ] **Step 5:** 提交 `feat: 云端 FakeLLM/DeepSeek 客户端与结构化 schema`

---

### Task 2: RAG（Hashing 嵌入 + Chroma + kb）

**Files:**
- Create: `cloud/rag.py`, `cloud/kb/fmea_nozzle.md`, `cloud/kb/fmea_additive.md`, `cloud/kb/fmea_sg.md`, `cloud/kb/fmea_rectifier.md`, `cloud/kb/fmea_drill.md`, `cloud/kb/ipc_summary.md`, `cloud/kb/8d_nozzle.md`, `cloud/kb/8d_additive.md`
- Modify: `pyproject.toml`（加 `chromadb>=0.5`）
- Test: `tests/cloud/test_rag.py`

**Interfaces:**
- `class HashingEmbedder`: `embed_documents(texts: list[str]) -> list[list[float]]`；`embed_query(text: str) -> list[float]`
- `class KnowledgeBase`: `__init__(self, kb_dir: Path, persist_dir: Path, *, enabled: bool = True)`；`search(self, query: str, k: int = 4) -> list[dict]` 每项 `{"text","source"}`；`enabled=False` 或空库返回 `[]`；首次 `search` 或显式 `build()` 索引 `kb_dir.glob("*.md")`。

kb 文件各 ≥15 行中文，含故障名与推荐动作关键词（如 `clean_nozzle`、`dose_additive`、`adjust_sg`、`repair_rectifier`、`change_bit`），便于检索与 FakeLLM 理由拼接。

- [ ] **Step 1:**

```python
def test_rag_returns_sources_for_nozzle_query(tmp_path):
    # 复制或指向仓库 cloud/kb
    kb = KnowledgeBase(ROOT / "cloud" / "kb", tmp_path / "chroma", enabled=True)
    hits = kb.search("蚀刻喷嘴堵塞 残留", k=3)
    assert hits and all("source" in h and "text" in h for h in hits)
```

- [ ] **Step 2–5:** 失败 → 实现 → `use_rag` 关闭返回空 → 提交 `feat: 仓内知识库与 Hashing+Chroma RAG`

---

### Task 3: CloudTools

**Files:**
- Create: `cloud/tools.py`
- Test: `tests/cloud/test_tools.py`

**Interfaces:**
- `class CloudTools`:
  - `__init__(self, bus, store, recipe, clock, twin: TwinService | None, kb: KnowledgeBase, *, client_id: str = "cloud")`
  - `query_lot_history(self, lot_id: str | None = None) -> dict`：无 lot_id 取 `store.panels()` 中最新 `lot_id`；返回 `{lot_id, panels_n, defects, drill, plating, etch}`（字段来自 PanelRecord）。
  - `rag_search(self, query: str, k: int = 4) -> list[dict]`
  - `twin_counterfactual(self, params: dict, hypothesis: dict, *, kind: str) -> CounterfactualResult`；`twin is None` 时抛 `RuntimeError`
  - `twin_compare(self, candidates: list[dict]) -> list[Prediction]`
  - `publish_intent(self, intent: Intent) -> None`：`bus.publish(topics.intents(intent.target_process), intent.model_dump(), client_id)`
  - `record_episode(self, *, episode_id, process, trigger, handler, detail: dict | None = None) -> None`

- [ ] 先测 `publish_intent` 上总线可订阅到 `source=cloud`；`query_lot_history` 用 TraceStore 手写一条 PanelRecord。
- [ ] 提交 `feat: 云端工具集（履历/RAG/孪生/Intent/episode）`

---

### Task 4: LangGraph 图

**Files:**
- Create: `cloud/graph.py`
- Test: `tests/cloud/test_graph.py`

**Interfaces:**
- `CloudState` TypedDict：`event: dict`, `route: str`, `hypothesis: dict | None`, `accepted_cause: str | None`, `intents: list[dict]`, `episode_id: str`, `handler: str`, `detail: dict`, `messages: list`
- `build_cloud_graph(llm: LlmClient, tools: CloudTools, *, use_rag: bool, use_counterfactual_rca: bool) -> CompiledGraph`
- `run_cloud_graph(graph, event: dict) -> CloudState`：`graph.invoke(initial_state)`

节点行为（实现必须与测试一致）：
- `supervisor`：`llm.complete(..., SupervisorDecision)` → 写 `route`
- `quality_rca`：拼 context（event + `query_lot_history` + 可选 rag）→ `RootCauseHypothesis` → 若 `use_counterfactual_rca` 且 twin 可用则 counterfactual；`defect_cleared` 则 `accepted_cause=hypothesis.process`、`handler=cloud`，否则 `handler=human`、`accepted_cause=None`
- `process_tuner`：仅当 `accepted_cause` 非空；`TunePlan` → compare → 选优 → `Intent`（intent 名如 `set_conveyor_speed` / `set_current_density` 等与 params 对齐）publish；append intents
- `maint`：`MaintPlan` → Intent publish
- 路由边：supervisor 后按 route 条件边；quality_rca 后若 accepted_cause 则 process_tuner 否则 END；tuner/maint → END

- [ ] FakeLLM 脚本驱动：AOI 事件 → route quality_rca → 采纳 etch → 发出含 `clean_nozzle` 或调速 Intent；反事实失败则 handler=human 且无 Intent。
- [ ] 提交 `feat: LangGraph 云端 supervisor/RCA/tuner/maint`

---

### Task 5: CloudOrchestrator 触发

**Files:**
- Create: `cloud/orchestrator.py`
- Test: `tests/cloud/test_orchestrator.py`

**Interfaces:**
- `CloudOrchestrator(bus, recipe, clock, store, twin, llm, *, use_event_trigger=True, use_rag=True, use_counterfactual_rca=True, kb_dir=None, persist_dir=None, defect_rate_fallback=0.15)`
- `client_id = "cloud"`
- 订阅 `plant/events/+` 入队列；订阅 `topics.aoi_result()` 更新批次缺陷率滑动窗（按 lot 聚合：该 lot panels 任一有缺陷则批次缺陷）。
- `on_tick`：若应唤醒则对队列事件（或合成 aoi 阈值事件）逐个 `run_cloud_graph`；清空已处理；更新 `last_wake_t`。
- 工厂方法 `from_ablation(ablation, bus, recipe, clock, store, twin, llm=None)`：无 llm 且无 `DEEPSEEK_API_KEY` 时用空脚本 FakeLLM（仅保证不挂；测试传入 FakeLLM）。

- [ ] 测：无事件不调图；推送 etch 事件后 on_tick 产生 episode；`use_event_trigger=False` 时无事件但间隔≥60 仍唤醒（可用 Fake 记录调用次数）。
- [ ] 提交 `feat: 云端编排器事件与 AOI 阈值触发`

---

### Task 6: runner 接入

**Files:**
- Modify: `sim/runner.py`
- Test: `tests/sim/test_runner.py`（扩展）或 `tests/cloud/test_runner_cloud.py`

**规则：**
- `ablation.use_cloud` 为真时，构造 `TwinService`（若尚未因 twin 开关创建则仍按现逻辑），再 `CloudOrchestrator.from_ablation(..., twin=该实例或新建共享引用)`。
- **共享 twin：** runner 内先创建可选的单个 `TwinService` 变量，边缘后 append twin，再把同一引用传给 cloud。

伪代码：

```python
twin = None
if ablation.use_twin_lookahead and ablation.twin_fidelity != "none":
    twin = TwinService(...)
    extra.append(twin)
if ablation.use_cloud:
    extra.append(CloudOrchestrator.from_ablation(ablation, bus, recipe, clock, store, twin))
```

- [ ] `edge_only` 不出现 cloud client 订阅；带 `use_cloud` 的临时 ablation 短跑不崩溃。
- [ ] 提交 `feat: runner 按消融挂载云端编排器`

---

### Task 7: eval_cases 与客观分

**Files:**
- Create: `bench/eval_cases/` 至少 8 个 yaml；`bench/eval_runner.py`
- Modify: `bench/metrics.py`, `bench/metrics_spec.md`, `tests/bench/test_metrics.py`
- Test: `tests/bench/test_eval_cases.py`, `tests/cloud/test_eval_fake.py`

**Interfaces:**
- `load_eval_case(path) -> EvalCase`（pydantic）
- `root_cause_top1(predicted: list[str], truth: list[str]) -> float`
- `action_accept_rate(actions: list[str], acceptable: list[set[str] | list[str]]) -> float`
- `score_eval_case(case, *, llm: FakeLLM) -> dict`：短跑或直接 `run_cloud_graph` 注入 case.context 为 event；返回 `hit`, `action_ok`, `predicted_process`, `actions`

用例 id 示例：`nc-01`（nozzle/etch）、`ad-01`（additive/plating）、`sg-01`、`dw-01`、`rl-01`，各场景至少 1 条，总数 ≥8。

- [ ] FakeLLM 套件汇总 `top1 >= 0.75` 且 `action_accept_rate >= 0.75`。
- [ ] `metrics_spec.md`：根因准确率、LLM 调用次数标「已实现（M4）」并指向函数名。
- [ ] 提交 `feat: eval_cases 客观分与 FakeLLM 达标套件`

---

### Task 8: live 标记、隔离与安全检查

**Files:**
- Create: `tests/cloud/test_live_deepseek.py`, `tests/cloud/test_security_cloud.py`
- Modify: 确认 `tests/test_isolation.py` 覆盖新建 cloud 文件

**规则：**
- `test_live_deepseek.py`：`pytestmark = pytest.mark.live`；无 Key 则 `pytest.skip`；有 Key 时对一条合成事件调用 DeepSeekLLM+graph，断言返回 `SupervisorDecision` 或完整 state 含 route。
- `test_security_cloud.py`：断言 `.env.example` 存在且无真实密钥形态；`cloud/` 源码无 `exec(` / `eval(`；`DeepSeekLLM` 不把 key 写进异常消息。
- 全量 `python -m pytest -q`（不含 live 或 live skip）通过。

- [ ] 提交 `feat: 云端 live 标记与安全/隔离验收`

---

## 自审

- 规格触发/消融 → Task 5–6。
- 图与工具 → Task 3–4。
- DeepSeek + Fake 双轨 → Task 1、8。
- RAG kb+Chroma → Task 2。
- eval_cases 门槛 → Task 7。
- 不做 Guard/哈希链/看板。
- 嵌入钉死 HashingVectorizer，避免联网下载模型。

## 执行交接

Plan complete and saved to `docs/superpowers/plans/2026-10-09-m4-cloud-agents.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每任务新子智能体 + 任务间评审  

**2. Inline Execution** — 本会话按 executing-plans 连续执行  

Which approach?
