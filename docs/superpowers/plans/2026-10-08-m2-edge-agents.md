# M2 工序边缘智能体 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为钻孔、电镀、蚀刻各部署一个边缘智能体：SPC/EWMA 判异、工艺窗口包络、自动换针与补药、电镀到蚀刻前馈、传感器置信度降级、断网缓存补报；并让 `baseline_rule` 走“SPC + 延迟人工 OCAP”。

**Architecture:** 边缘智能体是 `Controller`，只通过 `Bus` 订阅遥测/化验/测量/`lot_step`/`intents`，通过 `plant/.../command` 和 `plant/intents/<process>` 发布。不 import `sim`。`on_tick` 在 `Plant.step_tick` 之后被调用，发出的指令最早在下一拍生效。

**Tech Stack:** Python 3.12, numpy, pydantic v2, scikit-learn（IsolationForest）, pytest。单元测试只用 `InMemoryBus`。

**规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.2、§4.5、§5、§6、§7.5、§10 M2。

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

## File Structure

```
common/intents.py          # Intent 模型
edge/__init__.py
edge/spc.py                # EWMA + Western Electric
edge/envelope.py           # 按 Recipe 窗口裁剪/拒绝指令
edge/ocap.py               # 报警 → 指令
edge/agent.py              # ProcessEdgeAgent
edge/factory.py            # 按 AblationConfig 组装控制器
bench/human_model.py       # 延迟人工 OCAP
sim/etch.py                # 初值改读配方（M1 遗留）
common/recipe.py           # violations 转 float（M1 遗留）
tests/test_isolation.py    # 相对 import（M1 遗留）
sim/runner.py              # 接入 controllers
```

## 数值与语义约定

- 1 tick = 1800 s = 30 仿真分钟。人工 SPC 响应延迟默认 **2 tick**（60 分钟）。
- 日界：48 tick。补药日累计上限 `DAILY_DOSE_CAP_ML_L = 6.0`；单次上限沿用工站 `ml_l ∈ (0, 3]`。
- SPC 在 **tick 均值** 上判异（同一 `tick` 的 30 条遥测取平均），不在 substep 噪声上判。
- Western Electric：R1 一点超 3σ；R2 连续 3 点中 2 点超 2σ 同侧；R3 连续 5 点中 4 点超 1σ 同侧；R4 连续 8 点同侧。基线用前 `BASELINE_TICKS = 8` 个 in-control tick 的均值/标准差；σ 下限 `1e-6`。
- EWMA：`λ = 0.2`，报警 `|z| > 3`，`z = (ewma - μ) / (σ * sqrt(λ/(2-λ)))`。
- 前馈：`speed = window.clamp(target * (T_nom / max(T_mean, 1e-6)))`，`T_nom = recipe.specs["copper_thickness_um"].target`。`use_peer_feedforward=false` 时不发。
- 置信度：IsolationForest（`n_estimators=50, contamination=0.05, random_state` 来自 seed）在基线 tick 的均值向量上 fit；之后 `decision_function < 0` 或某键 tick 内方差 `< 1e-12`（spoof）则 `confidence=low`。low 时禁止前馈与工艺微调，仍允许换针、补药、维修、急停。
- 客户端 id：`edge-drill` / `edge-plating` / `edge-etch` / `human`。
- 事件载荷：`{"t", "tick", "process", "key", "rule", "value", "replayed"}`。断网时入本地队列，链路恢复后按原 tick 顺序补发且 `replayed=true`。
- 指令载荷：`{"command", "params", "source", "reason"}`。边缘 `source="edge"`，前馈 `source="peer"`，人工 `source="human"`。
- OCAP（`edge/ocap.py`）：
  - drill `bit_hits >= 0.9 * bit_rated_life_hits` 或 vibration/current 的 WE/EWMA → `change_bit`
  - plating 化验 `additive_ml_l < window.min` → `dose_additive`
  - plating `rect_current_r*` WE/EWMA → `repair_rectifier`
  - etch `sg` WE/EWMA → 先 `repair_regenerator` 再 `adjust_sg`（delta 裁剪到窗口与 ±0.05）
  - etch 线宽测量某列均值 `< specs.line_width_um.min` → `clean_nozzle` 该列 zone
- 同一 `(process, rule, key)` 在故障未恢复前不重复发同类维修指令（冷却：该 key 连续 3 个 tick 回到 μ±2σ 才解除）。

---

### Task 1: M1 遗留（隔离、violations、蚀刻初值）

**Files:**
- Modify: `tests/test_isolation.py`, `common/recipe.py`, `sim/etch.py`, `tests/common/test_recipe.py`, `tests/sim/test_etch.py`

**Interfaces:**
- `Recipe.violations` 使用 `str(float(value))`
- `EtchStation.__init__` 从 `recipe.window("etch", ...).target` 读 sg/温度/喷淋/传送速度
- `find_sim_imports` 处理 `ImportFrom.level > 0`

- [ ] **Step 1: 失败测试**

`tests/test_isolation.py` 的 `test_checker_detects_violations` 增加相对 import 文件：

```python
(tmp_path / "edge" / "rel.py").write_text("from ..sim.drill import DrillStation\n", encoding="utf-8")
```

断言 violations 含 `edge/rel.py`。`test_recipe.py` 断言 `violations("plating", {"additive_ml_l": np.float64(1.31)})` 含 `"1.31"` 不含 `"np."`。`test_etch.py` 改配方 etch sg target 后工站 `sg` 等于新 target。

- [ ] **Step 2: 跑测试确认失败**

`python -m pytest tests/test_isolation.py tests/common/test_recipe.py tests/sim/test_etch.py -q`

- [ ] **Step 3: 实现**

`ImportFrom`：`level > 0` 时用 `path.relative_to(root).parts` 向上 `level` 层再拼 `node.module`。`violations`：`out.append(f"{process}.{param}={float(value)} outside [{w.min}, {w.max}]")`。`EtchStation` 四初值改读 window target。

- [ ] **Step 4: 测试通过并提交**

```bash
git add tests/test_isolation.py common/recipe.py sim/etch.py tests/common/test_recipe.py tests/sim/test_etch.py
git commit -m "fix: 隔离检查相对 import、violations 转 float、蚀刻初值读配方"
```

---

### Task 2: Intent 协议与指令包络

**Files:**
- Create: `common/intents.py`, `edge/envelope.py`, `edge/__init__.py`
- Test: `tests/common/test_intents.py`, `tests/edge/test_envelope.py`

**Interfaces:**
- `Intent` pydantic frozen：`intent: str`, `target_process: Literal["drill","plating","etch"]`, `lot_id: str | None = None`, `params: dict`, `recipe_window: dict[str, float] | None = None`, `deadline: float | None = None`, `rationale: str = ""`, `policy_version: str = "m2"`, `source: Literal["edge","peer","cloud","human"] = "edge"`
- `envelope.bound_command(recipe, process, command, params) -> tuple[str, dict] | None`：返回裁剪后的 `(command, params)`；无法裁进窗口则 `None`（拒绝）。

映射：
- `set_rpm` → `spindle_rpm`；`set_feed` → `feed_rate_m_min`；`set_current_density` → `current_density_asd`（params 键 `asd`）；`set_bath_temp`/`set_etch_temp` → `c` 对应 window；`set_conveyor_speed` → `m_min`；`set_spray_pressure` → `bar`；`adjust_sg` → 把 `sg+delta` clamp 到 sg 窗口再反算 delta，若 clamp 后 |delta|<1e-9 则 None；`dose_additive` clamp `ml_l` 到 `(0, 3]`。`change_bit`/`repair_*`/`clean_nozzle`/`stop`/`resume` 原样通过。

- [ ] **Step 1–5:** 先写 `test_intent_roundtrip` 与 `test_envelope_clamps_and_rejects`；失败后再实现；提交 `feat: Intent 协议与工艺窗口包络`

---

### Task 3: SPC / EWMA

**Files:**
- Create: `edge/spc.py`
- Test: `tests/edge/test_spc.py`

**Interfaces:**
- `class ChannelStats`：`update(x: float) -> None`；`ready: bool`（n>=8）；`mu`, `sigma`
- `western_electric(xs: list[float], mu, sigma) -> str | None` 返回 `"R1"`…`"R4"` 或 None。只看序列末尾。
- `class Ewma`：`update(x) -> float`（标准化 z）；`alarm -> bool`
- `class TickAggregator`：`add(tick: int, values: dict[str, float])`；`mean_of(tick: int) -> dict[str, float] | None`

WE 判定用最近窗口：R1 看最后 1 点；R2 最后 3 点；R3 最后 5 点；R4 最后 8 点。测试用无噪声构造序列。

- [ ] 提交 `feat: EWMA 与 Western Electric 判异`

---

### Task 4: ProcessEdgeAgent 核心（判异、事件、OCAP 指令）

**Files:**
- Create: `edge/ocap.py`, `edge/agent.py`
- Test: `tests/edge/test_agent_core.py`

**Interfaces:**
- `ocap.commands_for(process, key, value, recipe) -> list[tuple[str, dict, str]]` 每项 `(command, params, reason)`
- `class ProcessEdgeAgent`：
  - `__init__(self, process: str, bus: Bus, recipe: Recipe, clock: SimClock, store: TraceStore | None = None, *, feedforward: bool = True, confidence_modulation: bool = True, seed: int = 0)`
  - `client_id` 属性 `f"edge-{process}"`
  - 订阅 `telemetry(process)`、`lab_assay()`（仅 plating）、`measurement(process)`、`intents(process)`、`lot_step(process)`
  - `on_tick(clock)`：对上一拍均值跑 SPC；报警则 `_emit_event` 并经 envelope 后 `bus.publish(command_topic, {command, params, source:"edge", reason}, client_id)`
  - 实现 `Controller.on_tick`

测试（InMemoryBus + 假遥测，不启动 Plant）：连续 8 个正常 tick 建基线，第 9 拍发布 sg=1.40 的 30 个点，第 10 次 `on_tick` 后总线上有 `repair_regenerator` 与 `adjust_sg`，事件主题有 `rule=R1`。

- [ ] 提交 `feat: 工序边缘智能体 SPC 闭环`

---

### Task 5: 局部闭环（换针、补药、日上限）

**Files:**
- Modify: `edge/agent.py`, `edge/ocap.py`
- Test: `tests/edge/test_local_loops.py`

**规则：**
- `bit_hits` 用遥测均值；`>= 0.9 * recipe.constants["bit_rated_life_hits"]` 发一次 `change_bit`，直到 `bit_hits` 回落到 `< 0.1 * rated` 才允许再发。
- 化验 `additive_ml_l`：`dose = min(3.0, window.target - value, remaining_daily)`；`remaining_daily` 按 `tick // 48` 重置。`dose<=0` 不发。
- 测试用纯总线，不依赖 Plant。

- [ ] 提交 `feat: 边缘自动换针与补药`

---

### Task 6: 电镀 → 蚀刻前馈

**Files:**
- Modify: `edge/agent.py`
- Test: `tests/edge/test_feedforward.py`

电镀 agent 收到 `measurement(plating)` 后发布 `Intent(intent="copper_thickness", target_process="etch", lot_id, params={"thickness_mean": float}, source="peer")` 到 `topics.intents("etch")`。

蚀刻 agent 收到 Intent 后计算传送速度，`source="peer"` 发 `set_conveyor_speed`。`feedforward=False` 时双方都不发。

厚度均值：`zones` 嵌套 list 的全局 mean。

- [ ] 提交 `feat: 电镀到蚀刻边-边前馈`

---

### Task 7: 置信度调节

**Files:**
- Modify: `edge/agent.py`, `pyproject.toml`（加 `scikit-learn>=1.4`）
- Test: `tests/edge/test_confidence.py`

基线 8 tick 的均值向量 fit IsolationForest。之后异常或某键方差 `< 1e-12` → `low`。low 期间：前馈 Intent 不发、`set_*` 不发；`change_bit`/`dose_additive`/`repair_*`/`clean_nozzle`/`stop` 仍发。`confidence_modulation=False` 时始终 `high`。

- [ ] 提交 `feat: 传感器置信度降级`

---

### Task 8: 断网缓存与补报

**Files:**
- Modify: `edge/agent.py`
- Test: `tests/edge/test_outage.py`

`_emit_event`：若 `not bus.is_link_up(client_id)` 则 append 队列，不 publish。`on_tick` 开头若链路已恢复，按 FIFO publish 且 `replayed=True`。

测试：`set_link("edge-etch", False)` 期间制造 R1，总线上无事件；恢复后下一 `on_tick` 出现 `replayed=True` 的同一 key/rule。

- [ ] 提交 `feat: 边缘断网事件缓存与补报`

---

### Task 9: 模拟人工 + runner 组装

**Files:**
- Create: `bench/human_model.py`, `edge/factory.py`
- Modify: `sim/runner.py`
- Test: `tests/bench/test_human_model.py`, `tests/edge/test_factory.py`, `tests/sim/test_runner.py`（扩展）

**Interfaces:**
- `HumanModel(bus, recipe, clock, delay_ticks: int = 2)`：`client_id="human"`。订阅 `plant/events/+`。收到事件后在 `detect_tick + delay_ticks` 的 `on_tick` 执行 OCAP，`source="human"`。
- `edge.factory.make_controllers(ablation, bus, recipe, clock, store, seed) -> list[Controller]`：
  - `use_edge_agent=True`：三个 `ProcessEdgeAgent`（feedforward/confidence 跟 ablation）
  - `use_edge_agent=False`：三个只发事件、不发指令的 agent（可复用 `ProcessEdgeAgent(..., emit_commands=False)`）+ 一个 `HumanModel`
- `run(..., ablation: AblationConfig | None = None)`：None 时行为与现在相同（无边缘）。传入则 `make_controllers` 并入 `controllers` 参数（先边缘后用户传入的）。
- CLI 增加 `--ablation` 路径，可选。

验收测试：`nozzle_clog` + `edge_only` 跑 96 tick，FPY **高于** 无控制器的基线（M1 该场景 FPY≈0.086）。`baseline_rule` 下首个维修指令的 `source=="human"` 且 `t` 比事件 `t` 至少晚 3600。

- [ ] 提交 `feat: baseline_rule 人工延迟与 runner 接入边缘`

---

### Task 10: episode_log 与 sg 恢复动作

**Files:**
- Modify: `edge/agent.py`, `bench/schema.py`（若需）
- Test: `tests/edge/test_episodes.py`

每次发出维修/换针/补药时写 `EpisodeRecord`：`episode_id=f"{process}-{tick}-{key}"`，`handler="edge"` 或 `"human"`，`t_detect=now`，`trigger=rule`。不读 `fault_truth`。

`sg` OCAP 必须两条指令都发出（repair + adjust），测试断言。

- [ ] 提交 `feat: 边缘处置 episode 埋点`

---

## 自审

- 规格 M2 验收：SPC、包络、换针补药、前馈、断网 → Tasks 3–8。
- `baseline_rule` → Task 9。
- 隔离/配方遗留 → Task 1。
- 不引入 LLM、孪生、Guard。
- IsolationForest 需要 sklearn，写入 `pyproject.toml`。
