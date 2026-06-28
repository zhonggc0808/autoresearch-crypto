# exp_0133 v2.2 + Moirai profit-armed market TP shadow

- diagnostic only; no live/checkpoint/config/production strategy change
- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- direction: `B_arm_first_no_new_shape_library`
- signal pool: `1h_strict_engulf_v2p0_macd4h`, `2h_wick_r75_v2p0_then_macd2h_12h`, `2h_wick_r75_v1p5_macd2h`
- arming: `open_profit_atr_entry_based`, thresholds `{1.5, 2.0, 2.5, 3.0}`, latched once reached
- trigger condition: armed plus market signal plus `current_return_after_cost > 0`
- execution: next 5m open close-to-flat; no direct reverse/open; same-side lockout
- conflict policy: if the baseline reverses on the same bar, market TP is not attributed
- baseline OOS/full/DD/roll12/trades: 629.72% / 42285.03% / -51.12% / 3.99% / 236
- verdict: `OBSERVE`; counts `{'OBSERVE': 8, 'REJECT': 4}`

## Matrix

| variant | live hit | live trigger | exits | armed | dOOS | DD improve | roll12 | top20 cut | valuable | missed winners | net delta proxy | year W/L/F | verdict |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2h_wick_r75_v1p5_macd2h_armATR1p5 | False |  | 2 | 218 | 8.46% | 0.00% | 3.99% | 0 | 2 | 0 | 10873.81 | 2/0/6 | OBSERVE |
| 2h_wick_r75_v1p5_macd2h_armATR2p0 | False |  | 2 | 215 | 8.46% | 0.00% | 3.99% | 0 | 2 | 0 | 10873.81 | 2/0/6 | OBSERVE |
| 2h_wick_r75_v1p5_macd2h_armATR2p5 | False |  | 2 | 212 | 8.46% | 0.00% | 3.99% | 0 | 2 | 0 | 10873.81 | 2/0/6 | OBSERVE |
| 2h_wick_r75_v1p5_macd2h_armATR3p0 | False |  | 2 | 208 | 8.46% | 0.00% | 3.99% | 0 | 2 | 0 | 10873.81 | 2/0/6 | OBSERVE |
| 1h_strict_engulf_v2p0_macd4h_armATR1p5 | False |  | 6 | 218 | 13.46% | 4.42% | 3.34% | 1 | 4 | 2 | -49836.15 | 2/2/4 | OBSERVE |
| 1h_strict_engulf_v2p0_macd4h_armATR2p0 | False |  | 6 | 215 | 13.46% | 4.42% | 3.34% | 1 | 4 | 2 | -49836.15 | 2/2/4 | OBSERVE |
| 1h_strict_engulf_v2p0_macd4h_armATR2p5 | False |  | 6 | 212 | 13.46% | 4.42% | 3.34% | 1 | 4 | 2 | -49836.15 | 2/2/4 | OBSERVE |
| 1h_strict_engulf_v2p0_macd4h_armATR3p0 | False |  | 6 | 208 | 13.46% | 4.42% | 3.34% | 1 | 4 | 2 | -49836.15 | 2/2/4 | OBSERVE |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR1p5 | True | 2026-06-26 22:00:00 | 24 | 218 | 99.66% | 7.49% | 14.50% | 7 | 15 | 9 | 276819.08 | 3/5/0 | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR2p0 | True | 2026-06-26 22:00:00 | 24 | 215 | 99.66% | 7.49% | 14.50% | 7 | 15 | 9 | 276819.08 | 3/5/0 | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR2p5 | True | 2026-06-26 22:00:00 | 24 | 212 | 99.66% | 7.49% | 14.50% | 7 | 15 | 9 | 276819.08 | 3/5/0 | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR3p0 | True | 2026-06-26 22:00:00 | 24 | 208 | 99.66% | 7.49% | 14.50% | 7 | 15 | 9 | 276819.08 | 3/5/0 | REJECT |

## Live Case

| variant | status | hit before 1590 | trigger CST | close | next open | armed ATR | trigger ATR | net return | reasons |
|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR1p5 | triggered | True | 2026-06-26 22:00:00 | 1564.52 | 1564.52 | 2.44 | 66.64 | 10.46% | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR2p0 | triggered | True | 2026-06-26 22:00:00 | 1564.52 | 1564.52 | 2.44 | 66.64 | 10.46% | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR2p5 | triggered | True | 2026-06-26 22:00:00 | 1564.52 | 1564.52 | 2.65 | 66.64 | 10.46% | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_armATR3p0 | triggered | True | 2026-06-26 22:00:00 | 1564.52 | 1564.52 | 4.14 | 66.64 | 10.46% | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 1h_strict_engulf_v2p0_macd4h_armATR1p5 | no_trigger | False |  |  |  |  |  |  |  |
| 1h_strict_engulf_v2p0_macd4h_armATR2p0 | no_trigger | False |  |  |  |  |  |  |  |
| 1h_strict_engulf_v2p0_macd4h_armATR2p5 | no_trigger | False |  |  |  |  |  |  |  |
| 1h_strict_engulf_v2p0_macd4h_armATR3p0 | no_trigger | False |  |  |  |  |  |  |  |
| 2h_wick_r75_v1p5_macd2h_armATR1p5 | no_trigger | False |  |  |  |  |  |  |  |
| 2h_wick_r75_v1p5_macd2h_armATR2p0 | no_trigger | False |  |  |  |  |  |  |  |
| 2h_wick_r75_v1p5_macd2h_armATR2p5 | no_trigger | False |  |  |  |  |  |  |  |
| 2h_wick_r75_v1p5_macd2h_armATR3p0 | no_trigger | False |  |  |  |  |  |  |  |

## Conclusion

- No row qualifies as `SHADOW_CANDIDATE` because no variant passes long-window, attribution, and live-case gates together.
- The ATR arming thresholds `{1.5, 2.0, 2.5, 3.0}` did not change the exit set inside each selected signal family; when these market signals fire, the trade is already well beyond the arming threshold.
- `2h_wick_r75_v1p5_macd2h` is the cleanest long-window observe branch: only `2` exits, `0` top20 cuts, positive proxy attribution, but it does not hit the 2026-06-23 Bitget live-case.
- `2h_wick_r75_v2p0_then_macd2h_12h` still explains the live-case near `1564.52`, but remains rejected for live because it cuts `7/20` top winners and loses `5` year slices.
- `1h_strict_engulf_v2p0_macd4h` remains observe-only: long-window metrics are okay, but attribution proxy stays net negative and live-case has no trigger.
- No live/demo routing, checkpoint, config, execution, oracle, or production strategy change is authorized.

## Reporting Contract

- raw result: full matrix in `research_workspace\diagnostics\exp_0133_v22_moirai_market_tp_profit_arming_shadow.csv`
- regime-permission result: not applicable here because input is already the v2.2 + Moirai post-gate baseline stream.
- safe-execution result: the overlay emits only close-to-flat and is evaluated at next open.
- OOS return, max drawdown, rolling 12m, trade count: columns in the matrix CSV.
- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl`.
- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl`.
- MFE capture and giveback-to-loss stats: matrix CSV columns.
- conclusion: research-only shadow evidence; no live/demo routing or checkpoint promotion is authorized.

Exits CSV: `research_workspace\diagnostics\exp_0133_v22_moirai_market_tp_profit_arming_shadow_exits.csv`
Live-case CSV: `research_workspace\diagnostics\exp_0133_v22_moirai_market_tp_profit_arming_shadow_live_case.csv`
