# M5 Guard、审批与哈希链追溯 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现总线级 ActionGuard（三道关 + 急停快速通道）、审批队列与 HumanModel 模拟审批、SQLite 旁路哈希链校验与降级，使未盖章命令无法到达 Plant 执行。

**Architecture:** `ActionGuard`（`client_id="guard"`）订阅 `plant/+/+/command` 与 `plant/line/command`；忽略已盖章消息；校验后同主题转发并附加 `guarded: true`。Plant 丢弃未盖章载荷。审批写入 `approval_queue`，由扩展后的 `HumanModel` 按仿真时钟决策并回调 Guard。`trace_chain` 旁路四类埋点；校验失败后仅白名单与已批准人工动作可自动盖章。

**Tech Stack:** Python 3.12, pydantic v2, numpy, sqlite3, pytest。

**规格：** `docs/superpowers/specs/2026-10-09-m5-guard-trace-design.md`；父规格 §4.6、§6、§7.5、§10 M5；ADR-006。

## Global Constraints

- Python 3.12；依赖用 `pyproject.toml` 管理；测试用 `pytest`。
- 所有随机过程接受 `seed`；同一 seed 必须得到逐位一致的结果。随机数一律通过 `common.rng.make_rng(seed, stream)` 获取。
- 消息总线抽象为 `Bus` 接口；单元测试不依赖 Mosquitto。
- 仿真时间由 `SimClock` 驱动。禁止使用 `time.time()` / `datetime.now()` 作为仿真时间。
- 代码标识符用英文；文档和日志说明用中文。
- 四类埋点字段只能新增，不能修改语义（本里程碑仅新增 `approval_queue` / `trace_chain` 表）。
- `twin/`、`edge/`、`cloud/`、`guard/` 不允许 import `sim` 包的任何模块。
- JSON 序列化：`json.dumps(obj, sort_keys=True, ensure_ascii=False)`。
- 测试放在 `tests/<包名>/test_*.py`；`--import-mode=importlib`，`pythonpath = ["."]`。
- 不写解释“这行做什么”的注释。

## File Structure

```
guard/__init__.py
guard/policy.py              # 白名单、高风险、command→twin 映射、门槛阈值
guard/chain.py               # entry_hash / 纯函数（可选薄封装；主 API 在 TraceStore）
guard/action_guard.py        # ActionGuard Controller
bench/schema.py              # approval_queue + trace_chain API；record_episode 钩 decide 链
bench/human_model.py         # 审批队列决策
sim/plant.py                 # guarded 门闩
sim/runner.py                # 挂载 ActionGuard + 审批用 HumanModel
edge/factory.py              # HumanModel 挂载策略（ocap vs approval）
tests/guard/test_*.py
tests/sim/test_plant.py      # send() 默认带 guarded=True（单测模拟已盖章）
tests/bench/test_schema.py   # 链与审批表
tests/bench/test_human_model.py
```

## 数值与语义约定

- **快速通道：** `{stop, resume, change_bit}` — 仅查命令已知 + process/equipment 合法；跳过关 2/3；不入审批队。
- **伪造盖章：** 入站载荷已有 `guarded is True` → Guard **忽略**（防环路）。若发送方是非 `guard` 且自带 `guarded: true`，Plant 侧也会执行——因此 **Plant 只信任盖章，Guard 是唯一合法盖章者**；测试中伪造场景：由 spy 以 `edge` 身份发 `guarded:true` 时，Guard 忽略该消息（不重盖章），但 Plant 会执行——规格「若自带则 Guard 视为伪造并拒绝」落实为：Guard 对**未盖章**路径做校验；对**已盖章且 sender≠guard`** 的消息，Guard 额外写拒绝 action 并**不**转发（Plant 若已同步收到同条则竞态）。**钉死实现：** Bus 同步投递下，Guard 与 Plant 均订阅；为避免伪造盖章被 Plant 执行，Plant 在 `_execute` 要求 `guarded is True` **且**（可选）不校验 sender。规格要求「伪造拒绝」→ Guard 在订阅回调里若发现 `guarded is True` 且 `payload.get("_guard_stamp") != "guard"`，则视为伪造：不转发、写拒绝；Plant 只接受带 `_guard_stamp=="guard"` 或简单方案：**仅接受 `guarded is True`，且边缘/云端发布时剥离任何 `guarded` 键**。

  **最终钉死（简单可靠）：**
  1. 边缘/云端/Human OCAP 发布命令时**不得**带 `guarded`；若带了，Guard 入站时若 `guarded is True` 则当作伪造：**删除队列意义**——写 `accepted=false` 拒绝 action + chain，**不转发**；Plant 侧：`guarded is not True` → 静默丢弃。
  2. Guard 转发时设置 `guarded: True`、`guard_reason: str`。
  3. Guard 忽略自己转发的消息：`payload.get("guarded") is True` → return（自己的转发也会进回调，直接忽略，避免环路）。**与伪造冲突：** 伪造也带 `guarded:True`，若直接忽略则不写拒绝。

  **修正钉死：**
  - 入站若 `guarded is True`：**一律忽略**（防环路）。伪造盖章靠 **Plant 不执行未盖章** + **发布方规范不带 guarded**；另加测试：边缘发布带 `guarded:True` 时，因 Guard 忽略、**Plant 会执行**——不可接受。
  - **因此 Plant 改为：** 仅当 `guarded is True` **且** `payload.get("guard_id") == "guard"` 时执行。Guard 盖章时写入 `guard_id: "guard"`。伪造若抄这两个字段仍可通过——可接受为 MVP（信任总线内客户端）；额外：Guard 对无 `guard_id` 的 `guarded:True` 写告警拒绝（可选）。MVP 用 `guarded is True` + `guard_id == "guard"`。

- **关 1：** `bound_command(recipe, process, command, params)`；`None` → 拒绝。`process=="line"` 时跳过 bound_command 工艺窗口，仅允许 `hold_lot`/`scrap_lot`。
- **关 2 门槛（锁定）：** `y_min = 0.90 + 0.05 * (1 - confidence)`；`oos_max = 0.15 * confidence + 0.05`。通过条件：`pred.yield_prob >= y_min and pred.oos_prob <= oos_max`。`confidence`：若 twin 有 `predictions`，则 `twin_confidence([o.q05 <= o.y <= o.q95 for o in twin.predictions])`；否则 `0.5`。若 `use_twin_confidence_gate` 为 False，则 `confidence` 固定按 `1.0` 代入公式（门槛最松）。`confidence < 0.5` 且 `use_twin_confidence_gate` → 不自动放行（转关 3 或拒绝）。
- **command→twin 映射（无法映射则跳过关 2，detail warning）：**

  | command | kind | 合并进 simulate 的 params 键 |
  |---------||------|------------------------------|
  | `set_current_density` | thickness | `asd` ← params[`asd`]；其余用 recipe 默认 `time_min`/`additive_ml_l` |
  | `set_bath_temp` | thickness | 用当前/默认 thickness params（温度不进机理则 **跳过关 2**） |
  | `dose_additive` | thickness | `additive_ml_l` ← params |
  | `set_etch_temp` | width | `temp_c` |
  | `set_conveyor_speed` | width | `speed_m_min` |
  | `set_spray_pressure` | width | `spray_bar` |
  | `adjust_sg` | width | `sg` |
  | `set_rpm` / `set_feed` | roughness | 用 recipe/默认 roughness 参数；若模型只需 hits 则 **跳过关 2** |
  | 维护类 `clean_nozzle`/`repair_*` | — | 跳过关 2 |

  实现函数：`map_command_to_twin(recipe, process, command, params) -> tuple[str, dict] | None`。

- **高风险（关 3）：** `hold_lot`、`scrap_lot`；关 2 未通过转入；`param_tune` 且相对 recipe 窗口 target 的相对偏差 `|v - target| / max(high-low, 1e-9) > 0.25`（微调阈值 **0.25**）。维护/加药/换针不因该阈值进关 3（换针已在快速通道）。
- **审批延迟：** `approval_delay_s = 900`（15 仿真分钟）。`clock.now >= t_submit + approval_delay_s` 时决策。
- **审批概率：** `make_rng(seed, "human_approval")`；若 `process` 与当前活动真值故障工序一致（`store.faults()` 中 `t_cleared is None` 且 `t_start <= now` 的任一 `process` 匹配）→ `rng.random() < 0.9` 批准；否则 `rng.random() < 0.9` 驳回（即匹配时 0.9 批，不匹配时 0.9 驳 = `random() >= 0.1` 时驳回… 规格：一致以 0.9 批准，否则以 0.9 驳回 → 不匹配时 `rng.random() < 0.9` → 驳回）。
- **哈希：** `prev_hash` 创世 `"GENESIS"`；`entry_hash = sha256((prev_hash + "\n" + payload).encode("utf-8")).hexdigest()`；payload 为 canonical JSON 字符串。
- **链写入：** act 由 Guard 放行/拒绝时；decide 在 `TraceStore.record_episode` 成功后自动 `append_chain(kind="decide", ...)`；sense 本里程碑可选（无强制测试）。
- **校验失败降级：** `ActionGuard.auto_actions_enabled = False`；仅快速通道与 `resolve_approval(..., approved=True)` 可盖章。
- **runner：** `ablation is not None` 时创建并挂载 `ActionGuard`（关 1 始终生效）；注入 `bus, store, recipe, clock, twin, seed, ablation`。`use_human_gate` 时确保 HumanModel 带审批能力在列表中（见 Task 5）。
- **lot_id 链键：** `params.get("lot_id")` 或 episode/`"default"`。

---

### Task 1: TraceStore — approval_queue 与 trace_chain

**Files:**
- Modify: `bench/schema.py`
- Test: `tests/bench/test_schema.py`（追加用例）；新建 `tests/guard/test_chain.py` 亦可但本任务放 schema 测

**Interfaces:**
- 表 `approval_queue`：`request_id TEXT PK`, `t_submit REAL`, `process TEXT`, `equipment TEXT`, `command TEXT`, `params TEXT`, `source TEXT`, `lot_id TEXT`, `topic TEXT`, `status TEXT`, `t_decide REAL`, `decider TEXT`, `reason TEXT`
- 表 `trace_chain`：`seq INTEGER PK AUTOINCREMENT`, `lot_id TEXT`, `kind TEXT`, `ref TEXT`, `t REAL`, `payload TEXT`, `prev_hash TEXT`, `entry_hash TEXT`
- `_ORDER` 增加两表；`dump()` 自动覆盖
- `enqueue_approval(...) -> str` 返回 `request_id`（`uuid4` hex 或 `f"apr-{n}"` 单调）
- `list_approvals(status: str | None = None) -> list[dict]`
- `update_approval(request_id, *, status, t_decide, decider, reason) -> None`
- `append_chain(*, lot_id: str, kind: str, ref: str, t: float, payload: dict) -> str` 返回 `entry_hash`
- `verify_chain(lot_id: str | None = None) -> tuple[bool, str]`
- `record_episode`：在写入后若 `t_decide is not None`，调用 `append_chain(kind="decide", lot_id=detail.get("lot_id","default"), ref=episode_id, t=t_decide or t_detect, payload={episode 摘要})`

- [ ] **Step 1: 失败测试**

```python
def test_trace_chain_tamper_detected():
    store = TraceStore()
    h1 = store.append_chain(lot_id="L1", kind="act", ref="a1", t=0.0, payload={"command": "stop"})
    store.append_chain(lot_id="L1", kind="act", ref="a2", t=1.0, payload={"command": "resume"})
    assert store.verify_chain("L1")[0] is True
    store._conn.execute("UPDATE trace_chain SET payload = ? WHERE ref = 'a1'", ['{"command":"hacked"}'])
    store._conn.commit()
    ok, reason = store.verify_chain("L1")
    assert ok is False and reason
```

- [ ] **Step 2:** `python -m pytest tests/bench/test_schema.py::test_trace_chain_tamper_detected -q` → FAIL（无方法）。
- [ ] **Step 3:** 扩展 `_SCHEMA`、实现 API；另测 `enqueue_approval` / `update_approval` 与创世 `prev_hash=="GENESIS"`。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5:** 提交 `feat: approval_queue 与 trace_chain 存储 API`

---

### Task 2: guard.policy — 白名单、映射、门槛、高风险

**Files:**
- Create: `guard/__init__.py`, `guard/policy.py`
- Test: `tests/guard/test_policy.py`

**Interfaces:**
- `FAST_PATH: frozenset[str] = frozenset({"stop", "resume", "change_bit"})`
- `HIGH_RISK_COMMANDS: frozenset[str] = frozenset({"hold_lot", "scrap_lot"})`
- `PARAM_TUNE_REL_THRESHOLD: float = 0.25`
- `map_command_to_twin(recipe, process, command, params) -> tuple[str, dict] | None`
- `twin_thresholds(confidence: float) -> tuple[float, float]` → `(y_min, oos_max)`
- `passes_twin_gate(pred, confidence: float) -> bool`
- `is_high_risk(recipe, process, command, params) -> bool`
- `confidence_from_twin(twin) -> float`（`twin is None` → `0.5`）

- [ ] **Step 1:**

```python
from guard.policy import twin_thresholds, passes_twin_gate, FAST_PATH
from twin.types import Prediction

def test_thresholds_tighten_when_confidence_low():
    y_hi, oos_hi = twin_thresholds(1.0)
    y_lo, oos_lo = twin_thresholds(0.0)
    assert y_hi == 0.90 and oos_hi == 0.20
    assert y_lo == 0.95 and oos_lo == 0.05
    pred = Prediction(mean=1.0, q05=0.9, q95=1.1, yield_prob=0.92, oos_prob=0.08)
    assert passes_twin_gate(pred, 1.0) is True
    assert passes_twin_gate(pred, 0.0) is False
```

- [ ] **Step 2–4:** 失败 → 实现 → 另测 `set_current_density` 映射非 None、`clean_nozzle` 映射 None、`is_high_risk("hold_lot")`。
- [ ] **Step 5:** 提交 `feat: Guard 策略常量与孪生门槛`

---

### Task 3: Plant 盖章门闩

**Files:**
- Modify: `sim/plant.py` `_execute` 开头
- Modify: `tests/sim/test_plant.py` — `send(..., guarded=True)` 默认写入 `guarded=True`, `guard_id="guard"`
- Test: 同文件新增 `test_unguarded_command_discarded`

**Interfaces:**
- `_execute`：若 `payload.get("guarded") is not True` 或 `payload.get("guard_id") != "guard"` → **return**（不写 action_log）。

- [ ] **Step 1:**

```python
def test_unguarded_command_discarded():
    plant, bus, store, _ = make()
    topic = topics.command("plating")
    bus.publish(topic, {"command": "set_current_density", "params": {"asd": 2.2}, "source": "edge", "reason": "x"}, "edge")
    steps(plant, 2)
    assert store.actions() == []
    assert plant.stations["plating"].current_density_asd == 2.0
```

- [ ] **Step 2:** 先改 Plant 门闩使该测试通过；再改 `send()` 默认盖章，修复既有 plant 测试。
- [ ] **Step 3:** `python -m pytest tests/sim/test_plant.py -q` 全绿。
- [ ] **Step 4:** 提交 `feat: Plant 仅执行 Guard 盖章命令`

---

### Task 4: ActionGuard 核心（关 1/2/3 + 快通 + 链 act）

**Files:**
- Create: `guard/action_guard.py`
- Test: `tests/guard/test_action_guard.py`

**Interfaces:**
- `class ActionGuard:`
  - `__init__(self, bus, store, recipe, clock, twin, seed: int, ablation: AblationConfig)`
  - `client_id = "guard"`
  - `auto_actions_enabled: bool = True`
  - 订阅 `plant/+/+/command` 与 `topics.line_command()`
  - `on_tick(self, clock) -> None`：可空实现（审批不在此轮询）
  - `resolve_approval(self, request_id: str, approved: bool, reason: str) -> None`
  - 内部：`_on_command(topic, payload)`；`_stamp_forward(topic, payload, reason)`；`_reject(...)`；`_enqueue(...)`

**处理流程（钉死）：**
1. 若 `payload.get("guarded") is True`：return（防环路）。
2. 解析 `process`/`equipment`/`command`/`params`/`source`。
3. 若 `command in FAST_PATH`：合法则 `_stamp_forward`（即使 `auto_actions_enabled` 为 False 也允许）。
4. 若非快通且 `not auto_actions_enabled`：`_reject(reason="chain_invalid_auto_disabled")`。
5. 关 1：`line` 只允 hold/scrap；否则 `bound_command`；失败 `_reject`。
6. 关 2：若 `ablation.use_twin_lookahead and twin is not None` 且可映射：算 confidence（尊重 `use_twin_confidence_gate`）；不通过则标 `needs_approval`；不可映射则 detail warning 继续。
7. 关 3：若 `needs_approval or is_high_risk(...)`：若 `use_human_gate` → enqueue，不转发；否则若 `use_human_gate` 为 False → 直接 `_stamp_forward`（高风险自动过）。
8. 否则 `_stamp_forward`。
9. `_stamp_forward`：deepcopy payload，设 `guarded=True`, `guard_id="guard"`, `guard_reason=...`；`bus.publish(topic, payload, self.client_id)`；`record_action(accepted=True, reason=...)`；`append_chain(kind="act", ...)`。
10. `_reject`：`record_action(accepted=False, reason=f"rejected_by=guard;{reason}")`；`append_chain`；不 publish。

- [ ] **Step 1:**

```python
def test_fast_path_stop_stamped_and_executed():
    # bus+store+recipe+clock+Plant+ActionGuard；publish stop 无 guarded；guard 回调后 plant.step_tick
    ...
    assert any(a.command == "stop" and a.accepted for a in store.actions())
    assert plant.stations["drill"].stopped
```

```python
def test_envelope_reject_unknown_command():
    # publish levitate → 拒绝 action accepted=False，station 不变
```

- [ ] **Step 2–4:** 实现最小 Guard；覆盖 `use_human_gate=True` 时 `hold_lot` 入队不转发。
- [ ] **Step 5:** 提交 `feat: ActionGuard 三道关与快速通道`

---

### Task 5: HumanModel 审批 + factory/runner 挂载

**Files:**
- Modify: `bench/human_model.py`
- Modify: `edge/factory.py`
- Modify: `sim/runner.py`
- Test: `tests/bench/test_human_model.py`；`tests/guard/test_approval.py`；`tests/guard/test_runner_guard.py`

**Interfaces:**
- `HumanModel.__init__(self, bus, recipe, clock, delay_ticks=2, *, store=None, guard=None, seed=0, ocap=True, approval_delay_s=900)`
  - `ocap=False` 时不订阅 `plant/events/+`（或订阅但不发令）
  - `store`+`guard` 非空时：`on_tick` 处理 `list_approvals("pending")`
- 批准：`update_approval(... approved)` + `guard.resolve_approval(request_id, True, reason)`
- 驳回：`update_approval(... rejected)` + `guard.resolve_approval(request_id, False, reason)`（Guard 写拒绝 action）
- `resolve_approval`：从队列行恢复 topic/payload，批准则 `_stamp_forward`（**不受** `auto_actions_enabled` 限制）；驳回则 `_reject`
- `make_controllers`：
  - `use_edge_agent=True` → edge agents；若调用方需要审批 HumanModel 由 runner 另加，**或** factory 返回 `(agents, approval_human_spec)`——**钉死：** factory 保持 edge 逻辑；`runner` 在 `ablation.use_human_gate` 时追加 `HumanModel(..., store=store, guard=guard, seed=scenario.seed, ocap=not ablation.use_edge_agent)`。
  - 当 `use_edge_agent=False`：factory 仍追加 OCAP HumanModel；runner **不要**再追加第二个——若 `use_human_gate`，给 factory 的 HumanModel 注入 store/guard。**钉死更简：** 扩展 `make_controllers(..., guard=None)`；OCAP HumanModel 与审批合并为一个实例。
- `runner`：创建 `guard = ActionGuard(...)` 后 `extra.append(guard)`；`make_controllers(..., guard=guard)`；顺序：controllers（含 human）→ twin → cloud → **guard 应先于 plant 消费？** 同 tick 内命令：edge.on_tick 发布 → Guard 同步回调盖章 → 入 Plant 队列 → 下 tick 执行。Guard 作为订阅者即可，不必在 `extra` 最前；仍 `extra.append(guard)` 以便未来 tick 逻辑。

- [ ] **Step 1:**

```python
def test_approval_delay_and_seed_stable():
    # enqueue hold_lot；advance now 到 +900；human.on_tick；同一 seed 两次状态一致
```

- [ ] **Step 2–4:** 实现；`test_runner_guard_short_scenario`：`run(..., ablation=AblationConfig(), n_ticks=3)` 不崩溃且 bus 有 `client_id=guard` 或 actions 含 guard 相关 reason。
- [ ] **Step 5:** 提交 `feat: HumanModel 审批与 runner 挂载 Guard`

---

### Task 6: 链校验降级 + 消融 + 隔离

**Files:**
- Modify: `guard/action_guard.py`（暴露 `check_chain()` 或在 `on_tick` 调用 `verify_chain`）
- Test: `tests/guard/test_chain_degrade.py`；`tests/guard/test_ablation_human_gate.py`；依赖既有 `tests/test_isolation.py`

**Interfaces:**
- `ActionGuard.on_tick`：若 `verify_chain()[0] is False` → `auto_actions_enabled = False`
- 降级后：`set_current_density` 拒绝；`stop` 仍放行；`resolve_approval(True)` 仍放行
- `use_human_gate=False`：高风险不入队，直接盖章

- [ ] **Step 1–4:** 篡改链 → on_tick → 自动动作拒绝；快通仍可；isolation 全绿。
- [ ] **Step 5:** 提交 `feat: 哈希链校验失败降级与消融门`

---

### Task 7: 安全清单与 OODA 短集成

**Files:**
- Create: `tests/guard/test_security_guard.py`（镜像 `tests/cloud/test_security_cloud.py` 风格：无 sim import、无密钥硬编码）
- Test: `tests/guard/test_ooda_smoke.py` — 短场景：人工/测试直接向 command 主题发 `clean_nozzle` 或 `stop`，经 Guard 到 Plant

- [ ] **Step 1:** 安全测试：AST 或 `find_sim_imports` 已覆盖 guard；本文件断言 `guard` 包存在且 `ActionGuard` 无 `import sim`。
- [ ] **Step 2:** OODA smoke：`run` 或手搭 Plant+Guard+publish stop → station stopped。
- [ ] **Step 3:** `python -m pytest tests/guard tests/sim/test_plant.py tests/bench/test_schema.py tests/bench/test_human_model.py tests/test_isolation.py -q`
- [ ] **Step 4:** 提交 `test: M5 Guard 安全与 OODA 冒烟`

---

## Spec coverage（自审）

| 规格项 | 任务 |
|--------|------|
| 命令路径 / 盖章 / Plant 门闩 | T3, T4 |
| 快速通道 | T2, T4 |
| 关 1 bound_command | T4 |
| 关 2 孪生门槛公式 | T2, T4 |
| 关 3 / 审批队列表 | T1, T4, T5 |
| HumanModel 延迟与 0.9 概率 | T5 |
| trace_chain / verify / 降级 | T1, T6 |
| decide 链钩子 | T1 |
| runner 挂载 | T5 |
| 隔离与安全 | T6, T7 |
| 消融 use_human_gate | T6 |
| 不做看板/区块链 | 遵守 |

## Placeholder scan

无 TBD/TODO；阈值与映射已钉死。

## Type consistency

- `guarded` + `guard_id=="guard"` 贯穿 T3/T4
- `resolve_approval(request_id, approved, reason)` 贯穿 T4/T5
- `append_chain` / `verify_chain` 贯穿 T1/T4/T6
