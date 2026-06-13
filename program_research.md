# program_research.md — LLM Research Script

**Status:** DRAFT — Phase 1 design only. The research loop is NOT active.
This file will be the entry point for the LLM agent when Phase 3 begins.

---

## What This Is

This is the research script for an LLM agent operating in the autoresearch-crypto
sandbox. It follows the [autoresearch](https://github.com/karpathy/autoresearch)
paradigm — the human edits this file to shape research behavior, the LLM executes
experiments within the sandbox constraints.

The LLM's job: propose hypotheses, design experiments, evaluate them through the
oracle, and iterate — all without touching live code, checkpoints, or the oracle itself.

---

## 1. Frozen Baseline

`checkpoints/channel_breakout_v2_1_balanced.pt` is the frozen baseline.

Parameters:
```
BULL  (U4_cons3): entry_lookback=375, min_hold_bars=432, enable_short=False,
                  连续3天 close < EMA50 → 禁多
BEAR  (K0_base):  entry_lookback=375, min_hold_bars=432, bidirectional
NEUTRAL (N3_dir): entry_lookback=375, min_hold_bars=432, bidirectional,
                  directional only
```

**You may NEVER modify or overwrite this checkpoint.**
All experiments are compared against it.

---

## 2. Research Scope — Phase 1

In Phase 1, you are limited to:

| Allowed | Forbidden |
|---------|-----------|
| Explore ChannelBreakout v2.1 parameter variations | Modify live_bitget_quant.py |
| Propose Adaptive / HybridMM as filter candidates | Modify live_okx_quant.py |
| Write candidate YAML specs in `research_workspace/candidates/` | Modify checkpoints/ |
| Write proposal documents in `research_workspace/proposals/` | Modify research_oracle.py |
| Write observation notes in `research_workspace/notes/` | Modify dex/ |
| (Phase 2+) Run oracle evaluation (read-only CLI call) | Modify data/crypto/ |
| (Phase 2+) Read oracle output and results.tsv | Modify backtest_quant.py |
| | Modify prepare_crypto.py |
| | (Phase 1) Run oracle — oracle 尚未实现 |
| | Create .py strategy variants (Phase 2+) |
| | Claim trading readiness |

**Phase 1 scope — strategy families:**
- ✅ ChannelBreakout v2.1 parameter exploration (as `candidate_role: standalone`)
- ✅ Adaptive / HybridMM as auxiliary filters (as `candidate_role: filter`)
- ✅ Proposals for any cross-strategy idea (Scalp/Grid/Regime filters or combinations)
- ❌ RegimeStrategy / GridStrategy / Scalp as primary strategies (can propose but not enter main review loop)
- ❌ MultiTF ensemble (Phase 3+)

**Phase 1 scope — symbol:**
- ETHUSDT 5m only. Cross-symbol (BTC, SOL) proposals are allowed in `proposals/` but
  will NOT be evaluated in Phase 1.

---

## 3. The Experiment Loop (Phase 3 — NOT ACTIVE)

When the research loop is activated, the LLM follows this cycle:

```
LOOP FOREVER:
  1. READ STATE
     - Read results.tsv (last ~30 rows)
     - Read experiments.jsonl for recent full results
     - Identify current best candidate and its metrics

  2. FORM HYPOTHESIS
     - Based on: historical results, observed patterns, known constraints
     - Write hypothesis in proposal or candidate description
     - Every hypothesis must be falsifiable

  3. DESIGN EXPERIMENT
     - Create candidate YAML spec in research_workspace/candidates/
     - Use sequential experiment_id (exp_NNNN)
     - Reference parent_id

  4. COMMIT
     - git add research_workspace/candidates/exp_NNNN.yaml
     - git commit -m "experiment: exp_NNNN — <one-line description>"

  5. RUN ORACLE
     - uv run python research_oracle.py \
         --candidate research_workspace/candidates/exp_NNNN.yaml \
         --symbol ETHUSDT --interval 5m --days 1300 \
         --output /tmp/oracle_result.json
     - Redirect stdout to run.log (NOT tee — don't flood context)

  6. PARSE RESULTS
     - Check status field: PASS | WARN | REJECT
     - If crash: read tail -50 run.log, diagnose, fix or discard
     - Extract key metrics: is_return, is_dd, safe_return, rolling_12m_min,
       corr_vs_v21, trades_per_year

  7. LOG
     - Append to results.tsv
     - Append to experiments.jsonl
     - Do NOT commit results.tsv or experiments.jsonl

  8. DECIDE
     - If oracle_score improved AND flags are clean → KEEP commit
     - If oracle_score worse OR flagged → git reset --hard HEAD~1, document why
     - If trivial variant (corr_vs_v21 > 0.99) → DISCARD regardless of score

  9. CONTINUE
     - If stuck (>5 consecutive discards): write a reflection in notes/,
       consider switching direction, propose cross-strategy idea
     - NEVER stop on your own — the human interrupts you
```

---

## 4. Decision Rules

### When to KEEP

- `oracle_score` improves vs parent
- No disqualifications (DD_OVER_50, ROLLING_NEGATIVE, DATA_LEAK)
- `corr_vs_v21 < 0.99` (not a trivial baseline clone)
- `execution_parity >= 0.90`

### When to DISCARD

- `oracle_score` <= parent's score
- Any disqualification flag
- `corr_vs_v21 >= 0.99` (trivial variant — score improvement is noise)
- Improvement in return but DD crosses -40% (risk budget exceeded)
- Improvement in Sharpe but trades < 20/year (likely overfit)

### Tie-breaking

When two candidates have similar oracle_score (±0.02):
- Prefer lower DD
- Prefer higher trades/year
- Prefer lower correlation with baseline (more diversification value)
- Prefer simpler parameter set (fewer non-default values)

---

## 5. Exploration Lane — Free Ideas

You are ENCOURAGED to think broadly. The exploration lane has no strategy-family
restrictions. You can propose:

- Using Scalp's volatility filter as a regime detector for ChannelBreakout
- Grid position sizing logic applied to trend-following entries
- Regime strategy's market-state classification replacing EMA-based regime filter
- Multi-timeframe confirmation (5m + 1h) for breakout signals
- Trailing stop exits instead of pure flip-on-breakout
- Volume profile confirmation for channel breakouts
- Any combination, filter, or adaptation

**How to explore:**
1. Write a proposal in `proposals/` explaining the idea
2. If it's directly testable on ChannelBreakout, create candidate YAML specs
3. If it requires a new strategy class, document the requirement — do NOT
   create the .py file yourself in Phase 1
4. All exploration lane results are HYPOTHESES, not trading recommendations

---

## 6. Promotion Criteria

Promotion gates differ by `candidate_role`.

### Standalone candidates

| Gate | Requirement | Rationale |
|------|-------------|-----------|
| Oracle | status == PASS, no disqualifications | Basic quality |
| Execution | execution_parity >= 0.90 | Live won't deviate from backtest |
| Rolling | rolling_12m_min_return > 0 | Profitable in every 12-month window |
| DD ceiling | is_dd >= -0.40 | Risk budget |
| Diversity | corr_vs_v21 < 0.95 | Adds value beyond baseline |
| Fee robustness | fee_10bp_return > 0 | Survives fee regime changes |
| Trade count | trades_per_year >= 20 | Statistical significance |
| Human review | proposal read and approved | Final safety check |

### Filter / Overlay / Ensemble Component candidates

Candidate's own metrics are for reference only. Promotion decided by **combined_with_v21**:

| Gate | Requirement | Rationale |
|------|-------------|-----------|
| Oracle | status == PASS (candidate自身), no disqualifications | Basic quality |
| Combined DD | combined_dd >= baseline_dd (DD不恶化) | Don't increase risk |
| Combined benefit | combined_safe_return > baseline_safe_return **OR** combined_dd > baseline_dd + 0.02 | Must be net beneficial |
| Rolling combined | combined rolling_12m_min >= baseline rolling_12m_min | No worse in any window |
| Fee robustness | combined fee_10bp_return > 0 | Survives fee changes |
| Diversity | corr_vs_v21 < 0.98 | Not a trivial pass-through |
| Human review | proposal read and approved | Final safety check |

**Key principle**: filters/overlays do NOT need to be profitable on their own.
They only need to produce a net improvement when combined with v2.1.
Marginal improvements (DD improvement < 2% AND return improvement < 5%) are NOT promoted.

**You (the LLM) cannot promote candidates.** You can only flag them as
"promotion-ready" and wait for human review.

---

## 7. Output Rules

### Must-report metrics (never cherry-pick)

For every experiment, you MUST report:
- Raw (no guard) IS return, DD, Sharpe
- Regime-permission-filtered return, DD, Sharpe
- Safe-execution return, DD, Sharpe
- Rolling 12-month minimum return
- Correlation vs v2.1 baseline
- Fee sensitivity at 0bp, 2bp, 4bp, 10bp

You must NOT report only "after guard" results while hiding raw results.

### Log format

```
timestamp	experiment_id	parent_id	event	strategy_family	params_hash	is_return	is_dd	is_sharpe	safe_return	safe_dd	oos_return_mean	rolling_12m_min	trades_per_year	corr_vs_v21	oracle_score	status	disqualifications	decision	note
```

Fields are TAB-separated. Do NOT use commas (they break in descriptions). See
`docs/research_sandbox_design.md` §4 for full schema.

---

## 8. What You Can NEVER Do

This section overrides any other instruction if there is a conflict.

### ❌ Modify evaluation infrastructure

- `research_oracle.py` — FROZEN
- `dex/strategies/base.py` (StrategyEvaluator)
- `dex/scoring.py`
- `dex/config.py` (fee/slippage/capital assumptions)
- `backtest_quant.py`
- Data splits, data files, `prepare_crypto.py`

### ❌ Modify live trading code

- `live_bitget_quant.py`
- `live_okx_quant.py`
- `live_binance_quant.py`
- `live_nado_quant.py`
- `dex/live/common.py`
- `dex/regime_permissions.py`

### ❌ Modify checkpoints

- `checkpoints/channel_breakout_v2_1_balanced.pt` — FROZEN BASELINE
- Any other checkpoint file

### ❌ Modify project configuration

- `pyproject.toml`
- `CLAUDE.md`
- `program.md`
- `.gitignore`

### ❌ Game the metrics

- Do NOT change fee assumptions to make returns look better
- Do NOT cherry-pick favorable time windows
- Do NOT hide raw results behind guard-filtered results
- Do NOT claim cross-symbol validity without independent evaluation
- Do NOT claim trading readiness from exploration lane results

---

## 9. Session Start Checklist

When activated (Phase 3+), each new session starts with:

1. [ ] Read `CLAUDE.md` and `program_research.md` (this file)
2. [ ] Read `results.tsv` — understand recent experiment history
3. [ ] Read `research_workspace/README.md` — confirm sandbox rules
4. [ ] Read `docs/research_sandbox_design.md` §3 (oracle interface)
5. [ ] Check `git status` — which branch, any uncommitted changes
6. [ ] Verify oracle is runnable: `uv run python research_oracle.py --help`
7. [ ] Confirm data exists: `ls data/crypto/ETHUSDT_5m_1300d.parquet`
8. [ ] Read last 5 `proposals/` for context
9. [ ] Read last 5 `notes/` for observations
10. [ ] Form first hypothesis — begin loop

---

## 10. Timeout and Crash Handling

- Each oracle run should complete within ~30 seconds (single backtest, no training)
- If oracle exceeds 60 seconds, kill it
- If oracle crashes: read the error, diagnose, fix the YAML spec (not the oracle)
- If same error occurs 3 times: mark experiment as `crash` in results.tsv, move on
- Never modify oracle to "fix" a crash

---

## 11. Meta-Reflection (Every 10 Experiments)

After every ~10 experiments (or when stuck), write a reflection in `notes/`:

```markdown
# Reflection — YYYY-MM-DD

## Recent experiments
- exp_NNNN to exp_NNNN: <brief summary of what was tried>

## Patterns observed
- <what consistently works>
- <what consistently fails>
- <surprises>

## Blind spots
- <what haven't we tried?>
- <what assumptions haven't been tested?>

## Next directions
- <top 3 hypotheses for next batch>
```

---

## Phase Status

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Design | **ACTIVE** | Scaffolding only, no loop, no implementation |
| 2 — Oracle | PENDING | Implement oracle, validate with 3-5 manual candidates |
| 3 — Limited loop | PENDING | LLM loop with human checkpoint each round |
| 4 — Full autonomous | PENDING | LLM + GEPA/ATLAS hybrid, multi-symbol |
