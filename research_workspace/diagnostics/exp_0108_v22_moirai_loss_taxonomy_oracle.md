# exp_0108 v2.2 + Moirai loss taxonomy and oracle

- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- execution: `next_bar_open`
- attribution may use future path; strategy rules must not.
- max DD period: `2021-05-12 06:00:00` -> `2021-07-25 14:45:00`

## Decision

- status: `attribution_mixed_oracle_reject_dd_fix`
- live action: `no_change`
- checkpoint action: `no_change`
- read: `profit_giveback_to_loss` is only 11.63% of total losses and its oracle barely improves DD. `immediate_adverse` explains 71.09% of closed losses inside the max-DD period, but its oracle still improves full DD by only 1.03%.
- next: do not jump to MFE/trend-failure or early-abort overlays yet. The dominant bucket is `clean_loss` (81.81% of total losses, 92.11% of top10 losses), especially reversal losses; inspect reversal clean-loss structure next.

## Table 1: loss source classification

| class | count | sum pnl | avg pnl | max loss | top10 loss share | total loss share | max DD loss share | avg bars after MFE | avg giveback |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| profit_giveback_to_loss | 17 | -357328.69 | -21019.33 | -107642.76 | 7.89% | 11.63% | 7.87% | 1287.9 | 1.41 |
| immediate_adverse | 13 | -201843.06 | -15526.39 | -38382.32 | 0.00% | 6.57% | 71.09% | 607.8 | 7.70 |
| clean_loss | 97 | -2514364.99 | -25921.29 | -333685.00 | 92.11% | 81.81% | 21.04% | 527.4 | 5.08 |
| normal_winner | 109 | 7322983.72 | 67183.34 | 4.79 | 0.00% | 0.00% | 0.00% | 1012.3 | 0.56 |

## Giveback threshold sweep

| life MFE >= | count | sum pnl |
|---:|---:|---:|
| 2.00% | 67 | -1148485.45 |
| 4.00% | 26 | -449057.93 |
| 6.00% | 17 | -357328.69 |
| 8.00% | 7 | -284988.13 |
| 10.00% | 6 | -230777.19 |

## Immediate adverse sweep

| rule | count | sum pnl |
|---|---:|---:|
| 12 bars MAE>=1.00%, MFE<=0.30% | 12 | -213485.86 |
| 24 bars MAE>=1.20%, MFE<=0.50% | 17 | -219958.09 |
| 72 bars MAE>=2.00%, MFE<=0.80% | 21 | -462014.72 |
| 12 bars MAE>=1.5ATR, MFE<=0.5ATR | 24 | -481042.53 |
| 24 bars MAE>=1.5ATR, MFE<=0.5ATR | 21 | -470700.39 |
| 72 bars MAE>=1.5ATR, MFE<=0.5ATR | 14 | -176472.38 |

## Table 2: by direction

| class | long count | long pnl | short count | short pnl |
|---|---:|---:|---:|---:|
| profit_giveback_to_loss | 12 | -301996.23 | 5 | -55332.46 |
| immediate_adverse | 5 | -87811.17 | 8 | -114031.89 |
| clean_loss | 49 | -1293135.67 | 48 | -1221229.32 |
| normal_winner | 57 | 3651910.13 | 52 | 3671073.58 |

## Table 3: by entry type

| class | fresh count | fresh pnl | reversal count | reversal pnl | reentry count | reentry pnl |
|---|---:|---:|---:|---:|---:|---:|
| profit_giveback_to_loss | 11 | -272531.90 | 6 | -84796.79 | 0 | 0.00 |
| immediate_adverse | 5 | -102662.37 | 7 | -99168.85 | 1 | -11.85 |
| clean_loss | 22 | -408771.19 | 73 | -2074629.80 | 2 | -30964.00 |
| normal_winner | 31 | 2363627.36 | 77 | 4782823.69 | 1 | 176532.66 |

## Table 4: oracle upper bounds

| oracle | blocked decisions | return | Δreturn | DD | DD improvement | trades |
|---|---:|---:|---:|---:|---:|---:|
| avoid_giveback6 | 17 | 42480.26% | 195.23% | -51.04% | 0.15% | 236 |
| avoid_immediate24 | 17 | 44496.97% | 2211.94% | -50.59% | 1.03% | 236 |
| avoid_both | 30 | 44366.37% | 2081.34% | -50.51% | 1.18% | 236 |

Trades CSV: `research_workspace\diagnostics\exp_0108_v22_moirai_loss_taxonomy_oracle_trades.csv`
