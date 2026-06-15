# Candidate Status — Updated 2026-06-15

## Current Robust Baseline
v2.1 balanced
- 1300d OOS +156.6%, DD -33.8%, Sharpe 2.35
- 2600d OOS +252.2%, DD -33.6%, Sharpe 1.31
- Status: current robust baseline (passes both cycles)

## Research Candidates

### exp_0012 (L250 + ADX20)
Status: research_candidate_recent_regime_only
- 1300d: WIN (+183.8%, DD -33.8%)
- 2600d: FAIL (DD_OVER_50, rolling catastrophic)
- Not for demo/live.

### exp_0013_adaptive_lb_v0 (BULL=375, BEAR=250, NEUTRAL=500)
Status: failed_1300d_gate
- Failure: discrete regime lookback transition instability
- 1300d IS -25.8%, DD -55.4%, 186 trades, FEE_FRAGILE
- Transition handling is unsolved: regime flip recalculates channel -> false breakouts
- 2600d not tested (1300d gate fail).

## Next Research
Transition-guarded adaptive lookback (Phase 3C continued):
- Lock lookback during open positions
- Transition cooldown after regime flip (N bars no-new-entry)
- New regime must stabilize M bars before lookback change
- Conservative trigger: use max(old trigger, new trigger) for long

See: docs/phase3c_design.md
