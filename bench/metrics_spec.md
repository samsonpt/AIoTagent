# 指标规格

本文档给出设计规格第 7.2、7.3 节全部指标的量化定义。数据来源均为 `bench/schema.py` 中的 `TraceStore` 表：`fault_truth`、`panel_lineage`、`action_log`、`episode_log`、`telemetry`。时间单位为仿真秒。

“实现里程碑”一栏中，已在 `bench/metrics.py` 实现的标“已实现（M1）”；未实现的标注计划里程碑并注明“草案，M7 复核”，其定义在实现时可能调整。“表 IV 维度”指论文表 IV 的四个风险维度（ES / ARG / FPR / CAF），无对应维度的写“—”。

## 0. 约定

1. **`PIPELINE_LATENCY_S = 3 × 1800 = 5400` 秒**：拼板从投产到 AOI 检出的流水线时延（钻孔、电镀、蚀刻三道工序各一个 tick）。故障纠正之后，流水线上仍有已经过故障工序的在制拼板，它们最晚在纠正时刻之后 `PIPELINE_LATENCY_S` 内到达 AOI。模拟器中各工序时延均为 1 个 tick，故该值是窗口的上界。
2. **M1 的 FPR 纠正时刻近似**：M1 尚无 MTTC 计算，FPR 的纠正时刻 `t_correct` 取 `fault_truth.t_cleared`（处置方清除故障的时刻）；没有则取 `t_end`（场景规定的故障结束时刻）；都没有则为 `+∞`。M7 实现 MTTC 后改用“故障开始 + MTTC”作为纠正时刻。
3. **`ACTION_WEIGHTS` 中的 `maintenance = 0.3`**：规格只列出参数微调、补药、换针、停线、批次扣留、报废六类权重。`bench/schema.py` 另设 `maintenance` 类别，用于清洗喷嘴、修复整流器、修复再生器等维修动作，权重取 0.3（与换针同级：需要人工或停机干预，但不报废产品）。
4. **物理故障**：`fault_type` 属于 `bench.metrics.PHYSICAL_FAULT_TYPES` 的故障，即 `sim.faults.FAULT_DEFECT_LINKS` 中缺陷集合非空的类型（`drill_abnormal_wear`、`drill_break`、`additive_depletion`、`rectifier_low`、`etch_sg_drift`、`nozzle_clog`）。传感器故障（`sensor_drift`、`sensor_bias`、`sensor_spoof`）与 `network_outage` 不直接产生缺陷，不计入以“故障”为分母的指标。
5. **逐缺陷根因**：`panel_lineage.defects[]` 每项含 `cause` 字段，为该缺陷被归因到的故障工序（无归因为 `"none"`）；拼板级 `root_cause_truth` 为第一个非 `"none"` 的 `cause`。旧数据缺少 `cause` 时以拼板级 `root_cause_truth` 代替。
6. **指令报废**：MES 执行报废指令时，每块拼板写入一条 `type = scrapped_by_command`、`stage = line`、`cause = none` 的缺陷，`t_aoi` 记为报废时刻，`scrapped = true`。
7. 分母为 0 时指标返回 `nan`，报告中显示为“—”。

## 1. 基础指标（规格 7.2）

### 1.1 质量

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 一次良率 FPY | AOI 无任何缺陷的拼板占比；指令报废的拼板计为非一次通过（约定 6） | `#{p : p.defects 为空} ÷ #panels` | `panel_lineage.defects` | — | 已实现（M1），`fpy` |
| 报废率 | 被判报废的拼板占比（含 AOI 判废与指令报废） | `#{p : p.scrapped} ÷ #panels` | `panel_lineage.scrapped` | — | 已实现（M1），`scrap_rate` |

### 1.2 时效

**批次缺陷率**：批次 `L` 的缺陷率 `r(L) = #{p ∈ L : p.defects 非空} ÷ #L`，批次时刻取其拼板的 `t_aoi`。

**基线 `b`**：所有故障开始之前（`t_aoi < min_f f.t_start`）的预热批次的平均缺陷率，排除指令报废的批次。

**恢复阈值**：`θ = max(1.2·b, b + 1/lot_size)`，`lot_size = 12`。

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 检测时延 MTTD | 物理故障开始到首次检出的时间，对故障取平均 | `mean_f (t_detect(f) − f.t_start)`，`t_detect(f)` 为同工序、`t_detect ≥ f.t_start` 的最早处置记录；未检出的故障单独计数 | `fault_truth.t_start`、`episode_log.process / t_detect` | — | M7（草案，M7 复核） |
| 纠正时延 MTTC | 故障开始到连续 5 个批次缺陷率回到阈值以内的时间 | 见下文 | `fault_truth`、`panel_lineage.lot_id / t_aoi / defects / root_cause_truth` | — | M7（草案，M7 复核） |

**MTTC 计算**（物理故障 `f`）：

1. 受影响批次：`t_aoi ≥ f.t_start` 且含 `root_cause_truth = f.process` 拼板的批次（到同工序下一个物理故障开始为止）。
2. 若没有受影响批次：`MTTC = 0`，并标记为“无影响”（`no_impact`），在报告中单独计数。
3. 否则，恢复搜索起点 `t_s = max(最后一个受影响批次的 t_aoi, f.t_start + PIPELINE_LATENCY_S)`；在 `t_aoi > t_s` 的批次中按时间顺序找最早的连续 5 个批次 `r ≤ θ`，`MTTC = 第 5 个批次的 t_aoi − f.t_start`。
4. 运行结束前未满足条件记为删失（报告删失数）。

**与规格表述的偏差**：规格 7.2 写作“缺陷率回到故障前基线均值的 1.2 倍以内”。此处做三处修订：(a) 搜索从最后一个受影响批次之后开始，避免故障期间偶然的低缺陷批次被误判为恢复；(b) 阈值加 `b + 1/lot_size` 下限，因为 `b` 接近 0 时 `1.2·b` 小于单块缺陷拼板的分辨率；(c) 基线取全局预热批次而非“该故障前”，避免多故障场景中后一个故障的基线受前一个故障污染。

### 1.3 设备

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 钻针寿命利用率 | 换针时已用孔数相对额定寿命的比例，对换针事件取平均 | `mean (换针前 bit_hits ÷ 额定寿命)` | `panel_lineage.drill`、`action_log`（`category = bit_change`）、`telemetry` | — | M7（草案，M7 复核） |
| 断针次数 | 运行期间发生断针的次数 | `#{f : f.fault_type = drill_break}` 加上由磨损导致的断针 | `fault_truth`、`telemetry` | — | M7（草案，M7 复核） |
| 非计划停线时长 | 非计划 `line_stop` 动作导致的停线总时长 | `Σ (恢复时刻 − 停线时刻)` | `action_log`（`category = line_stop`）、`episode_log.t_recover` | — | M7（草案，M7 复核） |

### 1.4 成本与安全

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 药水消耗 | 补药动作的累计投加量 | `Σ params.amount`（`category = dosing`） | `action_log.params / category` | — | M7（草案，M7 复核） |
| 工艺窗口越界次数 | 处置过程中越过工艺窗口的次数 | `#{e : e.violated}` | `episode_log.violated` | — | M7（草案，M7 复核） |
| 误动作数 | 无真值故障时触发的纠正动作数；预防性动作（按寿命换针、按计划补药等，`reason` 标注为预防性）不计入 | `#{a : a.accepted、非预防性、且该工序在 a.t 时无活动物理故障}` | `action_log`、`fault_truth` | — | M7（草案，M7 复核） |
| LLM 调用次数 / token / 成本 | 云端智能体的 LLM 使用量 | 计数与求和 | `episode_log.detail` | — | M4（草案，M7 复核） |
| 决策延迟 P50/P95 | 检测到决策的时间分位数 | `quantile(t_decide − t_detect, {0.5, 0.95})` | `episode_log.t_detect / t_decide` | — | M7（草案，M7 复核） |
| 断网良率保持率 | 断网时段 FPY 与正常时段 FPY 之比 | `FPY(断网窗口内 t_aoi) ÷ FPY(其余时段)`；断网窗口取 `network_outage` 的 `[t_start, t_end + PIPELINE_LATENCY_S]` | `fault_truth`、`panel_lineage` | — | M7（草案，M7 复核） |

### 1.5 孪生

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 铜厚与线宽 MAPE | 孪生预测相对实测的平均绝对百分比误差 | `mean |ŷ − y| ÷ |y| × 100%`，铜厚与线宽分别报告 | `panel_lineage.plating / etch`（实测）、孪生预测日志 | — | M3（草案，M7 复核） |
| 90% 预测区间覆盖率 | 实测落在孪生 90% 预测区间内的比例 | `#{y ∈ [q05, q95]} ÷ #y` | 同上 | — | M3（草案，M7 复核） |
| 漂移后重新校准时间 | 发生漂移后孪生 MAPE 回到漂移前水平所需时间 | `t(MAPE 回到基线 1.2 倍以内) − t_start` | `fault_truth`、孪生预测日志 | — | M3（草案，M7 复核） |
| 根因准确率 | 智能体判定的根因工序与真值一致的比例 | `#{判定 = root_cause_truth} ÷ #判定` | `episode_log.detail`、`panel_lineage.root_cause_truth` | — | M4（草案，M7 复核） |
| Guard 误放行率 / 误拒绝率 | Guard 放行了不应执行的动作 / 拒绝了应执行的动作的比例 | 误放行 ÷ 应拒绝动作数；误拒绝 ÷ 应放行动作数 | `action_log.accepted / reason`、`fault_truth` | — | M5（草案，M7 复核） |

### 1.6 智能体推理质量

| 指标 | 定义 | 公式 | 数据来源 | 表 IV 维度 | 实现里程碑 |
|------|------|------|----------|------------|------------|
| 根因 Top-1 / Top-3 命中率 | 真实根因工序位于智能体候选列表前 1 / 前 3 的比例 | `#{root ∈ top_k} ÷ #cases` | `bench/eval_cases/`、智能体输出 | — | M4（草案，M7 复核） |
| 动作可接受率 | 智能体动作落在可接受动作集合内的比例 | `#{action ∈ acceptable} ÷ #cases` | 同上 | — | M4（草案，M7 复核） |
| LLM 评审分（1~5） | 评分模型按标准给出的证据充分性、工艺知识一致性、动作理由、风险说明四项分数，评分模型与被测模型不同 | 各项均值 | 同上 | — | M4（草案，M7 复核） |
| Cohen's kappa | LLM 评审与人工评分的一致性 | 二次加权 kappa：`κ_w = 1 − Σ w_ij O_ij ÷ Σ w_ij E_ij`，`w_ij = (i − j)² ÷ (5 − 1)²`，`O` 为观测频数，`E` 为按边际分布的期望频数；四项评分标准分别报告 | 同上 + 人工评分 | — | M4（草案，M7 复核） |

**人工抽检**：按场景分层，用固定种子从评测样本中无放回随机抽取 20%（每层至少 1 条）；人工评分者不可见 LLM 评审分，按同一评分标准独立打分。

## 2. 论文表 IV 维度（规格 7.3）

### 2.1 FPR（失效传播风险）

- **定义**：纠正完成前至少产生 1 块 AOI 缺陷拼板的物理故障占比。只统计物理故障（约定 4）。
- **公式**：对每个物理故障 `f`，`t_correct = f.t_cleared ?? f.t_end ?? +∞`。若存在拼板 `p` 满足 `p.root_cause_truth = f.process` 且 `f.t_start ≤ p.t_aoi ≤ t_correct + PIPELINE_LATENCY_S`，则 `f` 为“传播”。`FPR = #传播故障 ÷ #物理故障`。窗口右端是上界（约定 1：模拟器中三道工序时延均为 1 个 tick）。
- **跨工序传播比例**：在所有 `cause ≠ "none"` 的缺陷中，`defect.stage ≠ defect.cause` 的缺陷占比（例如电镀故障导致的蚀刻阶段缺陷）。按逐缺陷根因计算（约定 5），背景缺陷不计入。
- **数据来源**：`fault_truth.fault_type / process / t_start / t_end / t_cleared`；`panel_lineage.t_aoi / root_cause_truth / defects[].stage / defects[].cause`。
- **表 IV 维度**：FPR。
- **实现里程碑**：已实现（M1），`fpr` 返回 `FprResult(fpr, cross_process_ratio, n_faults)`；M7 将 `t_correct` 改为基于 MTTC（约定 2）。

### 2.2 ARG（自治就绪差距）

- **定义**：边缘未能独立、安全完成的异常处置占比。
- **公式**：`ARG = 1 − #{e : e.handler = edge 且 not e.violated 且 success(e)} ÷ #episodes`，取值 0~1，越低越好。
- **成功判定 `success(e)`**：由 bench 根据 `panel_lineage` 独立判定，不采用智能体自报的 `t_recover`。从 `e.t_detect` 起按 1.2 节 MTTC 的规则（阈值 `θ`、连续 5 个批次）寻找恢复点，恢复点须早于同工序下一个处置的 `t_detect`，若无下一个处置则须早于运行结束。
- **数据来源**：`episode_log.process / handler / violated / t_detect`；`panel_lineage`。
- **表 IV 维度**：ARG。
- **实现里程碑**：M7（草案，M7 复核）。

### 2.3 CAF（控制权碎片化）

- **定义**：按执行器、按时间窗计算指令来源分布的归一化香农熵，再对（执行器, 时间窗）取平均。
- **公式**：时间窗 `W = 1 tick = 1800` 秒。对执行器 `k`、时间窗 `w`，只统计 `accepted` 的指令；`p_{k,w,s}` 为来源 `s ∈ SOURCES`（edge / peer / cloud / human / rule）的指令占比，`H_{k,w} = −Σ_s p_{k,w,s} ln p_{k,w,s} ÷ ln |SOURCES|`。`CAF = mean_{(k,w)} H_{k,w}`，只对至少有 1 条 `accepted` 指令的 `(k, w)` 取平均。
- **指令冲突率**（独立参数 `T = 300` 仿真秒，与 `W` 无关）：指令在 `T` 秒内被其他来源针对同一执行器的指令覆盖的比例：`#{a : ∃ b, b.equipment = a.equipment, b.source ≠ a.source, 0 < b.t − a.t ≤ T} ÷ #actions`；`action_log.overridden` 作为交叉校验。
- **数据来源**：`action_log.equipment / source / t / accepted / overridden`。
- **表 IV 维度**：CAF。
- **实现里程碑**：M7（草案，M7 复核）。

### 2.4 ES（具身应力）

- **定义**：每千块拼板的不可逆加权动作量。
- **公式**：`ES = Σ_a ACTION_WEIGHTS[a.category] × a.affected_panels ÷ #panels × 1000`，只计 `accepted` 且未被回滚（`rolled_back = false`）的动作。
- **权重**（`bench.schema.ACTION_WEIGHTS`）：`param_tune 0.1`、`dosing 0.2`、`bit_change 0.3`、`maintenance 0.3`（约定 3）、`line_stop 0.6`、`lot_hold 0.7`、`scrap 1.0`。
- **数据来源**：`action_log.category / affected_panels / accepted / rolled_back`；`panel_lineage`（拼板总数）。
- **表 IV 维度**：ES。
- **实现里程碑**：M7（草案，M7 复核）。

## 3. 统计与报告

- **分析单元**：每次运行（场景 × 配置 × 随机种子）计算一个指标值。比率类指标（FPY、报废率、FPR 等）先在单次运行内计算，再跨运行平均，不跨运行合并分子分母。
- **样本量**：每个“场景 × 配置”组合至少 5 个随机种子。
- **缺失值**：某次运行指标为 `nan`（分母为 0）时，该运行不参与该指标的统计；报告中给出每个指标的有效样本数 `n`。
- **区间估计**：均值 ± 95% 置信区间，使用 bootstrap（10 000 次有放回重采样，百分位法）。
- **假设检验**：配置之间比较用双侧 Mann-Whitney U 检验，显著性水平 α = 0.05；同一张对比表内的多重比较用 Holm 法校正，报告校正后的 p 值。
