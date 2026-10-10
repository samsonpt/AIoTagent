# M6b 孪生看板设计规格

**日期：** 2026-10-10  
**状态：** 已批准  
**父规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.3、§4.7  
**相关：** M3 孪生；M6 看板（孪生页原为骨架）；`docs/superpowers/specs/2026-10-09-m6-dashboard-design.md`

## 1. 目标

把看板「孪生对比」从骨架做成可用视图：预测 vs 实测、残差、滚动 MAPE、孪生置信度、Guard 关 2 前瞻门控；与 `demo_live` 共享 SQLite，边跑边看。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 范围 | 看板全量愿景（对比 + 门控 + 批次筛选 + 滚动 MAPE + 前瞻面板） |
| 持久化 | 专用表 `twin_observation` + `twin_gate_log` |
| 实现路径 | TraceStore 扩表；TwinService / Guard 写入；DashboardStore 读取；重写 `ui/pages/twin.py` |
| 隔离 | `ui/` 不 import `twin` / `sim` |

## 3. 数据模型与写路径

### 3.1 表

**`twin_observation`**（对应 `TwinObservation`）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| t | REAL | 仿真时间 |
| tick | INTEGER | |
| kind | TEXT | `thickness` / `width` 等 |
| y | REAL | 实测 |
| yhat | REAL | 预测均值 |
| q05 | REAL | 区间下界 |
| q95 | REAL | 区间上界 |
| lot_id | TEXT | 可空 |

**`twin_gate_log`**（Guard 关 2）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK AUTOINCREMENT | |
| t | REAL | |
| tick | INTEGER | |
| process | TEXT | |
| command | TEXT | |
| kind | TEXT | 可空（map skip 时） |
| confidence | REAL | 可空 |
| yield_prob | REAL | 可空 |
| oos_prob | REAL | 可空 |
| passed | INTEGER | 0/1；未 simulate 时按语义记 0 或省略含义见 reason |
| reason | TEXT | 如 `passed` / `gate_fail` / `confidence_low` / `twin_disabled` / `no_twin` / `map_skip` |
| lot_id | TEXT | 可空 |
| detail | TEXT | JSON |

埋点约定：字段只新增；`CREATE TABLE IF NOT EXISTS`；旧库打开即建表。

### 3.2 API（TraceStore）

- `record_twin_observation(...)` / `twin_observations(*, kind=None, lot_id=None, limit=None) -> list[...]`
- `record_twin_gate(...)` / `twin_gates(*, passed=None, limit=None) -> list[...]`

### 3.3 写路径

1. `TwinService(..., store: TraceStore | None = None)`：`_observe` 在更新内存 `predictions` 后，若 `store` 非空则 `record_twin_observation`。
2. `sim.runner.run` 创建 `TwinService` 时传入与 Guard 相同的 `store`。
3. Guard 关 2：在 `simulate` / 置信度判断 / map skip / twin 不可用等分支写入 `twin_gate_log`（每条被评估的命令一条）。
4. 无 twin 或 `use_twin_lookahead=false` 时仍可写 reason=`twin_disabled`/`no_twin`（便于面板解释），observation 表可为空。

### 3.4 读路径

`DashboardStore` 薄封装上述查询；页面只依赖 `DashboardStore`。

## 4. 孪生页 UI

**文件：** `ui/pages/twin.py`（替换骨架）

| 区块 | 行为 |
|------|------|
| 筛选 | `kind`（全部 / thickness / width）；`lot_id` 下拉（来自 observation） |
| 预测 vs 实测 | 按 `t`：`y`、`yhat`、`q05`、`q95` 折线 |
| 残差 | `y − yhat` 表 + 简图 |
| 滚动 MAPE | 窗口 N 默认 20（slider）；分 kind 显示 % |
| 置信度 | 由 observation 滚动 90% 区间覆盖率估算；标出 0.5 阈值 |
| 前瞻门控 | `twin_gate_log` 表；可按 `passed` 过滤 |

空库提示：先运行 `demo_live`（默认 ablation 开启孪生前瞻且 `twin_fidelity≠none`）。

**非目标：** UI 触发 simulate；UI 下发工艺命令。

## 5. 测试与验收

### 5.1 测试

- schema 读写 twin 两表
- TwinService 带 store 落库；无 store 时行为与现网一致
- Guard 关 2 写 gate 日志（含 skip / confidence_low / passed）
- UI 辅助函数（残差、滚动 MAPE）单测；isolation 保持

### 5.2 冒烟

```text
python scripts/demo_live.py --db runs/demo_twin.db --ticks 24 --overwrite --no-cloud
python -m streamlit run ui/app.py -- --db runs/demo_twin.db
```

### 5.3 验收

1. 父规格 §4.7「孪生预测与实测对比」可用（非骨架文案）
2. 边跑边看：刷新可见新 observation / gate
3. 筛选、滚动 MAPE、置信度、前瞻面板可用

### 5.4 非目标

- 矩阵 / `compute_all` 接入 `twin_preds`（评测闭环另开）
- 修改机理模型或 Guard 数值阈值

## 6. 风险

| 风险 | 缓解 |
|------|------|
| `--no-cloud` 仍可能少 observation | 依赖测量通道与 TwinService.on_tick；冒烟用足够 ticks |
| gate 日志过密 | UI `limit` + 表分页/尾部截断 |
| 置信度与 Guard 瞬时值略有偏差 | 页面标明「由落库 observation 滚动估计」 |
