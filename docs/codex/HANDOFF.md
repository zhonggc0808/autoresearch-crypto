# Handoff

Last updated: 2026-06-28

## Current Handoff Summary

The repository memory structure has been moved toward repo-versioned memory:

- `AGENTS.md` is now the hard-rule entry point.
- `docs/codex/` stores long-term project memory.
- `.agents/skills/live-change-guard/` stores the first project skill for live-risk preflight.
- `.agents/skills/save-code/` stores the commit, push, and committed-HEAD archive workflow.

No strategy, live runtime, checkpoint, exchange config, oracle, or research artifact behavior was intentionally changed as part of this memory-structure update.

## Latest Tooling Update

Added `save-code` as a repo-level skill for the requested "save code" workflow:

- Review Git status and confirm the intended commit scope.
- Require explicit live-risk approval for protected paths before staging.
- Commit with a confirmed message, push to `origin/<current branch>`, and create `..\autoresearch-crypto-runtime-YYYYMMDD-HHMMSS-<shortsha>.tar.gz` from committed `HEAD`.

This workflow does not change the current ETH strategy baseline or live/demo routing.

## Latest Live Vol-Target Sizing

On 2026-06-28, after explicit user approval, Bitget live received an opt-in implementation of `exp_0140` V1 20d realized-vol entry sizing.

- New helper: `dex/live/position_sizing.py`
- Live entrypoint: `live_bitget_quant.py --vol-target-sizing`
- Tests: `tests/test_live_vol_target_position_sizing.py`
- Formula: `close.pct_change().rolling(5760).std().shift(1) * sqrt(365.25*288)`, `vol_ref=0.794812`, multiplier `clip(vol_ref / realized_vol_20d, 0.4, 1.0)`.
- Scope: new-entry notional only. It does not change ChannelBreakout signals, Moirai/TimesFM gates, exits, stop logic, checkpoint/config files, leverage, API routing, or order-price semantics.
- Execution semantics: completed 5m bars only; entry multiplier is fixed when the entry decision is made; no intratrade dynamic rebalance.
- Fallback: if history is missing/invalid, multiplier is `1.0` and the reason is logged.
- Pending maker opens store original `capital_per_trade`, entry multiplier, vol multiplier, realized vol, and reason so maker-to-IOC fallback keeps entry-fixed sizing.
- Logs/state include raw size, rounded size, fillability, and min-lot notional because `--capital 10 --leverage 1` remains below Bitget `0.01 ETH` lot near ETH `1500-1600`, and vol down-sizing can only worsen fillability.
- Rollback: restart without `--vol-target-sizing` or revert the live sizing change. No config/checkpoint rollback is needed.

Validation run:

- `uv run pytest tests/test_live_vol_target_position_sizing.py tests/test_live_common.py tests/test_live_entry_imports.py`
- `uv run ruff check dex/live/position_sizing.py live_bitget_quant.py tests/test_live_vol_target_position_sizing.py`

## Latest Live Runtime Fix

On 2026-06-27, Bitget live logs were reviewed for a short-to-long signal transition. The signal/gate path was normal, but two runtime bookkeeping issues were fixed:

- Bitget maker order submissions now carry `order_status=submitted` and `filled=false`; pending-order reconciliation updates them to `filled` or `canceled`.
- `scripts/audit_live_consistency.py` now ignores submitted/canceled maker events when normalizing live events, preventing canceled maker close attempts from becoming false closed-trade rows.

Related observability improvement:

- Bitget forecast-gate logs now use `Forecast gate:<model_family>` and write `forecast_gate_*` fields while keeping legacy `timesfm_gate_*` fields.
- Zero-size entry skips now include the minimum notional implied by `lot_sz * current_price`.

No strategy, checkpoint, live config, leverage, capital, gate threshold, or execution price semantics were changed.

Validation run:

- `uv run pytest tests/test_live_entry_imports.py tests/test_live_audit_consistency.py tests/test_live_common.py tests/test_live_forecast_gate.py`
- `uv run ruff check live_bitget_quant.py scripts/audit_live_consistency.py tests/test_live_entry_imports.py tests/test_live_audit_consistency.py`

## Latest Research Diagnostic

On 2026-06-28, `exp_0131` implemented a research-only market-signal take-profit matrix for `v2.2 + Moirai exp_0093`.

- New script: `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py`
- Outputs: `.md`, `.json`, `.csv`, `_exits.csv`, `_live_case.csv`
- Behavior: completed HTF/OI/MACD market signals only; current trade must be profitable; next-open close-to-flat; same-direction lockout; no MFE/giveback/fixed-TP trigger.
- Main matrix: 86 variants over 1h engulf, 2h wick, Bybit OI flush, 2h/4h MACD, daily context, and 2h-wick-then-MACD sequence confirmations.
- Best long-window row: `1h_strict_engulf_v2p0_macd4h`, OOS `+643.17%` versus baseline `+629.72%`, DD `-48.86%` versus `-51.12%`, rolling12 `+3.34%`, top20 winner cuts `1`.
- Live-case replay: `2h_wick_r75_v2p0_then_macd2h_12h` would have exited the 2026-06-23 Bitget short near `1564.52`, before the actual flatten near `1590`, but this variant failed due `7/20` top-winner cuts and `5` losing years.
- Decision: OBSERVE only. No live/demo routing, checkpoint, execution, config, oracle, or production strategy change.

Validation run:

- `uv run pytest tests/test_exp0131_market_signal_tp.py`
- `uv run ruff check research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py tests/test_exp0131_market_signal_tp.py`
- `uv run python research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py --mode matrix`
- `uv run python research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py --mode live-case`

`exp_0132` then compressed the exp_0131 matrix into attribution buckets.

- New script: `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py`
- Outputs: `.md`, `.json`, `.csv`, `_exit_attribution.csv`
- Method: compare trigger-time early-exit PnL proxy (`current_return * base_entry_notional`) against the baseline trade final PnL.
- Bucket counts: A long-window-pass/no-live-hit `10`; B live-case-hit/rejected-for-live `34`; C overtrigger-or-winner-damage REJECT `13`; D low-signal/neutral `29`.
- A representative `1h_strict_engulf_v2p0_macd4h`: 6 exits, 4 valuable by proxy, 2 missed winners, 0 saved losers, net proxy delta `-48547.48`.
- B representative `2h_wick_r75_v2p0_then_macd2h_12h`: explains the 2026-06-23 short with an exit near `1564.52`, but remains `rejected_for_live` due `7/20` top-winner cuts and `5` losing years.
- Decision: do not expand unarmed wick/engulf/MACD combinations. Next research, if any, should use a profit-armed market TP concept such as ATR-normalized open profit or entry-risk multiple.

Validation run:

- `uv run pytest tests/test_exp0132_market_signal_tp_attribution.py`
- `uv run ruff check research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py tests/test_exp0132_market_signal_tp_attribution.py`
- `uv run python research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py`

`exp_0133` then implemented the proposed arm-first profit-armed market TP shadow.

- New script: `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py`
- Outputs: `.md`, `.json`, `.csv`, `_exits.csv`, `_arms.csv`, `_live_case.csv`
- Scope: three representative signals only: `1h_strict_engulf_v2p0_macd4h`, `2h_wick_r75_v2p0_then_macd2h_12h`, `2h_wick_r75_v1p5_macd2h`.
- Arming: `open_profit_atr_entry_based`, entry ATR fixed from the completed bar before entry, thresholds `{1.5, 2.0, 2.5, 3.0}`, latched once reached.
- Trigger: market signal can close only after arming and while `current_return_after_cost > 0`; next-open close-to-flat; no reverse/open; same-side lockout; same-bar baseline reversal is not attributed to market TP.
- Result: no row qualifies as `SHADOW_CANDIDATE`.
- Cleanest observe branch: `2h_wick_r75_v1p5_macd2h_armATR*`, `2` exits, `+8.46%` delta OOS, `0` top20 cuts, positive proxy attribution, but no 2026-06-23 live-case hit.
- Live-case branch: `2h_wick_r75_v2p0_then_macd2h_12h_armATR*` still triggers near `2026-06-26 22:00 CST` at `1564.52`, but remains REJECT due `7/20` top-winner cuts and `5` losing year slices.
- Threshold read: `{1.5, 2.0, 2.5, 3.0}` ATR arming did not change exits inside each selected signal family; the selected signals already fire after the trade is beyond the arming threshold.
- Decision: OBSERVE/REJECT subsets only. No live/demo routing, checkpoint, execution, config, oracle, or production strategy change.

Validation run:

- `uv run pytest tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py`
- `uv run ruff check research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py`
- `uv run python research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py --mode all`

`exp_0134` then tested the live-case signal as a temporary riskoff trailing stop rather than immediate close-to-flat.

- New script: `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.py`
- Outputs: `.md`, `.json`, `.csv`, `_events.csv`, `_live_case.csv`
- Scope: only `2h_wick_r75_v2p0_then_macd2h_12h`.
- Mechanism: market signal activates riskoff trailing; ATR reference is fixed at activation; short anchor is `min(low since activation)`, long anchor is `max(high since activation)`; completed 5m close crossing the stop confirms, and next open executes.
- Variants: ATR multipliers `{1.0, 1.5}` by TTL `{until_baseline_exit, 24h}`.
- Live-case replay: all four variants protect the 2026-06-23 Bitget short before `1590`; ATR1.0 stops at `2026-06-26 22:25 CST` with execution open `1569.28`, ATR1.5 stops at `2026-06-26 23:05 CST` with execution open `1573.62`.
- Long-window result: all four variants are REJECT. Each has `34` stops, cuts `7/20` top winners, and has year W/L/F `3/5/0`, so hard gates fail despite positive OOS delta and positive net attribution.
- TTL read: `24h` and `until_baseline_exit` are identical in this run because all riskoff activations stop before TTL expiry.
- Decision: do not promote this riskoff trailing design. No live/demo routing, checkpoint, execution, config, oracle, or production strategy change.

Validation run:

- `uv run pytest tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py tests/test_exp0134_riskoff_trailing_stop.py`
- `uv run ruff check research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.py tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py tests/test_exp0134_riskoff_trailing_stop.py`
- `uv run python research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.py --mode all`

Full-suite note:

- `uv run pytest tests/` finished with `587 passed, 1 skipped, 16 failed`. The failures are outside exp0134 scope and come from existing LLM/reflection/oracle/timesfm/verdict compatibility tests: `tests/test_llm_hypothesis.py`, `tests/test_reflection.py`, `tests/test_research_oracle.py`, `tests/test_timesfm_live_gate.py`, and `tests/test_verdict_engine_v03.py`.

Market-signal exit line final state:

- Entry-block filter family: frozen.
- Market TP close-to-flat: frozen after `exp_0131`/`exp_0132`.
- ATR profit arming: frozen after `exp_0133`.
- Riskoff trailing after market signal: frozen after `exp_0134`.
- Do not continue this line by widening wick/engulf/MACD/OI parameter search.

`exp_0135` then started a separate native channel-structure diagnostic: Donchian internal return, Stage 1 only.

- New script: `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.py`
- Outputs: `.md`, `.json`, `.csv`, `_events.csv`, `_direction.csv`
- Scope: diagnostic-only; no signal stream modification, no execution action, no live/demo/checkpoint/config/oracle/production strategy change.
- Signal: long `close <= donchian_upper_prev*(1-tol)`, short `close >= donchian_lower_prev*(1+tol)`, with `m=375` and tol `{0bp, 50bp}`.
- Feature guard: Donchian high/low and Bollinger `{window=375, std=1.5}` are shifted by one completed 5m bar.
- Buckets: `regime x direction x tol x also_inside_bb x is_top20`.
- Events: rising-edge internal-return events while the baseline position remains unchanged; same-bar baseline exits/reversals are skipped.
- Result: `13,271` events across `40` buckets; verdict REJECT/no Stage 2.
- Best live-knowable direction: `NEUTRAL short tol0bp`, avg delta `+3.03%`, but `66` top20 events and only `50.85%` 72-bar adverse rate.
- Best ex-post sub-bucket: `NEUTRAL short tol0bp inside_bb=False is_top20=False`, avg delta `+4.54%`, adv72 `51.21%`; not live-usable because `is_top20` is known only after the trade.
- Decision: do not run the proposed Stage 2 ATR trailing variants unless a new live-knowable discriminator is found.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py tests/test_exp0134_riskoff_trailing_stop.py tests/test_exp0135_donchian_internal_return.py`
- `uv run ruff check research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.py tests/test_exp0135_donchian_internal_return.py`

`exp_0136` then tested core Donchian multitimeframe diversification, Stage 1 only.

- New script: `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py`
- Outputs: `.md`, `.json`, `.csv`, `_correlations.csv`, `_dd_overlap.csv`, `_top_winners.csv`, `_top_overlap.csv`, `_combos.csv`, `_stage_gates.csv`
- Scope: research-only; no live/demo/checkpoint/config/oracle/production strategy change.
- Sleeves: `5m_v22_moirai_baseline`, `5m_core_donchian`, `15m_core_donchian`, `1h_core_donchian`, `1h_core_donchian_fast`.
- Non-baseline design: core Donchian only; no regime split, BB confirmation, MTG, BCD, Moirai, or daily EMA permission.
- Result: verdict REJECT/no Stage 2. Non-5m sleeves had positive OOS and low daily correlation to `5m_v22_moirai`, but standalone DD was severe and fixed combos did not improve baseline max DD by the required `15%`.
- Key rows: baseline OOS `+629.72%`, DD `-51.12%`; `15m_core` OOS `+263.07%`, DD `-93.24%`; `1h_core` OOS `+185.00%`, DD `-97.47%`; `1h_fast` OOS `+55.13%`, DD `-97.41%`.
- Fixed combos: `70/20/10` worsened DD to `-58.16%`, `60/20/20` worsened DD to `-64.35%`, and `80/0/20` improved DD only `+1.02%` versus the required `+15%`.
- Decision: do not proceed to Donchian multitimeframe Stage 2 unless a materially different live-knowable sleeve family or risk control is proposed.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0131_market_signal_tp.py tests/test_exp0132_market_signal_tp_attribution.py tests/test_exp0133_market_tp_profit_arming.py tests/test_exp0134_riskoff_trailing_stop.py tests/test_exp0135_donchian_internal_return.py tests/test_exp0136_multitimeframe_diversification.py`
- `uv run ruff check research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py tests/test_exp0136_multitimeframe_diversification.py`

`exp_0137` then ran a cross-asset core Donchian sanity check, Stage 0 only.

- New script: `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.py`
- Outputs: `.md`, `.json`, `.csv`
- Scope: research-only; no live/demo/checkpoint/config/oracle/production strategy change.
- Assets/data: BTC uses longest available `data/crypto/BTCUSDT_5m_1300d.parquet`; SOL uses longest available `data/crypto/SOLUSDT_5m_730d.parquet`.
- Strategy: core Donchian only, `m=375`, `min_hold_bars=432`; no Moirai, BB, regime split, MTG, or BCD.
- Execution: completed-bar signal and next 5m open fill; each asset uses its own 70/30 IS/OOS split.
- Sanity gate: `max_dd > -85%` and `OOS_return > 0`.
- Result: `OBSERVE_READY_FOR_FULL_EXP0137`. BTC passed with full `+88.32%`, OOS `+40.19%`, DD `-63.19%`, `396` trades, `111.26` trades/year. SOL passed with full `+11.70%`, OOS `+23.41%`, DD `-69.35%`, `240` trades, `120.08` trades/year.
- Decision: a full cross-asset exp0137 can be designed next, but this Stage 0 result does not authorize live/demo routing, symbol-universe changes, checkpoint promotion, or production strategy changes.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.py --mode all`
- `uv run pytest tests/test_exp0137_cross_asset_core_donchian_sanity.py`
- `uv run pytest tests/test_exp0136_multitimeframe_diversification.py tests/test_exp0137_cross_asset_core_donchian_sanity.py`
- `uv run ruff check research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.py tests/test_exp0137_cross_asset_core_donchian_sanity.py`

`exp_0137b` then ran the ETH/BTC overlap diagnostic specified after the Stage 0 sanity pass.

- New script: `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.py`
- Outputs: `.md`, `.json`, `_sleeves.csv`, `_correlations.csv`, `_dd_overlap.csv`, `_top_winners.csv`, `_top_overlap.csv`, `_combos.csv`, `_stage_gates.csv`
- Scope: research-only; no live/demo/checkpoint/config/oracle/production strategy change; no BTC live route; no SOL; no portfolio allocator.
- ETH sleeve: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`.
- BTC sleeve: core Donchian `m=375`, `min_hold_bars=432`; shifted channel; completed 5m signal; next 5m open execution.
- Overlap: `2022-11-20 03:40:00` to `2026-06-12 02:55:00`; final verdict is based only on overlap Panel B metrics.
- Result: REJECT/no BTC shadow sleeve. BTC standalone gates passed, but fixed combos failed the required `15%` relative DD-improvement gate.
- Key rows: ETH same-window OOS `+229.87%`, DD `-35.98%`; BTC same-window OOS `+40.64%`, DD `-63.19%`; best `80/20` combo OOS `+185.58%`, DD `-35.42%`, DD improvement only `+1.58%`.
- Drawdown/top-winner read: during ETH max-DD window, BTC returned `-42.43%`; DD overlap ratio was `0.82`; top20 winner overlap was high (`9` ETH winners, `12` BTC winners, ETH overlap pnl `66.53%`, BTC overlap pnl `65.83%`).
- Decision: do not proceed to BTC shadow sleeve, do not optimize weights, and do not add BTC live/demo routing from this result.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0137_cross_asset_eth_btc_overlap.py`
- `uv run pytest tests/test_exp0136_multitimeframe_diversification.py tests/test_exp0137_cross_asset_core_donchian_sanity.py tests/test_exp0137_cross_asset_eth_btc_overlap.py`
- `uv run ruff check research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.py tests/test_exp0137_cross_asset_eth_btc_overlap.py`

`exp_0138` then tested the first position-sizing branch: constant under-sizing, reclaim-add, and reclaim-add plus wide DD throttle.

- New script: `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py`
- Outputs: `.md`, `.json`, `.csv`, `_attribution.csv`, `_stage_gates.csv`, `_events.csv`, `_trades.csv`
- Scope: research-only; no live/demo/checkpoint/config/oracle/production strategy change; no fixed-R profit taking.
- Baseline: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`, custom next-open simulator matching the existing baseline result.
- Variants: V0 constant `50%`; V1 `50% + add1 25% + add2 25%`; V2 V1 plus DD throttle; V3 constant `75%`; V4 `75% + add1 25%`; V5 V4 plus DD throttle.
- Add trigger: baseline still same direction, current after-cost open PnL positive, and completed 5m close breaks the prior 72-bar high since entry for longs or low since entry for shorts; execution is next 5m open.
- DD throttle tiers: `<15% = 1.0x`, `15-25% = 0.75x`, `25-40% = 0.5x`, `>40% = 0.25x`; it affects only new entries and adds, not existing exposure.
- Result: REJECT/no sizing shadow. Constant sizing reduces DD but cuts too much OOS/top-winner exposure; reclaim-add restores OOS but pulls DD near baseline; throttle improves DD but fails OOS retention and does not beat the constant-size controls with enough nonlinear value.
- Key rows: baseline OOS `+629.72%`, DD `-51.12%`; V0 OOS `+197.52%`, DD `-32.89%`; V1 OOS `+482.17%`, DD `-47.73%`; V2 OOS `+281.05%`, DD `-38.04%`; V4 OOS `+571.26%`, DD `-49.27%`; V5 OOS `+384.11%`, DD `-39.08%`.
- Decision: do not tune reclaim-add sizes, add cooldowns, or the same DD throttle tiers from this result; the next position-sizing idea needs a different hypothesis.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0138_position_sizing_reclaim_add.py`
- `uv run pytest tests/test_exp0136_multitimeframe_diversification.py tests/test_exp0137_cross_asset_core_donchian_sanity.py tests/test_exp0137_cross_asset_eth_btc_overlap.py tests/test_exp0138_position_sizing_reclaim_add.py`
- `uv run ruff check research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py tests/test_exp0138_position_sizing_reclaim_add.py`

`exp_0139` then tested the first risk-based entry-fixed position-sizing branch.

- New script: `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py`
- Outputs: `.md`, `.json`, `.csv`, `_stage0_atr_buckets.csv`, `_stage0_realized_vol_buckets.csv`, `_stage0_trades.csv`, `_stage_gates.csv`, `_events.csv`, `_trades.csv`
- Scope: research-only; no live/demo/checkpoint/config/oracle/production strategy change; no fixed-R profit taking; no intratrade dynamic rebalance.
- Baseline: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`, custom next-open simulator matching `exp_0138` baseline.
- Stage 0: baseline trade entry ATR buckets and realized-vol buckets. ATR uses `ATR(576).shift(1) / close.shift(1)`. Realized vol uses completed 20d 5m returns shifted one bar. Top20/worst20 labels are attribution-only.
- Stage 0 result: ATR pass is false. Q5 ATR had avg return `+4.33%`, median return `-0.64%`, only `3/20` worst losers, and only `2.18%` of top20 winner pnl. That does not prove high ATR entries are worse. V2 ATR risk parity is therefore `skipped_by_stage0`.
- V1 result: entry-fixed 20d vol targeting with clip `[0.4, 1.0]` and IS-entry median vol ref produced OOS `+638.77%`, DD `-44.12%`, rolling12 `+0.01%`, top20 damage `9.98%`, worst20 improvement `12.84%`, fee10 OOS `+556.39%`, return/DD `864.38`.
- Verdict: REJECT/no sizing shadow for the exp0139 promotion gate, but V1 itself is not an ordinary failed sizing branch. It is the current only empirically effective position-stability observe scheme because it preserved OOS/top winners and improved rolling12; it still failed the first-pass DD gate because DD improve was only `+13.69%` and max DD stayed worse than the roughly `-40%` target.
- Decision: do not run ATR risk parity by bypassing Stage 0; do not treat V1 as live-ready. If sizing research continues, it needs a new idea specifically aimed at reducing V1's residual DD without destroying its top-winner retention.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0139_risk_based_position_sizing.py`
- `uv run ruff check research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py tests/test_exp0139_risk_based_position_sizing.py`

`exp_0140` then audited the `exp_0139` V1 vol-target stability scheme before allowing any refinement.

- New script: `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.py`
- Outputs: `.md`, `.json`, `.csv`, `_windows.csv`, `_top20.csv`, `_worst20.csv`, `_extreme_summary.csv`
- Scope: research-only attribution audit; no new sizing variant, no parameter search, no live/demo/checkpoint/config/oracle/production strategy change.
- Vol ref check: V1 is clean. `vol_ref_mode=train_only_entry_median`; full-sample median realized vol was `0.729250`, train-only median was `0.794812`, train/full ratio `1.0899`. Realized vol is shifted one bar and entry size is fixed at entry.
- V1 metrics reproduced: OOS `+638.77%`, DD `-44.12%`, DD improvement `+13.69%`, rolling12 `+0.01%`, top20 damage `9.98%`, fee10 OOS `+556.39%`.
- Rolling12 attribution: baseline worst rolling12 window `2021-05-08` to `2022-05-08` improved from baseline `-23.48%` to V1 `+1.21%`; V1's own worst rolling12 window `2022-07-18` to `2023-07-18` was `+0.01%`.
- MaxDD attribution: V1 max DD window `2022-02-26` to `2022-05-04` had median realized-vol percentile only `36.35%` and avg active V1 size `96.06%`, so the residual DD is a low-vol/high-size failure, not a clean high-vol-underthrottled case.
- Extreme trade attribution: baseline top20 winners kept avg V1 size `98.32%` and lost `9.98%` pnl; baseline worst20 losers had avg V1 size `95.38%` and saved `13.24%` pnl, with only `3/20` in Q5 realized-vol entries.
- Decision: `OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE`. No sizing shadow. Do not open `exp0141` by tuning vol window/clip from this audit.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.py --mode all`
- `uv run pytest tests/test_exp0139_risk_based_position_sizing.py tests/test_exp0140_vol_target_attribution_audit.py`
- `uv run ruff check research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.py tests/test_exp0139_risk_based_position_sizing.py tests/test_exp0140_vol_target_attribution_audit.py`

`exp_0141` then ran the requested Efficiency Ratio Stage 0 diagnostic.

- New script: `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.py`
- Outputs: `.md`, `.json`, `.csv`, `_bucket_summary.csv`, `_oos_summary.csv`, `_top_worst_summary.csv`, `_q1_cross_stats.csv`, `_rolling12_contribution.csv`, `_q1_extreme_distribution.csv`, `_q1_separability.csv`, `_stage0_verdict.csv`, `_trades.csv`
- Scope: Stage 0 only; no trading action, no sizing, no skip rule, no live/demo/checkpoint/config/oracle/production strategy change.
- ER windows: `{20, 50, 100}`. ER is shifted so entry features use only completed bars before entry execution.
- Baseline reference: full `+42285.03%`, OOS `+629.72%`, DD `-51.12%`, rolling12 `-23.48%`, trades `236`.
- Result: overall verdict `MIXED_HIGH_VARIANCE_BUCKET`. Low ER is not a clean low-quality bucket.
- Q1 cross stats: ER20 Q1 has `3` top20 winners and `3` worst20 losers; ER50 Q1 has `6` top20 winners and `6` worst20 losers, with `19.75%` top20 pnl and `41.94%` worst20 loss; ER100 Q1 has `7` top20 winners and `5` worst20 losers, with `22.89%` top20 pnl.
- Separability read: Q1 side, regime, realized-vol bucket, and breakout-strength bucket distributions overlap enough that ER single-variable sizing is frozen. Month-level gaps are attribution-only and not a live rule.
- Decision: REJECT/no ER Stage 1 sizing. Do not test `Q1 size 50%, Q2-Q5 100%` unless a new live-knowable separability hypothesis is stated first.

Validation run:

- `uv run python research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.py --mode all`
- `uv run pytest tests/test_exp0141_efficiency_ratio_diagnostic.py`
- `uv run ruff check research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.py tests/test_exp0141_efficiency_ratio_diagnostic.py`

## Current Baseline To Load First

Read:

- `docs/codex/CURRENT_STATE.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/DECISIONS.md`

Current baseline:

- `channel_breakout_v2_2_m375_bbm375_1p5`
- main gate TimesFM `exp_0068`
- Moirai2 `exp_0093` is challenger shadow only

## Latest Research Thread

Latest useful evidence is around gate attribution:

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

Key read:

- TimesFM `exp_0068` remains main.
- Moirai2 `exp_0093` is challenger shadow.
- Moirai `risk_floor=3%` has interesting diagnostic DD repair but is not promoted.
- Market-signal TP is not promoted; live-case repair needs top-winner damage control before it can be reconsidered.
- Market-signal TP should not continue as a larger wick/engulf/MACD matrix. `exp_0133` shows simple ATR arming at `1.5-3.0` ATR does not repair the selected live-case branch, and `exp_0134` shows riskoff trailing protects the live-case but still cuts too many top winners; do not treat either as live-ready.
- Donchian internal-return Stage 1 is rejected in `exp_0135`; do not proceed to Stage 2 ATR trailing without a new live-knowable discriminator.
- Core Donchian multitimeframe diversification Stage 1 is rejected in `exp_0136`; low daily correlation alone is not enough because standalone DD and fixed-combo DD gates failed.
- Cross-asset core Donchian sanity Stage 0 passed for BTC and SOL in `exp_0137`; next work may design the full cross-asset diagnostic, but no live/demo or symbol-universe change is authorized.
- ETH/BTC overlap diagnostic rejected BTC core Donchian as a shadow sleeve in `exp_0137b`; low daily correlation (`0.25`) was outweighed by synchronized drawdown and high top-winner overlap.
- First position-sizing branch is rejected in `exp_0138`; constant under-sizing is mostly linear damage control, reclaim-add recovers OOS by restoring near-baseline DD, and the wide DD throttle does not provide enough nonlinear value over the constant controls.
- First risk-based sizing promotion gate is rejected in `exp_0139`; V1 vol targeting is the current only empirically effective position-stability scheme and is now available as an explicit Bitget live opt-in via `--vol-target-sizing`. V2 ATR risk parity is skipped by Stage 0.
- `exp_0140` confirms V1 vol targeting is clean and rolling12-positive. Residual max DD occurs in a low-volatility window with high active size, so the audit does not authorize a one-shot refinement, clip/window tuning, or default config/checkpoint promotion.
- `exp_0141` rejects ER single-variable low-bucket sizing. Low ER Q1 is mixed high-variance across ER20/50/100 and carries both top winners and worst losers, so no ER Stage 1 sizing is authorized.

## Modified Files In Latest Live Vol-Target Sizing

- `dex/live/position_sizing.py`
- `live_bitget_quant.py`
- `tests/test_live_vol_target_position_sizing.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest ER Diagnostic

- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.py`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.md`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.json`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_bucket_summary.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_oos_summary.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_top_worst_summary.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_q1_cross_stats.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_rolling12_contribution.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_q1_extreme_distribution.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_q1_separability.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_stage0_verdict.csv`
- `research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic_trades.csv`
- `tests/test_exp0141_efficiency_ratio_diagnostic.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Vol-Target Audit

- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.py`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.md`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.json`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit.csv`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit_windows.csv`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit_top20.csv`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit_worst20.csv`
- `research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit_extreme_summary.csv`
- `tests/test_exp0140_vol_target_attribution_audit.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Risk-Based Position Sizing Diagnostic

- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.md`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.json`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_atr_buckets.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_realized_vol_buckets.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_trades.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_stage_gates.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_events.csv`
- `research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic_trades.csv`
- `tests/test_exp0139_risk_based_position_sizing.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Position Sizing Diagnostic

- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.md`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.json`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.csv`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_attribution.csv`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_stage_gates.csv`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_events.csv`
- `research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic_trades.csv`
- `tests/test_exp0138_position_sizing_reclaim_add.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In This Memory Update

- `AGENTS.md`
- `.gitignore`
- `docs/codex/PROJECT_BRIEF.md`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/LIVE_GUARD.md`
- `docs/codex/HANDOFF.md`
- `.agents/skills/live-change-guard/SKILL.md`
- `.agents/skills/live-change-guard/agents/openai.yaml`
- `.agents/skills/save-code/SKILL.md`
- `.agents/skills/save-code/agents/openai.yaml`
- `.agents/skills/save-code/scripts/save-code.ps1`
- `C:\Users\81094\.codex\config.toml` (enabled native Codex memories)

## Modified Files In Latest Attribution Compression

- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py`
- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.md`
- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.json`
- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.csv`
- `research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution_exit_attribution.csv`
- `tests/test_exp0132_market_signal_tp_attribution.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Profit-Arming Shadow

- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.md`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.json`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.csv`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow_exits.csv`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow_arms.csv`
- `research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow_live_case.csv`
- `tests/test_exp0133_market_tp_profit_arming.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Riskoff Trailing Stop

- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.py`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.md`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.json`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop.csv`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop_events.csv`
- `research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop_live_case.csv`
- `tests/test_exp0134_riskoff_trailing_stop.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Multitimeframe Diversification Diagnostic

- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.md`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.json`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_correlations.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_dd_overlap.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_top_winners.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_top_overlap.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_combos.csv`
- `research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic_stage_gates.csv`
- `tests/test_exp0136_multitimeframe_diversification.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Cross-Asset Donchian Sanity Diagnostic

- `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.py`
- `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.md`
- `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.json`
- `research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.csv`
- `tests/test_exp0137_cross_asset_core_donchian_sanity.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest ETH/BTC Overlap Diagnostic

- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.py`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.md`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic.json`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_sleeves.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_correlations.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_dd_overlap.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_top_winners.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_top_overlap.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_combos.csv`
- `research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic_stage_gates.csv`
- `tests/test_exp0137_cross_asset_eth_btc_overlap.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Donchian Internal Return Diagnostic

- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.py`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.md`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.json`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic.csv`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic_events.csv`
- `research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic_direction.csv`
- `tests/test_exp0135_donchian_internal_return.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Live Runtime Fix

- `live_bitget_quant.py`
- `scripts/audit_live_consistency.py`
- `tests/test_live_entry_imports.py`
- `tests/test_live_audit_consistency.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Research Diagnostic

- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py`
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.md`
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.json`
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.csv`
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp_exits.csv`
- `research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp_live_case.csv`
- `tests/test_exp0131_market_signal_tp.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Next Session Startup Prompt

Use this when starting a new session:

```text
先不要改代码。

请先读取：
- AGENTS.md
- docs/codex/PROJECT_BRIEF.md
- docs/codex/CURRENT_STATE.md
- docs/codex/DECISIONS.md
- docs/codex/EXPERIMENT_LEDGER.md
- docs/codex/LIVE_GUARD.md
- docs/codex/HANDOFF.md

然后输出：
1. 你理解的当前项目状态
2. 当前禁止触碰的文件
3. 最近已否定的方向
4. 本次任务你建议怎么做
5. 哪些地方需要我确认

确认前不要修改任何文件。
```

## End Of Session Prompt

Use this before ending a research or implementation session:

```text
请不要继续改代码。

请根据本轮工作更新：
- docs/codex/CURRENT_STATE.md
- docs/codex/HANDOFF.md

如果有实验结果，更新：
- docs/codex/EXPERIMENT_LEDGER.md
- docs/codex/DECISIONS.md（仅当方向被通过、拒绝、暂停或关闭）

要求：
1. 只记录长期有价值的信息
2. 不写闲聊
3. 明确哪些结论已拒绝
4. 明确下次接着做什么
5. 标出所有被修改的文件
```
