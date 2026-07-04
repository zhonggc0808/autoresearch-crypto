# Handoff

Last updated: 2026-07-04

## Current Handoff Summary

The repository memory structure has been moved toward repo-versioned memory:

- `AGENTS.md` is now the hard-rule entry point.
- `docs/codex/` stores long-term project memory.
- `.agents/skills/live-change-guard/` stores the first project skill for live-risk preflight.
- `.agents/skills/save-code/` stores the commit, push, and committed-HEAD archive workflow.
- `AGENTS.md` now also records the local Obsidian vault path:
  `D:\obsidianrepo\zgc的知识库`.

No strategy, live runtime, checkpoint, exchange config, oracle, or research artifact behavior was intentionally changed as part of this memory-structure update.

## Latest Research Governance Standard

On 2026-07-01, `docs/RESEARCH_STANDARD.md` v1 was accepted as the project research-governance standard.

- Codex automation is enabled only for Stage A-D: Data Audit, Baseline Freeze, Event Census, and Action Testing.
- Stage E-G are Phase 2 and not active. If Stage D passes, stop with `PHASE2_MANUAL_REVIEW_REQUIRED` for manual review.
- The standard includes `REVIEW_REQUIRED`, default v1 thresholds, conditional DSR/PBO triggers, an auto-available versus dataset-required feature matrix, and a no one-click live-pipeline rule.
- Event Census remains lightweight, with top/worst K used as a protective tail diagnostic rather than the final judge. Action Testing decompounds signal attribution with `pnl_pct_entry_equity`, `log_return`, `fixed_notional_return`, `mae_4h_atr`, optional `R_multiple`, `delta_pct`, normalized `saved_loss_sum`, `missed_profit_sum`, and `saved_loss_to_missed_profit_ratio`; research top/worst K must not use raw compounded dollar PnL. Full stress and fixed-vs-compounded consistency checks belong to Phase 2 pre-live review.
- This was a documentation/governance change only. It did not change strategy, live runtime, checkpoint, config, oracle, scoring, exchange routing, or production behavior.

## Latest Fibonacci Entry Confirmation Probe

On 2026-07-04, `exp_0218` tested whether V2.2's Bollinger breakout confirmation can be replaced with causal Fibonacci extension windows before the existing TimesFM `exp_0068` gate.

- Package/report: `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_report.md`
- Scope: ETHUSDT 5m `2600d`; ChannelBreakout v2.2 `m375/bbm375_1p5`; no-Bollinger checkpoint copy plus Fib extension gate; TimesFM `context=1024`, `horizon=72`, `min_edge=-1%`, `risk_floor=5%`.
- Method: no production strategy edit. The script disables `bollinger_breakout_enabled` only in memory, applies Fib extension windows to no-BB decision bars, then applies TimesFM.
- Current BB+TimesFM baseline: OOS `+581.55%`, DD `-43.55%`, rolling12 `-19.93%`, `221` trades.
- No-BB+TimesFM control: OOS `+488.81%`, DD `-43.20%`, rolling12 `-27.71%`, `222` trades.
- Best headline Fib replacement `fib_ext_1p000_1p618`: OOS `+579.17%`, DD `-44.14%`, rolling12 `-27.59%`, `181` trades, `45` Fib blocks, normalized net `-271.43%`, hurt top20 `8`, rescued worst20 `4`.
- Stricter windows over-blocked: `fib_ext_1p236_1p618` left only `7` trades, OOS `+23.77%`, hurt top20 `19`, rescued worst20 `18`.
- Event census on current BB+TimesFM trades found `fib_extension_lt_1p236` covers `214/221` trades and includes `19/20` top winners and `19/20` worst losers; `fib_extension_gt_1p618` has zero events.
- Verdict: `REJECT`. Do not replace V2.2 Bollinger confirmation with these Fibonacci extension windows, and do not tune the tested thresholds from this result.

Validation:

- `uv run python research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation.py`
- `uv run ruff check research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation.py`

Modified files:

- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation.py`
- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest BEAR Cooldown Ablation

On 2026-07-03, `exp_0217` compared the current ETH v2.2 `m375/bbm375_1p5` baseline with `exit_logic.bear_cooldown` disabled, both raw and TimesFM `exp_0068` gated.

- Package/report: `research_workspace/diagnostics/exp_0217_bear_cooldown_ablation/exp_0217_report.md`
- Scope: ETHUSDT 5m `2600d`; ChannelBreakout v2.2 `m375/bbm375_1p5`; TimesFM `context=1024`, `horizon=72`, `min_edge=-1%`, `risk_floor=5%`.
- TimesFM baseline: OOS `+549.49%`, DD `-43.59%`, rolling12 `+5.15%`, `221` trades, `25` blocks.
- TimesFM no-BEAR-cooldown: OOS `+349.01%`, DD `-43.43%`, rolling12 `-6.44%`, `262` trades, `30` blocks.
- Removing cooldown loses `-200.49pp` OOS and `-11.59pp` rolling12 while adding `41` trades; DD improves only `+0.16pp`.
- Raw/no-gate also worsens: OOS `+469.23% -> +290.46%`, rolling12 `-8.49% -> -18.58%`, trades `246 -> 292`.
- Verdict: `REJECT`. Keep BEAR cooldown; this does not authorize live/config/checkpoint/oracle/strategy/scoring/execution changes.

Validation:

- `uv run python research_workspace/diagnostics/exp_0217_bear_cooldown_ablation.py`
- `uv run ruff check research_workspace/diagnostics/exp_0217_bear_cooldown_ablation.py`

Modified files:

- `research_workspace/diagnostics/exp_0217_bear_cooldown_ablation.py`
- `research_workspace/diagnostics/exp_0217_bear_cooldown_ablation/`
- `research_workspace/diagnostics/exp_0217_bear_cooldown_ablation_cache.json`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest Live Component Supplemental Audit

On 2026-07-01, `exp_0205` retro-reviewed already-live Moirai2 `exp_0093` and V1 vol targeting under Research Standard v1.

- Package:
  `research_workspace/diagnostics/exp_0205_live_component_v1_standard_audit/`
- Report:
  `research_workspace/diagnostics/exp_0205_live_component_v1_standard_audit/artifacts/exp_0205_report.md`
- Moirai2 `exp_0093`: positive normalized block value (`+42.68%`, saved/missed `2.70`, hurt topK `1`, rescued worstK `4`) but only `10` affected trades in the ETH 2600d run, below the v1 `affected_N >= 20` floor. Verdict: `RETRO_REVIEW_REQUIRED_LEGACY_LIVE_N_LT20`.
- Rolling12 was corrected to the later/current full-path daily-equity rolling 365d口径, not the older gate-shortlist reset-window replay. It matches later raw/Moirai sizing docs for raw v2.2 `-27.96%` and Moirai2 `-23.48%`, and adds TimesFM `-19.93%` on the same口径.
- V1 vol targeting: compounded OOS/DD/rolling12 improve, but fixed-notional normalized sizing delta is negative (`-58.35%`) with saved/missed `0.53`. Verdict: `RETRO_REVIEW_REQUIRED_LIVE_CONFLICT_DECOMPOUNDED_NEGATIVE`.
- TimesFM `exp_0068` retro-passes the normalized Stage D shape, but any pass still stops at `PHASE2_MANUAL_REVIEW_REQUIRED`.
- No live/config/checkpoint/execution/oracle/production behavior changed. Do not expand, retune, promote, or rollback from this audit without explicit operator review.

## Latest Phase2 Manual Review

On 2026-07-01, `exp_0206` completed a manual second-round review of TimesFM `exp_0068`, Moirai2 `exp_0093`, and V1 20d vol targeting.

- Package:
  `research_workspace/diagnostics/exp_0206_live_component_phase2_manual_review/`
- Report:
  `research_workspace/diagnostics/exp_0206_live_component_phase2_manual_review/artifacts/exp_0206_report.md`
- Operator command interpretation: `--timesfm-candidate configs\live\timesfm_gate_exp_0068.json` selects TimesFM `exp_0068`, not Moirai2. With `--vol-target-sizing`, the command runs TimesFM + V1 vol targeting.
- TimesFM `exp_0068`: `MANUAL_PASS_KEEP_MAIN`; allowed only as explicit main-gate selection, no retune/default/checkpoint/config change.
- Moirai2 `exp_0093`: `MANUAL_REVIEW_OBSERVE_LEGACY_LIVE_N_LT20`; may remain legacy-live/shadow only if sparse evidence is accepted, but should not replace TimesFM without live-shadow parity and at least `30` live decisions.
- V1 vol targeting: `MANUAL_CONDITIONAL_KEEP_OPT_IN_RISK_STABILIZER`; may stay as explicit opt-in account-stability sizing, but do not default, tune clip/ref/window, or call it signal-quality improvement.
- No live/config/checkpoint/execution/oracle/production behavior changed.

## Latest Model Sibling Replacement Audit

On 2026-07-02, `exp_0207` checked sibling models for the current TimesFM, Moirai, and Chronos forecast-gate families.

- Package:
  `research_workspace/diagnostics/exp_0207_model_sibling_replacement_audit.*`
- Local model inventory: `google/timesfm-2.5-200m-pytorch`, `Salesforce/moirai-2.0-R-small`, `Salesforce/moirai-1.1-R-base`, `amazon/chronos-2`, `amazon/chronos-bolt-small`, and `amazon/chronos-bolt-base`.
- Online siblings found but not local: TimesFM 1.0/2.0 and 2.5 transformer/flax variants; Moirai 1.0/1.1 small/base/large plus Moirai-MoE; Chronos original T5 tiny/mini/small/base/large and Bolt tiny/mini.
- Verdict: `NO_LOCAL_SIBLING_REPLACEMENT_READY`.
- TimesFM `exp_0068` remains main. Moirai2 `exp_0093` remains challenger shadow only.
- Chronos-2 `edge=-1.5/risk=5` is the best offline observe branch, but it is not replacement-ready because yearly reset is weaker than TimesFM and worst rolling12 remains negative.
- No new model download, live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

Modified files:

- `research_workspace/diagnostics/exp_0207_model_sibling_replacement_audit.md`
- `research_workspace/diagnostics/exp_0207_model_sibling_replacement_audit.csv`
- `research_workspace/diagnostics/exp_0207_model_sibling_replacement_audit.json`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest TimesFM / Chronos Latest Download Smoke

On 2026-07-02, `exp_0210` downloaded the latest official TimesFM and Chronos-2 snapshots, then ran smoke inference and a cached replay audit.

- Report:
  `research_workspace/diagnostics/exp_0210_timesfm_chronos_latest_download_smoke.md`
- Downloaded TimesFM:
  `research_workspace/diagnostics/timesfm_2_5_200m_pytorch_latest/`
- Downloaded Chronos-2:
  `research_workspace/diagnostics/chronos_2_latest/`
- Upstream revisions: TimesFM `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`; Chronos-2 `29ec3766d36d6f73f0696f85560a422f50e8498c`.
- The downloaded `model.safetensors` files exactly match the existing local model hashes, so prior full-window forecast-cache reports remain valid.
- TimesFM smoke passed in a CPU-forced project environment; unforced CUDA still fails on the local RTX 5060 Laptop `sm_120` with the shared PyTorch build.
- Chronos-2 smoke passed in an isolated CPU dependency environment using `chronos-forecasting`, `numpy<2`, and `scipy>=1.11`.
- Verdict unchanged: TimesFM `exp_0068` remains main; Chronos-2 conservative remains offline OBSERVE only.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest TimesFM Frozen Calibrator

On 2026-07-02, `exp_0211` tested the requested TimesFM frozen-feature plus small calibrator path.

- Package:
  `research_workspace/diagnostics/exp_0211_timesfm_frozen_calibrator/`
- Report:
  `research_workspace/diagnostics/exp_0211_timesfm_frozen_calibrator/artifacts/exp_0211_report.md`
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, next-open execution.
- Model handling: TimesFM forecasts were frozen; no foundation-model fine-tuning was performed.
- Calibrator: linear ridge trained on IS raw-decision outcomes with TimesFM directional features only, then stacked as an additional block gate after existing TimesFM `exp_0068`.
- Baseline TimesFM `exp_0068`: OOS `+581.55%`, full `+57042.77%`, DD `-43.55%`, rolling12 `-19.93%`, `221` trades.
- Matrix: q5/q10/q15/q20 all `REJECT`; OOS delta versus TimesFM was `-105.99%`, `-120.44%`, `-124.34%`, and `-146.85%`; normalized net action value was negative in every row.
- Saved/missed stayed below `1.0`, hurt topK was `1/2/2/3`, rescued worstK was `2/2/3/3`.
- Do not promote this calibrator, and do not infer TimesFM fine-tuning is warranted from this result.
- Validation: `uv run python research_workspace/diagnostics/exp_0211_timesfm_frozen_calibrator/experiment.py`; `uv run ruff check research_workspace/diagnostics/exp_0211_timesfm_frozen_calibrator/experiment.py`.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest TimesFM LoRA Offline Contrast

On 2026-07-02, `exp_0212` tested a LoRA-style offline adapter contrast for TimesFM.

- Package:
  `research_workspace/diagnostics/exp_0212_timesfm_lora_offline_contrast/`
- Report:
  `research_workspace/diagnostics/exp_0212_timesfm_lora_offline_contrast/artifacts/exp_0212_report.md`
- Scope: ETHUSDT 5m `2600d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, next-open execution.
- Adapter: low-rank residual on frozen TimesFM final hidden state, trained on IS decision residuals against h72 realized return. Foundation TimesFM weights were not modified, and no deployable checkpoint was produced.
- Matrix: ranks `2/4/8` and scales `0.25/0.50/1.00`.
- Baseline TimesFM `exp_0068`: OOS `+581.55%`, full `+57042.77%`, DD `-43.55%`, rolling12 `-19.93%`, `221` trades.
- Best observe row: `lora_r2_s0p25`, normalized net `+41.75%`, saved/missed `1.43`, hurt topK `2`, rescued worstK `6`, but OOS delta versus TimesFM `-273.81%` and year W/L/F `3/4/1`.
- Verdict: observe-only/no replacement. Do not promote, checkpoint, live-route, or infer TimesFM fine-tuning approval from this result.
- Validation: `uv run python research_workspace/diagnostics/exp_0212_timesfm_lora_offline_contrast/experiment.py`; `uv run ruff check research_workspace/diagnostics/exp_0212_timesfm_lora_offline_contrast/experiment.py`.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest Priority Model Interface / Latency Probe

On 2026-07-02, `exp_0213` checked the prioritized new model candidates for current availability, inference interface, dependency constraints, output shape, and preloaded CPU latency.

- Report:
  `research_workspace/diagnostics/exp_0213_priority_model_interface_latency_probe.md`
- Artifact CSV/JSON:
  `research_workspace/diagnostics/exp_0213_priority_model_interface_latency_probe.csv`
  `research_workspace/diagnostics/exp_0213_priority_model_interface_latency_probe.json`
- Scope: synthetic linear close probe only; no gate replay and no strategy OOS/DD/rolling12/trade metrics.
- Lag-Llama `time-series-foundation-models/Lag-Llama`: probabilistic samples passed, h72 CPU latency about `4.147s`; closest to the existing quantile-gate shape, but requires isolated GluonTS/Lightning dependencies and a trusted PyTorch 2.6+ `weights_only=False` checkpoint-load workaround.
- Granite TTM `ibm-granite/granite-timeseries-ttm-r2`: point forecast passed via `512-96-r2`, h72 CPU latency about `0.0102s`; strongest latency-first scout, but no native q10/q90 gate was validated.
- Time-MoE `Maple728/TimeMoE-50M`: point generation passed only with `transformers==4.40.2`, h72 CPU latency about `4.004s`; newer Transformers failed on `DynamicCache.seen_tokens`.
- Sundial `thuml/sundial-base-128m`: generated samples passed, h72 CPU latency about `1.045s`; prior `exp_0127` Sundial meta-ablation did not beat the current stack.
- MOMENT/Timer were not run and remain observe-only.
- Next handoff: run a bounded Lag-Llama decision-pack gate probe first, then a Granite TTM point-gate scout. Time-MoE and Sundial need a stronger pre-registered output-quality hypothesis before deeper replacement work.
- Validation: JSON parsed with `uv run python -m json.tool`; CSV parsed with Python `csv.DictReader`; all four model smoke commands completed as recorded in the report.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest Priority Model Bounded Gate Probes

On 2026-07-02, `exp_0214`, `exp_0215`, and `exp_0216` ran bounded replacement probes for Lag-Llama, Granite TTM, and Sundial.

- Lag-Llama package:
  `research_workspace/diagnostics/exp_0214_lag_llama_bounded_gate_probe/`
- Lag-Llama report:
  `research_workspace/diagnostics/exp_0214_lag_llama_bounded_gate_probe/artifacts/exp_0214_report.md`
- Granite package:
  `research_workspace/diagnostics/exp_0215_granite_ttm_point_gate_scout/`
- Granite report:
  `research_workspace/diagnostics/exp_0215_granite_ttm_point_gate_scout/artifacts/exp_0215_report.md`
- Sundial package:
  `research_workspace/diagnostics/exp_0216_sundial_bounded_gate_probe/`
- Sundial report:
  `research_workspace/diagnostics/exp_0216_sundial_bounded_gate_probe/artifacts/exp_0216_report.md`
- Scope: all three use the fixed 80-trade raw v2.2 diagnostic pack from `exp_0205d`: normalized top20 winners, worst20 losers, and middle40 controls. None is a full strategy replay.
- Lag-Llama: `context=32`, default lag history `1092`, `horizon=72`, `20` samples; p95 CPU latency `2.94s`. All four quantile-gate rows rejected. Best row `lagllama_edge_m1p0_risk4p0` blocked `68/80`, hit `17/20` top winners and `18/20` worst losers, saved/missed `0.28`, normalized net `-587.52%`.
- Granite TTM: `512-96-r2`, h72 point directional scout; p95 CPU latency `0.0035s`. All four point-edge rows rejected. Best row `ttm_point_edge_m1p0` blocked `29/80`, hit `4/20` top winners and `5/20` worst losers, saved/missed `0.68`, normalized net `-55.39%`.
- Sundial: `context=1024`, `horizon=72`, `8` samples; p95 CPU latency `0.357s`. All four quantile-gate rows rejected. Best row `sundial_edge_m1p0_risk5p0` blocked `19/80`, hit `4/20` top winners and `2/20` worst losers, saved/missed `0.34`, normalized net `-147.22%`.
- Decision: do not run a full Lag-Llama cache under this quantile-gate framing. Granite remains a latency reference only unless a new point-forecast hypothesis is pre-registered. Sundial remains observe/offline; the bounded 80-pack does not justify replacement or full-cache promotion.
- Validation: `uv run ruff check` on all three experiment scripts; JSON parsed with `uv run python -m json.tool`; all three forecast caches contain `80` final rows.
- No live/demo routing, checkpoint/config, oracle, strategy, scoring, sizing, or execution behavior changed.

## Latest Indicator Confluence Census

On 2026-07-02, `exp_0208` ran the requested first-pass indicator-confluence Stage C census.

- Package:
  `research_workspace/diagnostics/exp_0208_indicator_confluence_census/`
- Proposal:
  `research_workspace/proposals/exp_0208_indicator_confluence_census.md`
- Report:
  `research_workspace/diagnostics/exp_0208_indicator_confluence_census/artifacts/exp_0208_report.md`
- Scope: ETHUSDT 5m `1300d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, completed-bar decision and next-open execution, official Binance Funding/OI as-of artifacts from `exp_0201`.
- Frozen labels: `weak_breakout_quality`, `volatility_nonexpansion`, `same_side_crowded_extreme`, the three pairwise conjunctions, and strict all-three. No score, OR rule, threshold sweep, or post-result retune.
- TimesFM baseline on this sample: OOS `+251.85%`, full `+925.37%`, DD `-47.31%`, rolling12 `-25.73%`, `129` trades.
- Result: all `7` labels are `REJECT`. `weak_breakout_quality` had only `9` events, top20 `2`, worst20 `1`, MAE4h ratio `0.72`, and lag counts `9/9/9`. All volatility/crowding/confluence rows had `0` events.
- No Stage D action replay was run because no label reached KEEP. Do not continue by threshold retuning this label set.
- No live/demo routing, checkpoint/config, oracle, scoring, production strategy, execution, or sizing behavior changed.

Validation:

- `uv run ruff check research_workspace/diagnostics/exp_0208_indicator_confluence_census`
- `uv run pytest research_workspace/diagnostics/exp_0208_indicator_confluence_census/tests -q`
- `uv run python -m research_workspace.diagnostics.exp_0208_indicator_confluence_census.experiment`

Modified files:

- `research_workspace/proposals/exp_0208_indicator_confluence_census.md`
- `research_workspace/diagnostics/exp_0208_indicator_confluence_census/`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest Indicator Component Coverage

On 2026-07-02, `exp_0209` decomposed the sparse `exp_0208` labels into component coverage and descriptive relaxation probes.

- Package:
  `research_workspace/diagnostics/exp_0209_indicator_component_coverage/`
- Proposal:
  `research_workspace/proposals/exp_0209_indicator_component_coverage.md`
- Report:
  `research_workspace/diagnostics/exp_0209_indicator_component_coverage/artifacts/exp_0209_report.md`
- Scope: ETHUSDT 5m `1300d`, ChannelBreakout v2.2 plus TimesFM `exp_0068`, completed-bar decision and next-open execution, official Binance Funding/OI as-of artifacts from `exp_0201`.
- Baseline is unchanged from `exp_0208`: OOS `+251.85%`, full `+925.37%`, DD `-47.31%`, rolling12 `-25.73%`, `129` trades.
- Result: no component or relaxation probe passed tail-separation. Component classes were `EMPTY=4`, `SPARSE_LT_10=6`, `WINNER_RISK=4`, `NO_ADVERSE_SHAPE=1`, `MIXED=1`; relaxation classes were `EMPTY=7`, `SPARSE_LT_10=3`.
- Volatility zeroes are overlap failures: `BBWidth p10` (`N=20`) and range non-expansion (`N=13`) each hit `4/20` top winners and only `3/20` worst losers, while the original and relaxed volatility probes stayed `0`.
- Crowding zeroes are sparsity/overlap failures: same-side Funding p95/p5 had only `N=3` and hit `2` top winners; OI accel p90 had `N=13` but only `2` worst losers and MAE4h ratio `1.10`; Funding+OI remained `0`.
- No Stage D action replay was run. Do not choose relaxed thresholds from this diagnostic without a separately pre-registered event hypothesis.
- No live/demo routing, checkpoint/config, oracle, scoring, production strategy, execution, or sizing behavior changed.

Validation:

- `uv run ruff format --check research_workspace/diagnostics/exp_0209_indicator_component_coverage`
- `uv run ruff check research_workspace/diagnostics/exp_0209_indicator_component_coverage`
- `uv run pytest research_workspace/diagnostics/exp_0209_indicator_component_coverage/tests -q`
- `uv run python -m research_workspace.diagnostics.exp_0209_indicator_component_coverage.experiment`

Modified files:

- `research_workspace/proposals/exp_0209_indicator_component_coverage.md`
- `research_workspace/diagnostics/exp_0209_indicator_component_coverage/`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest Funding/OI As-Of Experiment

On 2026-06-30, `exp_0201` completed a research-only Funding/OI gate experiment.

- Proposal:
  `research_workspace/proposals/exp_0201_funding_oi_asof_gate.md`
- Package:
  `research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/`
- Frozen variants: baseline, funding-extreme, OI-confirmation, and combined
  Funding/OI.
- Baseline: raw `channel_breakout_v2_2_m375_bbm375_1p5`, no TimesFM/Moirai.
- V1 is allow/block only; no dynamic sizing.
- Missing/stale data is fail-open and counted; blocked reversals flatten.

Official source build:

- `4,005` settled Binance Funding observations.
- `384,245` valid Binance 5m OI snapshots from all `1,336` requested archives.
- `101` impossible zero-OI rows were dropped and counted; no imputation.
- `235` 5m slots were missing; no archive day was missing.
- Maximum normal source timestamp jitter was `35s`.
- Joint feature coverage was above `99.96%` for `+5m/+10m/+1h`, and all
  no-future checks passed.

Frozen result:

- Baseline: OOS `+240.09%`, full `+788.13%`, DD `-47.67%`, rolling12m
  `-26.23%`, `139` trades.
- Funding extreme: OOS unchanged, full `+784.70%`, no top20/worst20 capture;
  `OBSERVE` as inert/non-improving.
- OI confirmation: OOS `+196.91%`, DD `-44.02%`, rolling12m `-16.77%`,
  `9/20` top winners and `11/20` worst losers hit; `REJECT`.
- Combined Funding/OI: same OOS/DD/rolling and top-winner damage; `REJECT`.
- `+10m` and `+1h` timing sensitivity did not rescue the OI variants.

Validation:

- `uv run pytest research_workspace/diagnostics/exp_0201_funding_oi_asof_gate/tests -q`
  -> `21 passed`
- `uv run ruff check research_workspace/diagnostics/exp_0201_funding_oi_asof_gate`
  -> passed
- `uv run ruff format --check research_workspace/diagnostics/exp_0201_funding_oi_asof_gate`
  -> passed

No live/demo, strategy, checkpoint, config, scoring, oracle, exchange routing,
or production behavior changed. The result is recorded in
`DECISIONS.md`/`EXPERIMENT_LEDGER.md` and does not authorize threshold tuning
or promotion.

## Latest Price/OI/Funding Regime Attribution

On 2026-06-30, `exp_0202` tested the user's combined price/OI/Funding regime
framing as a research-only diagnostic.

- Proposal:
  `research_workspace/proposals/exp_0202_price_oi_funding_regime.md`
- Package:
  `research_workspace/diagnostics/exp_0202_price_oi_funding_regime/`
- Source data: reused `exp_0201` official Binance as-of Funding/OI artifacts.
- Baseline: raw `channel_breakout_v2_2_m375_bbm375_1p5`, no TimesFM/Moirai,
  no dynamic sizing, completed-bar decision and next-open execution.
- Price direction: completed 4h close-to-close return before entry open.
- Funding "turn negative" proxy: `funding_rate <= 0` or `funding_z <= 0` at
  entry open.

Bucket read:

- `healthy_long_trend`: `34` trades, PnL `+12534.82`, but mixed extremes
  (`6/20` top winners and `8/20` worst losers).
- `short_covering_rally`: `34` trades, PnL `+13394.60`, also mixed
  (`5/20` top and `5/20` worst), so it should not be automatically blocked.
- `short_trend_confirm`: `14` trades, PnL `+12899.74`, with `3/20` top and
  `2/20` worst.

Gate replay:

- `crowded_extreme_block`: no baseline-entry triggers; OOS/DD/trades unchanged
  at `+240.09%`, `-47.67%`, `139`; `OBSERVE` as inert.
- `confirmation_only`: OOS `+162.82%`, DD `-53.24%`, rolling12m `-31.54%`,
  `12/20` top winners and `10/20` worst losers hit; `REJECT`.
- `+10m` and `+1h` publication-lag sensitivity did not rescue
  `confirmation_only`.

Validation:

- `uv run pytest research_workspace/diagnostics/exp_0202_price_oi_funding_regime/tests -q`
  -> `8 passed`
- `uv run ruff check research_workspace/diagnostics/exp_0202_price_oi_funding_regime`
  -> passed
- `uv run ruff format --check research_workspace/diagnostics/exp_0202_price_oi_funding_regime`
  -> passed

Decision: price/OI/Funding combinations are useful attribution labels, but the
strict confirmation framing is not a trading gate. No live/demo, strategy,
checkpoint, config, scoring, oracle, exchange routing, or production behavior
changed.

## Latest Crowded Sensitivity Experiment

On 2026-06-30, `exp_0203` followed `exp_0202` by testing only narrow
same-side crowded-risk brakes. It did not retest `confirmation_only`.

- Proposal:
  `research_workspace/proposals/exp_0203_crowded_sensitivity.md`
- Package:
  `research_workspace/diagnostics/exp_0203_crowded_sensitivity/`
- Source data: reused `exp_0201` official Binance as-of Funding/OI artifacts.
- Baseline: raw `channel_breakout_v2_2_m375_bbm375_1p5`, no TimesFM/Moirai,
  no production sizing. `size_50` and confirmed delay rows use only local
  replay `position_sizes`.
- Grid: `x,y in {1.0, 1.25, 1.5, 1.75, 2.0}` crossed with `block`, `size_50`,
  and `delay_1bar_confirm` actions (`75` non-baseline rows).
- Crowded formulas:
  - Long: `price_4h_ret > 0 and oi_change_z >= x and funding_z >= y`
  - Short: `price_4h_ret < 0 and oi_change_z >= x and funding_z <= -y`

Result:

- All `75` non-baseline rows are `OBSERVE`; no `SHADOW_CANDIDATE`.
- Maximum canonical crowded count was only `9/139` baseline entries (`6.47%`),
  at `x=1/y=1`; every row is below the `N >= 10` evidence floor.
- Best canonical row: `crowded_x1_y1_delay_1bar_confirm`, OOS `+247.05%`,
  DD `-45.29%`, rolling12m `-19.91%`, `0/20` top winners affected and `3/20`
  worst losers affected. It remains OBSERVE because it is sparse and the `+1h`
  publication-lag replay removed the OOS edge.
- `crowded_x1_y1_size_50` also improved canonical OOS/DD (`+244.95%`,
  `-45.21%`) with `0/20` top and `3/20` worst affected, but is also sparse and
  lag-sensitive.

Validation:

- `uv run pytest research_workspace/diagnostics/exp_0203_crowded_sensitivity/tests -q`
  -> `4 passed`
- `uv run ruff check research_workspace/diagnostics/exp_0203_crowded_sensitivity`
  -> passed
- `uv run ruff format --check research_workspace/diagnostics/exp_0203_crowded_sensitivity`
  -> passed

Decision: no promotion/live-routing. The narrow brake shape is cleaner than
confirmation gating, but the current Funding/OI crowded definition is too
sparse and lag-sensitive. If this line continues, it needs a new way to raise
event count without reverting to broad entry filtering.

## Latest Crowding Event Scout

On 2026-07-01, `exp_0204a` ran the high-priority crowding event scout proposed
after `exp_0203`.

- Proposal:
  `research_workspace/proposals/exp_0204a_crowding_event_scout.md`
- Package:
  `research_workspace/diagnostics/exp_0204a_crowding_event_scout/`
- Scope: event-foundation census only; no strategy action, no size/delay/block
  replay, no production behavior.
- Source data: reused `exp_0201` official Binance as-of Funding/OI artifacts.
- Tested `25` high-priority definitions across Funding/OI rolling percentile,
  OI acceleration, leverage intensity, and funding slope/persistence.
- Liquidation labels were not available locally and were not used as gate
  features.
- Rolling percentile features are causal: shifted/as-of live-known values are
  ranked against prior rolling history, not full-sample or OOS values.

Result:

- Verdict counts: `KEEP=0`, `OBSERVE=4`, `REJECT=21`.
- No definition justifies `exp_0204b` full crowding census.
- `leverage_intensity p75/p80/p85`: OBSERVE with `N=16/14/11`, years `4`,
  stable lag counts, and acceptable top/worst separation at p75, but
  `MAE_4h_ratio < 1.0`; this does not support a risk-brake hypothesis.
- `oi_accel p85`: OBSERVE with `N=10`, top20 `1`, worst20 `3`, lag stable,
  but `MAE_4h_ratio=0.92`.
- `funding_slope_persistence`: REJECT despite `N=21`, years `5`, and
  `MAE_4h_ratio=1.69`, because it hit `4/20` top winners and only `4/20`
  worst losers.
- Funding/OI percentile rows: mostly high MAE shape but all sparse (`N < 10`);
  REJECT.

Validation:

- `uv run pytest research_workspace/diagnostics/exp_0204a_crowding_event_scout/tests -q`
  -> `8 passed`
- `uv run ruff check research_workspace/diagnostics/exp_0204a_crowding_event_scout`
  -> passed
- `uv run ruff format --check research_workspace/diagnostics/exp_0204a_crowding_event_scout`
  -> passed

Decision: do not run `exp_0204b` from this evidence. No live/demo, strategy,
checkpoint, config, scoring, oracle, exchange routing, or production behavior
changed.

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

`exp_0142` then ran the requested raw/no-gate v2.2 vol shock loss attribution.

- New script: `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.py`
- Outputs: `_trades.csv`, `_summary.csv`, `_by_class.csv`, `_side_regime_month.csv`, `_equity.csv`, `.json`, and `_report.md`
- Scope: attribution-only on newly simulated raw `channel_breakout_v2_2_m375_bbm375_1p5` trades. No TimesFM, Moirai, LightGBM, new filter, gate, live/demo/checkpoint/config/oracle/production strategy, or training change.
- Lookahead guard: `entry_vol_z`, `entry_rv_72`, `entry_range_pct_72`, and `entry_range_z` are shifted one completed bar before sampling at `entry_step`.
- Baseline reference: raw v2.2 full `+25768.70%`, OOS `+501.71%`, DD `-53.98%`, rolling12 `-27.96%`, `246` trades.
- Main threshold read: `entry_vol_z > 2.5` flagged `15` trades, hit only `2/20` worst20 losses, hit `2/20` top20 winners, and had positive flagged pnl `+192823.96`.
- Class read: clean_loss median/p75 vol-z was `0.116` / `0.929`; normal_winner was `-0.125` / `0.683`, so clean losses are not clearly separated from winners.
- Decision: REJECT. Do not continue this line into BOCPD/HMM, LightGBM, or a gate without a materially different raw-trade separability hypothesis.
- Future blocked-signal semantics are documented in the report only: flat blocked entry stays flat/HOLD; blocked reversal closes to flat; blocked reversal must never mean keep current position.

Validation run:

- `uv run python -m py_compile research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.py`
- `uv run python research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.py`
- `uv run pytest tests/test_exp0142_raw_vol_shock_loss_attribution.py`
- `uv run ruff check research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.py tests/test_exp0142_raw_vol_shock_loss_attribution.py`

`exp_0143` then tested the requested V1 vol-target low-vol slow-bear sizing overlay.

- New script: `research_workspace/diagnostics/exp_0143_v1_voltarget_low_vol_slow_bear_sizing_diagnostic.py`
- Outputs: `_matrix.csv`, `_periods.csv`, `_top20_worst20.csv`, `_attribution.csv`, `_trades.csv`, `_events.csv`, `_series.csv`, `.json`, and `_report.md`
- Scope: sizing-only research on top of V1 vol targeting. No model training, signal change, entry/reversal change, gate, live/demo/checkpoint/config/oracle/production strategy, or allocation change.
- Implementation guard: baseline and all three variants are independent path-dependent replays, not post-trade pnl rescaling. Bar `t` sizing uses only bar `t-1` confirmed variant equity DD, rolling30 strategy return, vol percentile, and V1 target size. `combo` is its own replay and takes the lower of the two overlay target sizes inside that replay.
- Variants: `lv_eq_mul_070`; `eqdd_step_soft`; `combo`.
- Baseline reference: V1 vol-target OOS `+639.10%`, DD `-44.14%`, rolling12 `-0.02%`, fee10 OOS `+556.67%`, average entry size `92.15%`.
- Main result: all variants are REJECT. `lv_eq_mul_070` OOS `+470.33%`, DD `-37.30%`, rolling12 `-12.39%`, top20 cost `48.05%`; `eqdd_step_soft` OOS `+354.84%`, DD `-36.83%`, rolling12 `-14.13%`, top20 cost `83.92%`; `combo` OOS `+335.73%`, DD `-36.83%`, rolling12 `-13.67%`, top20 cost `84.22%`.
- Period read: 2022-02-26 to 2022-05-04 low-vol slow-bear average active size fell from V1 `96.08%` to `72.54%` for `lv_eq_mul_070` and `55.59%` for `eqdd_step_soft`/`combo`; this confirms the overlay fires in the intended window, but the broader OOS/rolling/top20 tradeoff is unacceptable.
- Decision: REJECT. Do not tune these thresholds, turn the conditions into a gate, add BOCPD/HMM, change reversal semantics, or infer any live/default sizing change from this result.

Validation run:

- `uv run ruff check research_workspace/diagnostics/exp_0143_v1_voltarget_low_vol_slow_bear_sizing_diagnostic.py tests/test_exp0143_low_vol_slow_bear_sizing.py`
- `uv run pytest tests/test_exp0143_low_vol_slow_bear_sizing.py`
- `uv run python research_workspace/diagnostics/exp_0143_v1_voltarget_low_vol_slow_bear_sizing_diagnostic.py`

`exp_0144` then ran the requested pre-registered Stage0 raw feature separation sanity check.

- New script: `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_sanity.py`
- Outputs: `_sanity.csv`, `_v1_validation.csv`, and `_report.md`
- Scope: pre-registered sanity check only. No model training, no gate, no signal/reversal change, no live/demo/checkpoint/config/oracle/production strategy, no allocation change.
- Discovery sample: raw `channel_breakout_v2_2_m375_bbm375_1p5` fixed-size/no-gate trades (`246` trades).
- Validation sample: V1 vol-target trades/pnl only (`236` trades, `vol_ref=0.794812`). V1 does not define thresholds or direction.
- Features: only `donchian_width_pct`, `bars_since_last_reversal`, and `ADX(14)`. No vol-z/range-z reuse, no 35-feature scan, no bivariate cross, no post-run threshold editing.
- Feature guards: Donchian width and ADX are shifted one completed bar before entry. `bars_since_last_reversal` updates only on direct long<->short reversals; fresh entries and same-side reentries do not update the reversal step. `mae_72`, `bars_held`, `life_mae`, `life_mfe`, and `main_class` remain label/attribution-only and are not features.
- Binning: raw IS quintile cutpoints only; raw OOS and V1 validation apply those fixed cutpoints.
- Result: REJECT. Every lowest/highest quintile had positive raw flagged pnl. Best worst20 capture was only `5/20`, below the `7/20` gate. Top20 hits were often high: `donchian_width_pct` lowest hit `7/20`, ADX lowest `6/20`, ADX highest `5/20`.
- Closest partial row: `donchian_width_pct` highest had bad-label-rate `23.81%` (`2.79x` full raw bad-label rate) and top20 `2/20`, but raw pnl was positive `+143511.93`, worst20 capture only `4/20`, and OOS/V1 direction did not validate.
- Decision: do not proceed to `exp_0145_logistic_lightgbm_meta_label_shadow`, LSTM, TCN, or a gate from these three features.

Validation run:

- `uv run ruff check research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_sanity.py tests/test_exp0144_stage0_raw_feature_separation.py`
- `uv run pytest tests/test_exp0144_stage0_raw_feature_separation.py`
- `uv run python research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_sanity.py`

`exp_0200_model_direction_standalone_baseline` then implemented and ran the locked research-only standalone model-direction design.

- New script: `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline.py`
- New test: `tests/test_exp0200_model_direction_standalone.py`
- Outputs: report, json, matrix, validation, confidence buckets, trades, fills, and side/year CSVs under `research_workspace/diagnostics/`.
- Scope: research-only standalone model-direction line, not a ChannelBreakout patch, not a gate promotion, and not live/demo/config/checkpoint/oracle/production work.
- Data: `data/crypto/ETHUSDT_5m_2600d.parquet`, `2019-09-23 08:35:00` to `2026-06-12 02:55:00`.
- Models: Logistic Regression and LightGBM only; LightGBM was run with `uv run --with lightgbm` without modifying project dependencies.
- Features: 14 completed-bar shifted price/volume features. Funding/OI were omitted because no aligned local funding/OI data files were found.
- Labels and thresholds followed the proposal: horizons `{72, 288}`, cost buffers `{0.30%, 0.20%}`, confidence grid `{0.60, 0.65, 0.70}`, purged walk-forward validation with horizon embargo, validation-only threshold selection, final 30% OOS run once.
- Accounting definitions: `annual_trades` means `annual_round_trip_equivalent`; direct long/short flips count as close plus open and charge two one-way fills; `NO_TRADE ratio` is model bar-level flat output ratio, not realized trade count.
- Result: overall REJECT. Logistic rows failed fee10/slippage or stability. The best row, `lightgbm_h288_cb30bp`, had OOS `+5.70%`, fee10/slippage stress `+3.68%`, DD `-7.07%`, rolling12 `+0.08%`, annual round-trip equivalent `5.96`, and `NO_TRADE` `99.94%`, but failed because the highest-confidence bucket was negative/non-monotonic and profit depended on a single positive year.
- Decision: do not retune threshold/cost buffer/features after this OOS, do not continue to LSTM/TCN/Transformer, and do not promote any model-direction route from this result.

Validation run:

- `uv run pytest tests/test_exp0200_model_direction_standalone.py`
- `uv run ruff check research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline.py tests/test_exp0200_model_direction_standalone.py`
- `uv run --with lightgbm python research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline.py --mode all`

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

Latest useful evidence includes gate attribution and recent rejected diagnostics:

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
- `exp_0142` rejects raw v2.2 pre-entry vol shock/range shock attribution as a gate seed. `entry_vol_z > 2.5` misses most worst20 losses, flags top winners, and has positive total pnl; do not expand this into BOCPD/HMM.
- `exp_0143` rejects the V1 low-vol slow-bear sizing overlay path. It does reduce DD and lowers size in the intended 2022 low-vol slow-bear window, but rolling12 turns strongly negative, OOS retention falls below the pre-registered gate, and top20 cost is too high.
- `exp_0144` rejects the three-feature raw separation Stage0. `donchian_width_pct`, `bars_since_last_reversal`, and `ADX(14)` do not cleanly separate worst20 raw losses from top20 winners, and no `exp_0145` meta-label shadow is authorized from this feature set.
- `exp_0200` rejects the standalone low-turnover model-direction baseline as framed. The one positive LightGBM OOS row fails confidence monotonicity/highest-bucket expectancy and year stability, so it does not authorize retuning, deep models, direct Moirai/TimesFM trading, or live/demo routing.

## Modified Files In Latest Exp0200 Model Direction Diagnostic

- `research_workspace/proposals/exp_0200_model_direction_standalone_baseline.md`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline.py`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_report.md`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline.json`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_matrix.csv`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_validation.csv`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_confidence_buckets.csv`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_trades.csv`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_fills.csv`
- `research_workspace/diagnostics/exp_0200_model_direction_standalone_baseline_side_year.csv`
- `tests/test_exp0200_model_direction_standalone.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Stage0 Raw Feature Separation

- `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_sanity.py`
- `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_sanity.csv`
- `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_v1_validation.csv`
- `research_workspace/diagnostics/exp_0144_stage0_raw_feature_separation_report.md`
- `tests/test_exp0144_stage0_raw_feature_separation.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Modified Files In Latest Low-Vol Slow-Bear Sizing Diagnostic

- `research_workspace/diagnostics/exp_0143_v1_voltarget_low_vol_slow_bear_sizing_diagnostic.py`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_report.md`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear.json`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_matrix.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_periods.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_top20_worst20.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_attribution.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_trades.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_events.csv`
- `research_workspace/diagnostics/exp_0143_low_vol_slow_bear_series.csv`
- `tests/test_exp0143_low_vol_slow_bear_sizing.py`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

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

## Modified Files In Latest Raw Vol-Shock Attribution

- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.py`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_report.md`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_loss_attribution.json`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_trades.csv`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_summary.csv`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_by_class.csv`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_side_regime_month.csv`
- `research_workspace/diagnostics/exp_0142_v22_raw_vol_shock_equity.csv`
- `tests/test_exp0142_raw_vol_shock_loss_attribution.py`
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

## Latest Kronos Latency Probe

- `exp_0205a` is complete with verdict
  `REJECT_FOR_LIVE_LATENCY / OBSERVE_OFFLINE`.
- Scope: Kronos-small, `context=512`, horizons `48/72/96`, sample counts
  `1/5/16`, `T=1.0`, latest `20` entry/reversal events plus `20` controls.
- The shared project environment remains PyTorch `2.6.0+cu124`. A separate,
  ignored PyTorch `2.7.1+cu128` environment now supports the RTX 5060 Laptop
  GPU's `sm_120` architecture and passes a CUDA tensor smoke.
- Five-event h48 GPU p95 was `0.739s/1.941s/5.505s` for sample counts
  `1/5/16`. One h96/sample16 stress event took `11.122s`. Peak reserved VRAM
  was only `476 MB`.
- Inference failures were `0`, but h48 output-quality failures were
  `5/5`, `5/5`, and `2/5` for sample counts `1/5/16`, mainly negative
  volume/amount; high/low repair was also required.
- CPU worst-case preflight (`h96/sample16`) exceeded the `45s` hard timeout.
- Kronos-small plus TimesFM loaded at approximately `1451 MB` process RSS.
- Do not run `exp_0205c`. Continue to `exp_0205b` only with explicit
  offline-only acceptance and strict invalid-output reject/repair handling.
- No trade, signal, gate, live, config, checkpoint, oracle, sizing, or
  execution behavior changed.

Modified files:

- `.gitignore`
- `research_workspace/proposals/exp_0205a_kronos_latency_probe.md`
- `research_workspace/diagnostics/exp_0205a_kronos_latency_probe/README.md`
- `research_workspace/diagnostics/exp_0205a_kronos_latency_probe/experiment.py`
- `research_workspace/diagnostics/exp_0205a_kronos_latency_probe/setup_cu128_env.ps1`
- `research_workspace/diagnostics/exp_0205a_kronos_latency_probe/tests/test_experiment.py`
- `research_workspace/diagnostics/exp_0205a_kronos_latency_probe/artifacts/`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest Kronos Incremental Census

- `exp_0205b` completed with verdict `REJECT`.
- Scope: `246` closed raw v2.2 base trades, horizons `48/72/96`, sample count
  `16`, context `512`, `T=1.0`, CUDA FP16 autocast.
- The run completed `738/738` event-horizon forecasts with zero inference
  failures; the post-reject JSONL feature cache has since been deleted, while
  final CSV/JSON artifacts remain.
- Frozen rule: a horizon conflicts when terminal directional return is
  negative and predicted MAE exceeds MFE; Kronos blocks when at least two
  horizons conflict.
- Kronos blocked `198/246` (`80.5%`), versus TimesFM `25` and current Moirai2
  `10`. Jaccard was `0.121` and `0.045`.
- The `172` Kronos-unique-vs-union blocks contained `78` winners and `94`
  losers. They caught `11/20` worst losers but also blocked `16/20` top
  winners; mean/median/sum PnL_R was `+22.305/-2.812/+3836.427`.
- This is broad destructive filtering, not selective incremental value. Do
  not run `exp_0205c` and do not tune the observed rule after seeing results.
- The ignored `.kronos_vendor_exp0205a` model/CUDA venv cache was removed after
  rejection, freeing approximately `5.70 GB`. A later cleanup removed the
  remaining Kronos HuggingFace cache, JSONL feature cache, and exp0205
  `__pycache__` directories. Reports, census CSV, summary CSV/JSON, and
  experiment metadata remain; the exp0205a setup script can rebuild the runtime
  if a new pre-registered branch is approved.
- No trade, signal, live config, checkpoint, oracle, sizing, or execution
  behavior changed.

Modified files:

- `research_workspace/proposals/exp_0205b_kronos_incremental_event_census.md`
- `research_workspace/diagnostics/exp_0205b_kronos_incremental_census/`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/HANDOFF.md`

## Latest Kronos Base Capacity Probe

- `exp_0205d` completed with verdict `REJECT`.
- Frozen pack: `80` trades = normalized top20 + worst20 + `40`
  interquartile/year/side controls.
- Base generated `240/240` event-horizon features with zero inference failures.
- Small blocked `61/80` (`76.2%`); base blocked `63/80` (`78.8%`).
- Small/base blocked-set Jaccard was `0.908`.
- Base blocked `17/20` top winners and `13/20` worst losers.
- Base had four blocks unique vs small: two winners/two losers, two top20/one
  worst20, mean PnL_R `+417.657`.
- Output coherence did not improve: structure/flow repair rows were `54/34`
  for base versus `53/31` for small.
- Base p50 latency h48/h72/h96 was `12.14s/18.20s/24.26s`.
- Capacity does not explain or repair the broad path-conflict gate. Do not run
  a full-246 base census or tune this rule.
- The recreated CUDA venv and small/base model cache were removed after the
  verdict, freeing approximately `6.0 GB`. A later cleanup removed the base
  JSONL feature cache. Reports, comparison CSV, summary CSV, pack CSV, and
  experiment metadata remain.
- No trade, signal, live config, checkpoint, oracle, sizing, or execution
  behavior changed.

Modified files:

- `research_workspace/proposals/exp_0205d_kronos_base_capacity_probe.md`
- `research_workspace/diagnostics/exp_0205d_kronos_base_capacity_probe/`
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
