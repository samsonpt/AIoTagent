# M5 Guard、审批与哈希链追溯设计规格

**日期：** 2026-10-09  
**状态：** 待审阅  
**父规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.6、§6、§7.5、§10 M5  
**相关 ADR：** ADR-006（SQLite 哈希链）、ADR-003（孪生隔离）

## 1. 目标

实现运行时动作守卫与批次级防篡改追溯，打通「感知 → 决策 → Guard →（审批）→ 执行」闭环：

1. 完整 OODA 短场景可跑通（含云端/边缘指令经 Guard 到 Plant）。
2. 篡改检测：`verify_chain` 失败后停止非白名单自动动作。
3. 通过安全加固检查；`guard/` 不 import `sim`。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 架构 | 方案 A：Guard 总线网关 + 审批队列 + 旁路哈希链 |
| 第三关审批 | 扩展 `HumanModel` 模拟；看板只读留给 M6 |
| 哈希链 | 独立 `trace_chain` 表；四类埋点表语义不变 |
| 拦截点 | 总线中间件；Plant 只执行 `guarded: true` |
| 急停/换针 | 快速通道：跳过孪生门槛与人工审批 |

## 3. 命令路径

1. 边缘 / 云端 / 人工向 `plant/<process>/<equipment>/command` 或 `plant/line/command` 发布**未盖章**载荷（不得自带有效 `guarded: true`，若自带则 Guard 视为伪造并拒绝）。
2. `ActionGuard`（`client_id="guard"`）订阅上述主题；忽略已盖章消息以防环路。
3. 校验通过后，Guard 发布同一主题载荷，附加 `guarded: true` 与 `guard_reason`。
4. `Plant._execute`：若 `payload.get("guarded") is not True`，丢弃且不记为成功执行（可选记拒绝遥测；默认静默丢弃未盖章，由 Guard 负责写拒绝 action）。

## 4. 三道关与快速通道

### 4.1 快速通道白名单

命令 ∈ `{stop, resume, change_bit}`：仅检查命令已知、process/equipment 合法，立即盖章转发。不入审批队，不调用孪生门槛。

### 4.2 关 1 — 工艺窗口

复用 `edge.envelope.bound_command(recipe, process, command, params)`。  
返回 `None` → 拒绝；否则使用裁剪后的 `(command, params)` 进入后续关。

### 4.3 关 2 — 孪生门槛

当 `use_twin_lookahead` 且注入了 `TwinService` 且命令非白名单：

- 由命令映射到 `simulate` 的 `kind` 与 `params`（映射表在实施计划钉死；无法映射则跳过关 2 仅记 warning detail）。
- 门槛随 `twin_confidence`（由孪生服务或滚动覆盖率提供；若暂无则用默认 0.5）收紧：
  - `y_min = 0.90 + 0.05 * (1 - confidence)`（示例，实施计划可微调但需测试锁定）
  - `oos_max = 0.15 * confidence + 0.05`
- `confidence < 0.5` 或 `use_twin_confidence_gate` 导致不达标 → **不自动放行**，转入关 3 审批（若 `use_human_gate`）或拒绝。
- 无 twin 可用：非白名单动作转入审批（`use_human_gate=true`）或拒绝（`false`）。

### 4.4 关 3 — 高风险人工门

高风险集合（初值）：`hold_lot`、`scrap_lot`、以及关 2 转来的动作；`set_*` 大范围改配方类若超出「微调」阈值亦视为高风险（实施计划用类别 `param_tune` 且 `|delta|` 超阈，或显式名单）。

- `use_human_gate=true`：写入 `approval_queue`（`status=pending`），不转发。
- `use_human_gate=false`：关 3 自动通过，盖章转发。

### 4.5 拒绝

不转发；写 `action_log`（`accepted=false`，`reason` 含 `rejected_by=guard`）并 `append_chain(kind="act", ...)`；仿真不崩溃。

## 5. 审批队列与 HumanModel

### 5.1 表 `approval_queue`

| 字段 | 说明 |
|------|------|
| request_id | 主键 |
| t_submit | 仿真时间 |
| process / command / params / source / lot_id | 动作内容 |
| status | pending / approved / rejected / expired |
| t_decide | 决定时刻 |
| decider | 默认 `human_model` |
| reason | 说明 |

### 5.2 HumanModel 扩展

- 保留 SPC→OCAP 延迟行为。
- 每拍检查 `pending`：当 `clock.now >= t_submit + approval_delay_s`（默认 **900** 仿真秒 = 15 分钟）时决策。
- 概率规则（父规格 §7.5）：若 `process` 与当前活动真值故障工序一致，以 0.9 批准，否则以 0.9 驳回；RNG：`make_rng(seed, "human_approval")`。
- 批准：更新队列状态，通知 Guard 盖章转发（Guard 提供 `resolve_approval(request_id, approved, reason)` 或 HumanModel 发布带 `approval_id` 的内部消息由 Guard 处理）。
- 驳回：更新队列；Guard 写拒绝 action。

## 6. 哈希链

### 6.1 表 `trace_chain`

`seq` INTEGER PK AUTOINCREMENT，`lot_id` TEXT，`kind` TEXT（`sense`|`decide`|`act`），`ref` TEXT，`t` REAL，`payload` TEXT（canonical JSON），`prev_hash` TEXT，`entry_hash` TEXT。

- 创世：某 `lot_id` 第一条的 `prev_hash = "GENESIS"`。
- `entry_hash = sha256( (prev_hash + "\n" + payload).encode("utf-8") ).hexdigest()`。
- payload 使用 `json.dumps(..., sort_keys=True, ensure_ascii=False)`。

### 6.2 写入点（M5 最低集）

- **act**：Guard 放行或拒绝时。
- **decide**：`record_episode` 或云端/边缘写入 episode 时（经 TraceStore 钩子或显式 `append_chain`）。
- **sense**（可选增强）：AOI 结果摘要；M5 至少实现 act+decide，sense 若时间不够可标测试可选。

### 6.3 校验与降级

- `verify_chain(lot_id: str | None = None) -> tuple[bool, str]`：按 seq 重算；失败返回原因。
- 失败后：`ActionGuard.auto_actions_enabled = False`；此后仅 **快速通道白名单** 与 **已批准的人工审批放行** 可盖章；其它自动来源拒绝。

## 7. 目录与接入

```
guard/__init__.py
guard/policy.py
guard/action_guard.py
guard/chain.py
bench/schema.py          # approval_queue + trace_chain API
bench/human_model.py     # 审批
sim/plant.py             # guarded 门闩
sim/runner.py            # 挂载 Guard
tests/guard/
```

`runner`：存在 ablation 时创建并挂载 `ActionGuard`（关 1 始终生效）。注入 `bus, store, recipe, clock, twin, seed`。

## 8. 与相邻里程碑边界

| M5 做 | 不做 |
|-------|------|
| Guard 三道关、审批队列表、HumanModel 审批、哈希链校验降级 | Streamlit 审批 UI（M6） |
| Plant 盖章门闩、误放行/误拒绝粗指标（可草案） | 全消融报告（M7） |
| 安全清单 | 区块链 |

## 9. 测试策略

- 单元：包络拒绝、白名单快通、审批延迟与概率（固定 seed）、链篡改检测。
- 集成：短场景指令经 Guard 到达 Plant（`guarded`）；未盖章不到达。
- 隔离：`guard/` 无 `sim` import。
- 消融：`use_human_gate=false` 高风险不入队。

## 10. 非目标

- 不实现看板。
- 不修改四类埋点字段既有语义（仅新增表）。
- 不在 Guard 内调用 LLM。
