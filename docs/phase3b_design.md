# Phase 3B Design — Candidate-Selectable Filters for ROLLING_NEGATIVE

**Status:** ADX gate implemented, tested, and closed. No further ADX tuning.
**Date:** 2026-06-15
**Updated:** 2026-06-15 — ADX proven ineffective on ETH 5m (mean ADX 35-37, <20 bars only 5-6%).
**Target:** Worst 6-month window (2023-02→08, NEUTRAL 61.6%, return -25.1%)

---

## 1. Problem

v2.1 balanced and all Phase 3A candidates share a common disqualification:
`ROLLING_NEGATIVE`. The worst 6-month window is concentrated in NEUTRAL regime
(61.6% NEUTRAL, 38.4% BULL, 0% BEAR). In NEUTRAL regime, the strategy uses
N3_dir (directional only, bidirectional). Channel breakouts generate repeated
false signals in ranging markets.

No parameter adjustment (channel length, hold duration, per-regime tuning)
can fix this. A new signal dimension is required.

---

## 2. Constraint: `--baseline` Must Be Bit-Identical

This is non-negotiable. The frozen baseline v0.1.0 oracle output must not
change when new filters are added. Design:

### Option A: Filter is a candidate-level overlay

The filter is applied AFTER the core strategy signal generation, BEFORE the
final signal output. The filter is part of the candidate evaluation pipeline,
not the baseline pipeline.

```
baseline path:    checkpoint → _generate_v21_signals → metrics   (unchanged)
candidate path:   candidate → _generate_v21_signals → FILTER → metrics  (new)
```

The filter function:
- Takes raw signals + market data as input
- Returns modified signals
- Is never called during `--baseline` mode
- Lives in a new module `dex/filters.py`

### Option B: Filter is a strategy parameter

The filter logic is embedded inside `_generate_v21_signals()` only when
specific params are present. The baseline params (no filter keys) produce
identical output.

**Preference: Option A.** It keeps baseline isolation trivially verifiable.
Option B creates risk of accidental baseline contamination.

---

## 3. Candidate Direction 1: ADX Trend Strength Filter (Preferred)

### How it works

ADX (Average Directional Index) measures trend strength on a 0-100 scale.
Low ADX (< 20-25) indicates a ranging/consolidating market. The filter
blocks new position entries when ADX is below a threshold.

### ADX is already available

The v2.1 pipeline already computes ADX at line 232:
```python
adx_full, _, _ = compute_adx(df, 14)
```

It is currently only used for `build_permission_arrays()`. The ADX array
exists in memory during signal generation. The filter can reuse it.

### Parameters

```yaml
filter:
  type: adx_gate
  adx_period: 14            # ADX period (default 14, matches existing)
  adx_threshold: 20         # Minimum ADX to allow entry (0=disabled)
  adx_apply_to: [bear]      # Which regimes to filter: bull, bear, neutral
```

### Expected effect

- NEUTRAL regime (typically low ADX): blocks entries during ranging → fewer
  false breakouts → reduced drawdown in the worst 6-month window
- BEAR regime (often high ADX): mostly unaffected → preserves alpha
- BULL regime (moderate ADX): may reduce entry frequency but also reduce
  whipsaw

### Risk

- ADX is lagging (14-period lookback). A strong trend start is initially
  low-ADX, so the filter may delay entries during early trend stages.
- Parameter sensitivity: ADX threshold is data-dependent. 20 may be too
  restrictive for ETHUSDT 5m data.

### Implementation cost: Low

- New function: `apply_adx_filter(signals, adx_array, threshold, apply_to)`
- Called in oracle's run_oracle() when candidate specifies filter config
- No new dependencies
- Test via existing oracle test harness

---

## 4. Candidate Direction 2: Cross-Timeframe Confirmation

### How it works

Require alignment between 5m signal and 1h trend direction. For example:
- 5m LONG entry only if 1h EMA50 slope > 0
- 5m SHORT entry only if 1h EMA50 slope < 0

### Data requirement

Requires loading both 5m and 1h data. The 1h data must be aligned to 5m
bars (each 5m bar gets the prevailing 1h trend direction).

### Parameters

```yaml
filter:
  type: cross_tf_confirmation
  tf: 1h                      # Confirmation timeframe
  ma_period: 50               # Trend MA on confirmation timeframe
  require_alignment: true     # If true, block entries against 1h trend
  apply_to: [neutral]         # Which regimes to enforce (default: neutral only)
```

### Expected effect

- NEUTRAL regime: most benefit — 5m false breakouts filtered by 1h trend
- BEAR/BULL regime: moderate benefit — alignment already high

### Risk

- **Data alignment complexity.** Each 5m bar needs the correct 1h bar label.
  Wrong alignment = lookahead bias.
- **1h data may not be available.** The prepare_crypto.py downloads 5m data.
  Adding 1h data loading adds a dependency.
- **Delayed entries.** A strong 5m breakout may be in the direction of 1h
  but misaligned by a few bars.
- **Future function risk.** The 1h bar's EMA50 at bar close uses the full
  bar. When aligning to 5m bars, you must use the *previous* 1h bar's
  completed value, not the current one.

### Implementation cost: Medium-High

- New data loading for 1h data
- New alignment logic
- New filter function
- Higher test burden (data dependency)

---

## 5. Recommendation: Start with ADX

| Criteria | ADX Gate | Cross-TF |
|----------|---------|-----------|
| Implementation cost | Low | Medium-High |
| Data dependency | None (already loaded) | New 1h data required |
| Lookahead risk | None (ADX uses past bars) | Medium (alignment) |
| Directly attacks NEUTRAL whipsaw | Strong | Strong |
| Risk of harming BEAR alpha | Low (BEAR has high ADX) | Low |
| Parameter sensitivity | Moderate | Low |
| Baseline isolation | Trivial (new function) | Medium (new data path) |

**Start with ADX.** If ADX fails to solve ROLLING_NEGATIVE within 3
candidates, consider cross-TF as a fallback.

---

## 6. Oracle Extension Design

### New file: `dex/filters.py`

```python
def apply_adx_filter(
    signals: np.ndarray,
    adx: np.ndarray,
    threshold: float = 20.0,
    apply_to: Optional[List[str]] = None,
    regimes: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Block new entries when ADX is below threshold.

    Args:
        signals: Raw strategy signals (0-3).
        adx: ADX values (same length as signals).
        threshold: Minimum ADX to allow entry.
        apply_to: Which regimes to filter (None=all).
        regimes: Daily regime labels for per-regime filtering.

    Returns:
        Modified signals.
    """
```

### Changes to `scripts/research_oracle.py`

Add a new `--candidate` processing step:

```python
# After signal generation, before evaluation
if candidate_role != "standalone" and filter_config:
    from dex.filters import apply_adx_filter
    signals_raw_full = apply_adx_filter(
        signals_raw_full, adx_full,
        threshold=filter_config.get("adx_threshold", 20),
        apply_to=filter_config.get("apply_to"),
        regimes=regimes_full,
    )
```

This is ONLY called when `--candidate` specifies a filter. The `--baseline`
path never touches this code. Bit-identical guarantee preserved.

### Filter config in candidate JSON

```json
{
  "experiment_id": "exp_0009",
  "parent_id": "exp_0002",
  "candidate_role": "standalone",
  "filter": {
    "type": "adx_gate",
    "adx_threshold": 20,
    "apply_to": ["neutral"]
  },
  "params": {
    "strategy_type": "regime_permission_channel_breakout",
    ...
  }
}
```

---

## 7. Candidate Budget — Phase 3B

| Round | Candidates | Focus | Budget |
|-------|-----------|-------|--------|
| 1 | 5 max | ADX threshold + apply_to variants | 5 |
| 2 | 3 max | ADX × BEAR L250 combo | 3 |
| (fallback) | 3 max | Cross-TF if ADX fails | 3 |

Total max: 11 candidates (Phase 3A budget was 8, Phase 3B design is more
constrained, so +3 is acceptable).

---

## 8. Success Criteria

A candidate passes Phase 3B if:
1. No `ROLLING_NEGATIVE` disqualification (primary)
2. `DD_OVER_50` not triggered
3. OOS return ≥ +150% (within 10% of baseline)
4. Fee sensitivity at 10bp: OOS return still positive
5. Correlation vs baseline < 0.95 (not a trivial pass-through)

---

## 9. Implementation Order

1. ✅ This design document
2. Create `dex/filters.py` with `apply_adx_filter()`
3. Extend `research_oracle.py` to support `--candidate` filter config
4. Test: --baseline bit-identical (existing smoke test)
5. Test: ADX filter on known NEUTRAL window
6. Generate Phase 3B round-1 candidates (max 5)
7. Evaluate and report

Steps 2-7 are Phase 3B implementation. Do not start until this design
document is reviewed and approved.
