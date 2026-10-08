# ADR-002: 云端智能体编排采用 LangGraph

## Status
Accepted

## Date
2026-10-08

## Context
- 云端需要多智能体编排（Supervisor、质量根因、工艺调优、设备维护），支持工具调用、状态、人工介入与可测试性。
- 模型需可替换为国内可访问的 OpenAI 兼容接口（Qwen、DeepSeek 等）；系统部署形态是工厂本地，不依赖公有云。
- 单元测试需要注入假 LLM。

## Decision
使用 LangGraph 编排云端智能体，LLM 通过 OpenAI 兼容接口接入，模型名与地址可配置。

## Alternatives Considered

### Google ADK（配合 agents-cli）
- Pros：图式工作流、任务委派、人工介入、内置评测流程。
- Cons：部署、发布与可观测性环节绑定 Google Cloud；国内访问 Gemini 与 GCP 不稳定；对本项目的收益主要在评测方法，而非编排本身。
- Rejected：只借鉴其评测方法（评测数据集 + 按评分标准的 LLM 评分）。

### 自研轻量编排
- Pros：依赖最少。
- Cons：状态管理、人工介入与重试需自行实现，增加工作量且难以与同行工作对比。
- Rejected。

## Consequences
- 论文中按软件引用方式引用 LangGraph（仓库地址 + 版本号）。
- 云端智能体通过可注入的 LLM 客户端实现测试隔离。
