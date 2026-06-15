# Phase 4: Regime Routing
## Status: Concept design. No live changes.

Phase 3 proved: ChannelBreakout alpha is concentrated in BEAR.
Phase 4 asks: which regime should the strategy be active in?

## Phase 4A: Regime Permission Validation
Candidates:
  exp_0015: BEAR-only ChannelBreakout
  exp_0016: NEUTRAL-suppressed (flat in NEUTRAL)
  exp_0017: Non-BEAR flat router (trade only in BEAR)

Method: Use existing regime filter + permission config in candidate JSON.
No new strategy classes needed.

Constraints: v2.1 baseline unchanged. Track A demo unchanged.
All candidates research-only. Must pass validation framework.
