# M4 云端多智能体 + RAG 设计规格

**日期：** 2026-10-09  
**状态：** 待审阅  
**父规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.4、§7.2 根因评测、§10 M4  
**相关 ADR：** ADR-002（LangGraph）、ADR-004（事件触发 LLM）、ADR-003（孪生隔离）

## 1. 目标

在已落地的边缘智能体（M2）与四层孪生（M3）之上，实现工厂云端反思层：事件/AOI 阈值触发 → Supervisor 分派 → 质量根因（RAG + 反事实验证）→ 工艺调优 / 设备维护 → 经包络的 `Intent` 下发；并提供可复现的 `eval_cases` 客观评分与安全加固检查。

验收（与父规格 M4 对齐）：

1. 从 AOI 缺陷（或边缘事件）到根因、反事实验证、调参方案的流程可跑通。
2. `cloud/` 不 import `sim`；隔离测试通过。
3. 通过安全加固检查（密钥、输出 schema、无任意代码执行）。
4. FakeLLM 路径下 eval_cases：根因工序 Top-1 ≥ 0.75，动作可接受率 ≥ 0.75。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 编排 | 方案 A：薄 LangGraph + 工具化节点（非多轮自由对话） |
| 开发期 LLM | 真实 DeepSeek（OpenAI 兼容接口） |
| 测试 | 双轨：默认 FakeLLM；`@pytest.mark.live` 有 Key 时跑真实端到端 |
| RAG | 仓库内 `cloud/kb/*.md` + 本地 Chroma |
| Guard / 哈希链 | M4 不做，留给 M5 |

## 3. 触发与消融

`CloudOrchestrator` 实现 `Controller.on_tick`，在 `Plant.step_tick` 之后、与边缘/孪生同拍调度。

唤醒条件（`use_event_trigger=true`，默认）：

1. 本拍或缓存中存在未处理的 `plant/events/+`；或
2. 最近 5 个已完成批次的 AOI 缺陷率超过故障前基线均值 + 3σ（σ 在预热批次上估计；预热不足时用可配置绝对阈值兜底）。

`use_event_trigger=false`：周期性唤醒——当 `clock.now - last_wake_t ≥ 60` 仿真秒时进入图（消融对照）。在默认 `tick_seconds=1800` 下，等价于每个 tick 至多唤醒一次。

`use_cloud=false`：不挂载编排器。

## 4. 图拓扑

```
START → supervisor → quality_rca → process_tuner → END
                  ↘ maint → END
                  ↘ END
```

- **supervisor**：根据事件类型 / AOI 摘要路由。AOI 质量类 → `quality_rca`（成功采纳根因后可续 `process_tuner`）；钻针寿命/振动、整流器维修类 → `maint`；无法识别 → `END` 并记人工。
- **quality_rca**：缺陷聚类摘要 →（可选）`rag_search` → LLM 提出根因假设（工序 + 置信度 + 理由）→ 若 `use_counterfactual_rca` 则调用 `twin_counterfactual`；仅当 `defect_cleared=true` 采纳，否则驳回并 `handler=human`。
- **process_tuner**：LLM 或规则生成 ≤5 个候选参数 → `twin_compare` → 最优方案经 `bound`/Intent 模型校验后 `publish_intent`（`source=cloud`）。
- **maint**：生成换针 / 维修类 Intent 或写入 episode 工单细节（M4 以 Intent + episode.detail 为准）。

状态字段（示意）：`event`、`route`、`hypotheses`、`accepted_cause`、`candidates`、`intents`、`episode_id`、`llm_calls`、`token_usage`。

## 5. 工具

全部为纯 Python，禁止 import `sim`。

| 名称 | 行为 |
|------|------|
| `query_lot_history` | 从 `TraceStore` 读取指定/最近批次的工序快照与缺陷 |
| `rag_search` | Chroma 检索，返回文本片段与来源路径；`use_rag=false` 返回空 |
| `twin_counterfactual` | 调用已注入的 `TwinService.counterfactual` |
| `twin_compare` | 调用 `TwinService.compare` |
| `publish_intent` | 向 `plant/intents/<process>` 发布通过 Pydantic 校验的 Intent |
| `record_episode` | 写入 `episode_log`（`handler=cloud` 或转人工时 `human`） |

## 6. LLM 客户端

- 接口：`complete(messages, *, response_model: type[BaseModel]) -> BaseModel`。
- **DeepSeek**：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`（默认官方兼容 URL）、`DEEPSEEK_MODEL`；仅环境变量 / `.env`（仓库只提交 `.env.example`）。
- **FakeLLM**：按 prompt/场景键返回预设 JSON，供默认 pytest。
- 校验失败：本轮处置转人工，记入 episode，**不**自动重试执行动作（与父规格 §6 一致）。

## 7. RAG

- 语料：`cloud/kb/` 中文 Markdown，至少覆盖喷嘴堵塞、添加剂耗尽、蚀刻比重漂移、整流器异常、钻针磨损的 FMEA 摘要、IPC 判定要点摘要、2～3 份模拟 8D。
- 向量库：Chroma，持久化 `runs/chroma/`（已在 `runs/` gitignore）。
- 嵌入：优先可离线复现的本地句向量模型；实施计划中钉死具体包与模型名，并提供无 GPU 时的 CPU 路径。避免把嵌入再绑到 DeepSeek，以免测试与演示强依赖双 API。
- `rag_search(query, k=4)`。

## 8. eval_cases

路径：`bench/eval_cases/*.yaml`。

每条字段：`id`、`scenario`、`seed`、`tick_window`、`context`、`root_cause_process`、`acceptable_actions`、`notes`。

规模：从 `nozzle_clog`、`additive_depletion`、`sg_drift`、`drill_wear`、`rectifier_low` 各至少 1～2 条，合计 ≥8。

客观分：

- 根因工序 Top-1（主）；可选 Top-3。
- 最终动作是否 ∈ `acceptable_actions`。
- `episode.detail` 记录 `llm_calls` 与粗估 token。

FakeLLM 路径门槛：Top-1 ≥ 0.75，动作可接受率 ≥ 0.75。live 不加硬门槛，有 Key 跑通 1 条即可。

## 9. 目录与 runner 接入

```
cloud/
  __init__.py
  llm.py
  tools.py
  rag.py
  graph.py
  orchestrator.py
  kb/*.md
bench/eval_cases/*.yaml
tests/cloud/
.env.example
```

`sim/runner.py`：当 `ablation.use_cloud` 为真时，在边缘与孪生控制器之后追加 `CloudOrchestrator`（注入同一 `bus`、`recipe`、`clock`、`store`、可选 `TwinService` 引用）。

## 10. 与 M3 / M5 的边界

| M4 做 | 不做 |
|-------|------|
| 云端图、RCA、反事实门槛、调参/维护 Intent、RAG、eval_cases | Guard 三道关、人工审批队列（M5） |
| episode 与 LLM 用量埋点 | 哈希链篡改检测（M5） |
| 安全清单对 cloud 的检查 | 看板（M6）、全消融报告（M7） |
| 复用 M3 TwinService API | 修改孪生机理常数以迎合根因 |

## 11. 测试策略

- 单元：FakeLLM + InMemoryBus；图节点与工具契约。
- 隔离：`tests/test_isolation.py` 覆盖 `cloud/`。
- 集成：单场景短 tick，断言 Intent / episode。
- eval_cases 套件：FakeLLM 脚本化答案对齐真值。
- `@pytest.mark.live`：无 `DEEPSEEK_API_KEY` 则 skip。
- 安全：对照 `.cursor/skills/security-and-hardening` 检查清单做一次书面/自动化抽查。

## 12. 非目标

- 不在 M4 实现 Streamlit 看板或完整 OODA 审批闭环。
- 不引入 Google ADK；评测仅借鉴其“用例 + 客观分”思路，LLM-as-judge 可选且非本里程碑门槛。
- 不在 CI 默认路径调用真实网络 LLM。
