# Task 4 Report: ActionGuard 核心（关 1/2/3 + 快通 + 链 act）

## Status
**DONE** — `guard/action_guard.py` + `tests/guard/test_action_guard.py`；未接线 runner/HumanModel（Task 5）；`on_tick` 为空（链降级归 Task 6）；保留 `auto_actions_enabled` 并在非快通路径检查。设计 §4.3：无孪生 / `use_twin_lookahead=False` 时非快通不得自动盖章。

## Commits
- `fe79431` feat: ActionGuard 三道关与快速通道
- fix: 无孪生时非快通转审批并补 Guard 测试

## Test summary
```
python -m pytest tests/guard/test_action_guard.py -q
# 6 passed
```

Covered:
- `stop` 快通盖章后 Plant 执行（`drill.stopped`）+ `verify_chain` OK
- 未知命令 `levitate` → `accepted=False`，工站不变
- `use_human_gate=True` 时 `hold_lot` 入 `approval_queue`、不转发
- `twin=None` + human gate：`set_current_density` 入队、不盖章
- `auto_actions_enabled=False`：非快通 reject；`stop` 仍快通盖章
- `resolve_approval`：批准带 `guard_id`；拒绝写 `accepted=False`

## Self-review concerns
1. **双写 action_log**：Guard `_stamp_forward` 与 Plant `_execute` 成功路径各记一条；测试用 `any(...)`，指标聚合时需去重或按 `guard_reason` 区分。
2. **入队不写链**：`_enqueue` 仅写 `approval_queue`；`act` 链在批准盖章或拒绝时才写（依赖 Task 5 `resolve_approval`）。
3. **无 twin / twin_disabled**：非快通 → human gate 入队，否则 `_reject(no_twin|twin_disabled)`；有 twin 且 mapping 为 None 仍 warning 跳过关 2。
4. **伪造 `guarded:True`**：MVP 一律忽略（防环），不写拒绝；Plant 靠 `guard_id=="guard"` 挡未盖章。

## Compliance checklist
- [x] 盖章：`guarded=True`, `guard_id="guard"`, `guard_reason=str`
- [x] `guarded is True` → return（防环）
- [x] `client_id="guard"`；订阅 `plant/+/+/command` + `line_command`
- [x] 不 import `sim`；本地 `COMMAND_CATEGORY` 副本
- [x] `AblationConfig` from `common.config`；`bound_command` from `edge.envelope`
- [x] 快通不受 `auto_actions_enabled=False` 限制；非快通则 reject `chain_invalid_auto_disabled`
- [x] §4.3 无孪生/关闭 lookahead：非快通不自动盖章（入队或 reject）

## Files
- `guard/action_guard.py` — ActionGuard（快通 / 三关 / 盖章 / 拒绝 / 入队 / resolve_approval）
- `tests/guard/test_action_guard.py` — 快通、包络拒绝、hold 入队、无孪生入队、auto 禁用、resolve_approval

## Review fix (Important §4.3)
- Gate 2：`twin is None` 或 `use_twin_lookahead=False` → 非快通不自动盖章；`use_human_gate` 入队，否则 `_reject(no_twin|twin_disabled)`。
- 有 twin 且 `map_command_to_twin` 为 None：保持 warning 跳过关 2。
- 有 twin 且已映射：保持 yield/oos；失败走 needs_approval。
- 新增测试 3 条；`tests/guard/test_action_guard.py`：**6 passed**。
