# Current State

Last updated: 2026-06-27

## Source Of Truth

Use this file as the current-state entry point. Older handoff files are evidence, not the current operating source of truth when they conflict with this file.

## Repository Tooling

The code-save workflow is now skillized at `.agents/skills/save-code/`.

- It reviews Git scope, confirms live-risk classification, commits, pushes `origin/<current branch>`, and archives committed `HEAD` to a parent-directory `tar.gz`.
- It does not authorize live/demo routing, checkpoint, live config, oracle, protected strategy, scoring, or registry changes. Those still require separate explicit approval.

## Operating Baseline

Current ETH operating baseline remains:

- signal core: `channel_breakout_v2_2_m375_bbm375_1p5`
- checkpoint/config artifact: `checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json`
- main gate: TimesFM `exp_0068`
- live/demo gate config: `configs/live/timesfm_gate_exp_0068.json`
- research candidate: `research_workspace/llm_candidates/exp_0068.json`
- gate params: `context=1024`, `horizon=72`, `min_edge_pct=-0.01`, `risk_floor_pct=0.05`

Gate behavior:

- Forecast only at new entries and reversals.
- If a flat entry is blocked, hold/flat is preserved.
- If a reversal is blocked, first flatten instead of reversing.
- Normal exits, stop-loss logic, and runtime risk controls are not overridden by the forecast gate.

## Main Challenger

Moirai2 `exp_0093` is the current primary challenger shadow, not the main baseline.

- config: `configs/live/moirai2_gate_exp_0093.json`
- research spec: `research_workspace/llm_candidates/exp_0093_moirai2_primary_shadow.json`
- model: `Salesforce/moirai-2.0-R-small`
- local model path: `research_workspace/diagnostics/moirai_2_small`
- params: `context=1024`, `horizon=72`, `min_edge_pct=-0.02`, `risk_floor_pct=0.04`
- role: conservative primary challenger / demo-live runtime config
- fallback: TimesFM `exp_0068`

Known risk:

- Moirai2 has better multi-window profile in the final shortlist, but it missed TimesFM-only 2021 tail-loss cases.
- Do not replace TimesFM `exp_0068` as main without a separate promotion decision.

## Latest Research Focus

The current research line is gate attribution and drawdown repair:

- Compare TimesFM current gate versus Moirai2 variants.
- Measure which winners and losers are blocked.
- Attribute behavior by year and baseline drawdown bucket.
- Avoid broad entry-block filters; prior entry-block families failed or were paused.

Recent diagnostic evidence:

- `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md`
- `research_workspace/diagnostics/exp_0129_gate_focused_deep_dive.md`
- `research_workspace/diagnostics/exp_0130_gate_year_dd_slices.md`

Recent read:

- TimesFM `exp_0068` is still the most validated main gate.
- Moirai2 `-2%/4%` is a primary shadow challenger.
- Moirai2 `-2%/3%` shows cleaner drawdown repair in focused diagnostics, but is diagnostic-only and not the live shadow config.
- The shared blocked set between TimesFM and Moirai is small; models are complementary rather than duplicates.

## Current Non-Goals

Do not restart these lines without new upstream evidence:

- broad entry-block filters
- neutral-regime blanket blocking
- cooldown-after-drawdown entry blocking
- volatility-gate threshold sweeps as previously framed
- adaptive lookback switching
- regime EMA sensitivity sweeps
- B2N/BTN transition scans
- rolling12m long throttle/filter patches
- market-structure filters from the inconclusive scan

## Next Useful Work

Best next steps:

1. Validate live-shadow parity for `exp_0093` against research replay.
2. Continue attribution of Moirai `risk_floor=3%` versus live `risk_floor=4%`, but keep it diagnostic-only until promoted.
3. If proposing a gate promotion, produce raw / regime-permission / safe-execution / optional DD guard results and explicitly report top-winner damage.
4. Keep live/demo routing and checkpoint changes behind explicit approval.

## Current Protected Baseline Rule

No current research file authorizes:

- checkpoint change
- live routing change
- default oracle route change
- scoring change
- production strategy change
- TimesFM main gate replacement

Any of those requires a separate explicit task and approval.
