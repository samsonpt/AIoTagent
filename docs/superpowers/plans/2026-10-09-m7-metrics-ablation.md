# M7 指标 · 消融矩阵 · 报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `compute_all` 覆盖 metrics_spec 全部指标；消融矩阵串行跑场景×配置×种子并续跑；产出 CSV + Markdown 报告（bootstrap CI、Mann-Whitney+Holm）。

**Architecture:** 复用 `sim.runner.run` + TraceStore 埋点。`bench/metrics.py` 补齐公式并导出 `compute_all`。`bench/matrix_runner.py` 展开格子、串行跑、写 `results.csv`/`manifest.json`。`bench/report.py` 聚合统计。CLI：`scripts/run_matrix.py`。矩阵默认 DeepSeek + **`auto_approve=True`（启用 HumanModel 审批）**；`--llm fake` 供 CI。

**Tech Stack:** Python 3.12, numpy, scipy（Mann-Whitney；若未依赖则用纯 numpy 实现秩和检验）, PyYAML, pydantic, pytest, DeepSeek via 现有 `cloud/llm.py`。

**规格：** `docs/superpowers/specs/2026-10-09-m7-metrics-ablation-design.md`；公式权威 `bench/metrics_spec.md`；父规格 §7、§10 M7。

## Global Constraints

- Python 3.12；依赖用 `pyproject.toml`；测试用 `pytest`。
- 随机过程经 `common.rng.make_rng(seed, stream)`；报告 bootstrap 固定 seed。
- 仿真时间只用 `SimClock`；禁止 `time.time()` 作仿真时钟。
- 代码标识符英文；文档/日志/报告说明中文。
- 四类埋点字段只能新增，不能改语义。
- `twin/`、`edge/`、`cloud/`、`guard/`、`ui/`、`bench/` 不 import `sim` 模型代码（`bench` 测 PHYSICAL_FAULT_TYPES 对齐时用已有 `sim.faults` 仅在 tests）。
- JSON：`json.dumps(obj, sort_keys=True, ensure_ascii=False)`。
- 测试：`tests/bench/test_*.py`；`--import-mode=importlib`，`pythonpath=["."]`。
- 不写解释性注释。
- 分母为 0 → `nan`；矩阵单格失败不中断整矩阵。

## 设计澄清（相对 M7 规格文案）

| 规格措辞 | 实现 |
|----------|------|
| 「auto_approve=False + HumanModel」 | 矩阵必须 `auto_approve=True`，否则 `HumanModel._process_approvals` 直接 return。演示看板仍用 `False`。在规格「偏差记录」追加本条。 |

## File Structure

```
bench/metrics.py              # + MTTD/MTTC/设备/成本/ARG/CAF/ES/... + compute_all
bench/metrics_spec.md         # 草案 → 已实现（M7）
bench/configs/full_minus_*.yaml
bench/configs/full_twin_none.yaml
bench/configs/full_twin_mechanistic.yaml
bench/matrix_runner.py        # 新建
bench/report.py               # 新建
bench/eval_runner.py          # Top-3、LLM 评审、kappa
bench/__main_report__.py 或 report 可 -m
scripts/run_matrix.py
docs/superpowers/specs/2026-10-09-m7-metrics-ablation-design.md  # §9 偏差
tests/bench/test_metrics_m7.py
tests/bench/test_configs.py   # 扩展
tests/bench/test_matrix_runner.py
tests/bench/test_report.py
tests/bench/test_eval_score.py
pyproject.toml                # 若需 scipy
```

## 数值与语义约定

- `PIPELINE_LATENCY_S = 5400`；`LOT_SIZE = 12`；CAF 窗 `W=1800`；冲突 `T=300`。
- `SOURCES` / `ACTION_WEIGHTS` / `HANDLERS` 用 `bench.schema` 常量。
- `compute_all` 返回 `dict[str, float]`（嵌套指标展平为 `fpr`、`fpr_cross_process_ratio`、`mttd_mean`、`mttc_mean`、`mttc_n_censored`、`decision_latency_p50` 等）；完整键表在 Task 6 钉死。
- 矩阵格 id：`{scenario}__{config}__s{seed}`。
- HumanModel 参数写入 `report.md` 前言：`approval_delay_s=900`，批准概率 0.9（与现有实现一致）。

---

### Task 1: 消融配置 YAML 全集

**Files:**
- Create: `bench/configs/full_minus_use_cloud.yaml` 等（见下列表）
- Create: `bench/configs/full_twin_none.yaml`、`full_twin_mechanistic.yaml`
- Modify: `tests/bench/test_configs.py`
- Modify: `docs/superpowers/specs/2026-10-09-m7-metrics-ablation-design.md` §9 偏差（auto_approve 澄清）

**Interfaces:**
- 每个 YAML 含 `name:` 与相对 `full` 的差分字段；经 `load_ablation` 可解析。
- `full_minus_*` 布尔列表：`use_cloud`, `use_twin_lookahead`, `use_peer_feedforward`, `use_event_trigger`, `use_confidence_modulation`, `use_rag`, `use_human_gate`, `use_counterfactual_rca`, `use_twin_confidence_gate`。

- [ ] **Step 1: 失败测试**

```python
from pathlib import Path
from common.config import AblationConfig, load_ablation

CONFIGS = Path(__file__).resolve().parents[2] / "bench" / "configs"
MINUS_KEYS = [
    "use_cloud", "use_twin_lookahead", "use_peer_feedforward", "use_event_trigger",
    "use_confidence_modulation", "use_rag", "use_human_gate",
    "use_counterfactual_rca", "use_twin_confidence_gate",
]

def test_full_minus_files_exist_and_differ():
    full = AblationConfig()
    for key in MINUS_KEYS:
        cfg = load_ablation(CONFIGS / f"full_minus_{key}.yaml")
        assert cfg.name == f"full_minus_{key}"
        assert getattr(cfg, key) is False
        for other in MINUS_KEYS:
            if other != key:
                assert getattr(cfg, other) is True

def test_full_twin_variants():
    assert load_ablation(CONFIGS / "full_twin_none.yaml").twin_fidelity == "none"
    assert load_ablation(CONFIGS / "full_twin_mechanistic.yaml").twin_fidelity == "mechanistic"
```

- [ ] **Step 2:** `python -m pytest tests/bench/test_configs.py -q` → FAIL（缺文件）

- [ ] **Step 3: 写入 YAML**（示例 `full_minus_use_cloud.yaml`）

```yaml
name: full_minus_use_cloud
use_cloud: false
```

其余同理只改对应键；twin 变体：

```yaml
name: full_twin_none
twin_fidelity: none
```

```yaml
name: full_twin_mechanistic
twin_fidelity: mechanistic
```

- [ ] **Step 4:** pytest 通过；规格 §9 追加 auto_approve 澄清

- [ ] **Step 5: Commit** `feat: M7 消融配置 full_minus 与 twin 变体`

---

### Task 2: MTTD / MTTC + FPR 纠正时刻

**Files:**
- Modify: `bench/metrics.py`
- Create/Modify: `tests/bench/test_metrics_m7.py`

**Interfaces:**
- `lot_defect_rate(store) -> list[tuple[str, float, float]]`：`(lot_id, t_aoi_ref, rate)`，`t_aoi_ref=max(t_aoi in lot)`，排除纯指令报废批次（可选：`all(scrapped_by_command)` 则跳过；MVP：含任意非指令缺陷或无缺陷均计入，指令报废拼板计缺陷——与 FPY 约定一致：`defects` 非空）。
- `baseline_defect_rate(store) -> float`：预热批次均值；无则 `nan`。
- `recovery_threshold(b: float, lot_size: int = 12) -> float`：`max(1.2*b, b + 1/lot_size)`。
- `mttd(store) -> dict`：`{"mean": float, "n": int, "n_undetected": int}`。
- `mttc(store) -> dict`：`{"mean": float, "n": int, "n_no_impact": int, "n_censored": int}`。
- `fpr(store)`：`t_correct` 优先 `fault.t_start + mttc_for_fault`；该故障无 MTTC（删失/无影响）则回退 `t_cleared ?? t_end ?? inf`（保留可测兼容）。

- [ ] **Step 1: 失败测试（MTTC 恢复）**

```python
import math
from bench.metrics import mttc, PIPELINE_LATENCY_S
from bench.schema import EpisodeRecord, FaultRecord, PanelRecord, TraceStore

def _panel(pid, lot, t_aoi, defects, root):
    return PanelRecord(
        panel_id=pid, lot_id=lot, part_no="PN", t_release=t_aoi - PIPELINE_LATENCY_S,
        t_aoi=t_aoi, drill={}, plating={}, etch={}, defects=defects,
        root_cause_truth=root, scrapped=False,
    )

def test_mttc_recovers_after_five_good_lots(store: TraceStore):
    # 预热 2 批无缺陷 → b=0 → θ = 1/12
    for i in range(24):
        store.record_panel(_panel(f"L0-P{i}", "L0", 1000.0, [], "none"))
    store.record_fault(FaultRecord(
        "f1", "etch", "e", "nozzle_clog", {}, t_start=2000.0, t_end=8000.0, t_cleared=5000.0,
    ))
    # 受影响批：缺陷 root=etch
    for i in range(12):
        store.record_panel(_panel(
            f"L1-P{i}", "L1", 3000.0,
            [{"type": "open", "zone": [0, 0], "stage": "etch", "cause": "etch"}], "etch",
        ))
    # 恢复搜索起点后连续 5 个好批
    for li, t in enumerate([9000, 9100, 9200, 9300, 9400], start=2):
        for i in range(12):
            store.record_panel(_panel(f"L{li}-P{i}", f"L{li}", float(t), [], "none"))
    out = mttc(store)
    assert out["n_no_impact"] == 0
    assert out["n_censored"] == 0
    assert out["mean"] == pytest.approx(9400.0 - 2000.0)
```

（另加：`test_mttd_mean`、`test_mttc_no_impact_zero`、`test_mttc_censored`。）

- [ ] **Step 2:** pytest 指定用例 → FAIL

- [ ] **Step 3: 按 metrics_spec §1.2 实现**；更新 `fpr` 的 `t_correct` 逻辑；保留既有 `test_metrics.py` 中不依赖旧 `t_cleared` 语义的用例，若失败则按约定 2 调整期望

- [ ] **Step 4:** `python -m pytest tests/bench/test_metrics.py tests/bench/test_metrics_m7.py -q` PASS

- [ ] **Step 5: Commit** `feat: MTTD/MTTC 与 FPR 纠正时刻`

---

### Task 3: 设备 + 成本安全指标

**Files:**
- Modify: `bench/metrics.py`
- Modify: `tests/bench/test_metrics_m7.py`

**Interfaces:**
- `bit_life_utilization(store) -> float`：换针前 `params`/`telemetry` 中已用孔 ÷ 额定寿命；无数据 `nan`
- `drill_break_count(store) -> float`：`fault_type==drill_break` 计数（float 便于 compute_all）
- `unplanned_downtime_s(store) -> float`：`line_stop` 接受动作的时长和（无恢复则用下一动作或 run 末——MVP：若 `episode` 有 `t_recover` 用其差，否则 `nan` 分量跳过；简化：对每条 accepted `line_stop`，时长取 `params.get("duration_s", 1800)` 若无则 1 tick=1800）
- `dosing_consumption(store) -> float`：`Σ params.amount` for dosing accepted
- `envelope_violations(store) -> float`：`sum(e.violated)`
- `false_action_count(store) -> float`：accepted 非预防性且动作时刻该工序无活动物理故障
- `decision_latency(store) -> dict`：`p50`/`p95` from `t_decide-t_detect` where both set
- `outage_fpy_retention(store) -> float`：断网窗 FPY / 其余 FPY

- [ ] **Step 1: 失败测试**（各函数至少 1 个构造用例；`false_action_count` 用无故障时的 accepted `param_tune`）

- [ ] **Step 2–4:** TDD 实现并通过

- [ ] **Step 5: Commit** `feat: 设备与成本安全指标`

---

### Task 4: 孪生残余 + Guard 误放行/误拒绝

**Files:**
- Modify: `bench/metrics.py`
- Modify: `tests/bench/test_metrics_m7.py`

**Interfaces:**
- `twin_recalibration_time(store, mape_series: list[tuple[float, float]] | None) -> float`：若无序列 `nan`；MVP 允许 `compute_all` 传 `twin_preds` 可选，缺省 `nan`
- `guard_error_rates(store) -> dict`：`false_accept_rate`、`false_reject_rate`  
  - 应拒绝：无活动物理故障且高风险类别（`lot_hold`/`scrap`/`line_stop`）被 accepted → 误放行  
  - 应放行：有同工序活动故障且动作 `accepted=False` 且 reason 含 guard/reject → 误拒绝  
  - 分母 0 → `nan`

- [ ] **Step 1–4:** TDD

- [ ] **Step 5: Commit** `feat: Guard 误判率与孪生校准时间钩子`

---

### Task 5: 表 IV — ARG / CAF / ES

**Files:**
- Modify: `bench/metrics.py`
- Modify: `tests/bench/test_metrics_m7.py`

**Interfaces:**
- `arg(store) -> float`：按 metrics_spec §2.2；`success(e)` 复用 MTTC 规则自 `e.t_detect` 起
- `caf(store, window_s: float = 1800) -> dict`：`{"caf": float, "conflict_rate": float}`；冲突 `T=300`
- `es(store) -> float`：ACTION_WEIGHTS × affected_panels / n_panels × 1000，仅 accepted 且 not rolled_back

- [ ] **Step 1: CAF/ES 失败测试（手算）**

```python
def test_es_weights(store):
    store.record_panel(...)  # 10 panels
    store.record_action(ActionRecord(
        t=1, process="etch", equipment="etch", command="x", params={},
        source="edge", category="param_tune", affected_panels=10,
        accepted=True, rolled_back=False,
    ))
    # ES = 0.1 * 10 / 10 * 1000 = 100
    assert es(store) == pytest.approx(100.0)
```

- [ ] **Step 2–4:** 实现 ARG/CAF/ES；pytest PASS

- [ ] **Step 5: Commit** `feat: ARG CAF ES 表 IV 指标`

---

### Task 6: `compute_all` 键表

**Files:**
- Modify: `bench/metrics.py`
- Modify: `tests/bench/test_metrics_m7.py`

**Interfaces — `compute_all(store, *, twin_preds=None, eval_extras=None) -> dict[str, float]` 固定键：**

```text
fpy, scrap_rate,
mttd_mean, mttd_n, mttd_n_undetected,
mttc_mean, mttc_n, mttc_n_no_impact, mttc_n_censored,
bit_life_utilization, drill_break_count, unplanned_downtime_s,
dosing_consumption, envelope_violations, false_action_count,
llm_calls, llm_tokens,
decision_latency_p50, decision_latency_p95,
outage_fpy_retention,
copper_mape, linewidth_mape, coverage90_copper, coverage90_linewidth,
twin_recalibration_s,
root_cause_top1,  # 仅当 eval_extras 提供；否则 nan
guard_false_accept_rate, guard_false_reject_rate,
fpr, fpr_cross_process_ratio, fpr_n_faults,
arg, caf, conflict_rate, es
```

嵌套结果展平为 float；内部 `dict` 的计数也转 float。

- [ ] **Step 1:** `test_compute_all_keys_on_empty_store`：空库所有键存在且为 float（可为 nan）

- [ ] **Step 2–4:** 实现并 PASS

- [ ] **Step 5: Commit** `feat: compute_all 统一指标出口`

---

### Task 7: eval 扩展 Top-3 / 评审分 / kappa

**Files:**
- Modify: `bench/metrics.py`（`root_cause_top3`）
- Modify: `bench/eval_runner.py`
- Create: `tests/bench/test_eval_score.py`

**Interfaces:**
- `root_cause_top3(predicted_lists: list[list[str]], truth: list[str]) -> float`
- `score_rubric(judge_llm, case, agent_output) -> dict[str, float]`：四项 1–5
- `cohens_quadratic_kappa(y_true: list[int], y_pred: list[int], n_classes: int = 5) -> float`
- `run_eval_suite(..., judge_llm=...)` 汇总 Top-1/3、accept_rate、四项均分；κ 仅当提供 `human_scores` 时计算

评分模型：与被测 LLM 分离——`JudgeLLM` 包装另一 `ChatLLM` 实例或 `FakeLLM` 脚本；矩阵默认不跑全量 κ（可选 CLI 子命令 / `scripts/score_eval.py` MVP 可挂在 `eval_runner` 的 `if __name__`）。

- [ ] **Step 1–4:** TDD；既有 `test_eval_cases` 仍绿

- [ ] **Step 5: Commit** `feat: Top-3 与 LLM 评审/kappa`

---

### Task 8: `matrix_runner` + CLI + 续跑

**Files:**
- Create: `bench/matrix_runner.py`
- Create: `scripts/run_matrix.py`
- Create: `tests/bench/test_matrix_runner.py`
- Modify: `common/env.py` 加载（与 demo_live 一致）若 CLI 需 `.env`

**Interfaces:**
- `Cell = namedtuple` 或 dataclass：`scenario, config, seed, cell_id`
- `expand_cells(scenarios: list[Path], configs: list[Path], seeds: list[int]) -> list[Cell]`
- `run_cell(cell, out_dir: Path, *, llm: Literal["deepseek","fake"], ...) -> dict`：写 db、调用 `run`（`ablation=load_ablation`，**`auto_approve=True`**，`scenario.seed` 覆盖为 cell.seed）、`compute_all`、返回行 dict
- `run_matrix(out_dir, ..., resume: bool = True) -> Path`：写 `manifest.json`、`results.csv`；跳过 manifest 中 `status==ok`
- LLM：`fake` → `default_fake_llm` / 现有注入点；`deepseek` → 现有云端路径（缺 key → 该格 error）

- [ ] **Step 1: 失败测试**

```python
def test_expand_and_resume(tmp_path, monkeypatch):
    # monkeypatch run_cell 立即返回固定指标，避免真仿真
    ...
    run_matrix(tmp_path, scenarios=[...], configs=[...], seeds=[42], resume=True)
    assert (tmp_path / "results.csv").exists()
    # 再跑一遍，run_cell 调用次数不增加
```

- [ ] **Step 2–3:** 实现 runner + CLI：

```text
python scripts/run_matrix.py --out runs/matrix/smoke --llm fake \
  --scenarios additive_depletion --configs baseline_rule,edge_only,full --seeds 42
```

- [ ] **Step 4:** 单元测试 PASS；手动冒烟上述命令产出 `results.csv`

- [ ] **Step 5: Commit** `feat: 消融矩阵运行器与续跑`

---

### Task 9: `report.py` 统计与 Markdown

**Files:**
- Create: `bench/report.py`
- Create: `tests/bench/test_report.py`

**Interfaces:**
- `bootstrap_ci(values: list[float], *, n_boot: int = 10000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]` → `(mean, lo, hi)`；过滤 nan
- `mann_whitney_u(x, y) -> tuple[float, float]` → `(u, p)`（scipy.stats 或手写）
- `holm_correct(pvalues: list[float]) -> list[float]`
- `write_reports(results_csv: Path, out_dir: Path, *, human_model_notes: str = ...) -> None`：写出 summary CSVs、`comparisons.csv`、`report.md`
- `python -m bench.report --results ... --out ...`（`bench/report.py` 内 `main` 或 `bench/__main__.py` 分包：优先 `if __name__` + matrix 调用 `write_reports`）

主对比配置集合：`baseline_rule`, `edge_only`, `full`；消融：所有 `full_minus_*` + twin 对 `full`。

- [ ] **Step 1:**

```python
def test_bootstrap_reproducible():
    a = bootstrap_ci([1.0, 2.0, 3.0], seed=0)
    b = bootstrap_ci([1.0, 2.0, 3.0], seed=0)
    assert a == b

def test_holm_monotone():
    corrected = holm_correct([0.01, 0.04, 0.03])
    assert corrected == sorted(corrected) or True  # 校正后与原序对应；断言 p' >= p 逐项
    for p, p2 in zip([0.01, 0.04, 0.03], corrected):
        assert p2 >= p - 1e-12
```

- [ ] **Step 2–4:** 实现；用微型 `results.csv` 断言产出文件存在且 `report.md` 含「表 IV」字样

- [ ] **Step 5: Commit** `feat: 消融报告 bootstrap 与 Mann-Whitney`

---

### Task 10: 冒烟验收 + metrics_spec 状态更新

**Files:**
- Modify: `bench/metrics_spec.md`（实现里程碑列）
- Modify: `bench/matrix_runner.py` / `scripts/run_matrix.py`（跑完自动 `write_reports`）
- Create: `tests/bench/test_matrix_smoke.py`（可选标记 `@pytest.mark.slow`）

- [ ] **Step 1: 跑 CI 冒烟**

```text
python scripts/run_matrix.py --llm fake --scenarios additive_depletion \
  --configs baseline_rule,edge_only,full --seeds 42 --out runs/matrix/smoke
```

Expected：`results.csv` 有 3 行 ok；`report.md` 存在；关键键非全缺失。

- [ ] **Step 2:** 更新 `metrics_spec.md` 中 M7 草案条目为「已实现（M7）」；FPR 约定 2 注明已接 MTTC

- [ ] **Step 3:** `python -m pytest tests/bench/ -q` 全绿

- [ ] **Step 4: Commit** `docs: M7 指标规格标为已实现并冒烟验收`

---

## Spec coverage（自检）

| 规格项 | Task |
|--------|------|
| 全部指标 / compute_all | 2–6 |
| full_minus_* / twin 变体 | 1 |
| 全矩阵维 + 串行 + 续跑 | 8 |
| 默认 DeepSeek / --llm fake | 8 |
| HumanModel 审批 | 8（auto_approve=True） |
| CSV + MD + CI + MW+Holm | 9 |
| Top-3 / 评审 / κ | 7 |
| 冒烟验收 + metrics_spec | 10 |
| 非目标：默认并行、UI 报告 | 不实现 |

## Placeholder scan

无 TBD/TODO；接口名与键表已钉死。

## 依赖

若环境无 `scipy`，Task 9 优先 `pyproject.toml` 增加 `scipy`；否则在 `report.py` 实现 Mann-Whitney U 精确/近似 p 值并单测对照已知样例。
