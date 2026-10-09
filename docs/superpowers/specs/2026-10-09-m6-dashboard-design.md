# M6 Streamlit 看板设计规格

**日期：** 2026-10-09  
**状态：** 待审阅  
**父规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §4.7、§6、§10 M6  
**相关：** M5 `2026-10-09-m5-guard-trace-design.md`；ADR-006

## 1. 目标

实现边跑边看的 Streamlit 看板：共享 SQLite（WAL），核心三页可用，其余为导航骨架；人工审批经库决策桥由 Runner 侧 Guard 盖章执行。

验收对齐父规格 §10 M6（§4.7 所列视图「可用」= 核心页完整 + 骨架页可导航）。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 交互 | 只读视图 + 审批写回（不从看板下发普通工艺命令） |
| 联跑 | Runner 与 Streamlit 并行，共享同一 SQLite 文件（WAL） |
| HumanModel | 演示默认关闭自动审批，避免与 UI 抢批 |
| 视图范围 | 核心三页完整 + AOI / 推理链 / 孪生对比骨架 |
| 跨进程桥 | 方案 A：SQLite 决策桥（UI 写队列状态，Guard 每 tick 应用） |

## 3. 决策桥

1. UI 对 `pending` 批准/驳回：`update_approval` 设 `status=approved|rejected`，`decider="ui"`，`t_decide`，并将 `applied=0`（新增整型列，默认 0；HumanModel 路径在 `resolve_approval` 成功后同样置 `applied=1`）。
2. Runner：`ActionGuard.on_tick`（或等价薄桥）扫描 `status IN ('approved','rejected') AND applied=0`，调用 `resolve_approval(request_id, approved, reason)`，成功后 `applied=1`。
3. 演示配置下 HumanModel：`auto_approve=False`（或等价），不自动消费 pending；OCAP 行为可保留，由既有 `ocap` 开关控制。

## 4. 页面

**启动：** `streamlit run ui/app.py -- --db <path>`；侧栏可改库路径。`ui/` 禁止 import `sim`。

| 页 | 职责 |
|----|------|
| 审批 | 列表 pending / 近期；批准、驳回按钮 |
| 追溯 | 按 `lot_id` 查 `trace_chain` 与关联 action/episode；展示 `verify_chain` |
| 运行监视 | `telemetry` 时间序列曲线；工序筛选；轻量 SPC 点（有则绘，无则表） |
| AOI（骨架） | 缺陷表或占位说明 |
| 推理链（骨架） | `episode_log` 表 |
| 孪生（骨架） | 占位或可用预测摘要表 |

**顶栏：** 全局 `verify_chain()`；失败红色告警（Guard 降级逻辑仍在 Runner）。

**刷新：** Streamlit 约 1–2s 轮询只读查询。

## 5. 目录与接入

```
ui/__init__.py
ui/app.py
ui/db.py                 # TraceStore 或 sqlite 薄封装：读视图 + 写审批
ui/pages/...
bench/schema.py          # approval_queue.applied
guard/action_guard.py    # on_tick 应用 UI 决策
bench/human_model.py     # auto_approve
sim/runner.py            # 演示参数传入
scripts/demo_live.py
tests/ui/
pyproject.toml           # streamlit 依赖
```

## 6. 演示

1. `python scripts/demo_live.py --db runs/demo.db`（长跑或固定 ticks，可中断）
2. `streamlit run ui/app.py -- --db runs/demo.db`
3. 在审批页处理 pending，观察追溯/监视更新

## 7. 测试策略

- 单元：`ui/db` 写审批字段；Guard 扫描 `applied=0` 并盖章（可用 InMemory + 临时文件库）。
- 隔离：`ui/` 无 `sim` import（扩展 `tests/test_isolation.py` 或 `tests/ui/test_security_ui.py`）。
- 冒烟：决策桥端到端（无真实浏览器也可）。

## 8. 非目标

- MQTT / 同进程嵌入 Streamlit
- 看板下发 `set_*` / stop 等普通命令
- 精美可视化与产线级权限
- M7 消融矩阵报告
- 区块链
