# Current State

Last updated: 2026-07-04

## Source Of Truth

Use this file as the current-state entry point. Older handoff files are evidence, not the current operating source of truth when they conflict with this file.

## Repository Tooling

The code-save workflow is now skillized at `.agents/skills/save-code/`.

- It reviews Git scope, confirms live-risk classification, commits, pushes `origin/<current branch>`, and archives committed `HEAD` to a parent-directory `tar.gz`.
- It does not authorize live/demo routing, checkpoint, live config, oracle, protected strategy, scoring, or registry changes. Those still require separate explicit approval.
- `AGENTS.md` records the local Obsidian vault path as `D:\obsidianrepo\zgc的知识库` for agent reference.

## Research Standard

`docs/RESEARCH_STANDARD.md` is the accepted v1 research-governance standard.

- Active Codex automation scope is Stage A-D only: Data Audit, Baseline Freeze, Event Census, and Action Testing.
- Stage E-G are marked Phase 2 and are not active. A Stage D pass must stop with `PHASE2_MANUAL_REVIEW_REQUIRED`, not proceed toward shadow, pre-live, or live activation.
- `REVIEW_REQUIRED` is the explicit gray-zone state between auto-pass and reject.
- Event Census stays ledger-light; top/worst K is a protective tail diagnostic, not final proof. Action Testing must decompound attribution with paired fields such as `pnl_pct_entry_equity`, `log_return`, `fixed_notional_return`, `mae_4h_atr`, optional `R_multiple`, and `delta_pct`, plus normalized `saved_loss_sum`, `missed_profit_sum`, and `saved_loss_to_missed_profit_ratio`. Research top/worst K must not be ranked by raw compounded dollar PnL.
- The v1 default thresholds are declared calibration defaults; they can be changed only by a versioned standard update after at least three independent experiment families.
- DSR/PBO-style statistical tests are conditional, not mandatory for every experiment.
- The standard does not authorize one-click live/demo routing, checkpoint promotion, oracle routing, scoring changes, or production strategy changes.

## Latest Fibonacci Entry Confirmation Probe

`exp_0218` tested whether V2.2's Bollinger breakout confirmation can be replaced by causal Fibonacci extension windows before the existing TimesFM `exp_0068` gate.

- Evidence: `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_report.md`.
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 `m375/bbm375_1p5`, no-Bollinger checkpoint copy plus Fibonacci extension gate, then TimesFM `context=1024/horizon=72/min_edge=-1%/risk_floor=5%`; no live/config/checkpoint/oracle/strategy/scoring/execution change.
- Current BB+TimesFM baseline on the full-path口径: OOS `+581.55%`, DD `-43.55%`, rolling12 `-19.93%`, `221` trades.
- Removing BB and keeping TimesFM is worse: OOS `+488.81%`, DD `-43.20%`, rolling12 `-27.71%`, `222` trades.
- Best headline Fibonacci replacement row `fib_ext_1p000_1p618` nearly matches OOS at `+579.17%`, but worsens DD to `-44.14%`, rolling12 to `-27.59%`, blocks `45` no-BB trades, hurts `8/20` top winners, rescues only `4/20` worst losers, and has normalized net action value `-271.43%`.
- Stricter Fibonacci windows cut too much exposure: `fib_ext_1p236_1p618` leaves only `7` trades, OOS `+23.77%`, and hurts `19/20` top winners.
- Event census on current BB+TimesFM trades shows `fib_extension_lt_1p236` covers `214/221` trades and includes `19/20` top winners and `19/20` worst losers; `fib_extension_gt_1p618` has zero events. The Fib buckets do not separate good from bad entries.
- Verdict: `REJECT`. Do not replace V2.2 Bollinger confirmation with these Fibonacci extension rules, do not tune the tested thresholds from this result, and do not infer live/demo/checkpoint/config changes.

## Latest BEAR Cooldown Ablation

`exp_0217` compared the current ETH v2.2 `m375/bbm375_1p5` baseline with `exit_logic.bear_cooldown` disabled, both raw and with TimesFM `exp_0068` thresholds.

- Evidence: `research_workspace/diagnostics/exp_0217_bear_cooldown_ablation/exp_0217_report.md`.
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 `m375/bbm375_1p5`, TimesFM `context=1024/horizon=72/min_edge=-1%/risk_floor=5%`; no live/config/checkpoint/oracle/strategy/scoring/execution change.
- TimesFM-gated baseline: OOS `+549.49%`, DD `-43.59%`, rolling12 `+5.15%`, `221` trades, `25` blocks.
- TimesFM-gated no-BEAR-cooldown: OOS `+349.01%`, DD `-43.43%`, rolling12 `-6.44%`, `262` trades, `30` blocks.
- Delta from removing cooldown: OOS `-200.49pp`, rolling12 `-11.59pp`, trades `+41`, Sharpe `-0.041`; DD improved only `+0.16pp`.
- Raw/no-gate ablation is also worse on OOS and rolling12: OOS `+469.23% -> +290.46%`, rolling12 `-8.49% -> -18.58%`, trades `246 -> 292`.
- Verdict: `REJECT`. Keep BEAR cooldown in the current baseline; do not remove, retune, or bypass it from this result.

## Latest Live Component Standard Audit

`exp_0205` retro-reviewed the already-live Moirai2 gate and V1 vol targeting under Research Standard v1.

- Evidence: `research_workspace/diagnostics/exp_0205_live_component_v1_standard_audit/artifacts/exp_0205_report.md`.
- TimesFM `exp_0068` retro-passed the normalized Stage D shape but still stops at `PHASE2_MANUAL_REVIEW_REQUIRED`: affected `25`, normalized net `+67.27%`, saved/missed `2.22`, hurt topK `1`, rescued worstK `6`.
- Moirai2 `exp_0093` has positive normalized gate value but is sparse in the 2600d ETH single run: affected `10`, normalized net `+42.68%`, saved/missed `2.70`, hurt topK `1`, rescued worstK `4`. Because `affected_N < 20`, its already-live use is `RETRO_REVIEW_REQUIRED_LEGACY_LIVE_N_LT20`, not a v1 automatic pass.
- Rolling12 in `exp_0205` uses the later/current full-path daily-equity rolling 365d口径, not the older gate-shortlist reset-window replay. This matches later raw/Moirai sizing docs for raw v2.2 `-27.96%` and Moirai2 `-23.48%`, and adds TimesFM `-19.93%` on the same口径.
- V1 20d vol targeting improves compounded OOS/DD/rolling12 (`+9.06%`, `+7.00pp`, `+23.49pp`) but has negative fixed-notional normalized sizing delta (`-58.35%`) and saved/missed `0.53`. This is `RETRO_REVIEW_REQUIRED_LIVE_CONFLICT_DECOMPOUNDED_NEGATIVE`.
- No live/config/checkpoint/execution/oracle/production behavior was changed. The audit does not require automatic rollback and does not authorize expansion, retuning, or default promotion.

## Latest Phase2 Manual Review

`exp_0206` completed a manual second-round review for TimesFM `exp_0068`, Moirai2 `exp_0093`, and V1 20d vol targeting.

- Evidence: `research_workspace/diagnostics/exp_0206_live_component_phase2_manual_review/artifacts/exp_0206_report.md`.
- The operator command using `--timesfm-candidate configs\live\timesfm_gate_exp_0068.json` selects TimesFM `exp_0068`; Moirai2 config is available but not selected. With `--vol-target-sizing`, the command is TimesFM + V1 vol targeting.
- TimesFM `exp_0068`: `MANUAL_PASS_KEEP_MAIN`. It may be used as main gate when explicitly selected by operator config; verify live startup logs show `Forecast gate ENABLED: family=timesfm candidate=exp_0068`.
- Moirai2 `exp_0093`: `MANUAL_REVIEW_OBSERVE_LEGACY_LIVE_N_LT20`. It may remain legacy-live/shadow only if the operator accepts sparse evidence, but it must not replace TimesFM as main or expand without live-shadow parity; require at least `30` live decisions before renewed main-gate promotion discussion.
- V1 vol targeting: `MANUAL_CONDITIONAL_KEEP_OPT_IN_RISK_STABILIZER`. It may stay as explicit opt-in account-stability sizing, but must not become default, be retuned, or be described as a signal-quality improvement because its decompounded normalized net is negative.
- No live/config/checkpoint/execution/oracle/production behavior was changed by this review.

## Latest Model Sibling Replacement Audit

`exp_0207` audited local and online sibling models for the current forecast-gate families.

- Evidence: `research_workspace/diagnostics/exp_0207_model_sibling_replacement_audit.md`.
- Local model inventory: TimesFM `google/timesfm-2.5-200m-pytorch`; Moirai `Salesforce/moirai-2.0-R-small` and `Salesforce/moirai-1.1-R-base`; Chronos `amazon/chronos-2`, `amazon/chronos-bolt-small`, and `amazon/chronos-bolt-base`.
- Online siblings found but not local include TimesFM `1.0/2.0/2.5-transformers/flax`, Moirai `1.0`, `1.1` small/large, Moirai-MoE, and Chronos original T5 plus Bolt tiny/mini variants.
- Verdict: `NO_LOCAL_SIBLING_REPLACEMENT_READY`.
- TimesFM `exp_0068` remains main; Moirai2 `exp_0093` remains challenger shadow only.
- Best offline observe branch is Chronos-2 `edge=-1.5/risk=5`, but it is not a main replacement because yearly reset is weaker than TimesFM, worst rolling12 remains negative, and prior work required year-gap attribution before replacement discussion.
- No new model download, live/demo routing, checkpoint/config, oracle, scoring, strategy, or execution behavior changed.

## Latest Indicator Confluence Census

`exp_0208` tested the proposed first-pass indicator-confluence framing as a Stage C event census only.

- Evidence: `research_workspace/diagnostics/exp_0208_indicator_confluence_census/artifacts/exp_0208_report.md`.
- Scope: ETHUSDT 5m `1300d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, completed-bar decision and next-open execution, official Binance Funding/OI as-of artifacts from `exp_0201`.
- Frozen labels: `weak_breakout_quality`, `volatility_nonexpansion`, `same_side_crowded_extreme`, three pre-registered pairs, and strict all-three conjunction. No score, OR rule, threshold sweep, OOS retune, or action replay.
- TimesFM baseline on this sample: OOS `+251.85%`, full `+925.37%`, DD `-47.31%`, rolling12 `-25.73%`, `129` trades.
- Verdict counts: `REJECT=7`, `KEEP=0`. `weak_breakout_quality` was the only nonzero label (`N=9`, years `4`, top20 `2`, worst20 `1`, MAE4h ratio `0.72`, lag counts `9/9/9`), failing the `N>=10/15` and adverse-shape requirements. All volatility/crowding/confluence rows had `N=0`.
- No Stage D `size_50`, `delay_1bar_confirm`, or `block` replay was run. This result does not authorize live/demo routing, checkpoint/config, oracle, scoring, strategy, execution, or sizing changes.

## Latest Indicator Component Coverage

`exp_0209` decomposed the sparse `exp_0208` labels into component coverage and descriptive relaxation probes.

- Evidence: `research_workspace/diagnostics/exp_0209_indicator_component_coverage/artifacts/exp_0209_report.md`.
- Scope: ETHUSDT 5m `1300d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, completed-bar decision and next-open execution, official Binance Funding/OI as-of artifacts from `exp_0201`.
- Baseline is unchanged from `exp_0208`: OOS `+251.85%`, full `+925.37%`, DD `-47.31%`, rolling12 `-25.73%`, `129` trades.
- Component coverage classes: `EMPTY=4`, `SPARSE_LT_10=6`, `WINNER_RISK=4`, `NO_ADVERSE_SHAPE=1`, `MIXED=1`; no row passed the tail-separation diagnostic.
- Volatility: single components exist (`BBWidth p10 N=20`, range non-expansion `N=13`, ATR-normalized range low `N=10`) but overlap is sparse or empty. `BBWidth p10` and range non-expansion both hit `4/20` top winners and only `3/20` worst losers with MAE ratios below `1.0`; the original and relaxed volatility probes stayed `0`.
- Crowding: same-side Funding p95/p5 is too sparse (`N=3`, top20 `2`, worst20 `0`), OI accel p90 has no adverse shape (`N=13`, top20 `1`, worst20 `2`, MAE4h ratio `1.10`), Funding+OI remains `0`, and the loosest descriptive crowd probe only reaches `N=3`.
- Conclusion: the two zero-event labels were mainly empty because their components do not overlap cleanly on TimesFM-gated entries, not because one easy threshold tweak was missed. No threshold selection, action replay, live/demo routing, checkpoint/config, oracle, scoring, strategy, execution, or sizing change is authorized.

## Latest TimesFM/Chronos Latest Download Smoke

`exp_0210` downloaded the latest official TimesFM and Chronos-2 snapshots and ran a bounded smoke plus cached replay audit.

- Evidence: `research_workspace/diagnostics/exp_0210_timesfm_chronos_latest_download_smoke.md`.
- Downloaded TimesFM `google/timesfm-2.5-200m-pytorch` to `research_workspace/diagnostics/timesfm_2_5_200m_pytorch_latest`, upstream revision `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`.
- Downloaded Chronos `amazon/chronos-2` to `research_workspace/diagnostics/chronos_2_latest`, upstream revision `29ec3766d36d6f73f0696f85560a422f50e8498c`.
- Both downloaded `model.safetensors` files are byte-identical to the existing local models, so prior full-window caches and gate reports remain valid.
- Smoke inference passed for TimesFM after forcing CPU; the unforced run still hits the known RTX 5060 `sm_120` unsupported-CUDA issue in the shared PyTorch build.
- Chronos-2 smoke passed in an isolated `uv --with chronos-forecasting --with "numpy<2" --with "scipy>=1.11"` CPU environment.
- Verdict unchanged: TimesFM `exp_0068` remains main; Chronos-2 conservative remains offline OBSERVE only. No live/config/checkpoint/oracle/strategy/scoring/execution behavior changed.

## Latest TimesFM Frozen Calibrator

`exp_0211` tested the requested TimesFM frozen-feature plus small calibrator path.

- Evidence: `research_workspace/diagnostics/exp_0211_timesfm_frozen_calibrator/artifacts/exp_0211_report.md`.
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`; no TimesFM weight fine-tuning, no live/config/checkpoint/oracle/strategy/scoring/execution change.
- Calibrator: linear ridge model trained on IS raw-decision outcomes using frozen TimesFM directional features only; applied as an additional block gate stacked after the existing TimesFM `exp_0068`.
- Baseline TimesFM `exp_0068`: OOS `+581.55%`, full `+57042.77%`, DD `-43.55%`, rolling12 `-19.93%`, `221` trades.
- Tested IS score quantiles `5/10/15/20%`. All rows are `REJECT`: OOS delta versus TimesFM ranged from `-105.99%` to `-146.85%`, normalized net action value ranged from `-17.50%` to `-81.07%`, and saved/missed stayed below `1.0`.
- Some rows improved DD and rolling12, but only by cutting too much profitable OOS exposure. Do not promote this calibrator and do not infer TimesFM fine-tuning is warranted from this result.

## Latest TimesFM LoRA Offline Contrast

`exp_0212` tested a LoRA-style offline adapter contrast for TimesFM.

- Evidence: `research_workspace/diagnostics/exp_0212_timesfm_lora_offline_contrast/artifacts/exp_0212_report.md`.
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`; research-only offline contrast with no live/config/checkpoint/oracle/strategy/scoring/execution change.
- Adapter: low-rank residual on frozen TimesFM final hidden state, trained on IS decision outcomes. Foundation TimesFM weights were not modified and no deployable checkpoint was produced.
- Train/OOS decisions with targets: `141/105`.
- Best observe row by OOS was `lora_r2_s0p25`: normalized net `+41.75%`, saved/missed `1.43`, hurt topK `2`, rescued worstK `6`, but OOS delta versus TimesFM was `-273.81%` and year slices were `3/4/1`.
- No LoRA row is replacement-ready. Do not promote, checkpoint, live-route, or infer TimesFM fine-tuning approval from this offline result.

## Latest Priority Model Interface / Latency Probe

`exp_0213` checked the prioritized new model candidates for current availability, inference interface, dependency constraints, output shape, and preloaded CPU latency.

- Evidence: `research_workspace/diagnostics/exp_0213_priority_model_interface_latency_probe.md`.
- Scope: synthetic linear close probe only; this is not a gate replay and does not produce strategy OOS/DD/rolling12/trade metrics.
- Lag-Llama `time-series-foundation-models/Lag-Llama`: interface passed with probabilistic samples, h72 CPU latency about `4.147s`; closest fit to the current quantile-gate shape, but needs an isolated GluonTS/Lightning stack and a trusted PyTorch 2.6+ `weights_only=False` checkpoint-load workaround.
- Granite TTM `ibm-granite/granite-timeseries-ttm-r2`: interface passed via `512-96-r2`, h72 point output from the first 72 of 96 steps, CPU latency about `0.0102s`; strongest latency-first candidate, but no native q10/q90 gate was validated.
- Time-MoE `Maple728/TimeMoE-50M`: interface passed only with `transformers==4.40.2`, h72 CPU latency about `4.004s`; newer Transformers failed on `DynamicCache.seen_tokens`, and no probabilistic interface was validated.
- Sundial `thuml/sundial-base-128m`: interface passed with generated samples, h72 CPU latency about `1.045s`; prior `exp_0127` Sundial meta-ablation still warns it did not beat the current stack.
- MOMENT/Timer remain observe-only and were not run.
- No model is replacement-ready from this interface probe. Next approved research-only direction is a bounded Lag-Llama decision-pack gate probe, followed by a Granite point-gate scout.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest Priority Model Bounded Gate Probes

`exp_0214`, `exp_0215`, and `exp_0216` ran bounded replacement probes for the priority candidates.

- Lag-Llama evidence: `research_workspace/diagnostics/exp_0214_lag_llama_bounded_gate_probe/artifacts/exp_0214_report.md`.
- Granite TTM evidence: `research_workspace/diagnostics/exp_0215_granite_ttm_point_gate_scout/artifacts/exp_0215_report.md`.
- Sundial evidence: `research_workspace/diagnostics/exp_0216_sundial_bounded_gate_probe/artifacts/exp_0216_report.md`.
- Scope: fixed 80-trade raw v2.2 diagnostic pack only: top20 normalized winners, worst20 normalized losers, and middle40 controls. These are not full gate replays and do not produce strategy OOS/DD/rolling12/trade metrics.
- Lag-Llama `context=32`, default lag history `1092`, `horizon=72`, `20` samples, CPU p95 latency `2.94s`. All four TimesFM-style quantile gate rows are `REJECT`; best row still blocked `68/80`, hit `17/20` top winners, had saved/missed `0.28`, and normalized net action value `-587.52%`.
- Granite TTM `512-96-r2`, point h72 scout, CPU p95 latency `0.0035s`. All four point-edge rows are `REJECT`; best row `ttm_point_edge_m1p0` blocked `29/80`, hit `4/20` top winners, saved/missed `0.68`, and normalized net action value `-55.39%`.
- Sundial `context=1024`, `horizon=72`, `8` samples, CPU p95 latency `0.357s`. All four TimesFM-style quantile gate rows are `REJECT`; best row `sundial_edge_m1p0_risk5p0` blocked `19/80`, hit `4/20` top winners and `2/20` worst losers, saved/missed `0.34`, and normalized net action value `-147.22%`.
- Lag-Llama is not worth a full 246-decision cache under this quantile-gate framing. Granite remains useful as a latency reference, but the point gate did not pass the bounded pack. Sundial is fast enough for offline/live-shadow latency, but the 80-pack gate shape is not selective enough for replacement.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

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

2026-06-28 user explicitly authorized a Bitget live implementation of `exp_0140` V1 20d realized-vol entry sizing.

- Implementation is opt-in through `live_bitget_quant.py --vol-target-sizing`; commands without this flag keep the previous 100% entry sizing behavior except for any existing risk profile.
- Scope is entry sizing only: no signal change, no exit change, no checkpoint/config/API/leverage/order-price change, and no intratrade rebalance.
- Formula matches the clean audit: `realized_vol_20d = close.pct_change().rolling(5760).std().shift(1) * sqrt(365.25*288)`, `vol_ref=0.794812`, multiplier `clip(vol_ref / realized_vol_20d, 0.4, 1.0)`.
- If 20d completed 5m history is unavailable or invalid, multiplier falls back to `1.0` and the reason is logged.
- Bitget maker pending opens now store the original entry notional/multiplier context so maker-to-IOC fallback keeps entry-fixed sizing semantics.
- Logs/state record realized vol, raw/clipped multiplier, combined multiplier, raw size, rounded size, fillability, and min-lot notional. This is important because `--capital 10 --leverage 1` around ETH `1500-1600` remains below the `0.01 ETH` lot even before vol down-sizing.
- Rollback path: restart without `--vol-target-sizing` or revert the live sizing commit. No live config or checkpoint needs to be changed.

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
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_report.md`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_report.md`
- `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_report.md`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_report.md`
- `research_workspace/diagnostics/exp_0208_indicator_confluence_census/artifacts/exp_0208_report.md`
- `research_workspace/diagnostics/exp_0209_indicator_component_coverage/artifacts/exp_0209_report.md`

Recent research design evidence:

- `research_workspace/proposals/exp_0200_model_direction_standalone_baseline.md`

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
- `exp_0139` tested risk-based entry-fixed sizing. Stage 0 did not prove high ATR entries are worse: Q5 ATR had avg return `+4.33%`, only `3/20` worst losers, and only `2.18%` of top20 winner pnl, so ATR risk parity was `skipped_by_stage0`. V1 20d vol targeting is the current only empirically effective position-stability scheme: OOS `+638.77%` versus baseline `+629.72%`, DD `-44.12%` versus `-51.12%`, rolling12 `+0.01%`, top20 damage `9.98%`, fee10 OOS `+556.39%`. It still failed the first-pass sizing-shadow DD gate (max DD better than roughly `-40%` or DD improve `>=20%`), but was later user-authorized as an opt-in Bitget live entry-sizing control because it is the only branch with clean rolling12-positive stability evidence.
- `exp_0140` audited the `exp_0139` V1 vol-target scheme. The vol reference is clean: `train_only_entry_median`, not full-sample, with realized vol shifted one bar. V1 turned the baseline worst rolling12 window (`2021-05-08` to `2022-05-08`) from `-23.48%` to `+1.21%`, and its own worst rolling12 stayed near flat at `+0.01%`. However, V1 max DD occurred in a low-volatility window (`2022-02-26` to `2022-05-04`, median vol percentile `36.35%`) while average active size stayed `96.06%`, so the residual DD is not a clean "high vol but under-throttled" narrow-refinement case. Verdict: `OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE`; no `exp0141` one-shot refinement from this audit. Live use is limited to the explicit `--vol-target-sizing` Bitget entry-sizing switch added after separate user approval.
- `exp_0141` tested Efficiency Ratio Stage 0 only on baseline trades with windows `{20, 50, 100}`. All three windows are `MIXED_HIGH_VARIANCE_BUCKET`: low ER Q1 contains both top winners and worst losers (`ER20`: `3` top20 / `3` worst20; `ER50`: `6` top20 / `6` worst20; `ER100`: `7` top20 / `5` worst20). ER50 Q1 carries `19.75%` of top20 pnl and `41.94%` of worst20 loss; ER100 Q1 carries `22.89%` of top20 pnl. Side/regime/realized-vol/breakout-strength distributions overlap enough that ER single-variable low-bucket sizing is REJECT/frozen. No trading action, sizing, skip rule, live/config/checkpoint/oracle/production change.
- `exp_0142` reran `channel_breakout_v2_2_m375_bbm375_1p5` raw/no-gate trades and tested pre-entry vol shock/range shock attribution only. Raw v2.2 baseline was full `+25768.70%`, OOS `+501.71%`, DD `-53.98%`, rolling12 `-27.96%`, `246` trades. `entry_vol_z > 2.5` flagged `15` trades, hit only `2/20` worst20 losses, hit `2/20` top20 winners, and had positive flagged pnl `+192823.96`; clean_loss vol-z median/p75 (`0.116`/`0.929`) was not clearly above normal_winner (`-0.125`/`0.683`). Verdict: REJECT. Do not continue this vol-shock line into BOCPD/HMM or a gate without a new, clean hypothesis.
- `exp_0143` tested three path-dependent sizing overlays on top of V1 vol targeting: `lv_eq_mul_070`, `eqdd_step_soft`, and `combo`. Each variant independently replayed its own equity path; bar `t` sizing used only bar `t-1` confirmed variant equity DD, rolling30 strategy return, vol percentile, and V1 target size. V1 baseline was OOS `+639.10%`, DD `-44.14%`, rolling12 `-0.02%`, fee10 OOS `+556.67%`. The overlays improved DD to about `-37%`, and reduced the 2022-02-26 to 2022-05-04 low-vol slow-bear average active size (`96.08%` baseline to `72.54%`/`55.59%`), but all failed: rolling12 fell to `-12.39%` to `-14.13%`, OOS retention was only `52.53%` to `73.59%`, and top20 cost was `48.05%` to `84.22%`. Verdict: REJECT. Do not convert this into a gate, BOCPD/HMM branch, signal change, or live/default sizing change.
- `exp_0144` ran the pre-registered Stage0 raw feature separation sanity check on exactly three structural features: `donchian_width_pct`, `bars_since_last_reversal`, and `ADX(14)`. Raw v2.2 fixed-size/no-gate trades were the discovery sample (`246` trades, all cutpoints from raw IS); V1 vol-target trades were validation only (`236` trades). All six lowest/highest quintile buckets failed. Raw flagged pnl was positive in every bucket; worst20 capture never exceeded `5/20`; top20 hits were often high (`3` to `7/20` except donchian-width-high at `2/20`); only donchian-width-high reached the bad-label-rate multiple (`2.79x`) but still had positive raw pnl `+143511.93`, only `4/20` worst20, and failed OOS/V1 direction. Verdict: REJECT. Do not proceed to LightGBM/LSTM/TCN/meta-label shadow or any gate from these three features.
- `exp_0200_model_direction_standalone_baseline` ran the locked research-only standalone model-direction design once on ETH 5m 2600d data using Logistic Regression and LightGBM, horizons `{72, 288}`, cost buffers `{0.30%, 0.20%}`, shifted completed-bar features, purged walk-forward validation, validation-only threshold selection, and final 30% OOS exactly once. Overall verdict: REJECT. Logistic rows failed fee10/slippage or stability. The best row, `lightgbm_h288_cb30bp`, had OOS `+5.70%`, fee10/slippage stress `+3.68%`, DD `-7.07%`, rolling12 `+0.08%`, annual round-trip equivalent `5.96`, and `NO_TRADE` `99.94%`, but failed because the highest-confidence bucket was negative/non-monotonic and profit depended on a single positive year. Funding/OI were omitted because no aligned local files were found. This does not authorize post-OOS threshold/cost/feature edits, LSTM/TCN/Transformer, Moirai/TimesFM direct trading, live/demo routing, checkpoint/config/oracle changes, or production strategy changes.
- `exp_0208` rejects the first-pass indicator-confluence census. On the TimesFM `exp_0068` baseline over ETHUSDT 5m `1300d`, `weak_breakout_quality` had only `9` events and adverse excursion was lower than non-events (`MAE4h ratio 0.72`), while volatility non-expansion, same-side crowded extreme, all pair rows, and strict all-three had `0` events. No label reached KEEP, so no `size_50`, `delay_1bar_confirm`, or `block` action replay was run. Do not retune these thresholds from this result.
- `exp_0209` explains the `exp_0208` sparsity: the individual volatility and crowding components appear, but their overlaps are sparse/empty or carry winner risk, and relaxed descriptive probes do not reach enough count or tail separation. This closes immediate threshold-relaxation action for the indicator-confluence line.

## Latest Funding/OI Research

`exp_0201` completed the research-only Funding/OI as-of gate experiment under
`research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/`.

Source contract:

- Funding and OI are aligned independently by `published_at <= bar_open`.
- Rolling features are computed at source-observation frequency and exclude the
  current observation from their historical mean/std.
- Missing/stale features are fail-open and explicitly counted.
- Official Binance sources supplied `4,005` settled Funding observations and
  `384,245` valid 5m OI snapshots across all `1,336` requested archives.
- `101` impossible zero-OI rows were dropped and counted; `235` individual 5m
  slots were missing and were not imputed.
- Maximum normal timestamp jitter was `35s`.
- Joint feature coverage was `99.9621%` at `+5m`, `99.9639%` at `+10m`, and
  `99.9704%` at `+1h`; all no-future checks passed.

Frozen experiment result:

- Baseline raw v2.2: OOS `+240.09%`, full `+788.13%`, DD `-47.67%`,
  rolling12m `-26.23%`, `139` trades.
- `funding_extreme`: OOS unchanged, full `+784.70%`, no top20/worst20 capture.
  Verdict `OBSERVE` as non-improving/inert.
- `oi_confirmation`: OOS `+196.91%`, DD `-44.02%`, rolling12m `-16.77%`;
  `9/20` top winners and `11/20` worst losers hit. Verdict `REJECT`.
- `funding_and_oi`: same OOS/DD/rolling profile and `9/20` top-winner damage.
  Verdict `REJECT`.
- OI variants remained below baseline OOS under `+5m/+10m/+1h`. Do not tune
  these thresholds or reopen this exact gate design.

`exp_0202` then tested the user's combined price/OI/Funding regime framing under
`research_workspace/diagnostics/exp_0202_price_oi_funding_regime/`.

- Price direction uses completed 4h close-to-close return before the next-open
  entry bar. "Funding turns negative" is proxied as `funding_rate <= 0` or
  `funding_z <= 0` at the entry open.
- Baseline trade buckets were mixed, not clean filters:
  `healthy_long_trend` had `34` trades, PnL `+12534.82`, but hit `6/20` top
  winners and `8/20` worst losers; `short_covering_rally` had `34` trades, PnL
  `+13394.60`, with `5/20` top and `5/20` worst; `short_trend_confirm` had
  `14` trades, PnL `+12899.74`, with `3/20` top and `2/20` worst.
- Strict `crowded_extreme_block` (`price up/down + OI z >= 2 + same-side
  Funding z >= abs(2)`) did not trigger on baseline entries: OOS/DD/trades
  unchanged at `+240.09%`, `-47.67%`, `139`; verdict `OBSERVE` as inert.
- `confirmation_only` (longs require `healthy_long_trend`, shorts require
  `short_trend_confirm`) is `REJECT`: OOS fell to `+162.82%`, DD worsened to
  `-53.24%`, rolling12m to `-31.54%`, and it hit `12/20` top winners despite
  hitting `10/20` worst losers. It also stayed below baseline under `+10m/+1h`.
- Conclusion: price/OI/Funding combinations are useful attribution labels, but
  not a mandatory entry filter as tested. Do not promote or tune
  `confirmation_only`; if this line continues, start with a new sparse-risk
  hypothesis such as relaxed crowded-threshold sensitivity, not broad blocking.

`exp_0203` ran that relaxed crowded-threshold sensitivity under
`research_workspace/diagnostics/exp_0203_crowded_sensitivity/`.

- The experiment deliberately excluded `confirmation_only` and tested only
  same-side crowded-risk brakes:
  `price_4h_ret > 0 and oi_change_z >= x and funding_z >= y` for longs, and
  `price_4h_ret < 0 and oi_change_z >= x and funding_z <= -y` for shorts.
- Grid: `x,y in {1.0, 1.25, 1.5, 1.75, 2.0}` crossed with actions
  `block`, `size_50`, and `delay_1bar_confirm`; `75` non-baseline rows.
- All non-baseline rows are `OBSERVE`; no `SHADOW_CANDIDATE`.
- Claude's sample-size warning bound the result: maximum canonical crowded
  count was only `9/139` entries (`6.47%`) at `x=1/y=1`; every other row had
  fewer triggers, often zero.
- Best canonical row was `crowded_x1_y1_delay_1bar_confirm`: OOS `+247.05%`
  versus baseline `+240.09%`, DD `-45.29%` versus `-47.67%`, rolling12m
  `-19.91%` versus `-26.23%`, `0/20` top winners affected and `3/20` worst
  losers affected. It remains `OBSERVE` because `N=9` is below the `N >= 10`
  evidence floor and `+1h` publication-lag sensitivity removed the OOS edge.
- `crowded_x1_y1_size_50` also improved canonical OOS/DD (`+244.95%`,
  `-45.21%`) with `0/20` top and `3/20` worst affected, but is also sparse and
  lag-sensitive.
- Conclusion: the narrow crowded brake has a cleaner shape than
  `confirmation_only`, especially `delay_1bar_confirm`/`size_50`, but the
  current as-of Funding/OI definition is too sparse for promotion. Do not
  promote or live-route; if continuing, the next hypothesis must increase event
  count without becoming a broad entry filter.

`exp_0204a` then ran the high-priority crowding event scout under
`research_workspace/diagnostics/exp_0204a_crowding_event_scout/`.

- Scope: event-foundation census only, not a strategy-action replay. It tested
  `25` high-priority definitions across four families: Funding/OI rolling
  percentile, OI acceleration, leverage intensity, and funding
  slope/persistence.
- Liquidation labels are not locally available and were not used as gate
  features. Rolling percentiles are causal: shifted/as-of values are ranked only
  against prior rolling history, never against full-sample or OOS values.
- Verdict counts: `KEEP=0`, `OBSERVE=4`, `REJECT=21`.
- `leverage_intensity p75/p80/p85` reached OBSERVE with sufficient-ish counts
  and lag-stable event counts (`N=16/14/11`, years `4`), but all had
  `MAE_4h_ratio < 1.0`, so they do not support a crowded-risk brake.
- `oi_accel p85` reached OBSERVE (`N=10`, top20 `1`, worst20 `3`, lag stable),
  but `MAE_4h_ratio=0.92`, so it also lacks adverse-excursion shape.
- `funding_slope_persistence` had the best MAE shape (`N=21`, years `5`,
  `MAE_4h_ratio=1.69`, lag stable), but it hit `4/20` top winners and only
  `4/20` worst losers, failing worst/top separation. Verdict `REJECT`.
- Funding/OI percentile rows often had high MAE ratios, but all were sparse
  (`N < 10`) and are `REJECT`.
- Conclusion: no 0204a definition provides a statistical foundation for
  `exp_0204b`. Do not run the full crowding census from this evidence unless a
  new event-count hypothesis is introduced.

Evidence:

- `research_workspace/proposals/exp_0201_funding_oi_asof_gate.md`
- `research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/artifacts/exp_0201_source_manifest.json`
- `research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/artifacts/exp_0201_experiment_report.md`
- `research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/artifacts/exp_0201_experiment_matrix.csv`
- `research_workspace/proposals/exp_0202_price_oi_funding_regime.md`
- `research_workspace/diagnostics/exp_0202_price_oi_funding_regime/artifacts/exp_0202_report.md`
- `research_workspace/diagnostics/exp_0202_price_oi_funding_regime/artifacts/exp_0202_bucket_summary.csv`
- `research_workspace/diagnostics/exp_0202_price_oi_funding_regime/artifacts/exp_0202_experiment_matrix.csv`
- `research_workspace/proposals/exp_0203_crowded_sensitivity.md`
- `research_workspace/diagnostics/exp_0203_crowded_sensitivity/artifacts/exp_0203_report.md`
- `research_workspace/diagnostics/exp_0203_crowded_sensitivity/artifacts/exp_0203_experiment_matrix.csv`
- `research_workspace/diagnostics/exp_0203_crowded_sensitivity/artifacts/exp_0203_lag_sensitivity.csv`
- `research_workspace/proposals/exp_0204a_crowding_event_scout.md`
- `research_workspace/diagnostics/exp_0204a_crowding_event_scout/artifacts/exp_0204a_report.md`
- `research_workspace/diagnostics/exp_0204a_crowding_event_scout/artifacts/exp_0204a_summary.csv`
- `research_workspace/diagnostics/exp_0204a_crowding_event_scout/artifacts/exp_0204a_events.csv`

Validation:

- `uv run pytest research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/tests -q`
  (`21 passed`)
- `uv run ruff check research_workspace/diagnostics/exp_0201_funding_oi_asof_gate`
- `uv run pytest research_workspace/diagnostics/exp_0202_price_oi_funding_regime/tests -q`
  (`8 passed`)
- `uv run ruff check research_workspace/diagnostics/exp_0202_price_oi_funding_regime`
- `uv run pytest research_workspace/diagnostics/exp_0203_crowded_sensitivity/tests -q`
  (`4 passed`)
- `uv run ruff check research_workspace/diagnostics/exp_0203_crowded_sensitivity`
- `uv run pytest research_workspace/diagnostics/exp_0204a_crowding_event_scout/tests -q`
  (`8 passed`)
- `uv run ruff check research_workspace/diagnostics/exp_0204a_crowding_event_scout`

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
- tuning the 20d vol-targeting window/ref/clip or making it a checkpoint/config/default profile without a separate approval; current live use is only the explicit `--vol-target-sizing` Bitget switch
- Efficiency Ratio single-variable Q1/Q2 low-bucket sizing as framed in `exp_0141`; low ER is a mixed high-variance bucket, not a clean bad-trade bucket
- raw v2.2 pre-entry vol-shock/range-shock gates as framed in `exp_0142`; `vol_z > 2.5` did not concentrate worst20 losses and flagged profitable/top-winner exposure
- low-vol slow-bear V1 sizing overlays as framed in `exp_0143`; the overlays reduce DD but destroy rolling12/OOS/top20 retention
- Stage0 structural-feature separation from `exp_0144`; `donchian_width_pct`, `bars_since_last_reversal`, and `ADX(14)` do not cleanly separate raw worst20 losses from top20 winners
- model-direction tuning from `exp_0200`; the one-shot Logistic/LightGBM V1 failed the strict cost, confidence-monotonicity, and year-stability gates, so do not retune thresholds/cost buffers/features or continue into LSTM/TCN/Transformer from this result
- Funding/OI entry-gate tuning from `exp_0201`; extreme Funding was inert and OI confirmation damaged `9/20` top winners while reducing OOS under all publication-lag assumptions
- Price/OI/Funding `confirmation_only` gating from `exp_0202`; the labels are
  attribution-useful but mixed, and the strict confirmation gate damaged `12/20`
  top winners while reducing OOS and worsening DD
- Promotion of `exp_0203` crowded sensitivity rows; the best rows improved
  canonical OOS/DD with no top20 damage, but all rows are sparse (`max N=9`) and
  lag-sensitive, so they stay OBSERVE only
- Full `exp_0204b` crowding census from the current 0204a evidence; 0204a found
  no KEEP definitions, so broad expansion is not justified without a new
  event-count hypothesis
- Expansion or retuning of already-live Moirai2/V1 behavior from `exp_0205`;
  Moirai2 is positive but below the v1 `affected_N >= 20` floor, and V1 has a
  fixed-notional normalized attribution conflict despite better compounded
  OOS/DD/rolling12
- Live-gate or block-matrix promotion of Kronos-small from `exp_0205a`; an
  isolated PyTorch `2.7.1+cu128` environment now executes on the RTX 5060
  Laptop `sm_120` device, but `h48/sample16` measured p95 `5.505s` and
  `h96/sample16` measured `11.122s`, above the `<3s` live line
- Continuation of the frozen Kronos `exp_0205b` path-conflict gate into
  `exp_0205c`; it blocked `198/246` base trades, including `16/20` normalized
  top winners, and its `172` unique-vs-existing-gate blocks had mean PnL_R
  `+22.305`
- Full-246 Kronos-base census or base-model threshold tuning from `exp_0205d`;
  base blocked `63/80`, hit `17/20` top winners, and had blocked-set Jaccard
  `0.908` versus small, so greater capacity did not repair selectivity

## Next Useful Work

Best next steps:

1. Validate live-shadow parity for already-live `exp_0093` against research replay, then manually review the `exp_0205` legacy-live `affected_N < 20` finding.
2. Continue attribution of Moirai `risk_floor=3%` versus live `risk_floor=4%`, but keep it diagnostic-only until promoted.
3. If proposing a gate promotion, produce raw / regime-permission / safe-execution / optional DD guard results and explicitly report top-winner damage.
4. Keep live/demo routing and checkpoint changes behind explicit approval.
5. Treat the market-signal exit line as frozen. `exp_0135` rejected Donchian internal-return Stage 1, and `exp_0136` rejected core Donchian multitimeframe diversification Stage 1, so do not run either Stage 2 branch unless a materially different live-knowable channel-structure discriminator is identified first.
6. `exp_0137` ETH/BTC overlap rejected BTC core Donchian as a shadow sleeve. Do not continue by optimizing BTC weights or adding BTC Moirai/BB/regime overlays unless a new independent hypothesis is stated first.
7. `exp_0138` rejected the first position-sizing branch. Do not tune reclaim-add sizes, add cooldowns, or DD throttle tiers from this result; a next sizing idea needs a different hypothesis than "initial under-size then add on 72-bar reclaim."
8. `exp_0139`/`exp_0140` leave V1 20d vol targeting as the only current position-stability scheme with live opt-in support. `exp_0205` adds that V1's fixed-notional normalized delta is negative even though compounded OOS/DD/rolling12 improve, so do not tune clip/window or expand default use until a manual risk review resolves that conflict. V2 ATR risk parity should stay skipped unless Stage 0 evidence changes.
9. `exp_0141` rejects ER single-variable low-bucket sizing. Do not proceed to `Q1 size 50%, Q2-Q5 100%` unless a materially new, live-knowable separability hypothesis is stated and tested first.
10. `exp_0142` rejects the raw v2.2 vol shock/chop attribution path. Do not expand it into BOCPD/HMM, LightGBM, or any gate unless a different raw-trade separability signal is demonstrated first.
11. `exp_0143` rejects the low-vol slow-bear V1 sizing overlay path. Do not tune these thresholds or turn the low-vol/equity-DD/recent-loss conditions into a gate unless a new hypothesis can preserve rolling12 and top20 winners.
12. `exp_0144` rejects the three-feature Stage0 raw separation check. Do not start `exp_0145_logistic_lightgbm_meta_label_shadow`, LSTM, TCN, or a gate from `donchian_width_pct`, `bars_since_last_reversal`, or `ADX(14)` unless a new pre-registered feature family is proposed.
13. `exp_0200` is rejected. Do not retune thresholds, cost buffers, feature lists, or LightGBM depth after seeing OOS, and do not continue this line into LSTM/TCN/Transformer or direct Moirai/TimesFM trading without a new pre-registered hypothesis.
14. `exp_0205a` is `REJECT_FOR_LIVE_LATENCY / OBSERVE_OFFLINE`. The original
    shared environment remains PyTorch `2.6.0+cu124`, while an ignored,
    research-only PyTorch `2.7.1+cu128` environment now validates `sm_120`.
    Five-event GPU follow-up gave h48 p95 `0.739s/1.941s/5.505s` for sample
    counts `1/5/16`; the h96/sample16 stress smoke was `11.122s`. VRAM was not
    the blocker (`476 MB` peak reserved), but sample16 misses the live line.
    Lower sample counts produced negative volume/amount on all five h48 events.
    Do not run `exp_0205c`; `exp_0205b` requires a separately accepted
    offline-only framing plus strict output rejection/repair.
15. `exp_0205b` completed that offline census and is `REJECT`. Across `246`
    closed base entry/reversal trades and `738` Kronos event-horizon forecasts,
    inference failures were zero. The frozen two-of-three path-conflict rule
    blocked `198` trades (`80.5%`), versus TimesFM `25` and Moirai2 `10`.
    Jaccard was low (`0.121`/`0.045`), but Kronos had `172` blocks unique to the
    existing-gate union, including `11/20` worst losers and `16/20` top winners.
    Those unique blocks had winner/loser count `78/94`, mean PnL_R `+22.305`,
    median `-2.812`, and sum `+3836.427`. This is broad destructive filtering,
    not useful incremental selectivity. Do not run `exp_0205c` or tune the
    observed conflict rule after seeing the census; a future Kronos branch
    requires a new pre-registered event definition. After rejection, the
    ignored Kronos model/CUDA venv cache was removed, freeing approximately
    `5.70 GB`; the remaining Kronos HuggingFace cache, JSONL feature caches,
    and exp0205 Python caches were also removed. Reports, census CSV, summary
    CSV/JSON, and experiment metadata remain. The exp0205a setup script can
    recreate the environment if a new hypothesis is approved.
16. `exp_0205d` tested whether Kronos-base capacity fixes the broad small-model
    block shape on a frozen `80`-trade pack (`20` top, `20` worst, `40`
    IQR/year/side controls). Base completed `240/240` forecasts with zero
    failures, but blocked `63/80` (`78.8%`) versus small `61/80` (`76.2%`).
    Their blocked-set Jaccard was `0.908`. Base blocked `17/20` top winners and
    `13/20` worst losers. Its four unique-vs-small blocks contained two winners
    and two losers, only one unique worst20 hit, two unique top20 hits, and mean
    PnL_R `+417.657`. Structure/flow repair rows were also not improved
    (`54/34` base versus `53/31` small). This rejects the capacity explanation
    and blocks a full base census. The recreated CUDA venv and small/base model
    cache were removed after the verdict, freeing approximately `6.0 GB`; the
    later cache cleanup removed the remaining base JSONL feature cache. Reports,
    derived CSV/JSON evidence, and experiment metadata remain.

## Current Protected Baseline Rule

No current research file authorizes:

- checkpoint change
- live routing change
- default oracle route change
- scoring change
- production strategy change
- TimesFM main gate replacement

Any of those requires a separate explicit task and approval.
