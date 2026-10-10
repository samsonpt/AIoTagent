# M6b 孪生看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将看板孪生页从骨架做成可用视图：TraceStore 落库 observation/gate，DashboardStore 读取，完整对比/残差/滚动 MAPE/置信度/前瞻面板，支持 demo_live 边跑边看。

**Architecture:** `TwinService._observe` 与 Guard 关 2 写入 SQLite 新表；`ui/` 只经 `DashboardStore` 读库，不 import `twin`/`sim`。页面纯函数计算残差与滚动 MAPE，便于单测。

**Tech Stack:** Python 3.12, SQLite/TraceStore, Streamlit, pytest。

**规格：** `docs/superpowers/specs/2026-10-10-m6b-twin-dashboard-design.md`；父规格 §4.3、§4.7；相关 M6 看板规格。

## Global Constraints

- Python 3.12；`pytest`；随机经 `common.rng.make_rng`。
- 仿真时间只用 `SimClock`。
- 标识符英文；文档/日志/UI 文案中文。
- 埋点字段只新增不改语义；`CREATE TABLE IF NOT EXISTS`。
- `twin/`、`edge/`、`cloud/`、`guard/`、`ui/` 禁止 import `sim`。
- `ui/` 禁止 import `twin`（只读 SQLite）。
- JSON：`json.dumps(..., sort_keys=True, ensure_ascii=False)`。
- 测试：`tests/<包>/test_*.py`；`--import-mode=importlib`，`pythonpath=["."]`。
- 不写解释性注释。

## File Structure

```
bench/schema.py              # TwinObservationRecord / TwinGateRecord + DDL + API
twin/service.py              # store 可选参数；_observe 落库
sim/runner.py                # TwinService(..., store=store)
guard/action_guard.py        # 关 2 写 twin_gate_log
ui/db.py                     # twin_observations / twin_gates
ui/pages/twin.py             # 完整页（替换骨架）
tests/bench/test_schema.py   # 扩
tests/twin/test_observe_store.py
tests/guard/test_twin_gate_log.py
tests/ui/test_twin_page.py
```

## 数值与语义约定

- `twin_observation.kind`：`thickness` / `width` / `roughness`（与 TwinService 一致）。
- `twin_gate_log.passed`：INTEGER 0/1；未做 simulate 时：`passed=0`，`reason` 说明（`map_skip` / `twin_disabled` / `no_twin` / `confidence_low`）；simulate 后通过 `passed=1, reason=passed`，未通过 `passed=0, reason=gate_fail`。
- 滚动 MAPE：窗口内 `mean(|y-yhat|/|y|)*100`，`|y|<1e-9` 的点跳过；窗口内有效点为 0 → `nan`。
- 置信度展示：窗口内 `(q05≤y≤q95)` 比例，与 `twin.calibration.twin_confidence` 同源思路；页面标注「由落库 observation 滚动估计」。
- `_ORDER`：observation / gate 按 `t, id`。

---

### Task 1: TraceStore 孪生两表与 API

**Files:**
- Modify: `bench/schema.py`
- Modify: `tests/bench/test_schema.py`

**Interfaces:**
- `@dataclass(frozen=True) class TwinObservationRecord:` 字段 `t, tick, kind, y, yhat, q05, q95, lot_id: str | None = None, id: int | None = None`
- `@dataclass(frozen=True) class TwinGateRecord:` 字段 `t, tick, process, command, kind: str | None, confidence: float | None, yield_prob: float | None, oos_prob: float | None, passed: bool, reason: str, lot_id: str | None = None, detail: dict = field(default_factory=dict), id: int | None = None`
- DDL 加入 `_SCHEMA`；`_ORDER`、`_JSON_FIELDS`（`detail`）更新
- `record_twin_observation(self, rec: TwinObservationRecord) -> int`
- `twin_observations(self, *, kind: str | None = None, lot_id: str | None = None) -> list[TwinObservationRecord]`
- `record_twin_gate(self, rec: TwinGateRecord) -> int`
- `twin_gates(self, *, passed: bool | None = None) -> list[TwinGateRecord]`

- [ ] **Step 1: 失败测试**

```python
def test_twin_observation_and_gate_roundtrip():
    with TraceStore() as store:
        oid = store.record_twin_observation(
            TwinObservationRecord(
                t=1.0, tick=1, kind="thickness", y=25.0, yhat=24.5, q05=23.0, q95=26.0, lot_id="L1",
            )
        )
        assert oid >= 1
        rows = store.twin_observations(kind="thickness", lot_id="L1")
        assert len(rows) == 1 and rows[0].y == 25.0 and rows[0].yhat == 24.5
        gid = store.record_twin_gate(
            TwinGateRecord(
                t=2.0, tick=2, process="plating", command="set_asd", kind="thickness",
                confidence=0.8, yield_prob=0.9, oos_prob=0.1, passed=True, reason="passed",
                lot_id="L1", detail={"asd": 2.0},
            )
        )
        assert gid >= 1
        gates = store.twin_gates(passed=True)
        assert len(gates) == 1 and gates[0].reason == "passed"
```

- [ ] **Step 2:** `python -m pytest tests/bench/test_schema.py::test_twin_observation_and_gate_roundtrip -q` → FAIL

- [ ] **Step 3:** 实现 DDL + dataclass + CRUD（`passed` 存 0/1；读出转 bool）

- [ ] **Step 4:** 全文件 `tests/bench/test_schema.py` PASS

- [ ] **Step 5: Commit** `feat: twin_observation 与 twin_gate_log 表`

---

### Task 2: TwinService 落库 + runner 注入 store

**Files:**
- Modify: `twin/service.py`
- Modify: `sim/runner.py`
- Create: `tests/twin/test_observe_store.py`

**Interfaces:**
- `TwinService.__init__(..., store=None)`：保存 `self.store = store`
- `_observe`：现有 `self.predictions.append(obs)` 之后：

```python
if self.store is not None:
    from bench.schema import TwinObservationRecord
    self.store.record_twin_observation(
        TwinObservationRecord(
            t=obs.t, tick=obs.tick, kind=obs.kind, y=obs.y, yhat=obs.yhat,
            q05=obs.q05, q95=obs.q95, lot_id=obs.lot_id,
        )
    )
```

（`twin`→`bench` 允许；禁止 `sim`。）

- `sim/runner.py`：`TwinService(..., seed=scenario.seed, store=store)`

- [ ] **Step 1: 失败测试** — 构造最小 TwinService（InMemoryBus + recipe + clock + TraceStore），触发一次会 `_observe` 的路径（复用 `tests/twin/test_observe.py` 模式），断言 `store.twin_observations()` 非空且与 `predictions[-1]` 一致；`store=None` 时不抛错。

- [ ] **Step 2–4:** TDD 实现；既有 twin 测试仍绿

- [ ] **Step 5: Commit** `feat: TwinService 观测写入 TraceStore`

---

### Task 3: Guard 关 2 写 twin_gate_log

**Files:**
- Modify: `guard/action_guard.py`
- Create: `tests/guard/test_twin_gate_log.py`

**Interfaces:**
- 私有方法 `_log_twin_gate(self, *, process, command, kind, confidence, yield_prob, oos_prob, passed, reason, lot_id, detail=None) -> None`：构造 `TwinGateRecord`（`t=clock.now`, `tick=clock.tick`）并 `store.record_twin_gate`
- 关 2 各分支调用：
  - `twin_disabled` / `no_twin`：`passed=False`，confidence/probs=None，kind=None
  - `map_skip`：同上 + command/process
  - `confidence_low`：写入 confidence，probs=None，`passed=False`（在决定 needs_approval 时）
  - simulate 后：`passed=passes_twin_gate(...)`，reason `passed` 或 `gate_fail`，填 yield/oos

- [ ] **Step 1: 失败测试** — 用既有 Guard 测试夹具发一条可 map 的命令（或 map skip 的 maintenance），断言 `store.twin_gates()` 至少 1 条且 `reason` 符合预期。

- [ ] **Step 2–4:** 实现；`tests/guard/` 相关仍绿

- [ ] **Step 5: Commit** `feat: Guard 关 2 写入 twin_gate_log`

---

### Task 4: DashboardStore + 页面纯函数

**Files:**
- Modify: `ui/db.py`
- Create: `tests/ui/test_twin_page.py`（先测纯函数与 db 封装）

**Interfaces:**
- `DashboardStore.twin_observations(self, *, kind=None, lot_id=None, limit=500) -> list`
- `DashboardStore.twin_gates(self, *, passed=None, limit=500) -> list`
- 在 `ui/pages/twin.py`（或同文件顶部可测函数）：

```python
def residual_series(rows: list) -> list[tuple[float, float]]:
    """(t, y - yhat) for each row with numeric y/yhat."""

def rolling_mape(rows: list, window: int) -> float:
    """MAPE% over last `window` points; nan if none valid."""

def rolling_coverage(rows: list, window: int) -> float:
    """Fraction with q05 <= y <= q95 over last window; nan if empty."""
```

`rows` 为 `TwinObservationRecord` 或 duck-type 属性对象。

- [ ] **Step 1:**

```python
def test_rolling_mape_and_coverage():
    from ui.pages.twin import rolling_mape, rolling_coverage, residual_series
    from bench.schema import TwinObservationRecord
    rows = [
        TwinObservationRecord(t=float(i), tick=i, kind="thickness", y=10.0, yhat=9.0, q05=8.0, q95=12.0)
        for i in range(5)
    ]
    assert residual_series(rows)[0] == (0.0, 1.0)
    assert rolling_mape(rows, 5) == pytest.approx(10.0)
    assert rolling_coverage(rows, 5) == pytest.approx(1.0)
```

- [ ] **Step 2–4:** 实现 db 方法 + 纯函数（页面 `render` 可仍为骨架，本任务以函数与 db 为准）

- [ ] **Step 5: Commit** `feat: 孪生页数据层与 MAPE/覆盖率辅助函数`

---

### Task 5: 完整 `ui/pages/twin.py` 页面

**Files:**
- Modify: `ui/pages/twin.py`
- Modify: `tests/ui/test_twin_page.py`（可选：对筛选逻辑抽 `filter_observations` 单测）

**Interfaces — `render(store)` 区块顺序：**
1. 标题「孪生对比」；空 observation 且空 gate → `st.info` 提示跑 demo_live
2. 筛选：`kind` selectbox；`lot_id` selectbox（含「全部」）
3. 折线：DataFrame 列 `t,y,yhat,q05,q95` → `st.line_chart`
4. 残差表/图
5. slider `N` 默认 20 → 显示滚动 MAPE% 与覆盖率（置信度），标注阈值 0.5
6. 前瞻门控：`st.dataframe`；checkbox「仅未通过」→ `passed=False` 过滤

- [ ] **Step 1:** 手测或轻量测试：`filter` 辅助若抽出则单测；否则实现后 `python -c "from ui.pages.twin import render"` 可导入

- [ ] **Step 2–3:** 实现完整 UI；删除骨架占位长文案

- [ ] **Step 4:** `python -m pytest tests/ui/ tests/test_isolation.py -q` PASS

- [ ] **Step 5: Commit** `feat: 孪生对比看板完整页`

---

### Task 6: 联调冒烟与规格勾选

**Files:**
- 可选：`docs/superpowers/specs/2026-10-10-m6b-twin-dashboard-design.md` 状态保持「已批准」；若实现偏差记入文末

- [ ] **Step 1: 冒烟**

```text
python scripts/demo_live.py --db runs/demo_twin.db --ticks 24 --overwrite --no-cloud
```

用 Python 断言库内有 observation 或 gate：

```text
python -c "from bench.schema import TraceStore; s=TraceStore('runs/demo_twin.db'); print(len(s.twin_observations()), len(s.twin_gates())); s.close()"
```

Expected：至少一侧非 0（若 observation 为 0，检查 TwinService 是否订阅到测量；gate 在有命令时应 >0）。

- [ ] **Step 2:** `python -m pytest tests/bench/test_schema.py tests/twin/test_observe_store.py tests/guard/test_twin_gate_log.py tests/ui/test_twin_page.py tests/test_isolation.py -q` PASS

- [ ] **Step 3: Commit**（若有文档微调）`test: M6b 孪生看板冒烟验收` 或仅确认无未提交变更

---

## Spec coverage（自检）

| 规格项 | Task |
|--------|------|
| twin_observation / twin_gate_log | 1 |
| TwinService + runner store | 2 |
| Guard 关 2 日志 | 3 |
| DashboardStore + MAPE/覆盖率 | 4 |
| 完整 UI 区块 | 5 |
| 冒烟边跑边看 | 6 |
| ui 不 import twin/sim | 5 isolation |
| 非目标：矩阵 twin_preds | 不做 |

## Placeholder scan

无 TBD；接口与 reason 枚举已钉死。
