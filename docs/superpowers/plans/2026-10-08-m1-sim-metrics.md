# M1 产线模拟器与埋点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建扮演“现实”的 PCB 产线模拟器（钻孔、电镀、蚀刻、AOI、MES、故障注入、测量通道），以及四类埋点日志、消融配置和 M1 可计算的指标（FPY、报废率、FPR）。

**Architecture:** 离散时间流水线。每个 tick（1800 仿真秒）包含 30 个 substep，每个 substep 发布一次遥测；tick 结束时流水线前进一格：钻孔处理批次 k，电镀处理 k-1，蚀刻处理 k-2，AOI 检测 k-3。所有数据经 `Bus` 发布，所有动作和真值写入 `TraceStore`（SQLite）。

**Tech Stack:** Python 3.12, numpy, pydantic v2, PyYAML, paho-mqtt v2, sqlite3, pytest

**规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md`

## Global Constraints

- Python 3.12；依赖用 `pyproject.toml` 管理；测试用 `pytest`。
- 所有随机过程接受 `seed`；同一 seed 必须得到逐位一致的结果。随机数一律通过 `common.rng.make_rng(seed, stream)` 获取，禁止使用全局随机状态（`random`、`np.random.seed`、`np.random.rand`）。
- 消息总线抽象为 `Bus` 接口，两个实现：`InMemoryBus`（测试与批量评测）和 `MqttBus`（演示）。单元测试不依赖 Mosquitto。
- 仿真时间由 `SimClock` 驱动，与墙钟解耦。代码中禁止使用 `time.time()`、`datetime.now()` 作为仿真时间。
- 代码标识符用英文；文档和日志说明用中文。
- 四类埋点日志（`fault_truth`、`panel_lineage`、`action_log`、`episode_log`）是所有模块的共同契约，字段只能新增，不能修改语义。
- `twin/`、`edge/`、`cloud/`、`guard/` 不允许 import `sim` 包的任何模块，只能通过 `Bus` 获取数据；主题与载荷格式定义在 `common/topics.py`。由 `tests/test_isolation.py` 静态检查。
- JSON 序列化统一使用 `json.dumps(obj, sort_keys=True, ensure_ascii=False)`。
- 测试放在 `tests/<包名>/test_*.py`；pytest 使用 `--import-mode=importlib`，`pythonpath = ["."]`。
- 不写解释“这行做什么”的注释；只在代码无法表达的约束处写注释。

## File Structure

```
common/__init__.py
common/rng.py          # make_rng
common/clock.py        # SimClock
common/config.py       # AblationConfig, load_ablation
common/recipe.py       # ParamWindow, SpecLimit, Recipe, load_recipe
common/topics.py       # 主题命名与设备编号
common/bus.py          # Bus 协议, topic_matches, InMemoryBus, MqttBus
bench/__init__.py
bench/schema.py        # 记录 dataclass, ACTION_WEIGHTS, TraceStore
bench/metrics.py       # fpy, scrap_rate, fpr
bench/metrics_spec.md  # 全部指标公式
bench/recipes/PN-4L-001.yaml
bench/configs/{baseline_rule,edge_only,full}.yaml
bench/scenarios/*.yaml
sim/__init__.py
sim/mismatch.py        # MISMATCH_SCALE
sim/drill.py           # DrillStation
sim/plating.py         # PlatingStation
sim/etch.py            # EtchStation
sim/faults.py          # FaultSpec, Scenario, load_scenario, FaultInjector, FAULT_DEFECT_LINKS
sim/aoi.py             # Defect, PanelInspection, inspect_lot
sim/mes.py             # Lot, Mes
sim/measurement.py     # Measurement（唯一对外数据出口）
sim/plant.py           # Plant（编排、指令处理、动作日志）
sim/runner.py          # run(), RunSummary, CLI
tests/...
```

## 数值模型约定（所有任务共用）

- 流水线：tick = 1800 仿真秒，substeps = 30。批次大小 `lot_size = 12`，批次编号 `L0001`、`L0002`……，拼板编号 `L0001-P01`……`L0001-P12`。
- 板面分区：每块拼板 3×3 分区，下标 `(i, j)`，`i` 为沿传送方向的行，`j` 为横跨板宽的列；蚀刻喷淋分 3 个区，第 `j` 区作用于第 `j` 列。
- 模型失配缩放 `MISMATCH_SCALE = {"low": 0.5, "mid": 1.0, "high": 2.0}`，记为 `m`。
- 设备编号：`drill: DRL-01`、`plating: PLT-01`、`etch: ETC-01`、`aoi: AOI-01`、`lab: LAB-01`。

---

### Task 1: 基础设施（rng、时钟、消融配置、配方、主题）

**Files:**
- Create: `common/__init__.py`, `common/rng.py`, `common/clock.py`, `common/config.py`, `common/recipe.py`, `common/topics.py`, `bench/__init__.py`, `bench/recipes/PN-4L-001.yaml`
- Modify: `pyproject.toml`（`[tool.pytest.ini_options]` 增加 `pythonpath = ["."]`，`addopts = "-q --import-mode=importlib"`）
- Test: `tests/common/test_rng.py`, `tests/common/test_clock.py`, `tests/common/test_config.py`, `tests/common/test_recipe.py`, `tests/common/test_topics.py`

**Interfaces:**
- Produces:
  - `common.rng.make_rng(seed: int, stream: str) -> numpy.random.Generator`：`np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(stream.encode("utf-8"))]))`。
  - `common.clock.SimClock`（dataclass）：字段 `tick_seconds: float = 1800.0`、`substeps: int = 30`、`tick: int = 0`、`substep: int = 0`；属性 `substep_seconds`（= tick_seconds / substeps）、`now`（= tick × tick_seconds + substep × substep_seconds）；方法 `advance_substep()`（substep 加 1，达到 substeps 时归零且 tick 加 1）、`advance_tick()`（tick 加 1，substep 归零）。
  - `common.config.AblationConfig`（pydantic `BaseModel`，`model_config = ConfigDict(frozen=True, extra="forbid")`）：`name: str = "full"`、`use_edge_agent: bool = True`、`use_cloud: bool = True`、`use_twin_lookahead: bool = True`、`use_peer_feedforward: bool = True`、`use_event_trigger: bool = True`、`use_confidence_modulation: bool = True`、`use_rag: bool = True`、`use_human_gate: bool = True`、`twin_fidelity: Literal["none", "mechanistic", "hybrid"] = "hybrid"`、`use_counterfactual_rca: bool = True`、`use_twin_confidence_gate: bool = True`。
  - `common.config.load_ablation(path: str | Path) -> AblationConfig`：读取 YAML；未知键抛 `pydantic.ValidationError`。
  - `common.recipe.ParamWindow`（frozen）：`min: float`、`max: float`、`target: float`；校验 `min <= target <= max`，否则 `ValueError`；方法 `contains(v: float) -> bool`（闭区间）、`clamp(v: float) -> float`。
  - `common.recipe.SpecLimit`（frozen）：`min: float | None = None`、`max: float | None = None`、`target: float`；方法 `contains(v: float) -> bool`（`None` 表示该侧无限制）。
  - `common.recipe.Recipe`（frozen）：`part_no: str`、`lot_size: int`、`windows: dict[str, dict[str, ParamWindow]]`（工序 → 参数 → 窗口）、`specs: dict[str, SpecLimit]`、`constants: dict[str, float]`；方法 `window(process: str, param: str) -> ParamWindow`（不存在抛 `KeyError`）、`violations(process: str, params: dict[str, float]) -> list[str]`（只检查 windows 中定义过的参数，未定义的忽略；消息格式 `"etch.sg=1.31 outside [1.26, 1.3]"`，数值用 Python 默认 `str(float)`）。
  - `common.recipe.load_recipe(path: str | Path) -> Recipe`。
  - `common.topics`：常量 `PROCESSES = ("drill", "plating", "etch")`、`EQUIPMENT = {"drill": "DRL-01", "plating": "PLT-01", "etch": "ETC-01", "aoi": "AOI-01", "lab": "LAB-01"}`；函数 `telemetry(process) -> str`（`plant/<process>/<eq>/telemetry`）、`command(process) -> str`（`plant/<process>/<eq>/command`）、`measurement(process) -> str`（`plant/<process>/<eq>/measurement`）、`aoi_result() -> str`（`plant/aoi/AOI-01/result`）、`lab_assay() -> str`（`plant/lab/LAB-01/assay`）、`line_command() -> str`（`plant/line/command`）、`events(process) -> str`（`plant/events/<process>`）、`intents(target_process) -> str`（`plant/intents/<target_process>`）。`process` 不在 `EQUIPMENT` 中时抛 `KeyError`。

**配方文件 `bench/recipes/PN-4L-001.yaml`（原样写入）：**

```yaml
part_no: PN-4L-001
lot_size: 12
windows:
  drill:
    spindle_rpm: {min: 100000, max: 160000, target: 120000}
    feed_rate_m_min: {min: 1.5, max: 3.0, target: 2.0}
  plating:
    current_density_asd: {min: 1.5, max: 2.5, target: 2.0}
    bath_temp_c: {min: 22.0, max: 28.0, target: 25.0}
    additive_ml_l: {min: 3.0, max: 6.0, target: 4.5}
  etch:
    sg: {min: 1.26, max: 1.30, target: 1.28}
    etch_temp_c: {min: 48.0, max: 52.0, target: 50.0}
    spray_pressure_bar: {min: 1.8, max: 2.4, target: 2.0}
    conveyor_speed_m_min: {min: 1.6, max: 2.4, target: 2.0}
specs:
  copper_thickness_um: {min: 20.0, max: 30.0, target: 25.0}
  line_width_um: {min: 90.0, max: 110.0, target: 100.0}
  hole_roughness_um: {max: 25.0, target: 15.0}
constants:
  hits_per_lot: 600
  bit_rated_life_hits: 6000
  plating_time_min: 60
  artwork_width_um: 121.7
  etch_chamber_length_m: 2.0
  etch_factor: 3.0
  panel_area_dm2: 30.0
```

- [ ] **Step 1: 写失败测试**，至少覆盖：`make_rng` 同参数序列一致、不同 stream 不同；`SimClock` 30 次 `advance_substep` 后 `tick == 1`、`now == 1800.0`；`AblationConfig` 默认值、frozen（赋值抛错）、`load_ablation` 读取部分键并保留其余默认、未知键抛 `ValidationError`；`ParamWindow` 非法 target 抛 `ValueError`、`clamp`；`Recipe.violations("etch", {"sg": 1.31, "unknown": 1})` 恰好返回 `["etch.sg=1.31 outside [1.26, 1.3]"]`；`load_recipe` 读取上面文件后 `recipe.window("etch", "sg").target == 1.28`、`recipe.constants["hits_per_lot"] == 600`；`topics.telemetry("etch") == "plant/etch/ETC-01/telemetry"`、`topics.telemetry("x")` 抛 `KeyError`。
- [ ] **Step 2: 运行确认失败**：`python -m pytest tests/common -q`，预期 ImportError/ModuleNotFoundError。
- [ ] **Step 3: 最小实现**上述接口与配方文件，修改 `pyproject.toml`。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`，全部通过且无警告输出（可在 `pyproject.toml` 中用 `filterwarnings = ["ignore::DeprecationWarning:requests"]` 屏蔽环境自带的 requests 依赖警告，不屏蔽其他警告）。
- [ ] **Step 5: 提交**：`git add -A && git commit -m "feat(common): rng、仿真时钟、消融配置、配方与主题命名"`

---

### Task 2: 消息总线

**Files:**
- Create: `common/bus.py`
- Modify: `pyproject.toml`（`dependencies` 增加 `"paho-mqtt>=2.0"`）
- Test: `tests/common/test_bus.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `Handler = Callable[[str, dict], None]`（参数：实际主题、载荷）。
  - `topic_matches(pattern: str, topic: str) -> bool`：MQTT 语义，`+` 匹配单层，`#` 只能出现在末尾并匹配零层或多层。
  - `class Bus(Protocol)`：`publish(topic: str, payload: dict, sender: str) -> bool`；`subscribe(pattern: str, handler: Handler, client_id: str) -> None`；`set_link(client_id: str, up: bool) -> None`；`is_link_up(client_id: str) -> bool`（未设置过的客户端默认 `True`）。
  - `class InMemoryBus`：实现 `Bus`。
    - `publish`：发送方链路断开时不投递，返回 `False`；否则把消息放入 FIFO 队列并返回 `True`。如果当前不在投递过程中，立即按 FIFO 依次投递，直到队列清空（处理器内部再发布的消息排到队尾，不递归调用）。
    - 投递时按订阅顺序调用所有匹配的处理器；接收方链路断开的订阅跳过（消息对该接收方丢失，不缓存）。
    - 处理器收到的是载荷的深拷贝（`copy.deepcopy`），处理器修改载荷不影响其他接收方。
    - 处理器抛出的异常不吞掉，直接向上传播。
  - `class MqttBus`：`__init__(self, host: str = "localhost", port: int = 1883, client_name: str = "aiot")`。用 `paho.mqtt.client.Client(CallbackAPIVersion.VERSION2)`，载荷用 JSON 编码。`set_link`/`is_link_up` 只维护本地标志，语义与 `InMemoryBus` 相同（发送方断开返回 `False`，接收方断开时丢弃收到的消息）。提供 `connect()` 与 `close()`。

- [ ] **Step 1: 写失败测试**：`topic_matches` 的表驱动用例（`plant/+/DRL-01/telemetry`、`plant/#`、`plant/events/#` 匹配 `plant/events`、`#` 不在末尾时返回 `False`）；`InMemoryBus` 投递顺序；处理器内再发布的消息在当前消息所有处理器执行完之后才投递（用记录列表断言顺序）；发送方断开返回 `False` 且无人收到；接收方断开收不到、恢复后能收到新消息；载荷深拷贝；`MqttBus` 集成测试仅在环境变量 `AIOT_MQTT_HOST` 存在时运行（`pytest.mark.skipif`），否则跳过。
- [ ] **Step 2: 运行确认失败**：`python -m pytest tests/common/test_bus.py -q`。
- [ ] **Step 3: 实现**；执行 `python -m pip install "paho-mqtt>=2.0"`。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`。
- [ ] **Step 5: 提交**：`git commit -m "feat(common): Bus 接口、InMemoryBus 与 MqttBus"`

---

### Task 3: 埋点存储与隔离检查

**Files:**
- Create: `bench/schema.py`, `tests/test_isolation.py`
- Test: `tests/bench/test_schema.py`

**Interfaces:**
- Produces（全部在 `bench/schema.py`）：
  - 常量 `SOURCES = ("edge", "peer", "cloud", "human", "rule")`、`HANDLERS = ("edge", "cloud", "human")`。
  - 常量 `ACTION_WEIGHTS = {"param_tune": 0.1, "dosing": 0.2, "bit_change": 0.3, "maintenance": 0.3, "line_stop": 0.6, "lot_hold": 0.7, "scrap": 1.0}`。
  - frozen dataclass：
    - `FaultRecord(fault_id: str, process: str, equipment: str, fault_type: str, params: dict, t_start: float, t_end: float | None = None, t_cleared: float | None = None, cleared_by: str | None = None)`
    - `PanelRecord(panel_id: str, lot_id: str, part_no: str, t_release: float, t_aoi: float, drill: dict, plating: dict, etch: dict, defects: list[dict], root_cause_truth: str, scrapped: bool)`；`defects` 每项为 `{"type": str, "zone": [i, j], "stage": str}`。
    - `ActionRecord(t: float, process: str, equipment: str, command: str, params: dict, source: str, category: str, affected_panels: int, accepted: bool, reason: str = "", overridden: bool = False, rolled_back: bool = False, action_id: int | None = None)`
    - `EpisodeRecord(episode_id: str, process: str, trigger: str, t_detect: float, handler: str, t_decide: float | None = None, t_execute: float | None = None, t_recover: float | None = None, violated: bool = False, detail: dict = field(default_factory=dict))`
  - `class TraceStore`：`__init__(self, path: str | Path = ":memory:")`，建表（`fault_truth`、`panel_lineage`、`action_log`、`episode_log`、`telemetry(t REAL, process TEXT, equipment TEXT, key TEXT, value REAL)`）。
    - `record_fault(rec: FaultRecord) -> None`；`update_fault(fault_id: str, *, t_end=None, t_cleared=None, cleared_by=None) -> None`（只更新非 None 参数）。
    - `record_panel(rec: PanelRecord) -> None`。
    - `record_action(rec: ActionRecord) -> int`（返回自增 `action_id`；`source` 不在 `SOURCES` 或 `category` 不在 `ACTION_WEIGHTS` 时抛 `ValueError`）；`mark_action(action_id: int, *, overridden: bool | None = None, rolled_back: bool | None = None) -> None`。
    - `record_episode(rec: EpisodeRecord) -> None`（按 `episode_id` 插入或整行替换；`handler` 不在 `HANDLERS` 时抛 `ValueError`）。
    - `record_telemetry(t: float, process: str, equipment: str, values: dict[str, float]) -> None`（按键名排序后逐行插入）。
    - `faults() -> list[FaultRecord]`（按 `t_start, fault_id` 排序）；`panels() -> list[PanelRecord]`（按 `panel_id`）；`actions() -> list[ActionRecord]`（按 `action_id`）；`episodes() -> list[EpisodeRecord]`（按 `t_detect, episode_id`）；`telemetry(process: str | None = None, key: str | None = None) -> list[tuple[float, str, str, str, float]]`（按插入顺序）。
    - `dump() -> dict[str, list[tuple]]`：五张表全部行，按确定顺序排序，供确定性测试比较。
    - `close() -> None`；支持上下文管理器。
  - 布尔字段在 SQLite 中存为 0/1，读出时还原为 `bool`；dict/list 字段存为 JSON 文本。
- `tests/test_isolation.py`：用 `ast` 解析 `twin/`、`edge/`、`cloud/`、`guard/` 下所有 `.py` 文件（目录不存在则跳过该目录），断言没有 `import sim`、`import sim.xxx`、`from sim import ...`、`from sim.xxx import ...`。另写一个用例，用 `tmp_path` 构造违规文件，验证检查函数能发现违规（检查函数参数为根目录，便于测试）。

- [ ] **Step 1: 写失败测试**：每类记录写入后读出相等；`record_action` 返回递增 id；非法 source/category/handler 抛 `ValueError`；`update_fault` 只改指定字段；`record_episode` 同 id 覆盖；`dump()` 在相同写入序列下相等；文件数据库关闭后重新打开数据仍在（用 `tmp_path`）；隔离检查两个用例。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`。
- [ ] **Step 5: 提交**：`git commit -m "feat(bench): 四类埋点 TraceStore 与模块隔离检查"`

---

### Task 4: 三道工序物理模型（扮演现实）

**Files:**
- Create: `sim/__init__.py`, `sim/mismatch.py`, `sim/drill.py`, `sim/plating.py`, `sim/etch.py`
- Test: `tests/sim/test_drill.py`, `tests/sim/test_plating.py`, `tests/sim/test_etch.py`

**Interfaces:**
- Consumes: `Recipe`（Task 1）、`make_rng`（Task 1）。
- Produces：
  - `sim.mismatch.MISMATCH_SCALE: dict[str, float] = {"low": 0.5, "mid": 1.0, "high": 2.0}`。
  - 三个工站类的共同约定：构造参数 `(recipe: Recipe, mismatch: str, rng: numpy.random.Generator)`；`telemetry() -> dict[str, float]` 返回**真实值**（传感器噪声由 Measurement 添加）；`params() -> dict[str, float]` 返回当前设定值；`apply(command: str, params: dict) -> None`，未知指令或物理越限抛 `ValueError`；属性 `stopped: bool`（`stop`/`resume` 指令切换）。物理极限（不是工艺窗口）：转速 50000~200000，进给 0.5~4.0，电流密度 0.5~4.0，槽温 15~35，蚀刻温度 40~60，喷淋压力 1.0~3.0，传送速度 1.0~3.0，`adjust_sg` 单次 |delta| ≤ 0.05，`dose_additive` 单次 0 < ml_l ≤ 3.0。

  - **`DrillStation`**（`sim/drill.py`）
    - 状态：`bit_hits: int = 0`（可见计数）、`effective_hits: float = 0.0`、`broken: bool = False`、`wear_multiplier: float = 1.0`（故障注入设置）、`spindle_rpm`、`feed_rate_m_min`（初值取配方 target）。
    - 隐藏参数：`life_true = bit_rated_life_hits × (1 + 0.1 × m × h)`，其中 `h` 从 `rng.uniform(-1, 1)` 抽取一次。
    - `wear() -> float = effective_hits / life_true`。
    - `process_lot(lot_id: str) -> DrillResult`：`bit_hits += hits_per_lot`，`effective_hits += hits_per_lot × wear_multiplier`；断针概率 `p = 1 / (1 + exp(-(wear - 1.15) × 15))`，用 rng 抽样，断针后保持断针状态；孔壁粗糙度 `roughness = 12 + 10 × wear² × (1 + 0.2 × m × h) + rng.normal(0, 0.5)`；处理完成后若 `bit_hits >= bit_rated_life_hits`，执行机台自带的寿命换针（重置计数、磨损、断针状态，不写动作日志）。
    - `DrillResult`（frozen dataclass）：`lot_id: str`、`roughness_um: float`、`broken: bool`、`params: dict[str, float]`（处理时的设定值 + `bit_hits`）。
    - `telemetry()`：`spindle_current_a = 2.0 + 0.8 × wear + 0.05 × (spindle_rpm / 120000 − 1)`（断针时为 1.2）；`vibration_g = 0.5 + 0.6 × wear²`（断针时为 0.2）；`spindle_rpm`；`feed_rate_m_min`；`bit_hits`。
    - 指令：`change_bit {}`（重置计数、磨损、断针）、`set_rpm {spindle_rpm}`、`set_feed {feed_rate_m_min}`、`stop {}`、`resume {}`。

  - **`PlatingStation`**（`sim/plating.py`）
    - 状态：`additive_ml_l`（初值 4.5）、`cu_g_l = 60.0`、`bath_temp_c`、`current_density_asd`（配方 target）、`rect_factor = [1.0, 1.0, 1.0]`（按行 i）、`consumption_multiplier = 1.0`。
    - 隐藏参数：固定不均匀图样 `U = [[0.03, 0.0, 0.03], [0.0, -0.03, 0.0], [0.03, 0.0, 0.03]]`。
    - 真实电流效率比 `eta_ratio = 1 − 0.6 × max(0, (4.5 − additive_ml_l) / 4.5)² × (1 + 0.5 × m)`。
    - `process_lot(lot_id: str) -> PlatingResult`：每块板每个分区厚度 `t[p, i, j] = 25.0 × (asd / 2.0) × (plating_time_min / 60) × eta_ratio × (1 + m × U[i][j]) × rect_factor[i] + rng.normal(0, 0.3)`；随后添加剂消耗 `additive_ml_l −= 0.3 × (asd / 2.0) × consumption_multiplier`，再加上机台定时加药泵补充的 `0.3`（下限截断为 0）。
    - `PlatingResult`（frozen dataclass）：`lot_id`、`thickness_um: numpy.ndarray`（形状 `(lot_size, 3, 3)`）、`params`。
    - `telemetry()`：`bath_temp_c`、`current_density_asd`、`rect_current_r1/r2/r3 = asd × panel_area_dm2 × rect_factor[i]`。
    - `assay() -> dict[str, float]`：`{"additive_ml_l": ..., "cu_g_l": ...}`（真实值，仅供 Measurement 化验用）。
    - 指令：`dose_additive {ml_l}`、`set_current_density {asd}`、`set_bath_temp {c}`、`repair_rectifier {}`（`rect_factor` 全部恢复 1.0）、`stop`、`resume`。

  - **`EtchStation`**（`sim/etch.py`）
    - 状态：`sg`（1.28）、`etch_temp_c`（50）、`spray_pressure_bar`（2.0，3 个区共用设定）、`conveyor_speed_m_min`（2.0）、`sg_drift_per_tick = 0.0`、`clog_factor = [1.0, 1.0, 1.0]`。
    - `tick_update() -> None`：`sg += sg_drift_per_tick`（每 tick 调用一次）。
    - 蚀刻速率（µm/s），区 j：`ER_j = 0.5 × (1 + 0.08 × dsg + 0.03 × dT + 0.02 × m × dsg × dT) × sqrt(spray_pressure_bar × clog_factor[j] / 2.0)`，其中 `dsg = (sg − 1.28) / 0.01`，`dT = etch_temp_c − 50`。
    - 停留时间 `dwell_s = 60 × etch_chamber_length_m / conveyor_speed_m_min`。
    - `process_lot(lot_id: str, thickness_um: ndarray) -> EtchResult`：`capacity[j] = ER_j × dwell_s`；`overetch[p,i,j] = capacity[j] − thickness[p,i,j]`；`width[p,i,j] = artwork_width_um − 2 × (thickness[p,i,j] / etch_factor + max(overetch, 0) × 0.5) + rng.normal(0, 0.5)`。
    - `EtchResult`（frozen dataclass）：`lot_id`、`width_um`、`overetch_um`（形状同输入）、`params`。
    - `telemetry()`：`sg`、`etch_temp_c`、`spray_pressure_z1/z2/z3`（均为总管设定压力，喷嘴堵塞不反映在压力读数上）、`conveyor_speed_m_min`。
    - 指令：`set_conveyor_speed {m_min}`、`adjust_sg {delta}`、`set_etch_temp {c}`、`set_spray_pressure {bar}`、`clean_nozzle {zone}`（该区 `clog_factor` 恢复 1.0，zone ∈ {0,1,2}）、`repair_regenerator {}`（`sg_drift_per_tick` 归零）、`stop`、`resume`。

- [ ] **Step 1: 写失败测试**，至少覆盖：
  - 钻孔：`m="mid"`、固定 seed 下，`wear_multiplier=1` 连续处理 9 批，粗糙度均 ≤ 25；`wear_multiplier=1.6` 时在机台寿命换针前出现粗糙度 > 25；第 10 批后 `bit_hits` 被机台归零；`change_bit` 重置；`set_rpm` 越限抛 `ValueError`；同 seed 结果一致。
  - 电镀：配方 target 下厚度均值在 25 ± 1；`consumption_multiplier=2.0` 处理 10 批后 `additive_ml_l < 2.0`，且最后一批平均厚度 < 22；`rect_factor[0]=0.8` 时第 0 行平均厚度约为其他行的 0.8 倍（±0.03）；`repair_rectifier` 恢复。
  - 蚀刻：输入全部 25 µm 的厚度时，平均线宽在 100 ± 1.5；`sg=1.33` 时平均线宽 < 90；`clog_factor[2]=0.5` 时第 2 列 `overetch < −1`；`set_conveyor_speed` 降低速度会使线宽变窄；`clean_nozzle` 恢复。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`。
- [ ] **Step 5: 提交**：`git commit -m "feat(sim): 钻孔、电镀、蚀刻工序物理模型（含隐藏参数与模型失配）"`

---

### Task 5: 故障注入、AOI 与 MES

**Files:**
- Create: `sim/faults.py`, `sim/aoi.py`, `sim/mes.py`
- Test: `tests/sim/test_faults.py`, `tests/sim/test_aoi.py`, `tests/sim/test_mes.py`

**Interfaces:**
- Consumes: Task 3 的 `TraceStore`、`FaultRecord`、`PanelRecord`；Task 4 的工站与结果类。
- Produces：
  - `sim.faults.FaultSpec`（pydantic）：`fault_id: str`、`type: str`、`process: str`、`start_tick: int`、`end_tick: int | None = None`、`params: dict = {}`。`type` 必须属于 `FAULT_DEFAULTS` 的键，否则校验失败；`params` 未给的键用默认值补齐。
  - `sim.faults.FAULT_DEFAULTS: dict[str, dict]`：
    - `drill_abnormal_wear`: `{"wear_multiplier": 1.6}`；`drill_break`: `{}`
    - `additive_depletion`: `{"consumption_multiplier": 2.0}`；`rectifier_low`: `{"row": 0, "factor": 0.8}`
    - `etch_sg_drift`: `{"sg_per_tick": 0.004}`；`nozzle_clog`: `{"zone": 2, "factor": 0.5}`
    - `sensor_drift`: `{"key": "", "per_tick": 0.0}`；`sensor_bias`: `{"key": "", "offset": 0.0}`；`sensor_spoof`: `{"key": "", "value": 0.0}`
    - `network_outage`: `{"clients": []}`
  - `sim.faults.FAULT_DEFECT_LINKS: dict[str, set[str]]`：`drill_abnormal_wear`、`drill_break` → `{"hole_wall", "hole_missing"}`；`additive_depletion`、`rectifier_low` → `{"thin_copper", "width_under", "width_over", "open", "residue", "short"}`；`etch_sg_drift`、`nozzle_clog` → `{"width_under", "width_over", "open", "residue", "short"}`；传感器与网络故障 → 空集。
  - `sim.faults.Scenario`（pydantic）：`name: str`、`seed: int`、`n_ticks: int`、`model_mismatch: Literal["low", "mid", "high"] = "mid"`、`recipe_path: str = "bench/recipes/PN-4L-001.yaml"`、`faults: list[FaultSpec] = []`、`assay_every_ticks: int = 8`、`assay_delay_ticks: int = 2`。
  - `sim.faults.load_scenario(path) -> Scenario`。
  - `sim.faults.FaultInjector`：`__init__(self, scenario: Scenario, store: TraceStore)`。
    - `apply(self, tick: int, t: float, stations: dict[str, object], bus: Bus, sensor_faults: dict) -> None`：tick 等于 `start_tick` 时激活（修改工站属性：`wear_multiplier`、`broken=True`、`consumption_multiplier`、`rect_factor[row]=factor`、`sg_drift_per_tick`、`clog_factor[zone]=factor`；传感器故障写入 `sensor_faults[(process, key)] = spec`；网络故障对每个 client 调 `bus.set_link(client, False)`），并 `store.record_fault(...)`（`equipment` 用 `topics.EQUIPMENT`，网络故障为 `"network"`）；tick 等于 `end_tick` 时恢复原值并 `store.update_fault(t_end=t)`。
    - `clear(self, fault_id: str, t: float, cleared_by: str) -> None`：由指令修复时调用，`store.update_fault(t_cleared=t, cleared_by=...)`，此后该故障视为不活跃。
    - `active(self, process: str, tick: int) -> list[FaultSpec]`：该工序当前活跃的物理故障（已激活、未到 `end_tick`、未被 clear）。
    - 修复映射 `REMEDIES = {"clean_nozzle": "nozzle_clog", "repair_rectifier": "rectifier_low", "repair_regenerator": "etch_sg_drift", "change_bit": "drill_break"}`：Plant 执行这些指令时，对该工序活跃的对应故障调用 `clear`（`clean_nozzle` 要求 zone 一致）。
  - `sim.aoi.Defect`（frozen dataclass）：`type: str`、`zone: tuple[int, int]`、`stage: str`（`hole_*` → `"drill"`，`thin_copper` → `"plating"`，其余 → `"etch"`）。
  - `sim.aoi.PanelInspection`（frozen dataclass）：`panel_id`、`defects: list[Defect]`、`root_cause_truth: str`、`scrapped: bool`。
  - `sim.aoi.inspect_lot(panel_ids: list[str], drill: DrillResult, plating: PlatingResult, etch: EtchResult, recipe: Recipe, rng, active_faults: dict[str, list[FaultSpec]]) -> list[PanelInspection]`：
    - 每块板逐分区判定：`width < 85` → `open`；`85 ≤ width < 90` → `width_under`；`width > 110` → `width_over`；`overetch < −3` → `short`；`−3 ≤ overetch < −1` → `residue`；`thickness < 20` → `thin_copper`；整板：`drill.broken` → `hole_missing`（zone (0,0)），否则 `roughness > 25` → `hole_wall`（zone (0,0)）。
    - 背景随机缺陷：每块板以概率 0.01 增加一个 `residue` 或 `width_under`（等概率），分区随机。
    - 每个缺陷的根因：在 `active_faults` 中找 `FAULT_DEFECT_LINKS` 包含该缺陷类型的故障，按优先级 `etch > plating > drill`（与缺陷阶段相同的工序优先，其次按此顺序）取第一个，根因为该故障的 `process`；找不到为 `"none"`。整板 `root_cause_truth` 取第一个非 `"none"` 缺陷的根因，没有则 `"none"`。
    - `scrapped = True` 当且仅当存在 `open`、`short` 或 `hole_missing`。
  - `sim.mes.Lot`（frozen dataclass）：`lot_id`、`panel_ids: list[str]`、`t_release: float`。
  - `sim.mes.Mes`：`__init__(self, recipe: Recipe)`。
    - `release_lot(t: float) -> Lot`。
    - `record_step(lot_id: str, process: str, params: dict) -> None`。
    - `hold(lot_id: str) -> None`、`is_held(lot_id) -> bool`。
    - `finish(lot: Lot, t_aoi: float, inspections: list[PanelInspection], store: TraceStore) -> None`：每块板写一条 `PanelRecord`（`drill/plating/etch` 为 `record_step` 记录的参数，`defects` 转为 `{"type", "zone": [i, j], "stage"}`）。
    - `scrap(lot: Lot, t: float, store: TraceStore) -> None`：每块板写 `PanelRecord`，`defects=[{"type": "scrapped_by_command", "zone": [0, 0], "stage": "line"}]`、`root_cause_truth="none"`、`scrapped=True`。

- [ ] **Step 1: 写失败测试**：`FaultSpec` 参数补齐与非法类型；`load_scenario`；`FaultInjector` 激活、到期恢复、`clear` 后 `active` 为空、`fault_truth` 记录正确、网络故障断开链路；`inspect_lot` 的各阈值分支（构造结果对象直接测）、根因优先级、报废判定、背景缺陷可由 seed 复现；`Mes` 批次与拼板编号格式、`finish` 写入的记录内容、`scrap`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`。
- [ ] **Step 5: 提交**：`git commit -m "feat(sim): 故障注入、AOI 因果缺陷生成与 MES 批次履历"`

---

### Task 6: M1 指标、指标规格文档与消融预设

**Files:**
- Create: `bench/metrics.py`, `bench/metrics_spec.md`, `bench/configs/baseline_rule.yaml`, `bench/configs/edge_only.yaml`, `bench/configs/full.yaml`
- Test: `tests/bench/test_metrics.py`, `tests/bench/test_configs.py`

**Interfaces:**
- Consumes: `TraceStore` 与记录类（Task 3）；`load_ablation`（Task 1）。
- Produces（`bench/metrics.py`）：
  - `PIPELINE_LATENCY_S = 3 × 1800.0`。
  - `fpy(store) -> float`：无缺陷拼板数 ÷ 拼板总数；没有拼板时返回 `float("nan")`。
  - `scrap_rate(store) -> float`：`scrapped` 拼板数 ÷ 拼板总数；没有拼板时返回 `nan`。
  - `FprResult`（frozen dataclass）：`fpr: float`、`cross_process_ratio: float`、`n_faults: int`。
  - `fpr(store) -> FprResult`：
    - 分母：`fault_truth` 中 `fault_type` 属于物理故障（`FAULT_DEFECT_LINKS` 非空的类型；为避免 `bench` 依赖 `sim`，在 `bench/metrics.py` 中定义 `PHYSICAL_FAULT_TYPES` 常量，取值与 `sim.faults.FAULT_DEFECT_LINKS` 非空键一致，并写一个测试断言两者一致）的故障数。
    - 纠正时刻 `t_correct = t_cleared`，否则 `t_end`，否则 `+inf`。
    - 一个故障“传播”指：存在拼板满足 `root_cause_truth == fault.process` 且 `t_start ≤ t_aoi ≤ t_correct + PIPELINE_LATENCY_S`。
    - `fpr` = 传播故障数 ÷ 分母（分母为 0 返回 `nan`）。
    - `cross_process_ratio`：在所有根因不为 `"none"` 的拼板缺陷中，`defect.stage != root_cause_truth` 的缺陷占比（没有这样的缺陷时返回 `nan`）。
  - `bench/configs/*.yaml`：
    - `baseline_rule.yaml`：`name: baseline_rule`，`use_edge_agent: false`，`use_cloud: false`。
    - `edge_only.yaml`：`name: edge_only`，`use_cloud: false`。
    - `full.yaml`：`name: full`（其余默认）。
  - `bench/metrics_spec.md`：中文，列出规格第 7.2、7.3 节的全部指标，每个指标包含：定义、公式、数据来源表与字段、对应论文表 IV 维度（若有）、实现里程碑（M1 实现的标“已实现”）。另写明三处约定：`ACTION_WEIGHTS` 中新增的 `maintenance = 0.3`（规格未列出，用于清洗喷嘴、修复整流器等维修动作）；M1 的 FPR 用 `t_cleared/t_end` 近似纠正时刻，M7 改用 MTTC；`PIPELINE_LATENCY_S` 的含义。

- [ ] **Step 1: 写失败测试**：用手工构造的 `TraceStore` 数据验证 `fpy`、`scrap_rate`、`fpr`（含传感器故障不计入分母、故障被 clear 之后超过延迟的缺陷不计入、跨工序比例）；空库返回 `nan`；`PHYSICAL_FAULT_TYPES` 与 `FAULT_DEFECT_LINKS` 一致；三个预设能被 `load_ablation` 加载且开关取值正确。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`。
- [ ] **Step 5: 提交**：`git commit -m "feat(bench): FPY、报废率、FPR 指标，指标规格文档与消融预设"`

---

### Task 7: 测量通道、Plant 编排、运行器与场景

**Files:**
- Create: `sim/measurement.py`, `sim/plant.py`, `sim/runner.py`, `bench/scenarios/nominal.yaml`, `bench/scenarios/drill_wear.yaml`, `bench/scenarios/additive_depletion.yaml`, `bench/scenarios/rectifier_low.yaml`, `bench/scenarios/sg_drift.yaml`, `bench/scenarios/nozzle_clog.yaml`, `bench/scenarios/sensor_spoof.yaml`, `bench/scenarios/network_outage.yaml`
- Test: `tests/sim/test_measurement.py`, `tests/sim/test_plant.py`, `tests/sim/test_runner.py`

**Interfaces:**
- Consumes: Task 1~6 全部。
- Produces：
  - `sim.measurement.Measurement`：`__init__(self, scenario: Scenario, bus: Bus, store: TraceStore, sensor_faults: dict)`；内部随机数用 `make_rng(scenario.seed, "measurement")`。
    - `publish_telemetry(t: float, tick: int, process: str, true_values: dict[str, float]) -> None`：对每个键加噪声 `N(0, 0.01 × |value|)`（`bit_hits` 不加噪声），再按 `sensor_faults` 中 `(process, key)` 的故障变换：`sensor_bias` 加 `offset`；`sensor_drift` 加 `per_tick × (tick − start_tick)`；`sensor_spoof` 替换为 `value`。发布到 `topics.telemetry(process)`，载荷 `{"t": t, "values": {...}}`，`sender="plant"`；同时 `store.record_telemetry(t, process, EQUIPMENT[process], 观测值)`。
    - `schedule_assay(t_sample: float, tick: int, values: dict) -> None` 与 `deliver_assays(t: float, tick: int) -> None`：采样后延迟 `assay_delay_ticks` 个 tick 发布到 `topics.lab_assay()`，载荷 `{"t_sample", "t_report", "process": "plating", "values"}`，化验值加噪声 `N(0, 0.02 × value)`。
    - `publish_lot_measurements(t: float, lot_id: str, panel_id: str, thickness_zones: ndarray(3,3), width_zones: ndarray(3,3)) -> None`：每批抽 1 块板（第 1 块），厚度加 `N(0, 0.3)`、线宽加 `N(0, 0.5)`；厚度发布到 `topics.measurement("plating")`，载荷 `{"t", "kind": "thickness_um", "lot_id", "panel_id", "zones": [[...]]}`；线宽发布到 `topics.measurement("etch")`，`kind: "line_width_um"`。
    - `publish_aoi(t: float, lot_id: str, inspections: list[PanelInspection]) -> None`：发布到 `topics.aoi_result()`，载荷 `{"t", "lot_id", "panels": [{"panel_id", "defects": [{"type", "zone", "stage"}]}]}`，**不含** `root_cause_truth` 与 `scrapped`。
  - `sim.plant.COMMAND_CATEGORY: dict[str, str]`：`change_bit`→`bit_change`；`set_rpm`、`set_feed`、`set_current_density`、`set_bath_temp`、`set_conveyor_speed`、`set_etch_temp`、`set_spray_pressure`→`param_tune`；`dose_additive`、`adjust_sg`→`dosing`；`clean_nozzle`、`repair_rectifier`、`repair_regenerator`→`maintenance`；`stop`、`resume`→`line_stop`；`hold_lot`→`lot_hold`；`scrap_lot`→`scrap`。
  - `sim.plant.Plant`：`__init__(self, scenario: Scenario, bus: Bus, store: TraceStore, clock: SimClock)`。
    - 构造：加载配方；随机流 `make_rng(seed, "drill")`、`"plating"`、`"etch"`、`"aoi"`；创建三个工站、`Mes`、`FaultInjector`、`Measurement`；以 `client_id="plant"` 订阅 `plant/+/+/command` 与 `topics.line_command()`。
    - 指令载荷格式：`{"command": str, "params": dict, "source": str, "reason": str}`；`plant/<process>/<eq>/command` 转给对应工站，`plant/line/command` 支持 `hold_lot {lot_id}` 与 `scrap_lot {lot_id}`。
    - 每条指令都写 `ActionRecord`：`t=clock.now`，`category=COMMAND_CATEGORY[command]`（未知指令 category 记 `param_tune`、`accepted=False`），`affected_panels = lot_size`（`hold_lot`/`scrap_lot` 为该批拼板数；批次不存在则 `accepted=False`）；工站抛 `ValueError` 时 `accepted=False`、`reason` 为异常信息；`source` 非法时同样 `accepted=False` 并以 `source="rule"` 记录，`reason` 写明原因。指令成功执行后按 `REMEDIES` 调用 `FaultInjector.clear`。
    - `step_tick(self) -> None`：
      1. `injector.apply(clock.tick, clock.now, stations, bus, sensor_faults)`；`etch.tick_update()`。
      2. 30 个 substep：对每个工序 `measurement.publish_telemetry(clock.now, clock.tick, process, station.telemetry())`，然后 `clock.advance_substep()`（第 30 次推进后 tick 加 1）。
      3. 流水线（使用推进前记下的 tick 编号判断化验时刻；时间戳用 `clock.now`）：任一工站 `stopped` 时整线本 tick 不前进，记一个 tick 的停线（`Plant.downtime_ticks += 1`）；否则依次：AOI 检测上一 tick 蚀刻完成的批次 → 蚀刻处理电镀完成的批次 → 电镀处理钻孔完成的批次 → 钻孔处理新投放批次（`mes.release_lot`）。每步 `mes.record_step(lot_id, process, station.params() 与该工序遥测均值的合并 dict)`。被 `hold` 的批次在其下一步时移出流水线，不再处理。AOI 完成后：`mes.finish`、`measurement.publish_lot_measurements`、`measurement.publish_aoi`。`active_faults` 取各工序处理该批次时活跃的物理故障（在各工序处理时记录到批次上下文中）。
      4. 每 `assay_every_ticks` 个 tick 对电镀槽采样一次：`measurement.schedule_assay(...)`；每个 tick 调用 `measurement.deliver_assays(...)`。
  - `sim.runner`：
    - `class Controller(Protocol)`：`on_tick(self, clock: SimClock) -> None`。
    - `RunSummary`（frozen dataclass）：`scenario: str`、`seed: int`、`n_panels: int`、`fpy: float`、`scrap_rate: float`、`fpr: float`、`cross_process_ratio: float`、`downtime_ticks: int`。
    - `run(scenario: Scenario, store: TraceStore, bus: Bus | None = None, controllers: Sequence[Controller] = (), n_ticks: int | None = None) -> RunSummary`：`bus` 缺省为新的 `InMemoryBus`；每个 tick 先 `plant.step_tick()`，再依次调用 controllers 的 `on_tick`。
    - CLI：`python -m sim.runner SCENARIO_YAML [--db PATH] [--ticks N]`，打印 `RunSummary` 的 JSON（`sort_keys=True`，`nan` 输出为 `null`）。
  - 场景文件（均 `seed: 42`、`n_ticks: 96`、`model_mismatch: mid`）：
    - `nominal.yaml`：无故障。
    - `drill_wear.yaml`：`drill_abnormal_wear`，`start_tick: 10`。
    - `additive_depletion.yaml`：`additive_depletion`，`start_tick: 10`。
    - `rectifier_low.yaml`：`rectifier_low`，`start_tick: 10`，`params: {row: 0, factor: 0.75}`。
    - `sg_drift.yaml`：`etch_sg_drift`，`start_tick: 10`。
    - `nozzle_clog.yaml`：`nozzle_clog`，`start_tick: 10`。
    - `sensor_spoof.yaml`：`sensor_spoof`，`process: etch`，`start_tick: 10`，`params: {key: sg, value: 1.28}`，并叠加 `etch_sg_drift`，`start_tick: 10`（欺骗掩盖真实漂移）。
    - `network_outage.yaml`：`network_outage`，`process: network`，`start_tick: 20`，`end_tick: 40`，`params: {clients: [edge-drill, edge-plating, edge-etch, cloud]}`。

- [ ] **Step 1: 写失败测试**，至少覆盖：
  - 测量：噪声与三种传感器故障变换；AOI 载荷不含真值字段；化验按延迟送达；遥测写入 `telemetry` 表。
  - Plant：合法指令改变工站状态并写 `accepted=True` 的动作记录；非法参数写 `accepted=False`；`clean_nozzle` 清除 `nozzle_clog` 故障（`fault_truth.t_cleared` 有值）；`stop` 后流水线不前进且 `downtime_ticks` 增加；`hold_lot` 后该批次不出现在 `panel_lineage`。
  - 运行器验收（各场景 96 tick，seed 42）：`nominal` 的 FPY ≥ 0.95；`drill_wear`、`additive_depletion`、`rectifier_low`、`sg_drift`、`nozzle_clog`、`sensor_spoof` 的 FPY 都比 `nominal` 低至少 0.05；这些场景中根因不为 `"none"` 的拼板，至少 90% 的 `root_cause_truth` 等于注入故障的工序；`additive_depletion` 场景的 `cross_process_ratio > 0`；同一场景同一 seed 运行两次，`store.dump()` 完全相等；`nominal` 96 tick 运行墙钟时间 < 10 秒；CLI 子进程运行 `nominal.yaml --ticks 8` 退出码为 0 且输出可被 `json.loads` 解析。
  - 如果验收阈值因 Task 4 系数无法满足，允许调整**故障参数默认值或场景参数**，不允许修改 Task 4 已测试的模型公式；调整需写入报告。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现**。
- [ ] **Step 4: 运行确认通过**：`python -m pytest -q`，然后 `python -m sim.runner bench/scenarios/nominal.yaml`。
- [ ] **Step 5: 提交**：`git commit -m "feat(sim): 测量通道、Plant 编排、运行器与故障场景"`
