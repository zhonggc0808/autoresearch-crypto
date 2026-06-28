# Current State

Last updated: 2026-06-28

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

## Latest Live Runtime Note

2026-06-27 Bitget live log inspection found the running command using:

- `channel_breakout_v2_2_m375_bbm375_1p5`
- `configs/live/moirai2_gate_exp_0093.json`
- `--capital 10 --leverage 1`

During the 2026-06-27 22:15-22:40 Asia/Shanghai short-to-long transition:

- Moirai2 allowed the LONG reversal signal.
- The short was flattened by maker reduce-only order after one canceled maker attempt.
- No new long opened because `10 USDT` notional at `1x` was below the Bitget `0.01 ETH` size increment near `1593 USDT`.

Runtime bookkeeping was updated so Bitget maker order submissions are marked `order_status=submitted` / `filled=false`, later changed to `filled` or `canceled` by pending-order reconciliation. The live audit script now ignores submitted/canceled maker events when building closed-trade rows. Forecast-gate logs now print `Forecast gate:<model_family>` and add `forecast_gate_*` fields while preserving legacy `timesfm_gate_*` state fields.

This was a live-runtime observability and ledger fix only. It did not change strategy signals, gate thresholds, checkpoint/config routing, leverage, capital, order price semantics, or promotion status.

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
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.md`
- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.md`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.md`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.md`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.md`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.md`
- `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.md`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.md`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.md`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.md`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.md`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.md`

Recent read:

- TimesFM `exp_0068` is still the most validated main gate.
- Moirai2 `-2%/4%` is a primary shadow challenger.
- Moirai2 `-2%/3%` shows cleaner drawdown repair in focused diagnostics, but is diagnostic-only and not the live shadow config.
- The shared blocked set between TimesFM and Moirai is small; models are complementary rather than duplicates.
- Market-signal take-profit overlays remain research-only. In `exp_0131`, 86 completed-bar HTF/OI/MACD variants were tested after v2.2+Moirai. Ten passed the long-window diagnostic gate, but none also triggered the 2026-06-23 Bitget short live-case replay. The live-case-capturing `2h wick -> 2h MACD` variants would have exited near `1564.52`, but failed promotion-quality checks due top-winner damage and year instability.
- `exp_0132` compressed the market-signal TP matrix: A=`10` long-window-pass/no-live-hit variants remain OBSERVE for exit review only; B=`34` live-case-hit variants are `rejected_for_live`; C=`13` overtrigger/winner-damage variants are REJECT/archive; D=`29` are low-signal/neutral rejects. Do not expand wick/engulf/MACD combinations further without a new arming condition.
- `exp_0133` tested the proposed arm-first market TP shadow with only three representative signals and ATR-normalized latched arming thresholds `{1.5, 2.0, 2.5, 3.0}`. No row qualified as `SHADOW_CANDIDATE`: the clean long-window branch `2h_wick_r75_v1p5_macd2h` stayed OBSERVE but did not hit the 2026-06-23 live-case; the live-case branch `2h_wick_r75_v2p0_then_macd2h_12h` still exited near `1564.52` but remained REJECT due `7/20` top-winner cuts and `5` losing year slices. The tested arming thresholds did not change exits inside each selected signal family.
- `exp_0134` tested the same live-case market signal as a temporary riskoff trailing stop instead of immediate close-to-flat. All four rows protected the 2026-06-23 Bitget short replay before the `1590` reference (`ATR1.0` exited at `1569.28`; `ATR1.5` exited at `1573.62`), but every row is REJECT because top20 winner exits stay `7/20` and year slices stay `3` wins versus `5` losses. TTL `24h` made no difference because every activation stopped before expiry.
- Market-signal exit research is now frozen: entry-block filters, market TP close-to-flat, ATR profit arming, and riskoff trailing after market signal should not be reopened without a different upstream signal family.
- `exp_0135` started a separate native channel-structure diagnostic line using Donchian internal-return events only. Stage 1 produced `13,271` rising-edge events across `40` buckets and is REJECT/no Stage 2. The best direction bucket was `NEUTRAL short tol0bp` with avg delta `+3.03%`, but it had `66` top20 events and only `50.85%` 72-bar adverse rate. The best non-top20 sub-bucket was ex-post only (`NEUTRAL short tol0bp inside_bb=False is_top20=False`, avg delta `+4.54%`, adv72 `51.21%`), so it is not live-usable.
- `exp_0136` tested core Donchian multitimeframe diversification as Stage 1 only. Non-baseline sleeves intentionally excluded regime split, BB confirmation, MTG, BCD, Moirai, and daily EMA permission. `15m_core` and `1h_core` had positive OOS (`+263.07%` and `+185.00%`) and low daily correlation to `5m_v22_moirai` (`0.42` and `0.38`), but standalone DD was severe (`-93.24%` and `-97.47%`) and fixed-weight combos did not improve baseline max DD by the required `15%`. Verdict: REJECT/no Stage 2.
- `exp_0137` Stage 0 cross-asset sanity tested BTC/SOL core Donchian only (`m=375`, `min_hold_bars=432`, no Moirai/BB/regime/MTG/BCD) on each asset's longest available data. BTC `1300d` passed with full `+88.32%`, OOS `+40.19%`, DD `-63.19%`, `111.26` trades/year. SOL `730d` passed with full `+11.70%`, OOS `+23.41%`, DD `-69.35%`, `120.08` trades/year. Both clear `DD > -85%` and `OOS > 0`, so full cross-asset exp0137 is allowed as a research-only next step.
- `exp_0137` ETH/BTC overlap diagnostic then tested ETH `v2.2+Moirai` versus BTC core Donchian over the BTC `1300d` overlap only. Verdict: REJECT/no BTC shadow sleeve. BTC standalone gates passed, but fixed combos failed the required `15%` DD-improvement gate: `80/20` improved DD only `+1.58%`, while `70/30` and `60/40` worsened DD. ETH max-DD window BTC return was `-42.43%`, DD overlap ratio was `0.82`, and top20 winner overlap was high (`9` ETH and `12` BTC winners; ETH overlap pnl `66.53%`).
- `exp_0138` tested position sizing only: constant `50%/75%`, reclaim add, and reclaim add plus wide DD throttle. Verdict: REJECT/no sizing shadow. V0 constant `50%` improved DD `+35.66%` but OOS fell from baseline `+629.72%` to `+197.52%` and top20 winner damage was `92.98%`. V1/V4 reclaim add recovered OOS (`+482.17%` and `+571.26%`) but pulled DD back near baseline (`-47.73%` and `-49.27%`). V2/V5 throttle improved DD (`+25.59%` and `+23.55%`) but OOS stayed below the `65%` retention gate (`+281.05%` and `+384.11%`). No add/throttle branch beat its constant-size control enough to show nonlinear value.
- `exp_0139` tested risk-based entry-fixed sizing. Stage 0 did not prove high ATR entries are worse: Q5 ATR had avg return `+4.33%`, only `3/20` worst losers, and only `2.18%` of top20 winner pnl, so ATR risk parity was `skipped_by_stage0`. V1 20d vol targeting is the current only empirically effective position-stability observe scheme: OOS `+638.77%` versus baseline `+629.72%`, DD `-44.12%` versus `-51.12%`, rolling12 `+0.01%`, top20 damage `9.98%`, fee10 OOS `+556.39%`. It still failed the first-pass sizing-shadow DD gate (max DD better than roughly `-40%` or DD improve `>=20%`), so it is not live/shadow, but it should not be grouped with ordinary rejected sizing branches.
- `exp_0140` audited the `exp_0139` V1 vol-target scheme. The vol reference is clean: `train_only_entry_median`, not full-sample, with realized vol shifted one bar. V1 turned the baseline worst rolling12 window (`2021-05-08` to `2022-05-08`) from `-23.48%` to `+1.21%`, and its own worst rolling12 stayed near flat at `+0.01%`. However, V1 max DD occurred in a low-volatility window (`2022-02-26` to `2022-05-04`, median vol percentile `36.35%`) while average active size stayed `96.06%`, so the residual DD is not a clean "high vol but under-throttled" narrow-refinement case. Verdict: `OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE`; no sizing shadow, no `exp0141` one-shot refinement from this audit, and no live/config/checkpoint/oracle/production change.
- `exp_0141` tested Efficiency Ratio Stage 0 only on baseline trades with windows `{20, 50, 100}`. All three windows are `MIXED_HIGH_VARIANCE_BUCKET`: low ER Q1 contains both top winners and worst losers (`ER20`: `3` top20 / `3` worst20; `ER50`: `6` top20 / `6` worst20; `ER100`: `7` top20 / `5` worst20). ER50 Q1 carries `19.75%` of top20 pnl and `41.94%` of worst20 loss; ER100 Q1 carries `22.89%` of top20 pnl. Side/regime/realized-vol/breakout-strength distributions overlap enough that ER single-variable low-bucket sizing is REJECT/frozen. No trading action, sizing, skip rule, live/config/checkpoint/oracle/production change.

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
- core Donchian multitimeframe diversification as framed in `exp_0136`
- ETH/BTC core Donchian cross-asset shadow sleeve as framed in `exp_0137` overlap
- reclaim-add / wide-DD-throttle position sizing as framed in `exp_0138`
- ATR risk-parity sizing as framed in `exp_0139` unless a new Stage 0 diagnostic shows high-risk entries are genuinely worse
- 20d vol-targeting clip `[0.4, 1.0]` as a live/shadow sizing rule; `exp_0140` keeps it observe-only but does not authorize clip/window tuning
- Efficiency Ratio single-variable Q1/Q2 low-bucket sizing as framed in `exp_0141`; low ER is a mixed high-variance bucket, not a clean bad-trade bucket

## Next Useful Work

Best next steps:

1. Validate live-shadow parity for `exp_0093` against research replay.
2. Continue attribution of Moirai `risk_floor=3%` versus live `risk_floor=4%`, but keep it diagnostic-only until promoted.
3. If proposing a gate promotion, produce raw / regime-permission / safe-execution / optional DD guard results and explicitly report top-winner damage.
4. Keep live/demo routing and checkpoint changes behind explicit approval.
5. Treat the market-signal exit line as frozen. `exp_0135` rejected Donchian internal-return Stage 1, and `exp_0136` rejected core Donchian multitimeframe diversification Stage 1, so do not run either Stage 2 branch unless a materially different live-knowable channel-structure discriminator is identified first.
6. `exp_0137` ETH/BTC overlap rejected BTC core Donchian as a shadow sleeve. Do not continue by optimizing BTC weights or adding BTC Moirai/BB/regime overlays unless a new independent hypothesis is stated first.
7. `exp_0138` rejected the first position-sizing branch. Do not tune reclaim-add sizes, add cooldowns, or DD throttle tiers from this result; a next sizing idea needs a different hypothesis than "initial under-size then add on 72-bar reclaim."
8. `exp_0139`/`exp_0140` leave V1 20d vol targeting as the only current position-stability observe scheme. Its rolling12 improvement is real under a clean train-only vol_ref, but the residual max DD is low-volatility and high-size, so do not tune clip/window from this audit. V2 ATR risk parity should stay skipped unless Stage 0 evidence changes.
9. `exp_0141` rejects ER single-variable low-bucket sizing. Do not proceed to `Q1 size 50%, Q2-Q5 100%` unless a materially new, live-knowable separability hypothesis is stated and tested first.

## Current Protected Baseline Rule

No current research file authorizes:

- checkpoint change
- live routing change
- default oracle route change
- scoring change
- production strategy change
- TimesFM main gate replacement

Any of those requires a separate explicit task and approval.
