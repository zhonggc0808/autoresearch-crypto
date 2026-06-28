# exp_0140 v2.2 vol target attribution audit

- research-only
- attribution audit only; no new sizing variant and no parameter search
- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- audited variant: `V1_vol_target_20d_clip_0p4_1p0` from exp0139
- no live/checkpoint/config/oracle/production strategy change
- decision: `OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE`

## Vol Ref Cleanliness Check

- vol_ref_mode: `train_only_entry_median`
- full_sample_vol_ref: `0.729250`
- train_only_vol_ref: `0.794812`
- ref_delta_pct: `8.99%`
- clean_rerun_required: `False`
- clean_v1_verdict: `clean_original_v1_used`

## Variant Metrics

| variant | OOS | DD | DD improve | rolling12 | avg size | top20 damage | worst20 improve | fee10 OOS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_100 | 629.72% | -51.12% | 0.00% | -23.48% | 100.00% | 0.00% | 0.00% | 544.59% |
| V1_vol_target_20d_clip_0p4_1p0 | 638.77% | -44.12% | 13.69% | 0.01% | 92.13% | 9.98% | 12.84% | 556.39% |

## Rolling12 Attribution

| window | start | end | baseline return | V1 return | V1 avg active size | V1 pct active <75 |
|---|---|---|---:|---:|---:|---:|
| baseline_worst_rolling12 | 2021-05-08 | 2022-05-08 | -23.48% | 1.21% | 85.48% | 9.51% |
| v1_worst_rolling12 | 2022-07-18 | 2023-07-18 | -13.79% | 0.01% | 95.70% | 8.60% |

## MaxDD Attribution

| window | start | end | baseline DD | V1 DD | vol pct median | V1 avg active size | read |
|---|---|---|---:|---:|---:|---:|---|
| baseline_maxdd_window | 2021-05-11 | 2021-07-22 | -47.09% | -30.65% | 92.35% | 64.06% |  |
| v1_maxdd_window | 2022-02-26 | 2022-05-04 | -43.29% | -40.77% | 36.35% | 96.06% | max_dd_low_vol_environment |

## Extreme Trades

| group | count | avg V1 size | Q5 count | baseline pnl | V1 pnl | saved | lost | ratio | reduced-size count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| top20 | 20 | 98.32% | 0 | 4939240.19 | 4446155.98 | 0.00 | 493084.21 | 9.98% | 3 |
| worst20 | 20 | 95.38% | 3 | -1840338.35 | -1596708.08 | 243630.27 | 0.00 | 13.24% | 6 |

## Decision

- verdict: `OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE`
- reason: current only empirically effective position-stability scheme; not a sizing shadow because DD gate fails and residual max DD is low-vol/high-size
- rolling12_near_miss_positive: `True`
- one_shot_refinement_allowed: `False`
- live_action: `no_change`

## Evidence

- summary: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit.csv`
- windows: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit_windows.csv`
- top20 mapping: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit_top20.csv`
- worst20 mapping: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit_worst20.csv`
- extreme summary: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit_extreme_summary.csv`
- json: `research_workspace\diagnostics\exp_0140_v22_vol_target_attribution_audit.json`
