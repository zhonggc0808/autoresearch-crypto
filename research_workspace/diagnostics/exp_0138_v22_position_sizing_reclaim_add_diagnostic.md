# exp_0138 v2.2 position sizing reclaim-add diagnostic

- research-only
- position sizing diagnostic only
- no live/checkpoint/config/oracle/production strategy change
- no fixed-R profit taking
- DD throttle controls new entries/adds only; it does not reduce existing positions
- verdict: `REJECT`

## Variants

| variant | initial | adds | throttle | full | OOS | DD | DD improve | roll12 | adds | avg exp | max exp | top20 damage | worst20 improve | fee10 OOS | fee10 DD | W/L |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_100 | 100.00% | [] | False | 42285.03% | 629.72% | -51.12% | 0.00% | -23.48% | 0 | 74.81% | 100.00% | 0.00% | 0.00% | 544.59% | -51.44% | 8/0 |
| V0_constant_50 | 50.00% | [] | False | 3343.24% | 197.52% | -32.89% | 35.66% | -10.40% | 0 | 37.41% | 50.00% | 92.98% | 93.94% | 179.49% | -33.11% | 8/0 |
| V1_50_reclaim_add2 | 50.00% | [0.25, 0.25] | False | 31210.07% | 482.17% | -47.73% | 6.63% | -17.40% | 437 | 72.59% | 100.00% | 18.68% | 14.65% | 412.11% | -49.13% | 8/0 |
| V2_50_reclaim_add2_dd_throttle | 50.00% | [0.25, 0.25] | True | 4807.54% | 281.05% | -38.04% | 25.59% | -23.38% | 437 | 50.11% | 100.00% | 87.13% | 85.44% | 245.27% | -39.16% | 8/0 |
| V3_constant_75 | 75.00% | [] | False | 13247.41% | 375.91% | -42.92% | 16.04% | -16.48% | 0 | 56.11% | 75.00% | 70.48% | 72.46% | 333.49% | -43.20% | 8/0 |
| V4_75_reclaim_add1 | 75.00% | [0.25] | False | 38147.73% | 571.26% | -49.27% | 3.62% | -18.44% | 224 | 74.02% | 100.00% | 6.25% | 4.70% | 488.53% | -50.61% | 8/0 |
| V5_75_reclaim_add1_dd_throttle | 75.00% | [0.25] | True | 7531.08% | 384.11% | -39.08% | 23.55% | -22.55% | 224 | 52.55% | 100.00% | 81.38% | 80.83% | 322.68% | -39.52% | 8/0 |

## Attribution

| comparison | dOOS | dDD | dRoll12 | dReturn/DD | dTop20 | dWorst20 | dAdds |
|---|---:|---:|---:|---:|---:|---:|---:|
| V0_minus_baseline | -432.20% | 18.23% | 13.08% | -725.56 | -4592628.28 | 1728812.29 | 0 |
| V1_minus_V0 | 284.65% | -14.84% | -7.01% | 552.28 | 3670193.25 | -1459272.09 | 437 |
| V2_minus_V1 | -201.12% | 9.69% | -5.98% | -527.55 | -3380953.87 | 1302876.56 | 0 |
| V3_minus_baseline | -253.80% | 8.20% | 7.00% | -518.55 | -3480964.67 | 1333558.05 | 0 |
| V4_minus_V3 | 195.35% | -6.35% | -1.96% | 465.62 | 3172412.67 | -1247000.90 | 224 |
| V5_minus_V4 | -187.15% | 10.19% | -4.10% | -581.57 | -3711168.46 | 1400903.83 | 0 |

## Stage Gates

| variant | DD20 | OOS65 | roll12 | fee10 | nonlinear | observe | shadow |
|---|---|---|---|---|---|---|---|
| V0_constant_50 | True | False | True | True | False | False | False |
| V1_50_reclaim_add2 | False | True | True | True | False | False | False |
| V2_50_reclaim_add2_dd_throttle | True | False | True | True | False | False | False |
| V3_constant_75 | False | False | True | True | False | False | False |
| V4_75_reclaim_add1 | False | True | True | True | False | False | False |
| V5_75_reclaim_add1_dd_throttle | True | False | True | True | False | False | False |

## Read

- Interpret V0/V3 as linear constant-sizing controls before giving credit to reclaim-add variants.
- If V1/V2 do not beat V0, or V4/V5 do not beat V3, the conclusion is simple sizing rather than complex add logic.
- No result here authorizes live position sizing, exchange routing, checkpoint promotion, or production strategy changes.

## Evidence

- variants: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic.csv`
- attribution: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic_attribution.csv`
- stage gates: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic_stage_gates.csv`
- events: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic_events.csv`
- trades: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic_trades.csv`
- json: `research_workspace\diagnostics\exp_0138_v22_position_sizing_reclaim_add_diagnostic.json`
