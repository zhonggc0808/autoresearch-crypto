# Research Status — 2026-06-15

## Current Robust Baseline
v2.1 balanced
- 1300d OOS +156.6%, DD -33.8%, Sharpe 2.35
- 2600d OOS +252.2%, DD -33.6%, Sharpe 1.31
- Track A demo/live baseline
- Awaiting Bitget flip

## Defensive Candidate
exp_0015_bear_only_channelbreakout
- status: defensive_candidate_research_only
- passes: 1300d + 2600d, no DD_OVER_50
- validated: regime causality, state-machine
- limitation: EMA sensitivity untestable (oracle v0.1.0 hardcodes 50/200)
- not for demo/live
- not replacing v2.1 balanced

## Closed Research Tracks
Phase 3A (param search): 8 candidates, 0 pass
Phase 3B (ADX filter): ADX <20 only 5-6% on ETH 5m
Phase 3C (adaptive lookback): discrete switching unstable
Phase 4A (regime permission): BEAR-only validated
Phase 4B (regime router): BEAR-only = minimal safe router
Phase 4C (live-safe audit): causality + state-machine pass
Phase 4D (EMA sensitivity): oracle v0.1.0 hardcodes 50/200

## Known Limitation
Oracle v0.1.0 hardcodes build_daily_regime_labels(fast_days=50, slow_days=200)
in _generate_v21_signals and run_oracle. regime_filter parsed but unused.
Not fixing v0.1.0. Would require Phase 5 / oracle v0.2.

## Next Action
Track A: Bitget demo flip. Live safety / oracle alignment / state machine.
