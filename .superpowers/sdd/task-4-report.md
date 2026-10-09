# Task 4 Report: ActionGuard 核心（关 1/2/3 + 快通 + 链 act）

## Status
**DONE** — `guard/action_guard.py` + `tests/guard/test_action_guard.py`；未接线 runner/HumanModel（Task 5）；`on_tick` 为空（链降级归 Task 6）；保留 `auto_actions_enabled` 并在非快通路径检查。设计 §4.3：无孪生 / `use_twin_lookahead=False` 时非快通不得自动盖章。

## Commits
- `fe79431` feat: ActionGuard 三道关与快速通道
- fix: 无孪生时非快通转审批并补 Guard 测试
- `69d9d5e` fix: 无孪生时高风险命令勿调用 simulate

## Test summary
```
python -m pytest tests/guard/ -q
# 37 passed
```

Covered:
- `stop` 快通盖章后 Plant 执行（`drill.stopped`）+ `verify_chain` OK
- 未知命令 `levitate` → `accepted=False`，工站不变
- `use_human_gate=True` 时 `hold_lot` 入 `approval_queue`、不转发
- `twin=None` + human gate：`set_current_density` 入队、不盖章
- `auto_actions_enabled=False`：非快通 reject；`stop` 仍快通盖章
- `resolve_approval`：批准带 `guard_id`；拒绝写 `accepted=False`
- `use_twin_lookahead=False, use_human_gate=False` + 高风险 asd → `high_risk_auto` stamp（不 simulate）
- `twin=None` + human gate + 高风险 asd → enqueue（不抛异常）

## Self-review concerns
1. **双写 action_log**：Guard `_stamp_forward` 与 Plant `_execute` 成功路径各记一条；测试用 `any(...)`，指标聚合时需去重或按 `guard_reason` 区分。
2. **入队不写链**：`_enqueue` 仅写 `approval_queue`；`act` 链在批准盖章或拒绝时才写（依赖 Task 5 `resolve_approval`）。
3. **无 twin / twin_disabled**：非快通 → human gate 入队；否则非高风险 `_reject(no_twin|twin_disabled)`，高风险交 Gate 3 `high_risk_auto`；有 twin 且 mapping 为 None 仍 warning 跳过关 2。
4. **伪造 `guarded:True`**：MVP 一律忽略（防环），不写拒绝；Plant 靠 `guard_id=="guard"` 挡未盖章。

## Compliance checklist
- [x] 盖章：`guarded=True`, `guard_id="guard"`, `guard_reason=str`
- [x] `guarded is True` → return（防环）
- [x] `client_id="guard"`；订阅 `plant/+/+/command` + `line_command`
- [x] 不 import `sim`；本地 `COMMAND_CATEGORY` 副本
- [x] `AblationConfig` from `common.config`；`bound_command` from `edge.envelope`
- [x] 快通不受 `auto_actions_enabled=False` 限制；非快通则 reject `chain_invalid_auto_disabled`
- [x] §4.3 无孪生/关闭 lookahead：不调用 simulate；human gate 入队，否则非高风险 reject / 高风险 Gate 3 auto-stamp

## Files
- `guard/action_guard.py` — ActionGuard（快通 / 三关 / 盖章 / 拒绝 / 入队 / resolve_approval）
- `tests/guard/test_action_guard.py` — 快通、包络拒绝、hold 入队、无孪生入队、高风险无孪生、auto 禁用、resolve_approval

## Critical fix: 无孪生时高风险勿调用 simulate
- **Bug**：Gate 2 仅对非高风险 early-return；高风险落穿到 `self._twin.simulate` → `twin is None` 时 AttributeError。
- **Fix**：`twin_ready = use_twin_lookahead and twin is not None`；非 ready 时不 simulate——`use_human_gate` → `needs_approval`；否则非高风险 reject、高风险交 Gate 3 `high_risk_auto`。
- **Tests**：`test_twin_lookahead_off_high_risk_stamps_without_simulate`；`test_no_twin_high_risk_param_tune_enqueues_with_human_gate`。
- `python -m pytest tests/guard/ -q` → **37 passed**。
- Commit message: fix: 无孪生时高风险命令勿调用 simulate
