# exp_0141 efficiency ratio diagnostic

- Stage 0 only
- no trading action, no sizing, no skip rule
- no live/checkpoint/config/oracle/production strategy change
- low ER is pre-registered as either low quality, mixed high variance, or no signal
- overall verdict: `MIXED_HIGH_VARIANCE_BUCKET`

## Baseline

| full | OOS | DD | rolling12 | trades |
|---:|---:|---:|---:|---:|
| 42285.03% | 629.72% | -51.12% | -23.48% | 236 |

## Stage 0 Verdict

| ER | verdict | Q1 top20 | Q1 top20 share | Q1 worst20 | Q1 worst share | Q1/Q2 median | Q3-Q5 median | rolling12 low-ER loss share |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ER20 | MIXED_HIGH_VARIANCE_BUCKET | 3 | 13.04% | 3 | 10.16% | -0.24% | -0.80% | 43.07% |
| ER50 | MIXED_HIGH_VARIANCE_BUCKET | 6 | 19.75% | 6 | 41.94% | -0.46% | -0.56% | 32.53% |
| ER100 | MIXED_HIGH_VARIANCE_BUCKET | 7 | 22.89% | 5 | 14.95% | -0.22% | 0.01% | 71.13% |

## ER Bucket Summary

| ER | bucket | trades | OOS | avg ret | median ret | win | PF | top20 | top20 share | worst20 | worst share | pnl std |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ER20 | Q1 | 47 | 16 | 5.95% | -0.58% | 42.55% | 2.79 | 3 | 13.04% | 3 | 10.16% | 62871.25 |
| ER20 | Q2 | 47 | 19 | 2.14% | 0.10% | 51.06% | 1.99 | 2 | 16.48% | 5 | 20.57% | 105976.11 |
| ER20 | Q3 | 47 | 23 | 3.83% | -0.83% | 42.55% | 1.57 | 3 | 12.75% | 5 | 22.30% | 69919.70 |
| ER20 | Q4 | 47 | 24 | 3.54% | 0.89% | 51.06% | 4.35 | 7 | 32.15% | 3 | 17.26% | 115929.94 |
| ER20 | Q5 | 48 | 21 | 2.82% | -0.80% | 43.75% | 1.94 | 5 | 25.59% | 4 | 29.72% | 120995.47 |
| ER20 | missing | 0 | 0 | 0.00% | 0.00% | 0.00% | 0.00 | 0 | 0.00% | 0 | 0.00% | 0.00 |
| ER50 | Q1 | 47 | 26 | 6.75% | 0.19% | 53.19% | 1.48 | 6 | 19.75% | 6 | 41.94% | 88650.15 |
| ER50 | Q2 | 47 | 17 | 0.30% | -1.12% | 38.30% | 2.55 | 4 | 24.73% | 5 | 21.29% | 114746.44 |
| ER50 | Q3 | 47 | 20 | 4.13% | 0.74% | 55.32% | 6.76 | 5 | 30.89% | 3 | 10.23% | 115761.82 |
| ER50 | Q4 | 47 | 22 | 5.43% | -0.90% | 44.68% | 1.41 | 2 | 5.40% | 5 | 19.24% | 48964.54 |
| ER50 | Q5 | 48 | 18 | 1.69% | -0.56% | 39.58% | 2.39 | 3 | 19.23% | 1 | 7.30% | 105683.54 |
| ER50 | missing | 0 | 0 | 0.00% | 0.00% | 0.00% | 0.00 | 0 | 0.00% | 0 | 0.00% | 0.00 |
| ER100 | Q1 | 47 | 20 | 8.46% | -0.63% | 44.68% | 3.10 | 7 | 22.89% | 5 | 14.95% | 69356.58 |
| ER100 | Q2 | 47 | 21 | 2.45% | 0.19% | 51.06% | 1.12 | 3 | 9.47% | 4 | 34.82% | 75163.64 |
| ER100 | Q3 | 47 | 19 | 2.12% | -0.90% | 34.04% | 0.80 | 2 | 6.23% | 6 | 31.28% | 53345.49 |
| ER100 | Q4 | 47 | 24 | 1.73% | 0.10% | 51.06% | 3.24 | 3 | 20.34% | 4 | 14.84% | 109283.42 |
| ER100 | Q5 | 48 | 19 | 3.50% | 0.01% | 50.00% | 7.55 | 5 | 41.07% | 1 | 4.11% | 147221.34 |
| ER100 | missing | 0 | 0 | 0.00% | 0.00% | 0.00% | 0.00 | 0 | 0.00% | 0 | 0.00% | 0.00 |

## Q1 Cross Stats

| ER | Q1 top20 | Q1 top20 pnl | Q1 top20 share | Q1 worst20 | Q1 worst loss | Q1 worst share |
|---|---:|---:|---:|---:|---:|---:|
| ER20 | 3 | 643952.89 | 13.04% | 3 | -186887.39 | 10.16% |
| ER50 | 6 | 975553.83 | 19.75% | 6 | -771829.97 | 41.94% |
| ER100 | 7 | 1130473.54 | 22.89% | 5 | -275147.76 | 14.95% |

## Read

- `CLEAN_LOW_QUALITY_BUCKET` is the only outcome that would permit a later lightweight ER sizing check.
- `MIXED_HIGH_VARIANCE_BUCKET` means low ER carries both large winners and large losers; single-variable ER sizing is frozen.
- `NO_SIGNAL` means ER buckets did not separate trade quality enough to justify Stage 1.
- ER bucket thresholds here are full-sample diagnostic thresholds. Any later live-like sizing test must recompute train-only thresholds.

## Evidence

- summary: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic.csv`
- bucket summary: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_bucket_summary.csv`
- OOS summary: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_oos_summary.csv`
- top/worst summary: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_top_worst_summary.csv`
- Q1 cross stats: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_q1_cross_stats.csv`
- rolling12 contribution: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_rolling12_contribution.csv`
- Q1 extreme distribution: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_q1_extreme_distribution.csv`
- Q1 separability: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_q1_separability.csv`
- stage0 verdict: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_stage0_verdict.csv`
- trade features: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic_trades.csv`
- json: `research_workspace\diagnostics\exp_0141_efficiency_ratio_diagnostic.json`
