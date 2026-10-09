# M6 Streamlit 看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现共享 SQLite（WAL）的 Streamlit 看板：核心三页（审批写回、追溯、运行监视）+ 骨架页；UI 审批经库决策桥由 Guard 盖章；演示关闭 HumanModel 自动审批。

**Architecture:** UI 进程只读写 SQLite；批准/驳回写入 `approval_queue`（`decider=ui`, `applied=0`）。Runner 内 `ActionGuard.on_tick` 扫描未应用决策并 `resolve_approval`，成功后 `applied=1`。`ui/` 禁止 import `sim`。

**Tech Stack:** Python 3.12, Streamlit, sqlite3/TraceStore, pytest。

**规格：** `docs/superpowers/specs/2026-10-09-m6-dashboard-design.md`；父规格 §4.7、§10 M6。

## Global Constraints

- Python 3.12；依赖用 `pyproject.toml` 管理；测试用 `pytest`。
- 所有随机过程接受 `seed`；同一 seed 必须得到逐位一致的结果。随机数一律通过 `common.rng.make_rng(seed, stream)` 获取。
- 消息总线抽象为 `Bus` 接口；单元测试不依赖 Mosquitto。
- 仿真时间由 `SimClock` 驱动。禁止使用 `time.time()` / `datetime.now()` 作为仿真时间（UI 显示可用挂钟，写入 `t_decide` 时优先用库内仿真时间字段或侧栏注入的 `now`；决策桥测试用显式 `t_decide`）。
- 代码标识符用英文；文档和日志说明用中文。
- 四类埋点字段只能新增，不能修改语义。
- `twin/`、`edge/`、`cloud/`、`guard/`、`ui/` 不允许 import `sim` 包的任何模块。
- JSON 序列化：`json.dumps(obj, sort_keys=True, ensure_ascii=False)`。
- 测试放在 `tests/<包名>/test_*.py`；`--import-mode=importlib`，`pythonpath = ["."]`。
- 不写解释“这行做什么”的注释。
- 看板不从 UI 下发普通工艺命令（仅审批写回）。

## File Structure

```
ui/__init__.py
ui/app.py                 # 入口：侧栏选库 + 页切换 + 顶栏链告警
ui/db.py                  # DashboardStore：读视图 / decide_approval
ui/pages/approvals.py
ui/pages/trace.py
ui/pages/monitor.py
ui/pages/aoi.py           # 骨架
ui/pages/episodes.py      # 骨架
ui/pages/twin.py          # 骨架
bench/schema.py           # applied 列 + API
guard/action_guard.py     # on_tick 应用未落地决策
bench/human_model.py      # auto_approve
edge/factory.py           # 传入 auto_approve
sim/runner.py             # auto_approve 参数（默认 False 当演示；测试默认 True 保持 M5 行为）
scripts/demo_live.py
tests/ui/test_db.py
tests/ui/test_bridge.py
tests/ui/test_security_ui.py
tests/test_isolation.py   # ISOLATED_PACKAGES 加 ui
pyproject.toml
```

## 数值与语义约定

- `approval_queue.applied`：INTEGER NOT NULL DEFAULT 0；`1` 表示已由 Guard `resolve_approval` 执行盖章/拒绝落库副作用。
- UI 决策：`status∈{approved,rejected}`，`decider="ui"`，`applied` 保持 0 直至 Guard 应用。
- `ActionGuard.on_tick` 顺序：① `verify_chain` 降级（既有）；② `list_unapplied_decisions()` → 逐条 `resolve_approval` → `mark_approval_applied`。
- `resolve_approval` 成功后必须 `mark_approval_applied`（HumanModel 与 UI 桥共用，防双执行）。
- `HumanModel(auto_approve: bool = True)`：`False` 时跳过 `_process_approvals`。演示/`demo_live`/`runner(..., auto_approve=False)` 默认关自动批；**既有单元测试保持 `auto_approve=True`（默认）** 以免破坏 M5。
- `ui/db.DashboardStore(path)`：内部可用 `TraceStore` 或只读 sqlite3；写审批用 `decide_approval(request_id, approved, *, t_decide, reason="")`。
- Streamlit 页切换：侧栏 `radio`，不依赖 `pages/` 自动发现（便于 `--db` 传参）。
- 刷新：`st.rerun` 或 `time.sleep`+fragment 可选；MVP 用侧栏「刷新」按钮 + `st.rerun`，可选 `st.fragment` 自动每 2s（若 Streamlit 版本支持）。
- 顶栏：`verify_chain()` 失败 → `st.error`。

---

### Task 1: approval_queue.applied 与查询 API

**Files:**
- Modify: `bench/schema.py`
- Test: `tests/bench/test_schema.py`

**Interfaces:**
- DDL：`approval_queue` 增加 `applied INTEGER NOT NULL DEFAULT 0`
- `__init__`：`executescript` 后执行 `_ensure_approval_applied_column()`（`ALTER TABLE ... ADD COLUMN applied INTEGER NOT NULL DEFAULT 0`，忽略「duplicate column」）
- `enqueue_approval` INSERT 含 `applied=0`
- `list_approvals` 返回含 `applied`（int/bool 均可，钉死 int 0/1）
- `list_unapplied_decisions(self) -> list[dict]`：`status IN ('approved','rejected') AND applied=0`
- `mark_approval_applied(self, request_id: str) -> None`：设 `applied=1`
- `update_approval` 可增加可选 `applied=`；或只用 `mark_approval_applied`

- [ ] **Step 1: 失败测试**

```python
def test_unapplied_decisions_and_mark():
    store = TraceStore()
    rid = store.enqueue_approval(
        t_submit=0.0, process="line", equipment="line", command="hold_lot",
        params={"lot_id": "L1"}, source="edge", lot_id="L1", topic="plant/line/command",
    )
    store.update_approval(rid, status="approved", t_decide=1.0, decider="ui", reason="ok")
    rows = store.list_unapplied_decisions()
    assert len(rows) == 1 and rows[0]["request_id"] == rid and rows[0]["applied"] == 0
    store.mark_approval_applied(rid)
    assert store.list_unapplied_decisions() == []
```

- [ ] **Step 2:** `python -m pytest tests/bench/test_schema.py::test_unapplied_decisions_and_mark -q` → FAIL
- [ ] **Step 3:** 实现 DDL + API；旧测试仍绿
- [ ] **Step 4:** 全文件 `tests/bench/test_schema.py` 通过
- [ ] **Step 5:** 提交 `feat: approval_queue.applied 与未应用决策查询`

---

### Task 2: Guard 应用 UI 决策 + HumanModel.auto_approve

**Files:**
- Modify: `guard/action_guard.py`, `bench/human_model.py`, `edge/factory.py`, `sim/runner.py`（可选 kw）
- Test: `tests/ui/test_bridge.py`（或 `tests/guard/test_ui_bridge.py`）

**Interfaces:**
- `ActionGuard.on_tick`：在链校验后：

```python
for row in self._store.list_unapplied_decisions():
    approved = row["status"] == "approved"
    self.resolve_approval(row["request_id"], approved, row.get("reason") or row["status"])
    self._store.mark_approval_applied(row["request_id"])
```

- `resolve_approval` 末尾也可调用 `mark_approval_applied`（若已标记则幂等 UPDATE）；**钉死：只在 on_tick 循环末 `mark`，且 `resolve_approval` 内也 `mark`，保证 HumanModel 路径不残留 applied=0**。即 `resolve_approval` 成功路径末尾 `mark_approval_applied`；on_tick 循环可只 `resolve_approval`（mark 在其内）。
- `HumanModel(..., auto_approve: bool = True)`；`_process_approvals` 首行：`if not self.auto_approve: return`
- `make_controllers(..., auto_approve: bool = True)` 传给 HumanModel
- `run(..., auto_approve: bool = True)` 默认 True（兼容 M5 测试）；`demo_live` 传 `False`

- [ ] **Step 1:**

```python
def test_ui_decision_applied_on_guard_tick():
    # Plant+Guard+store；enqueue via guard path OR store.enqueue + 手动 pending
    # UI: update_approval approved decider=ui
    # guard.on_tick → list_unapplied empty；actions 含盖章或 reject 记录
```

- [ ] **Step 2–4:** 另测 `auto_approve=False` 时 HumanModel 不自动批；`auto_approve=True` 既有审批测仍过
- [ ] **Step 5:** 提交 `feat: Guard 应用 UI 审批决策与 auto_approve`

---

### Task 3: ui.db DashboardStore

**Files:**
- Create: `ui/__init__.py`, `ui/db.py`
- Modify: `pyproject.toml`（可本任务或 Task 5 加 streamlit；本任务可不加）
- Test: `tests/ui/test_db.py`

**Interfaces:**
- `class DashboardStore:`
  - `__init__(self, path: str | Path)`
  - `verify_chain(self, lot_id: str | None = None) -> tuple[bool, str]`
  - `list_approvals(self, status: str | None = None) -> list[dict]`
  - `decide_approval(self, request_id: str, approved: bool, *, t_decide: float, reason: str = "") -> None`  
    → `update_approval(..., status=approved|rejected, decider="ui", t_decide, reason)`，不改 `applied`
  - `telemetry(self, process: str | None = None, limit: int = 500) -> list[tuple]`
  - `chain_rows(self, lot_id: str) -> list[dict]`
  - `lot_ids(self) -> list[str]`
  - `panels(self) -> list` / `episodes(self) -> list`（骨架用）
  - `close()` / 上下文管理器可选

实现：委托 `TraceStore(path)`，勿复制 SQL。

- [ ] **Step 1:** 测 `decide_approval` 后 `list_unapplied_decisions` 非空且 `decider=="ui"`
- [ ] **Step 2–5:** 实现 → 提交 `feat: ui.db DashboardStore 数据层`

---

### Task 4: Streamlit 核心三页

**Files:**
- Create: `ui/app.py`, `ui/pages/approvals.py`, `ui/pages/trace.py`, `ui/pages/monitor.py`
- Modify: `pyproject.toml` 加 `streamlit>=1.32`
- Test: `tests/ui/test_pages_smoke.py` — 测纯函数（如 `approvals.render` 的数据准备）或 import 页面模块不崩；不强制启动 Streamlit server

**Interfaces:**
- `app.main()`：解析 `--db`（`sys.argv` 在 `--` 后）；侧栏页：审批 / 追溯 / 监视 / AOI / 推理链 / 孪生；顶栏链状态
- `approvals.render(store: DashboardStore)`：表格 + 批准/驳回；`t_decide` 用侧栏数字或 `max(telemetry.t, 0)` 回退 `0.0`（测试可注入）
- `trace.render(store)`：lot 选择、链表、`verify_chain` 结果
- `monitor.render(store)`：按 process 滤 telemetry，`st.line_chart` 或 DataFrame

- [ ] **Step 1:** `tests/ui/test_pages_smoke.py` import `ui.app` / 调用 `decide` 路径无 Streamlit 运行时（页面函数里对 `st` 的调用可用 `pytest.importorskip` 或把数据逻辑放 `ui/db` 已测、页面仅薄包装）
- [ ] **钉死测试策略：** 核心断言放在 `DashboardStore` 与 bridge；页面模块提供 `build_approval_table(rows) -> DataFrame` 纯函数并单测；`render` 内 `st.*` 不单测。
- [ ] **Step 5:** 提交 `feat: Streamlit 审批/追溯/监视核心页`

---

### Task 5: 骨架页 + demo_live + 隔离

**Files:**
- Create: `ui/pages/aoi.py`, `ui/pages/episodes.py`, `ui/pages/twin.py`, `scripts/demo_live.py`
- Modify: `tests/test_isolation.py` — `ISOLATED_PACKAGES` 含 `"ui"`
- Test: `tests/ui/test_security_ui.py`；`tests/ui/test_bridge.py` 补全

**Interfaces:**
- 骨架页：`st.dataframe` 展示 panels 缺陷摘要 / episodes / 占位 markdown
- `scripts/demo_live.py`：argparse `--db` `--ticks` `--scenario`；`TraceStore(db)` + `run(..., ablation=AblationConfig(), auto_approve=False, n_ticks=...)`；确保父目录存在
- 安全：`find_sim_imports` 含 ui；无 `exec`/`eval`

- [ ] **Step 1–4:** 实现与测试
- [ ] **Step 5:** 提交 `feat: 看板骨架页、demo_live 与 ui 隔离`

---

### Task 6: 端到端决策桥冒烟（无浏览器）

**Files:**
- Test: `tests/ui/test_bridge.py`（扩展）

**场景：**
1. 临时文件 DB + Plant + ActionGuard（`use_human_gate=True`, twin 可 None）
2. 发布需审批命令（如 `hold_lot`）→ pending
3. `DashboardStore.decide_approval(..., True)`
4. `guard.on_tick` → `applied=1`，有 stamp 或 accepted action
5. 篡改链 → `verify_chain` False → UI `verify_chain` API 同结果

- [ ] **Step 1–4:** 测试绿
- [ ] **Step 5:** 提交 `test: M6 决策桥端到端冒烟`

---

## Spec coverage（自审）

| 规格项 | 任务 |
|--------|------|
| applied + 决策桥 | T1, T2, T6 |
| HumanModel 演示关自动批 | T2, T5 |
| DashboardStore / 审批写回 | T3, T4 |
| 三核心页 | T4 |
| 骨架页 | T5 |
| 顶栏链告警 | T4 |
| demo_live | T5 |
| ui 无 sim | T5 |
| 不加 MQTT/下发命令 | 遵守 |

## Placeholder scan

无 TBD；Streamlit 页面测试策略已钉死为纯函数 + bridge E2E。
