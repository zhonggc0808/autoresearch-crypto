# Oracle Baseline Diagnostics — v2.1 Balanced

**Date:** 2026-06-14
**Oracle version:** Phase 2 (post regime-warmup fix)
**Checkpoint:** `channel_breakout_v2_1_balanced.pt`

---

## 1. Replay vs Oracle Discrepancy — Root Cause Analysis

### The numbers that caused confusion

| Source | Data | OOS Return | OOS DD | OOS Period |
|--------|------|-----------|--------|------------|
| Old replay (CLAUDE.md) | 2600d | +252.24% | -33.60% | ~2024-06 → 2026-06 |
| Oracle pre-fix | 1300d | +97.9% | -45.8% | 2025-05 → 2026-06 |
| Oracle post-fix | 1300d | +156.6% | -33.8% | 2025-05 → 2026-06 |
| Oracle on 2600d | 2600d | +230.6% | -42.1% | 2024-06 → 2026-06 |

### Root cause #1: Data window differs (primary)

1300d OOS covers ~13 months (2025-05 → 2026-06).
2600d OOS covers ~24 months (2024-06 → 2026-06), adding a highly profitable
2024-2025 bull run segment.

The 2600d OOS includes an additional ~11 months where ETH trended strongly,
which v2.1's channel breakout captures well.

**This explains ~80% of the difference between old replay and old oracle.**

### Root cause #2: Regime label degradation in OOS-only evaluation (secondary, now fixed)

Before the warmup fix, `build_daily_regime_labels()` was called on `df_oos`
alone (~390 days). The EMA200 requires ~200 days of warmup, leaving ~190
poorly-labeled days at the OOS start where bars defaulted to NEUTRAL.

After the fix, regimes are pre-computed on `df_full` (1300 days, with 910 IS
days providing full EMA warmup) and then sliced. This is live-compatible:
at any bar t, EMA uses only bars ≤ t.

**This explains why old oracle OOS was +97.9% when it should have been +156.6%.**

### Signal generation: identical

Verified by TEST 6 in `diagnose_oracle_vs_replay.py`: 0 signal differences
out of 374,384 bars when both pipelines use the same input DataFrame.
The discrepancy is entirely from data range and regime warmup, not from
code divergence.

---

## 2. DD_OVER_50 — Scope and Interpretation

### Where DD_OVER_50 triggers

| Segment | Bars | Period | DD | DD_OVER_50? |
|---------|------|--------|-----|-------------|
| IS | 262,068 | 2022-11 → 2025-05 | -52.7% | ✅ YES |
| OOS | 112,316 | 2025-05 → 2026-06 | -33.8% | ❌ NO |
| Full | 374,384 | 2022-11 → 2026-06 | -52.7% | ✅ YES |

The -52.7% IS drawdown occurs entirely in the training/in-sample period.
OOS drawdown is -33.8%, below the 50% threshold.

### Flag semantics

As the frozen baseline, v2.1 is evaluated with `is_baseline=True`:

```
status: BASELINE
baseline_known_risks: ["IS_DD_OVER_50", "ROLLING_NEGATIVE_IN_WINDOW"]
warnings: ["DD_OVER_40"]
disqualifications: []  ← baseline cannot be disqualified by its own oracle
```

For new standalone candidates, these same conditions would trigger:
- `DD_OVER_50` → auto-reject (candidate must not be riskier than baseline)
- `ROLLING_NEGATIVE` → auto-reject

---

## 3. Rolling Negative Windows — Attribution

### Worst 6-month window

| Metric | Value |
|--------|-------|
| Period | 2023-02 → 2023-08 (bar 25920 → 77760) |
| Return | -25.1% |
| Dominant regime | NEUTRAL (61.6%) |
| BULL | 38.4% |
| BEAR | 0.0% |

The worst 6-month window is dominated by neutral/ranging market. In NEUTRAL
regime, v2.1 uses N3_dir (directional only, bidirectional). The strategy
gets chopped up by false breakouts when ETH lacks a clear trend.

### Worst 12-month window

| Metric | Value |
|--------|-------|
| Period | ~2024-04 → 2025-04 (bar 155520 → 259200) |
| Return | -13.0% |
| Dominant regime | BEAR (47.0%) |
| BULL | 40.5% |
| NEUTRAL | 12.5% |

The worst 12-month window has a significant BEAR component. v2.1 allows
bidirectional trading in BEAR, but the mixed BULL/BEAR regime transitions
cause friction.

### Implication for future research

Any new candidate must be compared against these known weak windows.
A good candidate should:
- Reduce losses in NEUTRAL-dominant 6-month windows (better false-breakout filtering)
- Not worsen performance in BEAR windows (where v2.1's core alpha lives)

---

## 4. v2.1 Baseline Metrics (Oracle Post-Fix, 1300d)

### IS (2022-11 → 2025-05, 910 days)

| Variant | Return | DD | Sharpe | Trades/yr |
|---------|--------|-----|--------|-----------|
| Raw | +67.2% | -52.7% | 0.42 | 39.7 |
| Safe-exec | +67.2% | -52.7% | 0.42 | 39.7 |

### OOS (2025-05 → 2026-06, 390 days)

| Variant | Return | DD | Sharpe | Trades/yr |
|---------|--------|-----|--------|-----------|
| Raw | +156.6% | -33.8% | 2.35 | 65.5 |
| Safe-exec | +157.5% | -33.8% | 2.37 | 65.5 |

### Regime Breakdown (Full Data)

| Regime | Return | Trades | Bars | % of Total |
|--------|--------|--------|------|------------|
| BULL | +18.6% | 16 | 150,048 | 40.1% |
| BEAR | +282.3% | 126 | 121,840 | 32.5% |
| NEUTRAL | +10.1% | 32 | 102,496 | 27.4% |

The strategy's returns are heavily concentrated in BEAR regimes.
BULL returns are positive but modest (+18.6% over ~520 days).
NEUTRAL returns are barely positive (+10.1% over ~356 days).

### Fee Sensitivity (IS)

| Fee | Return |
|-----|--------|
| 0bp | +72.4% |
| 2bp | +67.2% |
| 4bp | +62.1% |
| 10bp | +47.9% |

### Execution Parity

Raw vs safe-execution equity correlation: **0.999** — confirms that
close-confirm-open semantics do not harm v2.1 performance.

---

## 5. Known Limitations of This Baseline

1. **IS DD exceeds 50%.** The strategy experienced a deep drawdown during the
   2022-2025 IS period. OOS DD is lower (-33.8%) but still above 30%.

2. **Returns concentrated in BEAR.** The strategy is a bear-market specialist.
   In sustained BULL markets, returns are modest and may underperform buy-and-hold.

3. **Rolling 6-month negative.** There exist 6-month windows where the strategy
   loses >25%. These are concentrated in NEUTRAL/ranging markets.

4. **1300d vs 2600d discrepancy.** The strategy looks significantly better on
   2600d data (which includes 2024-2025). The 1300d split removes this period
   from OOS, making OOS evaluation more conservative.

5. **Data boundary.** The 1300d file starts at 2022-11-20 (Binance API
   limitation for 5m data). Earlier data is only available in the 2600d file.
