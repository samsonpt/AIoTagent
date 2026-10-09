# M7 指标计算器 · 消融矩阵 · 报告设计规格

**日期：** 2026-10-09  
**状态：** 已批准  
**父规格：** `docs/superpowers/specs/2026-10-08-pcb-aiot-agent-design.md` §7、§8、§10 M7  
**公式权威：** `bench/metrics_spec.md`（实现后将「草案，M7 复核」改为「已实现（M7）」）  
**相关：** M1–M6 交付；`sim/runner.py`、`bench/human_model.py`、`bench/eval_runner.py`

## 1. 目标

实现父规格 M7 验收：第 7 节全部指标可计算；消融矩阵可无人值守跑完；产出基线与消融对比报告（原始 CSV + Markdown，含 bootstrap 95% CI 与 Mann-Whitney U + Holm）。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 范围 | 验收满配：§7.2/7.3 全部可算指标 + 完整消融配置 + 统计报告 |
| 矩阵规模 | 全部 `bench/scenarios/*.yaml` × 全部矩阵配置 × ≥5 种子（默认 42–46） |
| LLM | **默认真实 DeepSeek**；CLI `--llm fake` 供冒烟/CI |
| 审批 | `auto_approve=False` + **HumanModel**（§7.5：延迟 + 按根因一致概率批准） |
| 并行 | **默认串行**；预留 `--jobs N` 但不作为本里程碑必交付 |
| 实现路径 | 扩展现有 `sim.runner.run` + 加厚 `bench/metrics`；新增 `matrix_runner` / `report` |

## 3. 架构

```
bench/
  metrics.py           # compute_all(store) → 全部指标
  metrics_spec.md      # 公式权威；M7 后更新实现状态
  configs/             # + full_minus_* 、full_twin_none / full_twin_mechanistic
  matrix_runner.py     # 场景 × 配置 × seed 串行调度 + 续跑
  report.py            # CSV 聚合 + Markdown 报告
  eval_runner.py       # 扩展 Top-3、LLM 评审分、Cohen's κ
scripts/
  run_matrix.py        # CLI
runs/matrix/<stamp>/   # 每格 .db + results.csv + report.md + manifest.json
```

**单格数据流：**  
`load_scenario` + YAML/`AblationConfig` → `sim.runner.run(..., auto_approve=False)` + HumanModel → TraceStore SQLite → `compute_all` → `results.csv` 一行。

**不改动：** `ui/`、Guard 协议语义；矩阵运行不启动 Streamlit。

**续跑：** `manifest.json` 记录格子状态；同 `--out` 目录重跑跳过 `ok` 格，失败格可重试。

## 4. 指标与 `compute_all`

**入口：** `bench.metrics.compute_all(store: TraceStore, *, twin_preds=None, eval_extras=None) -> dict`

| 组 | 指标 | 实现策略 |
|----|------|----------|
| 质量 | FPY、报废率 | 已有；复核 |
| 时效 | MTTD、MTTC（含 no_impact / 删失计数） | 新建；FPR 的 `t_correct` 改为基于 MTTC（metrics_spec 约定 2） |
| 设备 | 钻针寿命利用率、断针次数、非计划停线时长 | 新建 |
| 成本安全 | 药水消耗、越界次数、误动作、LLM 用量、决策延迟 P50/P95、断网良率保持率 | 部分已有 + 新建 |
| 孪生 | MAPE、coverage90、校准时间、根因、Guard 误放行/误拒绝 | 已有 + 新建/复核 |
| 表 IV | FPR、ARG、CAF（+ 指令冲突率）、ES | FPR 复核；ARG/CAF/ES 新建 |
| 推理质量 | Top-1/Top-3、动作可接受率、LLM 1–5 分、Cohen's κ | 扩展 `eval_runner` |

**约定：**

- 分母为 0 → `nan`（不抛错）；报告显示为「—」
- 比率类指标：先在单次运行内计算，再跨运行平均（不跨 run 合并分子分母）
- 评分模型与被测 DeepSeek **不同**（独立评分器配置）
- 人工抽检 20% 的 κ：矩阵外可选子命令，避免阻塞无人值守全量矩阵

公式细节以 `bench/metrics_spec.md` 为准；实现时若发现与埋点字段不一致，先修订 metrics_spec 再改代码，并在本规格「偏差」小节记一笔。

## 5. 消融配置与矩阵调度

### 5.1 配置集合（`bench/configs/`）

| 配置 | 含义 |
|------|------|
| `baseline_rule` | 已有：`use_edge_agent=false`，`use_cloud=false` |
| `edge_only` | 已有：边缘开、云关 |
| `full` | 已有：全开，`twin_fidelity=hybrid` |
| `full_minus_<开关>` | 新建：在 `full` 上只关闭一个布尔开关 |
| `full_twin_none` | 新建：`twin_fidelity=none` |
| `full_twin_mechanistic` | 新建：`twin_fidelity=mechanistic` |

**布尔 `full_minus_*` 覆盖：**  
`use_cloud`、`use_twin_lookahead`、`use_peer_feedforward`、`use_event_trigger`、`use_confidence_modulation`、`use_rag`、`use_human_gate`、`use_counterfactual_rca`、`use_twin_confidence_gate`。

不单独做 `full_minus_use_edge_agent`（由 `baseline_rule` / `edge_only` 覆盖）。

### 5.2 矩阵维

- **场景：** `bench/scenarios/*.yaml` 全部（含 `nominal`）
- **配置：** §5.1 全集
- **种子：** 默认 `[42, 43, 44, 45, 46]`；CLI 可覆盖
- **model_mismatch：** 随场景 YAML 携带，不另扩一维

### 5.3 `matrix_runner` 行为

1. 展开格子 → 更新 `manifest.json`
2. 串行执行；每格库路径：  
   `runs/matrix/<stamp>/<scenario>__<config>__s<seed>.db`
3. 审批：HumanModel（规格 §7.5）
4. LLM：默认 DeepSeek（缺 `DEEPSEEK_API_KEY` 时失败并记 `status=error`，除非 `--llm fake`）
5. 成功：`results.csv` 追加一行（场景、配置、seed、全部指标键）；失败：`status=error` + 摘要，**不中断**整矩阵
6. 同目录重跑：跳过已 `ok` 的格

### 5.4 CLI

```text
python scripts/run_matrix.py --out runs/matrix/<stamp> \
  [--llm deepseek|fake] [--seeds 42,43,44,45,46] \
  [--scenarios ...] [--configs ...]
```

跑完自动调用 `bench.report` 生成汇总与 `report.md`。

## 6. 报告与统计

**输入：** `results.csv`  
**输出（同目录）：**

| 文件 | 内容 |
|------|------|
| `results.csv` | 原始格子（含 `status`、`error`） |
| `summary_by_config.csv` | 按配置：均值、n、95% CI |
| `summary_by_scenario_config.csv` | 按场景×配置 同上 |
| `comparisons.csv` | 配置对：U、Holm 校正 p、方向 |
| `report.md` | 设置说明、HumanModel 参数、主表、表 IV、显著结果 |

**统计（对齐 metrics_spec §3）：**

- 分析单元 = 单次运行；`nan` 不参与该指标；报告有效 `n`
- 95% CI：bootstrap 10 000 次有放回、百分位法；固定 RNG seed 保证可复现
- 双侧 Mann-Whitney U，α=0.05；同一对比表内 Holm 校正
- 主对比：`baseline_rule` vs `edge_only` vs `full`
- 消融对比：`full` vs 各 `full_minus_*` / twin 变体

**独立入口：** `python -m bench.report --results <csv> --out <dir>`

## 7. 测试与验收

### 7.1 单元 / 公式

- `tests/bench/`：固定 TraceStore fixture，手算期望值覆盖每个新指标
- MTTC / ARG：无影响、删失、连续 5 批恢复
- CAF / ES：已知动作序列
- `report`：小 CSV → CI 含样本均值；Holm 单调；同 seed bootstrap 可复现

### 7.2 CI 冒烟（FakeLLM）

```text
python scripts/run_matrix.py --llm fake --scenarios additive_depletion \
  --configs baseline_rule,edge_only,full --seeds 42 --out runs/matrix/smoke
```

断言：非空 `results.csv` + `report.md`；`compute_all` 键覆盖已实现清单。

### 7.3 里程碑验收

1. 第 7 节全部指标可经 `compute_all` 计算（缺数据 → `nan`）
2. 全量矩阵可启动并续跑；默认 DeepSeek + HumanModel
3. 产出基线与消融对比报告
4. `metrics_spec.md` 对应条目标为「已实现（M7）」

### 7.4 非目标

- 矩阵默认并行（仅预留 `--jobs`）
- Streamlit 内嵌报告页
- 改写仿真物理模型或 Guard 协议

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 全量 × 真实 DeepSeek 墙钟极长、费用高 | 续跑；`--llm fake` 冒烟；可子集 `--scenarios`/`--configs` |
| API 失败导致格失败 | 单格 `error` 不中断；可重试失败格 |
| 埋点缺字段导致指标全 nan | 公式测试 + 冒烟断言关键键非全 nan（故障场景） |
| 评分模型与被测模型耦合 | 评分器独立配置；κ 抽检为可选子命令 |

## 9. 偏差记录

（实现期若修订 metrics_spec 公式或数据源，在此追加条目。）

### 9.1 auto_approve 与 HumanModel（2026-10-09）

§2 决策表与 §3 数据流原写「`auto_approve=False` + HumanModel」，与 `bench/human_model.py` 实现矛盾：`HumanModel._process_approvals` 在 `auto_approve=False` 时直接 `return`，不会执行延迟审批逻辑。

**修正：**

- **矩阵运行**（`matrix_runner` / `run_matrix.py`）：`auto_approve=True`，使 HumanModel 按 §7.5 参数执行审批。
- **演示看板**（`demo_live.py` / Streamlit）：仍用 `auto_approve=False`，由 UI 人工审批，不自动消费 pending。

§2 决策表「审批」行与 §3 数据流中的 `auto_approve=False` 以上述为准，矩阵侧以 `True` 为正式行为。
