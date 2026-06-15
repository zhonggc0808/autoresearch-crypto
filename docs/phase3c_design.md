# Phase 3C Design — Regime-Adaptive Lookback

**Status:** Concept design — no implementation, no candidates.
**Date:** 2026-06-15
**Based on:** exp_0012 finding — global L250 works on 1300d but fails on 2600d.

---

## 1. Problem Definition

exp_0012 (global L250) revealed a clear tension:

| Cycle | Baseline L375 | exp_0012 L250 | Winner |
|-------|--------------|---------------|--------|
| 1300d (2022-11 → 2026-06) | +156.6% | +183.8% | L250 |
| 2600d (2019-09 → 2026-06) | +252.2% | +161.9% | L375 |

L250 is not universally better. It outperforms in the recent market regime but
underperforms across the full cycle. The optimal lookback depends on market
conditions that change over time.

**Core hypothesis:** A single fixed lookback cannot be optimal across all
market regimes. The lookback should adapt to current conditions — shorter in
trending/volatile regimes, longer in ranging/stable regimes.

---

## 2. Candidate Mechanisms

### 2A. Regime-Based Lookback (highest priority)

Use the existing BULL/BEAR/NEUTRAL regime labels to select per-regime lookback,
but make the lookback **adaptive to recent regime duration** rather than fixed.

**Example:** If the current regime has persisted for >200 days (EMA200 warmup),
use a shorter lookback (L250). If the regime recently transitioned, use a
longer lookback (L375) until the new regime is confirmed.

**Why this might work:** The transition period between regimes is the most
dangerous time for a channel breakout — false signals spike. A longer lookback
during transitions filters noise, while a shorter lookback during established
regimes captures trends more quickly.

### 2B. Volatility-Based Lookback

Use ATR or rolling standard deviation to detect market regime shifts.

| Volatility State | Lookback | Rationale |
|-----------------|----------|-----------|
| High (top 25%) | L250 | Fast-moving markets need quick entry |
| Normal (25-75%) | L375 | Default |
| Low (bottom 25%) | L500+ | Slow markets need wider channels to avoid noise |

**Risk:** Volatility is itself volatile and can whipsaw the lookback parameter,
causing instability.

### 2C. Trend-Strength Lookback

Use ADX as a secondary signal (not primary gate).

| ADX | Lookback | Rationale |
|-----|----------|-----------|
| >30 (strong trend) | L250 | Capture strong moves faster |
| 20-30 (moderate) | L375 | Default |
| <20 (weak/ranging) | L500+ | No trade or wider channel |

**Warning:** ADX on ETH 5m averages 35-37 across all regimes with only 5-6% of
bars below 20. This makes ADX a weak differentiator on this instrument.

### 2D. Rolling-Performance Guard (safety layer)

A meta-rule: if the strategy's trailing 6-month return drops below a threshold
(e.g. -20%), revert all lookbacks to baseline L375 regardless of other signals.
This prevents adaptive mechanisms from amplifying losses during regime
transitions.

---

## 3. Evaluation Framework (from docs/validation_framework.md)

All Phase 3C candidates must pass:

| Gate | Requirement | Type |
|------|-------------|------|
| 2600d | No DD_OVER_50, rolling not catastrophic | Veto |
| 1300d | Preference signal, not required to beat baseline | Signal |
| Rolling 6m | No catastrophic degradation vs baseline | Veto |
| Rolling 12m | No catastrophic degradation vs baseline | Veto |
| Fee 10bp | Still positive on both cycles | Veto |
| Safe exec | Parity >= 0.99 | Veto |

**exp_0012 would have failed** the 2600d, rolling 6m/12m, and DD_OVER_50 gates.
This is the correctness check for the framework.

---

## 4. Implementation Order

1. ✅ This design document
2. Implement regime-adaptive lookback in candidate JSON format
3. Add oracle support for adaptive parameters (parse, apply, evaluate)
4. Test: baseline unchanged (parameter change only in --candidate path)
5. Generate Phase 3C candidates (max 5):
   - Regime-adaptive L250/L375
   - Volatility-based L250/L375/L500
   - ADX-based L250/L375 (fallback test)
   - Rolling-performance guard variant
6. Validate all against 1300d + 2600d
7. Decision: upgrade, keep research, or discard

---

## 5. Prohibited

- Fixed global L250 (proven unstable on 2600d)
- ADX threshold sweep (proven ineffective on ETH 5m)
- Direct demo/live entry without passing validation framework
- Bypassing 2600d veto gate
- Modifying baseline checkpoint, oracle frozen metrics, or live trading code

---

## 6. Success Criteria

A Phase 3C candidate that:
1. Passes all 7 validation gates on both 1300d and 2600d
2. Shows regime-dependent lookback behavior (not just fixed L250 again)
3. Does not trigger DD_OVER_50 on either cycle
4. Rolling 6m/12m within baseline range
5. Fee 10bp robust on both cycles
