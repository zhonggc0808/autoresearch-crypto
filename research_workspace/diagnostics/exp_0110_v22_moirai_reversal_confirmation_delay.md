# exp_0110 v2.2 + Moirai reversal confirmation delay

- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- execution: `next_bar_open`
- behavior: reversal becomes close-to-flat, then pending confirmation opens new direction if confirmed
- live/checkpoint: no change

## Summary

| variant | ΔOOS | DD improve | Δroll12 | rev retained | delayed/confirmed/expired | blocked pnl | missed winner pnl | blocked loser pnl | year W/L/F | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| signal_delay1 | -13.93% | 0.00% | -5.77% | 100.00% | 163/163/0 | 0.00 | 0.00 | 0.00 | 1/6/1 | False |
| signal_delay3 | -13.93% | 0.00% | -5.77% | 100.00% | 163/163/0 | 0.00 | 0.00 | 0.00 | 1/6/1 | False |
| signal_delay6 | -13.93% | 0.00% | -5.77% | 100.00% | 163/163/0 | 0.00 | 0.00 | 0.00 | 1/6/1 | False |
| signal_delay12 | -13.93% | 0.00% | -5.77% | 99.39% | 163/162/1 | 4.79 | 4.79 | 0.00 | 1/6/1 | False |
| price_delay1 | 246.82% | 0.00% | 7.32% | 61.96% | 136/101/35 | -265929.38 | 1085805.53 | -1351734.91 | 3/4/1 | False |
| price_delay3 | -1.27% | 0.00% | -6.29% | 51.53% | 128/84/44 | 1027875.66 | 2105890.88 | -1078015.22 | 2/5/1 | False |
| price_delay6 | -8.64% | 0.00% | -5.99% | 56.44% | 133/92/41 | 647475.22 | 1597194.75 | -949719.53 | 2/5/1 | False |
| price_delay12 | -14.05% | 0.00% | -6.15% | 50.31% | 128/82/46 | 450577.54 | 1653636.51 | -1203058.97 | 2/5/1 | False |
| strength_delay1 | -325.95% | -0.00% | -9.99% | 17.18% | 105/28/77 | 1400902.00 | 3571504.83 | -2170602.82 | 3/4/1 | False |
| strength_delay3 | 19.55% | -0.00% | -5.46% | 9.82% | 101/16/85 | 2045633.70 | 4171680.76 | -2126047.06 | 3/4/1 | False |
| strength_delay6 | 22.34% | -0.00% | -5.13% | 2.45% | 94/4/90 | 2492904.64 | 4562719.65 | -2069815.01 | 3/4/1 | False |
| strength_delay12 | 3.08% | -0.00% | -5.13% | 2.45% | 93/4/89 | 2417830.12 | 4636317.67 | -2218487.54 | 3/4/1 | False |

## Decision

- no variant passes the gate.
- `price_delay1` is the only interesting diagnostic: OOS and rolling12 improve, and the unretained baseline reversal PnL is net negative, but full DD is unchanged, baseline reversal retention is below 70%, and year-reset loses too many years.
- `signal` confirmation mostly only adds a one-bar close-to-flat delay; it does not reduce DD and hurts OOS.
- `strength` confirmation is too strict; it removes too many profitable reversal opportunities.
- live/checkpoint action: no change.

CSV: `research_workspace\diagnostics\exp_0110_v22_moirai_reversal_confirmation_delay.csv`
