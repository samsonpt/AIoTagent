# M3 四层工艺数字孪生 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现与模拟器隔离的四层孪生（状态估计、独立机理模型、残差+分位不确定度、RLS 校准与服务），在 `model_mismatch=mid` 下铜厚/线宽 MAPE ≤ 5%、90% 区间覆盖率 ≥ 85%，并提供 `simulate` / `compare` / `counterfactual`。

**Architecture:** 孪生只通过 `Bus` 订阅遥测、化验、测量、`lot_step`，禁止 import `sim`。机理系数与模拟器刻意不同（法拉第常数、蚀刻化学系数），由 RLS 与残差模型吸收失配。`TwinService` 是 `Controller`，在 `Plant.step_tick` 之后更新；`twin_fidelity=none|mechanistic|hybrid`。

**Tech Stack:** Python 3.12, numpy, pydantic v2, scikit-learn（GradientBoostingRegressor 分位回归）, pytest。

**规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.3、§6、§7.2 孪生指标、§10 M3；ADR-003。

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
twin/__init__.py
twin/types.py              # Prediction, CounterfactualResult, TwinObservation
twin/state.py              # ScalarFilter / ProcessState
twin/models/__init__.py
twin/models/plating.py     # 法拉第铜厚（独立系数）
twin/models/etch.py        # 独立蚀刻线宽
twin/models/drill.py       # 独立磨损-粗糙度
twin/models/residual.py    # 分位 GBM 残差
twin/calibration.py        # RLS + twin_confidence
twin/service.py            # 订阅总线；simulate/compare/counterfactual
bench/metrics.py           # mape / coverage90
bench/metrics_spec.md      # M3 指标标“已实现”
sim/runner.py              # ablation.use_twin_lookahead 时挂 TwinService
tests/twin/test_*.py
```

## 数值与语义约定

机理必须与 `sim/` **不同**，禁止复制 `NONUNIFORMITY`、`0.08*dsg`、`25.0*(asd/2)` 等模拟器常数。

- 电镀法拉第：`T_um = FARADAY_UM_PER_ASD_H * asd * (plating_time_min / 60) * eta`，`FARADAY_UM_PER_ASD_H = 13.2`（模拟器等效约 12.5）。`eta = 1 - 0.45 * deficit**2`，`deficit = max(0, (4.5 - additive) / 4.5)`。不建模整流器分区与空间非均匀项。
- 蚀刻：`rate = 0.48 * (1 + 0.07*((sg-1.28)/0.01) + 0.025*(temp-50)) * sqrt(spray/2)`；`dwell = 60 * chamber_m / speed`；`width = artwork - 2 * (T / etch_factor + max(rate*dwell - T, 0) * 0.45)`。
- 钻孔：`wear = bit_hits / rated`；`roughness = 12 + 9 * wear**2`；`p_break = 1/(1+exp(-(wear-1.2)*12))`。
- 状态滤波：一维标量滤波 `x ← x+u`，化验/遥测到达时 `x ← x + K (z-x)`，`K = p/(p+r)`，`p ← (1-K)*p + q`。默认 `q=1e-4`，`r=1e-2`。
- 残差：`GradientBoostingRegressor(loss="quantile", alpha=0.5/0.05/0.95, n_estimators=40, max_depth=2, random_state=seed)`。特征为机理预测值与当前状态向量。未拟合时区间用机理值 ± `1.645 * sigma_mech`，`sigma_mech` 铜厚 0.4、线宽 0.7。
- RLS：标量增益 `θ` 乘在机理输出上。`θ ← θ + P x (y - xθ) / (λ + x P x)`，`P ← (P - P x x P / (λ + x P x)) / λ`，`λ=0.98`，`θ0=1`，`P0=1`。
- `twin_confidence`：最近 `W=20` 条预测的 90% 覆盖率，下限 0、上限 1；不足 8 条时为 0.5。
- `twin_fidelity`：`none` 时 `simulate` 返回空预测（`mean=None`）；`mechanistic` 只用机理+RLS；`hybrid` 再加残差分位。
- `compare`：每个候选纯函数，禁止睡眠；测试用 20 个候选断言总耗时 < 2 s（宽松替代“100 ms/候选”的墙钟约束，避免 CI 抖动）。
- `counterfactual`：用假设覆盖 `params` 后重算该批次预测；`defect_cleared=true` 当超规格概率从 ≥0.5 降到 <0.5。
- 预测日志：`TwinService.predictions: list[TwinObservation]`，不改四类埋点表结构。
- 验收场景：`nominal.yaml`（`model_mismatch=mid`，seed=42）跑 48 tick，用第 16 个测量之后的铜厚/线宽计算 MAPE 与覆盖率。

---

### Task 1: 类型与状态滤波

**Files:**
- Create: `twin/__init__.py`, `twin/types.py`, `twin/state.py`
- Test: `tests/twin/test_state.py`

**Interfaces:**
- `TwinObservation`: `t: float`, `tick: int`, `kind: str`, `y: float`, `yhat: float`, `q05: float`, `q95: float`, `lot_id: str | None = None`
- `Prediction`: `mean: float | None`, `q05: float | None`, `q95: float | None`, `yield_prob: float`, `oos_prob: float`, `detail: dict`
- `CounterfactualResult`: `hypothesis: dict`, `before: Prediction`, `after: Prediction`, `defect_cleared: bool`
- `ScalarFilter(x0, q=1e-4, r=1e-2)`：`predict(u=0.0)`；`update(z: float) -> float`；属性 `x`, `p`
- `ProcessState`：`filters: dict[str, ScalarFilter]`；`get(key) -> float`；`set_filter(key, filter)`

- [ ] **Step 1: 失败测试**

```python
from twin.state import ScalarFilter

def test_filter_moves_toward_measurement():
    f = ScalarFilter(x0=4.5)
    f.predict(u=-0.1)
    f.update(4.2)
    assert 4.15 < f.x < 4.45
```

- [ ] **Step 2:** `python -m pytest tests/twin/test_state.py -q` 失败（模块不存在）。
- [ ] **Step 3:** 实现 `types.py` 与 `state.py`。
- [ ] **Step 4:** 测试通过。
- [ ] **Step 5:** 提交 `feat: 孪生状态滤波与预测类型`

---

### Task 2: 独立机理模型

**Files:**
- Create: `twin/models/__init__.py`, `twin/models/plating.py`, `twin/models/etch.py`, `twin/models/drill.py`
- Test: `tests/twin/test_models.py`

**Interfaces:**
- `plating.thickness_um(asd: float, time_min: float, additive_ml_l: float) -> float`
- `etch.width_um(*, sg, temp_c, spray_bar, speed_m_min, thickness_um, artwork_um, chamber_m, etch_factor) -> float`
- `drill.roughness_um(bit_hits: float, rated: float) -> float`
- `drill.break_prob(bit_hits: float, rated: float) -> float`

名义点：`asd=2, time=60, additive=4.5` → 厚度 **26.4**（13.2×2×1×1），**不得**等于模拟器的 25.0。
`sg=1.28, temp=50, spray=2, speed=2, T=25, artwork=121.7, chamber=2, factor=3` → 线宽与模拟器公式结果的绝对差 **> 0.3**。

- [ ] 先写 `test_plating_faraday_not_simulator_constant` 与 `test_etch_width_differs_from_sim_formula`；失败后实现；提交 `feat: 孪生独立电镀蚀刻钻孔机理`

---

### Task 3: 残差分位模型

**Files:**
- Create: `twin/models/residual.py`
- Test: `tests/twin/test_residual.py`

**Interfaces:**
- `class ResidualQuantiles`：`__init__(seed: int)`；`fit(X: np.ndarray, y: np.ndarray)`（`n>=8`）；`ready: bool`；`predict(X) -> tuple[np.ndarray, np.ndarray, np.ndarray]` 为 `(q05, q50, q95)`。
- 同一 `seed` 两次 `fit`+`predict` 逐位一致。
- `n<8` 时 `ready` 为 False，`predict` 抛 `RuntimeError`。

- [ ] 提交 `feat: 孪生残差分位回归`

---

### Task 4: RLS 校准与置信度

**Files:**
- Create: `twin/calibration.py`
- Test: `tests/twin/test_calibration.py`

**Interfaces:**
- `class RlsGain`：`__init__(theta=1.0, p=1.0, lam=0.98)`；`update(x: float, y: float) -> float` 返回新 `theta`；属性 `theta`
- 构造 `y = 0.8 * x` 迭代 30 次后 `abs(theta-0.8) < 0.05`
- `twin_confidence(coverages: list[bool], window=20) -> float`：不足 8 条返回 0.5；否则最近 window 条的均值

- [ ] 提交 `feat: 孪生 RLS 增益与置信度`

---

### Task 5: TwinService API

**Files:**
- Create: `twin/service.py`
- Test: `tests/twin/test_service.py`

**Interfaces:**
- `TwinService(bus, recipe, clock, *, fidelity="hybrid", seed=0)`
- `client_id = "twin"`
- `simulate(params: dict, *, kind: Literal["thickness","width","roughness"]) -> Prediction`
  - `params` 键：电镀 `asd, time_min, additive_ml_l`；蚀刻另加 `sg, temp_c, spray_bar, speed_m_min, thickness_um`；钻孔 `bit_hits`
  - `oos_prob`：铜厚用 specs `copper_thickness_um`，线宽用 `line_width_um`；正态分布近似，σ = (q95-q05)/3.29
  - `yield_prob = 1 - oos_prob`
- `compare(candidates: list[dict]) -> list[Prediction]` 保序
- `counterfactual(params: dict, hypothesis: dict, *, kind) -> CounterfactualResult`：`after` 用 `{**params, **hypothesis}`
- `fidelity="none"` 时 `mean is None`
- `observe_thickness(y, params)` / `observe_width(y, params)`：写入 RLS、残差训练集、`predictions`

测试：名义参数 `simulate` 厚度 mean 在 24~28；`compare` 20 个候选耗时 < 2 s；假设把 `asd` 升到 3.5 后 `oos_prob` 上升；`none` 返回空 mean。

- [ ] 提交 `feat: 孪生 simulate/compare/counterfactual`

---

### Task 6: 总线观测与隔离

**Files:**
- Modify: `twin/service.py`, `sim/runner.py`
- Test: `tests/twin/test_observe.py`, `tests/test_isolation.py`（已有，确认 twin 仍为空违规）

**规则：**
- 订阅 `telemetry(plating|etch|drill)`、`lab_assay()`、`measurement(plating)`、`measurement(etch)`。
- 化验更新 `additive_ml_l` 滤波；遥测更新 `sg` / `spindle_current_a` 等。
- `measurement(plating)` 的 `zones` 全局 mean 作为 `y`，用当前状态调用 `observe_thickness`。
- `measurement(etch)` 同理 `observe_width`，`thickness_um` 取最近一次铜厚观测，缺省 25.0。
- `runner.run`：若 `ablation is not None` 且 `ablation.use_twin_lookahead` 且 `twin_fidelity != "none"`，把 `TwinService` 追加到 controllers（边缘之后）。
- 隔离：`tests/test_isolation.py` 必须继续通过。禁止 `twin` import `sim`。

- [ ] 提交 `feat: 孪生订阅测量通道并接入 runner`

---

### Task 7: MAPE / 覆盖率验收

**Files:**
- Modify: `bench/metrics.py`, `bench/metrics_spec.md`, `tests/bench/test_metrics.py`
- Test: `tests/twin/test_accuracy.py`

**Interfaces:**
- `mape(y: np.ndarray, yhat: np.ndarray) -> float` 百分比，`|y|<1e-9` 的点跳过
- `coverage90(y, q05, q95) -> float` 比例

验收：

```python
def test_mid_mismatch_thickness_width_meet_thresholds():
    # nominal + mid，48 tick，hybrid twin
    # 丢弃前 16 条 thickness 与 width 观测
    assert mape(yt, yp) <= 5.0
    assert mape(yw, ywp) <= 5.0
    assert coverage90(yt, t05, t95) >= 0.85
    assert coverage90(yw, w05, w95) >= 0.85
```

若首次失败：只允许调整残差树数量/深度、RLS `λ`、或未拟合时的 `sigma_mech`，**禁止**把机理改成与模拟器相同。

`metrics_spec.md` 铜厚/线宽 MAPE 与 90% 覆盖率标“已实现（M3）”。

- [ ] 提交 `feat: 孪生 MAPE 与区间覆盖率验收`

---

### Task 8: 漂移后校准（合成序列）

**Files:**
- Test: `tests/twin/test_recalibrate.py`

不跑完整故障场景。对 `RlsGain`+`simulate`：先用 `y=x` 拟合 15 点，MAPE<2%；再把真值改为 `y=1.3x` 连续 10 点，期间 MAPE>5%；再继续 20 点后 MAPE 回到 <3%。记录 `recal_ticks`。不读 `fault_truth`。

- [ ] 提交 `feat: 孪生漂移后 RLS 再校准`

---

## 自审

- 规格 §4.3 四层：状态 Task1；模型 Task2–3；校准 Task4；服务 Task5–6。
- 保真度开关：Task5。
- 隔离：Task6 + 已有 `test_isolation.py`。
- MAPE / 覆盖率：Task7。漂移再校准：Task8（合成，避免场景耦合）。
- 不引入 LLM、Guard、看板。
- 机理常数与模拟器分离，避免循环论证（ADR-003）。
