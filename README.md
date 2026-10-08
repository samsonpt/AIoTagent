# PCB 产线自主工艺与运维智能体（AIoT Agentic MVP）

以 PCB 制板厂（钻孔、电镀、蚀刻、AOI）为场景，落地论文
《Artificial Intelligence of Things as a Foundation for Agentic AI Systems: Architectures, Applications, and Challenges》
（[doc/](doc/)，DOI: 10.36227/techrxiv.176972132.21250188/v1）中的云-边混合架构、OODA 认知闭环、
数字孪生前瞻仿真与运行时安全保障，并提供可复现的消融评测。

## 快速开始

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

## 目录

| 目录 | 职责 |
|------|------|
| `common/` | 配置、消融开关、仿真时钟、配方与工艺窗口、消息总线、意图协议 |
| `sim/` | 产线模拟器（扮演“现实”）与测量通道 |
| `edge/` | 工序边缘智能体（反射层） |
| `twin/` | 工艺数字孪生（智能体的世界模型） |
| `cloud/` | 云端多智能体（反思层）与知识库 |
| `guard/` | 动作守卫、人工审批、批次哈希链追溯 |
| `ui/` | 看板 |
| `bench/` | 埋点 schema、指标、场景、消融运行器 |
| `docs/` | 设计规格、实施计划、架构决策记录（ADR） |

## 文档

- 设计规格：`docs/superpowers/specs/`
- 实施计划：`docs/superpowers/plans/`
- 架构决策记录：`docs/decisions/`

## 研发流程

采用 Superpowers 流程：设计规格 → 里程碑实施计划 → worktree 隔离分支 → 子智能体逐任务 TDD 实现与评审 → 终审、验证、合并。
另借用 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)（MIT）中的
`documentation-and-adrs` 与 `security-and-hardening` 两个技能，见 `.cursor/skills/`。
