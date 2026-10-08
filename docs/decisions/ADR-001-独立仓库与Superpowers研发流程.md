# ADR-001: 独立仓库与 Superpowers 研发流程

## Status
Accepted

## Date
2026-10-08

## Context
- `AIoTagent` 最初位于 `D:/workspace` 仓库内，处于未跟踪状态；父仓库混合了多个不相关项目（PCB 知识库问答、A2A、Agents 等）。
- 项目计划用于论文实验，需要清晰的提交历史与可复现的实现过程。
- 开发由 AI 编码智能体完成，需要一套能约束智能体“先规格、再计划、TDD、评审”的流程。

## Decision
- 在 `AIoTagent` 内初始化独立 Git 仓库，`main` 只接收评审通过的合并。
- 主流程采用 Superpowers：设计规格 → 里程碑实施计划 → worktree 隔离分支 → 子智能体逐任务 TDD 实现与评审 → 终审、验证、合并。
- 从 addyosmani/agent-skills（MIT）只借用 `documentation-and-adrs` 与 `security-and-hardening` 两个技能。

## Alternatives Considered

### 放在父仓库中开分支
- Pros：不新增仓库。
- Cons：历史与无关项目混杂，难以单独开源，论文复现链接不清晰。
- Rejected。

### 整套采用 agent-skills 流程
- Pros：同样覆盖规格、计划、测试、评审、发布。
- Cons：与 Superpowers 定位重合，两套规格与计划格式会冲突，子智能体得到矛盾指令。
- Rejected：只借用 Superpowers 未覆盖的 ADR 与安全加固。

## Consequences
- 父仓库仍会把 `AIoTagent/` 显示为未跟踪目录，是否在父仓库 `.gitignore` 中忽略需由用户决定。
- 每个里程碑都有规格、计划、评审记录，可作为论文“实现”一节的依据。
