# Research Workspace

**This is the ONLY directory you (the LLM agent) are allowed to modify.**

Everything outside `research_workspace/` is read-only for you.
Violating this boundary will cause your changes to be rejected.

## What You CAN Do Here

### ✅ Write candidate YAML specs in `candidates/`

This is your PRIMARY interface. Use YAML (not JSON) for human readability.

Template:
```yaml
experiment_id: exp_NNNN          # sequential, check latest in directory
parent_id: null                  # or previous experiment_id
strategy: channel_breakout       # or other registered strategy family
candidate_role: standalone       # standalone | filter | overlay | ensemble_component
description: "一句话描述假设"
params:
  # strategy-specific parameters
  bull: { ... }
  bear: { ... }
  neutral: { ... }
execution:
  safe_reversal: true
  regime_filter: { fast_days: 50, slow_days: 200 }
  dd_guard: null                 # or { max_dd: 0.25, recovery_dd: 0.15 }

# --- When candidate_role = filter / overlay / ensemble_component ---
# base_strategy: channel_breakout_v2_1_balanced
# filter:
#   family: adaptive_hybrid
#   action: block_entries_when_filter_flat
# evaluation:
#   compare_to: v2_1_baseline
#   combined_metrics_required: true
```

### ✅ Write proposals in `proposals/`

Cross-strategy ideas, filter combinations, novel architectures.
One `.md` file per proposal. Template:
```markdown
# Proposal: <标题>
Date: YYYY-MM-DD
Status: draft | submitted | rejected | accepted

## Hypothesis
<一句话假设>

## Motivation
<为什么这个方向值得探索：历史数据中的现象、失败的实验、论文启发>

## Expected Effect
<预期对 return / DD / Sharpe / trades 的影响方向和幅度>

## Risk
<可能的风险：过拟合、执行差异、市场结构变化>

## Experiment Plan
<具体的 candidate 序列或参数空间>
```

### ✅ Write free-form notes in `notes/`

Observations, patterns noticed in results, dead ends, meta-reflections.

### ✅ (Phase 2+) Evaluate candidates via oracle (READ-ONLY call)

**Note: oracle does NOT exist in Phase 1. This section is for Phase 2+ reference only.**

```bash
uv run python research_oracle.py \
    --candidate research_workspace/candidates/exp_NNNN.yaml \
    --symbol ETHUSDT --interval 5m --days 2600 \
    --mode fixed-split \
    --output /tmp/oracle_result.json
```

You do NOT have permission to modify `research_oracle.py`.

### ✅ Read oracle results

```bash
cat /tmp/oracle_result.json | python -m json.tool
```

## What You CANNOT Do

### ❌ Modify files outside research_workspace/

Specifically FORBIDDEN:
- `research_oracle.py`
- `dex/` (any file)
- `backtest_quant.py`
- `live_bitget_quant.py` / `live_okx_quant.py` / `live_binance_quant.py`
- `checkpoints/` (any file, especially `channel_breakout_v2_1_balanced.pt`)
- `data/crypto/` (any file)
- `prepare_crypto.py`
- `pyproject.toml`

### ❌ Modify evaluation to improve scores

- Do NOT change fee assumptions, slippage models, or data splits
- Do NOT cherry-pick time windows
- Do NOT report only "after guard" results while hiding raw results

### ❌ Claim trading readiness

- Exploration lane results are HYPOTHESES, never trading signals
- Only promoted candidates (with human approval) can be considered for execution

### ❌ Create Python strategy variants without approval

- `strategy_variants/` is read-only for you in Phase 1
- If you believe a variant is needed, propose it in `proposals/` first
- A human will create the `.py` file if the proposal is accepted

## Dual-Lane Rules

### Exploration Lane (you are here by default)

- You CAN propose any idea (Scalp filters, Grid position sizing, Regime combinations)
- You CAN create candidate specs and evaluate them
- You CAN document observations and patterns
- You CANNOT promote candidates yourself — promotion requires ALL gates (see below)

### Promotion Lane (human-gated)

Gates differ by `candidate_role`:

**Standalone candidates** — ALL must pass:
1. Oracle status == PASS, no disqualifications
2. Safe execution parity >= 0.90
3. Rolling 12-month minimum return > 0
4. IS drawdown >= -40%
5. Correlation vs v2.1 baseline < 0.95
6. Fee robustness: return stays positive at 10bp fees
7. Trade count >= 20 per year
8. Human has reviewed and approved

**Filter / Overlay candidates** — own metrics for reference, promotion based on combined_with_v21:
1. Oracle status == PASS (candidate自身)
2. combined_dd >= baseline_dd (DD不恶化)
3. combined_safe_return > baseline_safe_return OR combined_dd > baseline_dd + 0.02
4. combined rolling_12m_min >= baseline rolling_12m_min
5. combined fee_10bp_return > 0
6. corr_vs_v21 < 0.98
7. Human has reviewed and approved

Filter/overlay candidates do NOT need to be profitable alone.
Marginal improvements (DD < 2% AND return < 5%) are not promoted.

## Frozen Baseline

`checkpoints/channel_breakout_v2_1_balanced.pt` is the frozen baseline.
- Never overwrite it
- Never modify its parameters
- All experiments are compared against it
- A candidate with correlation > 0.99 vs baseline is a trivial variant

## Cross-Symbol Experiments

v2.1 balanced has ONLY been verified on ETHUSDT 5m.
- BTC/SOL experiments require INDEPENDENT oracle runs
- Do NOT claim BTC/SOL applicability without separate evaluation
- Cross-symbol findings should be documented in `proposals/`

## Research Loop (for reference only — DO NOT auto-execute in Phase 1)

The full loop (Phase 3+) would be:
1. Read `results.tsv` (last ~30 rows) and current `experiments.jsonl`
2. Form hypothesis based on historical results + observed patterns
3. Create candidate YAML spec in `candidates/`
4. Run oracle evaluation
5. Parse oracle output
6. If crash: diagnose, fix spec, retry OR mark as failed
7. Log to `results.tsv` and `experiments.jsonl`
8. If improved vs parent: keep candidate
9. If worse: document why, move on
10. Continue

In Phase 1, this loop is NOT active. You are only designing candidates and
proposals for human review.
