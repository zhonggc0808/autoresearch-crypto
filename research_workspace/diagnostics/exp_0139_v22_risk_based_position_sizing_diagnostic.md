# exp_0139 v2.2 risk-based position sizing diagnostic

- research-only
- risk-based sizing diagnostic only
- no live/checkpoint/config/oracle/production strategy change
- first pass uses entry-fixed sizing only; no intratrade dynamic rebalance
- verdict: `REJECT`

## Stage 0 ATR Entry Buckets

| bucket | trades | OOS trades | avg pnl | median pnl | avg ret | median ret | win | PF | top20 | worst20 | avg MAE | avg MFE | top20 pnl share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 47 | 20 | 40970.84 | -456.48 | 6.79% | -0.48% | 42.55% | 3.93 | 6 | 3 | -4.74% | 16.20% | 43.62% |
| Q2 | 47 | 28 | 4785.51 | -52.45 | 1.49% | -0.11% | 46.81% | 1.23 | 5 | 7 | -3.29% | 8.83% | 17.25% |
| Q3 | 47 | 26 | 14971.87 | -1193.43 | 3.79% | -1.46% | 40.43% | 1.98 | 4 | 5 | -3.09% | 12.34% | 21.76% |
| Q4 | 47 | 18 | 19947.31 | 777.15 | 1.86% | 0.31% | 55.32% | 4.21 | 4 | 2 | -3.38% | 10.41% | 15.19% |
| Q5 | 48 | 11 | 9535.35 | -2448.14 | 4.33% | -0.64% | 45.83% | 2.06 | 1 | 3 | -6.44% | 19.20% | 2.18% |
| missing | 0 | 0 | 0.00 | 0.00 | 0.00% | 0.00% | 0.00% | 0.00 | 0 | 0 | 0.00% | 0.00% | 0.00% |

## Stage 0 Realized Vol Buckets

| bucket | trades | OOS trades | avg ret | median ret | top20 | worst20 | top20 pnl share |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 46 | 21 | 4.45% | 0.39% | 5 | 3 | 27.47% |
| Q2 | 46 | 22 | 5.56% | -0.86% | 3 | 4 | 21.97% |
| Q3 | 47 | 30 | 3.24% | 0.05% | 9 | 7 | 42.61% |
| Q4 | 46 | 18 | 3.49% | -0.63% | 3 | 3 | 7.95% |
| Q5 | 47 | 12 | 1.82% | -1.84% | 0 | 3 | 0.00% |
| missing | 4 | 0 | 0.73% | -0.16% | 0 | 0 | 0.00% |

## Stage 0 Flags

- ATR stage0 pass: `False`
- high ATR quality worse: `False`
- high ATR worst concentrated: `False`
- high ATR top20 pnl share: `2.18%`
- vol targeting top-winner risk: `normal` (0.00%)

## Variants

| variant | mode | skipped | avg size | min size | OOS | DD | DD improve | roll12 | top20 damage | worst20 improve | return/DD | fee10 OOS | fee10 DD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_100 | constant | False | 100.00% | 100.00% | 629.72% | -51.12% | 0.00% | -23.48% | 0.00% | 0.00% | 827.21 | 544.59% | -51.44% |
| V0_constant_50 | constant | False | 50.00% | 50.00% | 197.52% | -32.89% | 35.66% | -10.40% | 92.98% | 93.94% | 101.65 | 179.49% | -33.11% |
| V3_constant_75 | constant | False | 75.00% | 75.00% | 375.91% | -42.92% | 16.04% | -16.48% | 70.48% | 72.46% | 308.66 | 333.49% | -43.20% |
| V1_vol_target_20d_clip_0p4_1p0 | vol_target_20d | False | 92.13% | 40.00% | 638.77% | -44.12% | 13.69% | 0.01% | 9.98% | 12.84% | 864.38 | 556.39% | -45.42% |
| V2_atr_risk_parity_clip_0p4_1p0 | atr_risk_parity | True |  |  |  |  |  |  |  |  | nan |  |  |

## Stage Gates

| variant | skipped | DD ok | OOS >= 400 | top20 <=35 | roll12 | fee10 | return/DD > V3 | observe | shadow |
|---|---|---|---|---|---|---|---|---|---|
| V0_constant_50 | False | True | False | False | True | True | False | False | False |
| V3_constant_75 | False | False | False | False | True | True | False | False | False |
| V1_vol_target_20d_clip_0p4_1p0 | False | False | True | True | True | True | True | False | False |
| V2_atr_risk_parity_clip_0p4_1p0 | True | False | False | False | False | False | False | False | False |

## Read

- V0 and V3 are recomputed constant-sizing benchmarks in the same script and should be treated as linear controls.
- V2 ATR risk parity is only run when Stage 0 shows high-ATR entry quality is genuinely worse without high top-winner overlap.
- Top20 and worst20 labels are attribution-only and are never used by sizing logic.
- No result here authorizes live position sizing, exchange routing, checkpoint promotion, or production strategy changes.

## Evidence

- variants: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic.csv`
- ATR buckets: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_atr_buckets.csv`
- realized vol buckets: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_realized_vol_buckets.csv`
- trade features: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_stage0_trades.csv`
- stage gates: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_stage_gates.csv`
- events: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_events.csv`
- trades: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic_trades.csv`
- json: `research_workspace\diagnostics\exp_0139_v22_risk_based_position_sizing_diagnostic.json`
