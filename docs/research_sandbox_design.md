# Research Sandbox — Phase 1 Design

**Status:** DRAFT — scaffolding only, no implementation, no agent loop.
**Date:** 2026-06-13
**Context:** autoresearch-crypto v2.1 balanced verified. Before launching autonomous
research, need a sandbox that isolates experimentation from live execution.

---

## 1. Philosophy

借鉴 [autoresearch](https://github.com/karpathy/autoresearch) 和 [Auto-Quant](https://github.com/TraderAlice/Auto-Quant) 的
实验组织范式，但针对接近执行层的项目状态做更强的隔离：

- **Evaluation contract（评估契约）**：oracle + data split + fee model + execution semantics
  是只读的，LLM 不得修改。任何通过修改 evaluator 来提高分数的行为一律禁止。
- **Sandbox workspace（沙盒工作区）**：LLM 只能写 `research_workspace/`，不能接触
  live 代码、checkpoint、data 目录。
- **Dual-lane review（双通道评审）**：exploration lane 允许自由探索，promotion lane
  只有通过 oracle 全量评估的候选才能进入。

### 关键区别 vs Auto-Quant

| 维度 | Auto-Quant | autoresearch-crypto |
|------|-----------|---------------------|
| 项目阶段 | prototype / 研究平台 | 接近 demo execution candidate |
| LLM 可写范围 | `user_data/strategies/` (Python) | `research_workspace/` (先 YAML, 后 .py) |
| 策略形式 | FreqTrade IStrategy class | BaseStrategy subclass + param dict |
| 评估指标 | robust_sharpe (多时间窗口 min) | 多维 Pareto gate (见 §3) |
| 执行语义 | FreqTrade 模拟撮合 | 自研 backtest + safe-execution replay |
| 基线冻结 | 无固定基线 | v2.1 balanced 是冻结基线 |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   IMMUTABLE (LLM 不得修改)                │
│                                                         │
│  research_oracle.py       固定评估入口                    │
│  dex/                     策略框架 + 评估器               │
│  backtest_quant.py        回测引擎                       │
│  data/crypto/             OHLCV 数据                     │
│  checkpoints/             已验证 checkpoint              │
│  live_bitget_quant.py     实盘 (Bitget)                  │
│  live_okx_quant.py        实盘 (OKX)                     │
│  live_binance_quant.py    实盘 (Binance)                 │
│  dex/live/common.py       实盘共享库                      │
│  dex/regime_permissions.py 风控权限                       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   LLM SANDBOX (只允许写这里)              │
│                                                         │
│  research_workspace/                                    │
│  ├── candidates/           YAML/JSON 参数 spec           │
│  ├── strategy_variants/    .py 策略变体 (需审批)          │
│  ├── proposals/            跨策略探索笔记                  │
│  └── notes/                自由观察记录                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   HUMAN-EDITED                           │
│                                                         │
│  program_research.md      LLM 研究剧本                    │
│  results.tsv              人类可读事件日志 (gitignored)    │
│  experiments.jsonl        机器可读完整记录 (gitignored)    │
└─────────────────────────────────────────────────────────┘
```

---

## 3. research_oracle.py — Interface Draft

`research_oracle.py` 是固定评估入口。LLM 只能调用它，不能修改它。

### 3.1 CLI

```bash
# 评估已有的 checkpoint（使用 v2.1 frozen benchmark 的同一数据和切分）
uv run python research_oracle.py \
    --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
    --mode replay-v21 \
    --output results/latest.json

# 评估 candidate spec (YAML)，70/30 IS/OOS fixed split
uv run python research_oracle.py \
    --candidate research_workspace/candidates/exp_0001.yaml \
    --symbol ETHUSDT --interval 5m --days 2600 \
    --mode fixed-split --split-ratio 0.70 \
    --output results/latest.json

# 评估策略变体 (.py)，rolling window 验证
uv run python research_oracle.py \
    --variant research_workspace/strategy_variants/channel_breakout_variant_001.py \
    --symbol ETHUSDT --interval 5m --days 2600 \
    --mode rolling --rolling-windows "6m,12m,18m" \
    --output results/latest.json

# 评估 filter/overlay candidate（与 baseline 组合评估）
uv run python research_oracle.py \
    --candidate research_workspace/candidates/exp_0007.yaml \
    --symbol ETHUSDT --interval 5m --days 2600 \
    --mode fixed-split \
    --combined-with checkpoints/channel_breakout_v2_1_balanced.pt \
    --output results/latest.json
```

### 3.1.1 Evaluation Modes

| Mode | Description | Typical Use |
|------|-------------|-------------|
| `replay-v21` | 使用 v2.1 frozen benchmark 的同一数据文件、同一 IS 区间、同一 OOS 窗口 | 与 baseline 直接比较 |
| `fixed-split` | 指定天数，按 `--split-ratio` 切 IS/OOS（默认 70/30），OOS 作为单次 hold-out | 新实验的首次评估 |
| `rolling` | 指定天数，按 `--rolling-windows` 做 rolling walk-forward（每窗 IS+OOS），报告 OOS mean/min | 稳定性验证 |

### 3.1.2 Data Provenance (recorded in experiments.jsonl)

每次 oracle 运行必须记录以下字段，确保实验可复现：

```json
{
  "data": {
    "dataset_path": "data/crypto/ETHUSDT_5m_2600d.parquet",
    "data_hash": "sha256:abc123",
    "data_start": "2019-05-01",
    "data_end": "2026-06-13",
    "is_start": "2019-05-01",
    "is_end": "2023-12-01",
    "oos_start": "2023-12-01",
    "oos_end": "2026-06-13",
    "split_method": "fixed-split",
    "split_ratio": 0.70,
    "rolling_windows": null
  }
}
```

### 3.2 Input formats

**Candidate YAML spec** (preferred first-class input):

```yaml
# research_workspace/candidates/exp_0001.yaml
experiment_id: exp_0001
parent_id: null                    # null = from baseline
strategy: channel_breakout
candidate_role: standalone         # standalone | filter | overlay | ensemble_component
description: "测试熊市缩短通道 + 牛市长通道"
params:
  bull:
    entry_lookback: 500
    min_hold_bars: 432
    enable_short: false
    extra_conditions:
      - "连续3天close < EMA50 → 禁多"
  bear:
    entry_lookback: 300            # 熊市更短通道，更快反应
    min_hold_bars: 432
    enable_short: true
  neutral:
    entry_lookback: 375
    min_hold_bars: 432
    enable_short: true
    directional_only: true
execution:
  safe_reversal: true              # 使用 close-confirm-open 语义
  regime_filter:
    fast_days: 50
    slow_days: 200

# --- filter / overlay / ensemble_component 额外字段 ---
# 当 candidate_role != standalone 时，以下字段必填：
# base_strategy: channel_breakout_v2_1_balanced
# filter:
#   family: adaptive_hybrid
#   action: block_entries_when_filter_flat
# evaluation:
#   compare_to: v2_1_baseline
#   combined_metrics_required: true
```

**`candidate_role` 定义：**

| Role | 含义 | 评估方式 |
|------|------|---------|
| `standalone` | 独立策略，替代 v2.1 | 自身指标 vs baseline |
| `filter` | 辅助过滤器，叠加到 baseline 上 | filter-alone + combined_with_v21 双报告 |
| `overlay` | 信号覆盖层（如 regime permission 变体） | overlay-alone + combined_with_v21 双报告 |
| `ensemble_component` | 组合策略中的一个组件 | component-alone + ensemble 整体报告 |

**Strategy variant (.py)** — 仅在 YAML 路径验证有价值后使用：

```python
# research_workspace/strategy_variants/channel_breakout_variant_001.py
# 必须继承 BaseStrategy，必须可被 oracle 通过 importlib 加载
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy

class ChannelBreakoutVariant001(ChannelBreakoutTrendStrategy):
    """实验变体：加入 trailing stop 出场逻辑"""
    ...
```

### 3.3 Output — structured text block

```
=== RESEARCH ORACLE RESULTS ===
experiment_id:    exp_0001
strategy:         channel_breakout
timestamp:        2026-06-13T14:30:00Z
data_hash:        sha256:abc123
commit:           a1b2c3d

--- IS (2022-11 ~ 2026-06, 1300d) ---
is_return:        2.521
is_dd:            -0.336
is_sharpe:        0.85
is_trades:        255

--- OOS Walk-Forward ---
oos_return_mean:  2.180
oos_dd_mean:      -0.310
oos_sharpe_mean:  0.78

--- Rolling Windows ---
rolling_6m_min_return:   0.15
rolling_12m_min_return:  0.45
rolling_6m_min_sharpe:   0.20
rolling_12m_min_sharpe:  0.45

--- Safe Execution ---
safe_return:      2.571
safe_dd:          -0.332
safe_sharpe:      0.87
safe_trades:      255
execution_parity: 0.98          # correlation(safe_equity, raw_equity)

--- Regime Breakdown ---
bull:
  return:          -0.05
  trades:          30
  contribution:    -0.03
bear:
  return:          2.10
  trades:          150
  contribution:    1.80
neutral:
  return:          0.52
  trades:          75
  contribution:    0.75

--- vs Baseline (v2.1 balanced) ---
correlation:      0.72
excess_return:    0.38
excess_sharpe:    0.05
dd_delta:         -0.004           # negative = worse DD

--- Combined with Baseline ---
# 仅当 candidate_role != standalone 时输出此块
combined_return:  2.85
combined_dd:       -0.29
combined_sharpe:    0.91
combined_trades:    162
combined_safe_return: 2.92
combined_safe_dd:   -0.28
excess_vs_baseline:  0.33             # combined_return - baseline_return
dd_improvement:       0.046            # baseline_dd - combined_dd (positive = better)

--- Sensitivity ---
fee_sensitivity:
  fee_0bp_return:   2.65
  fee_2bp_return:   2.52
  fee_4bp_return:   2.38
  fee_10bp_return:  2.10
slippage_sensitivity:
  slip_0bp_return:  2.58
  slip_2bp_return:  2.52
  slip_5bp_return:  2.42

--- Flags ---
status:           PASS
warnings:         []
disqualifications: []
```

### 3.4 Output — JSON object (written to `--output`)

```json
{
  "experiment_id": "exp_0001",
  "parent_id": null,
  "candidate_role": "standalone",
  "timestamp": "2026-06-13T14:30:00Z",
  "strategy": "channel_breakout",
  "params_hash": "sha256:def456",
  "data_hash": "sha256:abc123",
  "commit": "a1b2c3d",

  "metrics": {
    "is": {
      "return": 2.521, "dd": -0.336, "sharpe": 0.85, "trades": 255,
      "win_rate": 0.48, "annual_return": 0.38, "annual_vol": 0.45
    },
    "oos": {
      "return_mean": 2.180, "dd_mean": -0.310, "sharpe_mean": 0.78
    },
    "rolling": {
      "6m_min_return": 0.15, "12m_min_return": 0.45,
      "6m_min_sharpe": 0.20, "12m_min_sharpe": 0.45
    },
    "safe_execution": {
      "return": 2.571, "dd": -0.332, "sharpe": 0.87, "trades": 255,
      "execution_parity": 0.98
    },
    "regime": {
      "bull": {"return": -0.05, "trades": 30},
      "bear": {"return": 2.10, "trades": 150},
      "neutral": {"return": 0.52, "trades": 75}
    },
    "vs_baseline": {
      "correlation": 0.72, "excess_return": 0.38,
      "excess_sharpe": 0.05, "dd_delta": -0.004
    },
    "combined_with_baseline": {
      "combined_return": 2.85, "combined_dd": -0.29,
      "combined_sharpe": 0.91, "combined_trades": 162,
      "combined_safe_return": 2.92, "combined_safe_dd": -0.28,
      "excess_vs_baseline": 0.33, "dd_improvement": 0.046
    },
    "sensitivity": {
      "fees": {"0bp": 2.65, "2bp": 2.52, "4bp": 2.38, "10bp": 2.10},
      "slippage": {"0bp": 2.58, "2bp": 2.52, "5bp": 2.42}
    }
  },

  "execution_config": {
    "safe_reversal": true,
    "regime_filter": {"fast_days": 50, "slow_days": 200},
    "dd_guard": null
  },

  "params": {
    "bull": {"entry_lookback": 500, "min_hold_bars": 432, "enable_short": false},
    "bear": {"entry_lookback": 300, "min_hold_bars": 432, "enable_short": true},
    "neutral": {"entry_lookback": 375, "min_hold_bars": 432, "enable_short": true, "directional_only": true}
  },

  "flags": {
    "status": "PASS",
    "warnings": [],
    "disqualifications": []
  }
}
```

### 3.5 Disqualification rules (oracle enforces)

| Flag | Condition | Effect |
|------|-----------|--------|
| `DD_OVER_50` | IS DD < -50% | Auto-reject |
| `DD_OVER_40` | IS DD < -40% | Warning, needs manual review |
| `TRADES_UNDER_30` | n_trades < 30 (1300d) | Warning, likely overfit |
| `OOS_DEGRADE` | OOS mean return < IS return * 0.5 | Warning, unstable |
| `EXEC_PARITY_LOW` | execution_parity < 0.85 | Warning, live execution may differ |
| `ROLLING_NEGATIVE` | rolling_6m_min_return < 0 | Auto-reject |
| `CORR_BASELINE_099` | correlation > 0.99 vs baseline | Warning, trivial variant |
| `FEE_FRAGILE` | fee_10bp_return < 0 | Warning, not robust to fee changes |
| `DATA_LEAK` | OOS data in IS window | Auto-reject |

---

## 4. results.tsv — Schema

**Location:** `results.tsv` (gitignored, survives `git reset --hard`)
**Purpose:** 人类可读事件日志，按时间排序，每次实验一行

### Columns

```
1.  timestamp            ISO-8601 UTC, e.g. 2026-06-13T14:30:00Z
2.  experiment_id        唯一 ID, e.g. exp_0001
3.  parent_id            父实验 ID (null = from baseline)
4.  candidate_role       候选角色: standalone | filter | overlay | ensemble_component
5.  event                事件类型: propose | evaluate | promote | reject | kill
6.  strategy_family      策略家族: channel_breakout | adaptive | hybrid_mm | ...
7.  params_hash          参数 SHA256 前 7 位
8.  is_return            IS 总收益 (decimal)
9.  is_dd                IS 最大回撤 (negative decimal)
10. is_sharpe            IS Sharpe
11. safe_return          safe-execution 总收益
12. safe_dd              safe-execution 最大回撤
13. oos_return_mean      OOS 平均收益
14. rolling_12m_min      滚动 12 个月最小收益
15. combined_return      与 baseline 组合收益 (仅 filter/overlay, standalone 填 N/A)
16. combined_dd          与 baseline 组合 DD   (仅 filter/overlay, standalone 填 N/A)
17. trades_per_year      年化交易次数
18. corr_vs_v21          与 v2.1 balanced 的 equity 相关性
19. oracle_score         综合评分 (0-1, oracle 计算)
20. status               PASS | WARN | REJECT
21. disqualifications    拒绝原因 (pipe 分隔, e.g. DD_OVER_50|ROLLING_NEGATIVE)
22. decision             keep | discard | promote
23. note                 简短描述
```

### Example rows

```
timestamp	experiment_id	parent_id	candidate_role	event	strategy_family	params_hash	is_return	is_dd	is_sharpe	safe_return	safe_dd	oos_return_mean	rolling_12m_min	combined_return	combined_dd	trades_per_year	corr_vs_v21	oracle_score	status	disqualifications	decision	note
2026-06-13T14:30:00Z	exp_0001	null	standalone	propose	channel_breakout	a1b2c3d	2.521	-0.336	0.85	2.571	-0.332	2.180	0.45	N/A	N/A	71	0.72	0.68	PASS		keep	缩短熊市通道到300，牛市延长到500
2026-06-13T14:37:00Z	exp_0002	exp_0001	standalone	propose	channel_breakout	d4e5f6g	3.100	-0.520	0.72	3.050	-0.540	2.400	-0.15	N/A	N/A	45	0.55	0.00	REJECT	DD_OVER_50|ROLLING_NEGATIVE	discard	过度激进参数，DD和滚动收益都不达标
2026-06-13T14:45:00Z	exp_0007	null	filter	propose	adaptive_hybrid	g7h8i9j	N/A	-0.12	0.15	N/A	-0.11	N/A	-0.05	2.85	-0.29	162	0.42	0.72	PASS		keep	Adaptive filter叠加v2.1，DD从-0.336降到-0.29
```

---

## 5. experiments.jsonl — Schema

**Location:** `experiments.jsonl` (gitignored)
**Purpose:** 机器可读完整记录，每行一个 JSON object，供脚本和 LLM 解析

### Fields (one JSON object per line)

```json
{
  "experiment_id": "exp_0001",
  "parent_id": null,
  "timestamp": "2026-06-13T14:30:00Z",
  "event": "propose",
  "commit": "a1b2c3d7",
  "data_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "env": {
    "python_version": "3.10.0",
    "torch_version": "2.9.1",
    "platform": "linux",
    "cuda_version": "12.8"
  },

  "strategy": {
    "family": "channel_breakout",
    "class": "ChannelBreakoutTrendStrategy",
    "params": {
      "bull": {"entry_lookback": 500, "min_hold_bars": 432, "enable_short": false},
      "bear": {"entry_lookback": 300, "min_hold_bars": 432, "enable_short": true},
      "neutral": {"entry_lookback": 375, "min_hold_bars": 432, "enable_short": true}
    },
    "params_hash": "sha256:def456"
  },

  "execution": {
    "safe_reversal": true,
    "regime_filter": {"fast_days": 50, "slow_days": 200},
    "dd_guard": null,
    "slippage_model": "fixed_2bp",
    "fee_model": "fixed_2bp"
  },

  "data": {
    "symbol": "ETHUSDT",
    "interval": "5m",
    "days": 1300,
    "is_start": "2022-11-01",
    "is_end": "2026-06-13",
    "oos_windows": 4,
    "hash": "sha256:abc123"
  },

  "metrics": { /* 同 oracle JSON output 的 metrics 块 */ },

  "flags": {
    "status": "PASS",
    "warnings": [],
    "disqualifications": []
  },

  "decision": {
    "action": "keep",
    "reason": "熊市通道缩短带来 excess_return=0.38，DD 没有明显恶化"
  },

  "artifacts": {
    "candidate_spec": "research_workspace/candidates/exp_0001.yaml",
    "equity_curve_csv": null,
    "trade_ledger_csv": null
  }
}
```

---

## 6. research_workspace/ — Directory Structure

```
research_workspace/
├── README.md                         # LLM 工作规则（见单独文件）
├── candidates/                       # YAML/JSON 参数 spec（LLM 主要写这里）
│   ├── exp_0001.yaml
│   ├── exp_0002.yaml
│   └── ...
├── strategy_variants/                # .py 策略变体（需人工审批后使用）
│   ├── _TEMPLATE.py                  # 变体模板
│   └── .gitkeep
├── proposals/                        # 跨策略探索笔记
│   ├── _TEMPLATE.md
│   └── .gitkeep
├── notes/                            # 自由观察记录
│   └── .gitkeep
└── .gitignore                        # 忽略临时文件，但提交 YAML/proposals
```

**`.gitignore` 内容：**
```
*.pyc
__pycache__/
*.tmp
*.log
```

注意：`candidates/*.yaml`、`proposals/*.md`、`strategy_variants/*.py` **应该**被 git 追踪
（它们是人类审查后的实验记录），但 `results.tsv` 和 `experiments.jsonl` 不被追踪。

---

## 7. Dual-Lane Review System

### Exploration Lane (自由探索)

```
LLM 可以自由做：
  ✓ 提出任何跨策略想法（Scalp 的过滤器、Grid 的仓位管理、Regime 的信号组合）
  ✓ 创建 proposal 文档：假设 + 预期效果 + 风险评估
  ✓ 创建 candidate YAML spec
  ✓ 在 notes/ 记录观察
  ✓ (Phase 2+) 运行 oracle 评估 candidate（只读调用）

LLM 不能做：
  ✗ 修改 dex/ 下的策略代码
  ✗ 修改 oracle
  ✗ 修改 checkpoints/
  ✗ 修改 live 代码
  ✗ 修改 data/
  ✗ (Phase 1) 运行 oracle — oracle 尚未实现，Phase 1 是纯设计阶段
  ✗ 声称 proposal 的结果可以实盘

Exploration lane 的所有结果都是 hypotheses，不是 trading recommendations。
```

### Promotion Lane (正式评审)

晋升门禁按 `candidate_role` 分为两组：

#### Standalone 候选

候选自身必须独立满足全部条件：

| Gate | Requirement |
|------|-------------|
| Oracle PASS | status == PASS, no disqualifications |
| Safe execution | execution_parity >= 0.90 |
| Rolling OOS | rolling_12m_min_return > 0 |
| DD ceiling | is_dd >= -0.40 |
| Correlation | corr_vs_v21 < 0.95 (提供多样化价值) |
| Fee robustness | fee_10bp_return > 0 |
| Trade count | trades_per_year >= 20 |
| Human review | proposal read + approved by human |

#### Filter / Overlay / Ensemble Component 候选

候选自身指标仅供参考。晋升判定基于 **combined_with_v21** 指标：

| Gate | Requirement |
|------|-------------|
| Oracle PASS | status == PASS, no disqualifications (candidate 自身) |
| Combined DD | combined_dd >= baseline_dd (DD 不恶化) |
| Combined safe return | combined_safe_return > baseline_safe_return **或** combined_dd > baseline_dd + 0.02 (addition must be net beneficial) |
| Rolling combined | combined rolling_12m_min_return >= baseline rolling_12m_min_return |
| Fee robustness | combined fee_10bp_return > 0 |
| Correlation vs baseline | corr_vs_v21 < 0.98 (不能只是 trivial filter) |
| Human review | proposal read + approved by human |

**关键原则**: filter/overlay 不需要自身盈利。它只需要在叠加到 v2.1 后产生净改善。
如果改善量在统计噪声范围内（DD 改善 < 2% 且 return 改善 < 5%），标记为 marginal，不 promotion。

晋升后的候选进入 `promoted/` 子目录（人工创建），但**仍然不直接接入 live**。
需要额外的 paper-trade 或 demo --once 验证后才能考虑实盘。

---

## 8. Frozen Baseline

`checkpoints/channel_breakout_v2_1_balanced.pt` 是冻结基线：

- **不得被任何 agent 覆盖或修改。**
- 所有实验的 `vs_baseline` 指标都以它为基准。
- 如果新候选的 `corr_vs_v21 > 0.99`，它是 v2.1 的 trivial variant，不做 promotion。
- 基线的更新只能由人类在手动 review 后执行。

当前基线参数：

```
BULL  (U4_cons3): entry_lookback=375, min_hold_bars=432, enable_short=False,
                  连续3天 close < EMA50 → 禁多
BEAR  (K0_base):  entry_lookback=375, min_hold_bars=432, bidirectional
NEUTRAL (N3_dir): entry_lookback=375, min_hold_bars=432, bidirectional,
                  directional only
```

---

## 9. Scope Boundaries — Phase 1 vs Future

### Phase 1 (本轮 — DESIGN ONLY)

- [ ] `docs/research_sandbox_design.md` (本文档)
- [ ] `research_workspace/README.md`
- [ ] `program_research.md` 草案
- [ ] `results.tsv` schema
- [ ] `experiments.jsonl` schema
- [ ] `research_workspace/` 空目录结构 + templates

明确不做：
- [ ] 不实现 `research_oracle.py`
- [ ] 不启动 LLM agent loop
- [ ] 不跑 GEPA/ATLAS
- [ ] 不改 live 代码
- [ ] 不改 checkpoint
- [ ] 不接入 demo

### Phase 2 (demo --once 通过后)

- [ ] 实现 `research_oracle.py` 最小可用版本
- [ ] 手工跑 3-5 个 candidate 验证 oracle 输出正确
- [ ] 启动单轮 LLM 实验（非 loop，人工 review 后手动下一步）

### Phase 3 (oracle 验证通过后)

- [ ] 启动有限 LLM loop（每轮需人工确认，非全自动）
- [ ] 并行运行 GEPA/ATLAS 做局部参数搜索
- [ ] LLM 负责高层假设和方向选择

### Phase 4 (长期)

- [ ] LLM + GEPA/ATLAS 混合模式全自动运行
- [ ] 跨币种独立实验
- [ ] 多策略组合 / ensemble

---

## 10. Key Invariants

这些是设计层面的硬约束，LLM 和未来的人类开发者都必须遵守：

1. **Oracle immutability**：`research_oracle.py` 的评估逻辑、数据切分方式、手续费/滑点
   假设一旦确定就冻结。任何"improvement"必须创建新的 oracle version，
   且旧 oracle 的结果仍然有效。

2. **Baseline immutability**：v2.1 balanced checkpoint 不得被任何程序覆盖。

3. **Live / sandbox separation**：没有任何代码路径可以从 `research_workspace/`
   直接调用 live 交易函数。sandbox 只能读、不能写 live state。

4. **Data provenance**：每个实验记录 data_hash，确保可复现。
   如果数据文件被更新（如下载了更多历史数据），所有旧实验结果仍然有效
   （因为 data_hash 不同）。

5. **No metric gaming**：不允许通过修改 evaluator、手续费、滑点、数据切分、
   或交易语义来提高分数。不允许只报告加了 guard/regime-filter 的结果
   而隐藏 raw 结果。

6. **Cross-symbol requires independent validation**：v2.1 balanced 仅在
   ETHUSDT 5m 上验证。任何声称适用于 BTC/SOL 的实验必须独立运行完整 oracle，
   不得直接泛化。
