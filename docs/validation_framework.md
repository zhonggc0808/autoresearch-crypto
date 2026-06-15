# Validation Framework

## Rules (established 2026-06-15)

1300d = 加分项 (当前 regime 适配)
2600d = 否决项 (长期稳健性)

## Decision Matrix

| 1300d | 2600d | Status |
|-------|-------|--------|
| Win | Win | current best candidate |
| Win | Lose | research candidate only |
| Lose | Win | research candidate |
| Lose | Lose | discard |

## Gate Requirements

All candidates must pass:
1. 2600d full-cycle backtest
2. 1300d recent-cycle backtest
3. 6m rolling min (no catastrophic degradation vs baseline)
4. 12m rolling min (no catastrophic degradation vs baseline)
5. Fee stress at 10bp
6. Safe execution parity >= 0.99
7. No DD_OVER_50 on either 1300d or 2600d

If 1300d and 2600d conflict: 2600d risk takes priority.

## Current Status

v2.1 balanced = current robust baseline
  - 1300d OOS +156.6%, DD -33.8%
  - 2600d OOS +252.2%, DD -33.6%
  - Passes both cycles

exp_0012 = research_candidate_recent_regime_only
  - 1300d OOS +183.8%, DD -33.8% (win)
  - 2600d OOS +161.9%, DD -50.7% (FAIL: DD_OVER_50, rolling catastrophic)
  - Not eligible for baseline upgrade
  - Not for demo/live
