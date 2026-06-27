# Decisions

Last updated: 2026-06-27

This file records durable research and safety decisions. It does not activate live routing, promote checkpoints, or change scoring by itself.

## Active Decisions

| Decision | Status | Reason | Evidence |
|---|---|---|---|
| Use `channel_breakout_v2_2_m375_bbm375_1p5` as current ETH signal core | Active baseline | Current handoff and later gate diagnostics use this as the base | `docs/codex/CURRENT_STATE.md` |
| Keep TimesFM `exp_0068` as main gate | Active baseline | Most validated conservative gate; catches 2021 tail-loss cases Moirai missed | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| Promote Moirai2 `exp_0093` to challenger shadow only | Shadow candidate | Better multi-window profile, fewer blocks, high retention; still not main | `research_workspace/llm_candidates/exp_0093_moirai2_primary_shadow.json` |
| Keep TimesFM `exp_0070` as aggressive reference only | Observe | Strong upside and fee-stress performance, but fragile in shorter windows and lower retention | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| Treat ETH as default deployment universe | Active | BTC has been weak/choppy and SOL has false-breakout risk in prior notes | `docs/codex/CURRENT_STATE.md` |

## Protected Decisions

| Area | Decision |
|---|---|
| Live execution | No live/demo routing change without explicit approval |
| Checkpoints | No checkpoint creation, deletion, or promotion without explicit approval |
| Oracle | Do not modify `scripts/research_oracle.py` unless the task is explicitly oracle implementation |
| Scoring | Do not replace established risk-adjusted scoring with pure Sharpe |
| ChannelBreakout | Do not modify `dex/strategies/channel_breakout.py` without explicit approval |

## Closed Or Paused Research Lines

| Branch | Status | Reason | Evidence |
|---|---|---|---|
| `cooldown_after_drawdown` | Closed | Blocks recovery entries and worsens metrics | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| `neutral_regime_entry_block` | Closed | NEUTRAL covers too much data; blanket block cuts too many profitable trades | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| `volatility_gate` | Paused | Threshold sweeps could not solve fee-vs-rolling tradeoff | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| Broad entry-block filters | Closed as main direction | Multiple filter families failed to improve the actual risk problem | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| Adaptive lookback switching | Closed/stale | Earlier discrete switching was unstable on long data | `docs/current/research_document_index_2026-06-17.md` |
| Regime EMA sensitivity sweep | Closed | Prior audit kept 50/200; no need to restart without new evidence | `docs/current/research_document_index_2026-06-17.md` |
| B2N / BTN transition scans | Closed | No actionable signal | `research_workspace/research_decision_register_v0.md` |
| N2B carried-long flatten | Closed | Diffuse/noisy, do not implement | `research_workspace/research_decision_register_v0.md` |
| Rolling12m long/risk branches | Closed | No patch or contract recommended | `research_workspace/research_decision_register_v0.md` |
| Market-structure shift filter | Closed/inconclusive | No filter authorized | `research_workspace/research_decision_register_v0.md` |

## Frozen Carry-Forward Artifact

`dex.execution_safety.apply_n2b_1d_block_reversals_only` is a frozen, opt-in local execution-safety helper.

Allowed interpretation:

- It applies only to `NEUTRAL -> BEAR` 1d warmup direct reversals.
- Blocked reversal becomes flat.
- Flat entries are not blocked.
- Carried positions are not changed.
- Runtime activation remains off unless separately approved.

Forbidden interpretation:

- It is not a baseline fix.
- It is not a rolling12m risk fix.
- It is not a live/demo fix.
- It is not a new strategy family.
- It does not authorize execution routing or scoring changes.

## Decision Update Rule

When adding a new decision, include:

- decision name
- status: `ACTIVE`, `SHADOW`, `OBSERVE`, `PAUSED`, `REJECTED`, or `CLOSED`
- evidence files
- exact reason
- what the decision does not authorize
