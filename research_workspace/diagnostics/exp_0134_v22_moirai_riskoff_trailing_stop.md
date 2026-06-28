# exp_0134 v2.2 + Moirai riskoff trailing stop

- diagnostic only; no live/checkpoint/config/production strategy change
- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- riskoff signal: `2h_wick_r75_v2p0_then_macd2h_12h` only
- mechanism: riskoff activation starts a temporary trailing stop; it does not close-to-flat immediately
- ATR ref: fixed at riskoff activation bar
- anchor: short `min(low since activation)`, long `max(high since activation)`
- stop detection: completed 5m close crosses stop; next open executes
- post-stop lockout: same-side reentry is suppressed until baseline leaves the stopped direction
- TTL: `until_baseline_exit` or `24h`; TTL expiry cancels trailing only
- priority: baseline reversal > riskoff activation > riskoff stop
- hard gate: top20 cut <=2, 6/23 protective exit, OOS not halved, year_losses <= year_wins, net_delta_pnl_proxy > 0
- baseline OOS/full/DD/roll12/trades: 629.72% / 42285.03% / -51.12% / 3.99% / 236
- verdict: `REJECT`; counts `{'REJECT': 4}`

## Matrix

| variant | live hit | live stop | stops | activations | dOOS | DD improve | roll12 | top20 cut | net delta proxy | year W/L/F | hard gate | verdict |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_until_baseline_exit | True | 2026-06-26 23:05:00 | 34 | 34 | 230.81% | 8.28% | 19.11% | 7 | 532734.89 | 3/5/0 | False | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_24h | True | 2026-06-26 23:05:00 | 34 | 34 | 230.81% | 8.28% | 19.11% | 7 | 532734.89 | 3/5/0 | False | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_until_baseline_exit | True | 2026-06-26 22:25:00 | 34 | 34 | 217.77% | 8.37% | 19.84% | 7 | 469673.69 | 3/5/0 | False | REJECT |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_24h | True | 2026-06-26 22:25:00 | 34 | 34 | 217.77% | 8.37% | 19.84% | 7 | 469673.69 | 3/5/0 | False | REJECT |

## Live Case

| variant | status | hit | activation CST | stop CST | execution open | stop price | return after cost | saved vs 1590 | reasons |
|---|---|---:|---|---|---:|---:|---:|---:|---|
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_24h | riskoff_stop | True | 2026-06-26 22:00:00 | 2026-06-26 22:25:00 | 1569.28 | 1568.22 | 10.13% | 20.72 | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_until_baseline_exit | riskoff_stop | True | 2026-06-26 22:00:00 | 2026-06-26 22:25:00 | 1569.28 | 1568.22 | 10.13% | 20.72 | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_24h | riskoff_stop | True | 2026-06-26 22:00:00 | 2026-06-26 23:05:00 | 1573.62 | 1567.07 | 9.82% | 16.38 | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |
| 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_until_baseline_exit | riskoff_stop | True | 2026-06-26 22:00:00 | 2026-06-26 23:05:00 | 1573.62 | 1567.07 | 9.82% | 16.38 | 2h_lower_wick_setup@2026-06-26 04:00:00+macd_bull_cross_2h |

## Conclusion

- final verdict: `REJECT`; no row passed the hard gate.
- live-case protection: 4/4 rows stopped before the 1590 reference; 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_24h -> 2026-06-26 22:25:00 @ 1569.28; 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p0_until_baseline_exit -> 2026-06-26 22:25:00 @ 1569.28; 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_24h -> 2026-06-26 23:05:00 @ 1573.62; 2h_wick_r75_v2p0_then_macd2h_12h_trailATR1p5_until_baseline_exit -> 2026-06-26 23:05:00 @ 1573.62.
- long-window blocker: top20 winner exits range `7-7`, above the hard limit `<=2`.
- year-slice blocker: year W/L/F combinations are `[(3, 5, 0)]`, so `year_losses <= year_wins` fails.
- TTL read: `24h` and `until_baseline_exit` are identical here because every activation stopped before TTL expiry.
- implementation status: research-only; this does not authorize live/demo routing, checkpoint promotion, execution changes, or production strategy changes.

## Reporting Contract

- raw result: full matrix in `research_workspace\diagnostics\exp_0134_v22_moirai_riskoff_trailing_stop.csv`
- regime-permission result: not applicable here because input is already the v2.2 + Moirai post-gate baseline stream.
- safe-execution result: the overlay writes a stop signal on completed-bar close confirmation and is evaluated at next open.
- OOS return, max drawdown, rolling 12m, trade count: columns in the matrix CSV.
- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl`.
- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl`.
- net attribution: `net_delta_pnl_proxy` uses next-open execution return proxy versus baseline trade PnL.
- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.

Events CSV: `research_workspace\diagnostics\exp_0134_v22_moirai_riskoff_trailing_stop_events.csv`
Live-case CSV: `research_workspace\diagnostics\exp_0134_v22_moirai_riskoff_trailing_stop_live_case.csv`
