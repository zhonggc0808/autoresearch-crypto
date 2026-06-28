# exp_0131 v2.2 + Moirai market-signal take-profit

- diagnostic only; no live/checkpoint/config change
- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- overlay: completed HTF market reversal signal, current trade must be profitable, next-open close-to-flat
- lockout: same-direction reentry is suppressed until the base signal leaves that direction
- no MFE/giveback/fixed take-profit signal is used for triggering
- baseline OOS/full/DD/roll12/trades: 629.72% / 42285.03% / -51.12% / 3.99% / 236
- verdict: `OBSERVE` from matrix pass count `10`; live-case replay is reported separately

## Top Rows

| variant | exits | OOS | dOOS | full DD | DD improve | roll12 | top20 cut | worst20 hit | year W/L/F | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1h_strict_engulf_v2p0_macd4h | 6 | 643.17% | 13.46% | -48.86% | 4.42% | 3.34% | 1 | 0 | 2/2/4 | True |
| 2h_wick_r75_v1p5_macd2h | 2 | 638.18% | 8.46% | -51.12% | 0.00% | 3.99% | 0 | 0 | 2/0/6 | True |
| 2h_wick_r75_v2p0_macd2h | 1 | 638.18% | 8.46% | -51.12% | 0.00% | 3.99% | 0 | 0 | 1/0/7 | True |
| 2h_wick_r75_v2p0_then_macd4h_12h | 3 | 637.16% | 7.44% | -51.12% | 0.00% | 3.99% | 1 | 0 | 2/2/4 | True |
| 2h_wick_r75_v1p5_macd4h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r75_v2p0_macd4h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r85_v1p5_macd4h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r85_v1p5_then_macd4h_12h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r85_v2p0_macd4h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r85_v2p0_then_macd4h_12h | 1 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 1 | 0 | 1/0/7 | True |
| 2h_wick_r75_v2p0_then_macd2h_12h | 24 | 729.38% | 99.66% | -47.29% | 7.49% | 14.50% | 7 | 0 | 3/5/0 | False |
| 2h_wick_r75_v2p0_then_macd2h_24h | 36 | 654.01% | 24.30% | -47.29% | 7.49% | 14.05% | 8 | 0 | 1/7/0 | False |
| 2h_wick_r75_v1p5_then_macd2h_24h | 45 | 624.69% | -5.03% | -47.29% | 7.49% | -3.76% | 10 | 0 | 2/6/0 | False |
| 2h_wick_r75_v1p5_then_macd2h_12h | 29 | 701.84% | 72.12% | -47.29% | 7.49% | -3.76% | 8 | 0 | 3/5/0 | False |
| 1h_strict_engulf_v2p0_macd2h | 15 | 597.15% | -32.57% | -48.86% | 4.42% | -0.78% | 4 | 0 | 5/2/1 | False |
| 1h_loose_engulf_v2p0_macd4h | 11 | 386.88% | -242.83% | -48.86% | 4.42% | -4.54% | 3 | 0 | 3/3/2 | False |
| 1h_strict_engulf_v1p5_macd2h | 17 | 613.51% | -16.21% | -48.86% | 4.42% | -0.78% | 5 | 0 | 6/2/0 | False |
| 1h_loose_engulf_v1p5_macd4h | 15 | 471.92% | -157.80% | -48.86% | 4.42% | -27.23% | 5 | 0 | 3/3/2 | False |
| 2h_wick_r85_v1p5_oiN0p5 | 12 | 664.01% | 34.29% | -48.86% | 4.42% | 3.29% | 3 | 0 | 3/3/2 | False |
| 1h_strict_engulf_v1p5_macd4h | 8 | 649.39% | 19.67% | -48.86% | 4.42% | -19.35% | 2 | 0 | 2/2/4 | False |
| 1h_loose_engulf_v1p5_macd2h | 27 | 671.98% | 42.26% | -49.83% | 2.52% | -12.91% | 9 | 0 | 3/5/0 | False |
| 2h_wick_r60_v2p0_oiN0p5 | 63 | 421.04% | -208.68% | -49.99% | 2.20% | -10.50% | 15 | 2 | 1/6/1 | False |
| 2h_wick_r60_v2p0_oi0 | 73 | 352.24% | -277.47% | -49.99% | 2.20% | -10.50% | 15 | 2 | 1/6/1 | False |
| 2h_wick_r60_v1p5_oiN0p5 | 74 | 320.75% | -308.97% | -49.99% | 2.20% | -19.08% | 16 | 2 | 0/7/1 | False |
| 2h_wick_r60_v1p5_oi0 | 87 | 313.43% | -316.28% | -49.99% | 2.20% | -19.08% | 17 | 2 | 0/7/1 | False |
| 2h_wick_r75_v1p5_then_macd4h_24h | 15 | 640.60% | 10.89% | -51.12% | 0.00% | 3.99% | 4 | 0 | 4/4/0 | False |
| 2h_wick_r85_v2p0_then_macd4h_24h | 5 | 629.72% | 0.00% | -51.12% | 0.00% | 3.99% | 2 | 0 | 2/2/4 | False |
| 2h_wick_r75_v2p0_then_macd4h_24h | 13 | 637.69% | 7.98% | -51.12% | 0.00% | 3.99% | 4 | 0 | 4/3/1 | False |
| 2h_wick_r60_v1p5_macd4h | 3 | 635.82% | 6.10% | -51.12% | 0.00% | 3.99% | 2 | 0 | 3/0/5 | False |
| 2h_wick_r60_v2p0_macd4h | 3 | 635.82% | 6.10% | -51.12% | 0.00% | 3.99% | 2 | 0 | 3/0/5 | False |

## Reporting Contract

- raw result: baseline and overlay rows use next-open evaluation; full matrix in `research_workspace\diagnostics\exp_0131_v22_moirai_market_signal_tp.csv`
- regime-permission result: not applicable here because the input is already the v2.2 + Moirai post-gate baseline signal stream.
- safe-execution result: overlay only emits close-to-flat and is evaluated at next open; it never directly reverses or opens a new position.
- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl` columns.
- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl` columns.
- conclusion: no live action; any non-rejected row remains research-only until separately reviewed and approved.

Exits CSV: `research_workspace\diagnostics\exp_0131_v22_moirai_market_signal_tp_exits.csv`
