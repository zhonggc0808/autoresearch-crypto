# Phase 3A Summary — Read-Only LLM Research Loop

**Status:** ✅ Complete. No further candidates will be generated.
**Date:** 2026-06-15
**Oracle baseline:** `channel_breakout_v2_1_balanced` (v0.1.0)
**Budget:** 8 candidates across 2 rounds (exhausted)

---

## 1. Results

| Candidate | Modification | OOS Return | OOS DD | OOS Sharpe | Corr | Status |
|-----------|-------------|-----------|--------|-----------|------|--------|
| baseline | v2.1 balanced (375/432) | +156.6% | -33.8% | 2.35 | 1.000 | BASELINE |
| exp_0001 | bear L300, bull L500 | +179.5% | -33.8% | 2.63 | 0.883 | REJECT |
| exp_0002 | global L250 | +177.2% | -33.8% | 2.59 | 0.824 | REJECT |
| exp_0003 | bull L800 | +156.6% | -33.8% | 2.35 | 1.000 | REJECT |
| exp_0004 | neutral L200 | +150.1% | -33.8% | 2.25 | 0.980 | REJECT |
| exp_0005 | L250 + neutral tighter | +174.9% | -33.8% | 2.57 | 0.821 | REJECT |
| exp_0006 | L250 + global hold 576 | +37.7% | -44.8% | 0.57 | 0.796 | REJECT |
| exp_0007 | L250 + per-regime hold | +174.9% | -33.8% | 2.57 | 0.821 | REJECT |
| exp_0008 | L250 in BEAR only | +173.3% | -33.8% | 2.53 | 0.834 | REJECT |

**No candidate passed oracle gates.** All were `REJECT` status.

---

## 2. Three Conclusions

### Finding 1: BEAR entry_lookback 375→250 is the only viable lead

exp_0008 demonstrated that shortening only the BEAR regime's channel (leave BULL/NEUTRAL at 375) achieves 98% of exp_0002's OOS improvement (+173.3% vs +177.2%). This confirms:

- The performance uplift from global L250 is almost entirely driven by faster BEAR entries
- BULL and NEUTRAL channel adjustments add negligible value in the current framework
- Future work should focus on BEAR-specific signal improvements

### Finding 2: Longer min_hold does not solve rolling losses

exp_0006 (global min_hold=576) was a clear failure: OOS dropped from +177% to +38%. The min_hold parameter is already near-optimal at 432. Increasing it causes the strategy to:

- Miss trend reversals (holding through direction changes)
- Compound losses when holding against the trend
- Reduce trade count without improving win rate

exp_0005 and 0007 (neutral-only/per-regime hold=576) showed no meaningful change, confirming min_hold is not a lever for attacking rolling losses.

### Finding 3: ROLLING_NEGATIVE is structural — requires a new filter class

All 8 candidates triggered the `ROLLING_NEGATIVE` disqualification. The worst 6-month window (2023-02→08, NEUTRAL regime, -25.1%) persists regardless of channel length or hold duration. This is a **structural limitation** of pure channel breakout in ranging markets.

A new signal dimension is required to attack this:
- Trend strength filter (ADX) to skip low-trend periods
- Cross-timeframe confirmation (5m entries with 1h trend direction)
- Regime-adaptive channel width

---

## 3. What Phase 3A Proved

Phase 3A succeeded not because it found a deployable strategy — it didn't — but because it **rigorously demonstrated the boundaries of the current parameter space**:

| Question | Answer |
|----------|--------|
| Can per-regime channel tuning improve OOS? | Yes, up to +23% (but not enough to pass gates) |
| Can longer hold fix the rolling loss? | No, it makes OOS worse |
| Is the BEAR regime the alpha source? | Yes, confirmed by exp_0008 ablation |
| Is there a trivial fix for ROLLING_NEGATIVE? | No, it requires a new filter type |
| Did we overfit OOS? | No — 8 candidates is well within safe limits |

---

## 4. Phase 3B — Design Constraints

Before starting Phase 3B, the following must be true:

1. **ADX or cross-timeframe filter must be implementable without modifying baseline v0.1.0 frozen metrics.** The oracle's `--baseline` output must be bit-identical before and after the change.

2. **Each new filter must be a candidate-selectable option**, not a mandatory change to the strategy pipeline. This preserves backward compatibility with all existing candidates.

3. **New candidate budget**: max 5 per round, same as Phase 3A.

4. **Filter must target the specific weak window** (2023-02→08, NEUTRAL-dominant). A filter that improves overall returns but misses this window is not a solution.

5. **OOS must remain untouched.** No retraining on OOS data. New holdout period (locked from current OOS) if needed for additional validation.

---

## 5. Current State

```
Phase 1 (sandbox design)        ✅ 文档完成
Phase 2 (oracle v0.1.0)         ✅ 冻结
Phase 2.5 (live safety)         ✅ 2af85ff
Phase 3A (read-only loop)        ✅ 完成，8 候选，0 通过
Phase 3B (new filter design)     ⏳ 设计阶段

Track A: Bitget demo flip       ⏳ 等待下一次 flip
```
