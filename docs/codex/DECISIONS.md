# Decisions

Last updated: 2026-06-28

This file records durable research and safety decisions. It does not activate live routing, promote checkpoints, or change scoring by itself.

## Active Decisions

| Decision | Status | Reason | Evidence |
|---|---|---|---|
| Use `channel_breakout_v2_2_m375_bbm375_1p5` as current ETH signal core | Active baseline | Current handoff and later gate diagnostics use this as the base | `docs/codex/CURRENT_STATE.md` |
| Keep TimesFM `exp_0068` as main gate | Active baseline | Most validated conservative gate; catches 2021 tail-loss cases Moirai missed | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| Promote Moirai2 `exp_0093` to challenger shadow only | Shadow candidate | Better multi-window profile, fewer blocks, high retention; still not main | `research_workspace/llm_candidates/exp_0093_moirai2_primary_shadow.json` |
| Keep TimesFM `exp_0070` as aggressive reference only | Observe | Strong upside and fee-stress performance, but fragile in shorter windows and lower retention | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| Treat ETH as default deployment universe | Active | BTC has been weak/choppy and SOL has false-breakout risk in prior notes | `docs/codex/CURRENT_STATE.md` |
| Keep `exp_0131` market-signal take-profit research-only | Observe | Ten variants passed the long-window diagnostic gate, but none also hit the 2026-06-23 Bitget short live-case. The rules that did hit the live-case had unacceptable top-winner damage and year instability. This does not authorize live/demo routing, checkpoint, execution, or production strategy changes. | `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.md`, `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp_live_case.csv` |
| Stop expanding unarmed wick/engulf/MACD market-TP combinations | Observe/Rejected subsets | `exp_0132` compressed exp_0131 into A/B/C buckets: A stays exit-review-only, B is `rejected_for_live`, C is REJECT/archive. Further work should require a new arming condition, such as unusually profitable trades by ATR or entry-risk multiple. This does not authorize live/demo routing, checkpoint, execution, or production strategy changes. | `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.md` |
| Keep `exp_0133` profit-armed market TP research-only | Observe/Rejected subsets | Arm-first ATR thresholds `{1.5, 2.0, 2.5, 3.0}` did not produce a live-ready row. Clean long-window rows did not hit the 2026-06-23 live-case; live-case rows still failed due `7/20` top-winner cuts and `5` losing year slices. This does not authorize live/demo routing, checkpoint, execution, config, oracle, or production strategy changes. | `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.md`, `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.csv`, `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow_live_case.csv` |
| Reject `exp_0134` riskoff trailing stop for live candidacy | Rejected | The trailing design protected the 2026-06-23 Bitget short replay, but all four rows failed hard gates: top20 winner cuts remained `7/20` and year slices were `3` wins versus `5` losses. This does not authorize live/demo routing, checkpoint, execution, config, oracle, or production strategy changes. | `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.md`, `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.csv`, `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop_live_case.csv` |
| Freeze market-signal exit line | Closed | Entry-block filters, market TP close-to-flat, ATR profit arming, and riskoff trailing after market signal all failed to produce a live-ready candidate. Do not reopen this line by parameter search without a different upstream signal family. | `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.md`, `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.md`, `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.md`, `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.md` |
| Reject `exp_0135` Donchian internal-return Stage 1 | Rejected | Native channel-structure diagnostic found no live-knowable bucket strong enough for Stage 2. The best direction bucket was `NEUTRAL short tol0bp`, but top20 exposure was high and 72-bar adverse follow-through was only near coin-flip. This does not authorize live/demo routing, checkpoint, execution, config, oracle, or production strategy changes. | `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.md`, `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.csv`, `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic_events.csv`, `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic_direction.csv` |
| Reject `exp_0136` core Donchian multitimeframe diversification Stage 1 | Rejected | The 15m/1h core Donchian sleeves had positive OOS and low daily correlation versus `5m_v22_moirai`, but standalone DD was severe and fixed-weight diagnostic combos did not improve baseline max DD by the required `15%`. `1h_fast` had DD desync but failed combo/OOS gates. This does not authorize live/demo routing, checkpoint, execution, config, oracle, or production strategy changes. | `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.md`, `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.csv`, `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_combos.csv`, `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_stage_gates.csv` |
| Keep `exp_0137` cross-asset core Donchian sanity research-only | Observe | BTC `1300d` and SOL `730d` both passed the minimal standalone sanity gate for core Donchian `m=375/h=432`: max DD was above `-85%` and OOS was positive. This only permits a full research diagnostic for cross-asset diversification; it does not authorize live/demo routing, checkpoint, execution, config, oracle, production strategy, or symbol-universe changes. | `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.md`, `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.csv`, `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.json` |
| Reject `exp_0137` ETH/BTC core Donchian overlap as shadow sleeve | Rejected | The BTC sleeve passed standalone gates, but overlap combinations failed the required `15%` relative DD-improvement gate and showed synchronized drawdown/top-winner timing. The best `80/20` combo improved DD only `+1.58%`; higher BTC weights worsened DD and OOS retention. This does not authorize BTC live routing, portfolio allocation, checkpoint, execution, config, oracle, production strategy, or symbol-universe changes. | `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.md`, `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_combos.csv`, `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_dd_overlap.csv`, `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_stage_gates.csv` |
| Reject `exp_0138` reclaim-add / DD-throttle position sizing branch | Rejected | Constant `50%/75%` sizing reduced DD but damaged OOS and top winners; reclaim-add recovered OOS but pulled DD back near baseline; DD throttle improved DD but failed OOS retention and did not beat the constant-size controls with enough nonlinear value. This does not authorize live/demo sizing, checkpoint, execution, config, oracle, production strategy, or allocation changes. | `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.md`, `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.csv`, `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_attribution.csv`, `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_stage_gates.csv` |
| Reject `exp_0139` first risk-based position sizing pass | Rejected | Stage 0 did not show high ATR entries are worse, so ATR risk parity was skipped. Entry-fixed 20d vol targeting improved OOS and rolling12 with low top-winner damage, but DD only improved to `-44.12%`, failing the first-pass DD gate. This does not authorize live/demo sizing, checkpoint, execution, config, oracle, production strategy, or allocation changes. | `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.md`, `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.csv`, `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_atr_buckets.csv`, `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage_gates.csv` |
| Enable V1 vol targeting as an opt-in Bitget live entry-sizing control | ACTIVE | User explicitly approved live implementation on 2026-06-28. The V1 vol reference is clean (`train_only_entry_median`, shifted realized vol) and rolling12 improvement is real: baseline worst rolling12 `-23.48%` became V1 `+1.21%`, while V1 worst rolling12 was `+0.01%`. Live behavior is limited to `live_bitget_quant.py --vol-target-sizing`, which only changes new-entry notional using `vol_ref=0.794812`, 20d completed 5m realized vol, and clip `[0.4, 1.0]`. It does not authorize checkpoint/config/default-profile promotion, clip/window tuning, intratrade rebalance, signal changes, exit changes, oracle changes, or production strategy changes. | `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.md`, `dex/live/position_sizing.py`, `live_bitget_quant.py`, `tests/test_live_vol_target_position_sizing.py` |
| Reject `exp_0141` ER single-variable low-bucket sizing | Rejected | Efficiency Ratio Stage 0 found low ER is a mixed high-variance bucket, not a clean low-quality bucket. ER20/50/100 Q1 all contain both top20 winners and worst20 losers; ER50 Q1 has `6` top20 winners, `6` worst20 losers, `19.75%` top20 pnl, and `41.94%` worst20 loss. Side, regime, realized-vol bucket, and breakout-strength bucket distributions overlap, so Q1/Q2 ER sizing is frozen unless a new live-knowable separability hypothesis is proposed. This does not authorize live/demo sizing, checkpoint, execution, config, oracle, production strategy, or allocation changes. | `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.md`, `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.csv`, `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_stage0_verdict.csv`, `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_q1_cross_stats.csv`, `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_q1_separability.csv` |

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
