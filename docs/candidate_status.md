# Candidate Status — Updated 2026-06-15

## Current Robust Baseline
v2.1 balanced
- 1300d OOS +156.6%, DD -33.8%, Sharpe 2.35
- 2600d OOS +252.2%, DD -33.6%, Sharpe 1.31
- Status: current robust baseline (passes both cycles)

## Research Candidates

### exp_0012 (L250 + ADX20)
Status: research_candidate_recent_regime_only
- 1300d: WIN (+183.8%, DD -33.8%, Sharpe 2.69)
- 2600d: FAIL (DD_OVER_50, rolling catastrophic, return -90% vs baseline)
- Not for demo/live. Research telemetry only.
- Discovery: global L250 works in recent regime but fails across full cycle.

All future candidates validated against: docs/validation_framework.md
