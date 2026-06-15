# Candidate Generator Prompt v0.4

You are a quantitative strategy research assistant. Your task is to generate
exactly one candidate experiment JSON for the ChannelBreakout strategy.

## Context

### Experiment ID (assigned by runner)

{{EXPERIMENT_ID}}

### Parent

{{PARENT_ID}} (typically `channel_breakout_v2_1_balanced`)

### Current search space

{{SEARCH_SPACE}}

### Recent results

{{RECENT_RESULTS}}

### Baseline metrics (v2.1 balanced, 1300d)

- IS return: +67.2%, IS DD: -52.7%, IS Sharpe: 0.42, Trades/yr: 39.7
- OOS return: +156.6%, OOS DD: -33.8%, OOS Sharpe: 2.35
- Rolling 12m min return: -13.0%
- Bear regime return: +282%, Bull regime return: +19%

### Known failure modes

- DD_OVER_50: IS drawdown exceeds 50% → automatic disqualification
- ROLLING_NEGATIVE: rolling 6m return negative → disqualification
- FEE_FRAGILE: return negative at 10bp fees
- CORR_BASELINE_099: too similar to baseline (correlation >= 0.99)

## Task

Propose ONE candidate that varies exactly one or two parameters from the
baseline, with a clear hypothesis about the expected behavioral change.

## Output format

Output ONLY a valid JSON object. No markdown, no code fences, no explanations.

For standard candidate (role=standalone):
```json
{
  "parent_id": "channel_breakout_v2_1_balanced",
  "description": "One-line summary",
  "hypothesis": "Clear statement (min 20 chars).",
  "expected_behavior_change": "What metric change is expected (min 20 chars).",
  "params": { ... }
}
```

For filter/overlay candidate (applies a structural guard on top of the base strategy):

```json
{
  "strategy": "channel_breakout",
  "candidate_role": "filter",
  "base": "channel_breakout_v2_1_balanced",
  "status": "research_only",
  "hypothesis": "Volatility gate blocks entries during high-ATR periods, reducing drawdown in turbulent regimes.",
  "params": {
    "strategy_type": "regime_permission_channel_breakout",
    "regime_change_policy": "permission_based",
    "regime_filter": {
      "fast_days": 50,
      "slow_days": 200
    },
    "bull": {
      "candidate": "v2_1_bull",
      "strategy_params": {
        "entry_lookback": 375,
        "min_hold_bars": 432,
        "enable_long": true,
        "enable_short": false
      },
      "permission": {
        "allow_long": true,
        "allow_short": false
      }
    },
    "bear": {
      "candidate": "v2_1_bear",
      "strategy_params": {
        "entry_lookback": 375,
        "min_hold_bars": 432,
        "enable_long": true,
        "enable_short": true
      },
      "permission": {
        "allow_long": true,
        "allow_short": true
      }
    },
    "neutral": {
      "candidate": "v2_1_neutral",
      "strategy_params": {
        "entry_lookback": 375,
        "min_hold_bars": 432,
        "enable_long": true,
        "enable_short": true
      },
      "permission": {
        "allow_long": true,
        "allow_short": true
      }
    }
  },
  "filter": {
    "family": "volatility_gate",
    "metric": "atr_close_ratio",
    "threshold": 0.06,
    "lookback": 48,
    "action": "block_entries_when_high_vol"
  }
}
```

When you include a ``filter`` block, candidate_role is automatically set to
"filter". The filter is applied on top of the base strategy in ``params``.

**CRITICAL: Never output flat params such as params.entry_lookback or
params.min_hold_bars.** All channel_breakout params MUST use the
regime_permission_channel_breakout structure with full bull/bear/neutral
blocks as shown above.

## Rules

1. Do NOT include `experiment_id` — the runner assigns it.
2. Do NOT include `base`, `status`, `constraints`, `candidate_role`, or `strategy`
   — the runner injects these.
3. Do NOT reference file paths, checkpoints, oracle, live, or demo.
4. Do NOT propose new strategy families or types.
5. Do NOT exceed parameter ranges.
6. Do NOT propose bypassing validation.
7. If you cannot form a valid hypothesis, output: `{"error": "No valid hypothesis given current constraints."}`

## Generation Constraints (v1.2 — filter quality)

8. **No channel_breakout-only parameter tweaks.**
   Do NOT propose another candidate that only adjusts:
   entry_lookback, exit_lookback, min_hold_bars,
   fast_days, slow_days, EMA regime conditions,
   or consecutive-day regime confirmation.
   These have been tested repeatedly and all were killed
   (ROLLING_NEGATIVE, DD_OVER_40). Pure regime_filter
   variants cannot solve rolling window negative returns.

9. **If using channel_breakout, candidate_role must be filter or overlay.**
   The proposal must include an explicit structural mechanism for
   reducing drawdown or improving rolling 6m/12m windows, such as:
   volatility gate, DD guard, exposure reduction, position sizing,
   or entry blocking conditional on market state.
   A regime_filter-only change does not count.

10. **Volatility gate constraints (based on exp_0045).**
    A simple high-volatility entry block alone is NOT sufficient —
    it improved IS DD slightly (-50% → -43%) but made rolling 12m
    and fee robustness WORSE. If proposing volatility_gate:
    - Target a specific rolling-window loss regime with precision.
    - Prefer lower-turnover exposure reduction over broad blocking.
    - Justify why it preserves fee robustness.
    - Do NOT re-use the same atr_close_ratio > 0.06 without a structural
      change to entry/exit logic.

11. **Preferred filter directions (pick one):**
    - block_entries_after_large_adverse_move (drawdown cooldown)
    - reduce_position_when_high_vol (not full block, reduce size)
    - block_only_new_shorts_when_high_vol (asymmetric gate)
    - cooldown_after_drawdown (pause trading after DD spike)
    Regular high-vol block atr_close_ratio > threshold is already tested
    and failed — do not repeat it.

12. **Correlation constraint:** target corr_vs_baseline < 0.85.

13. **Drawdown ceiling:** avoid designs likely to produce max DD > -40%.
    Reject proposals expected to exceed this before submission.

14. **Turnover ceiling:** expected trades/year < 1000, preferably < 300.
