# exp_0095 BTC v2.2 + Moirai2 exp_0093 compare

- base: `channel_breakout_v2_2_m375_bbm375_1p5`
- gate: Moirai2 exp_0093 (`min_edge=-2%`, `risk_floor=4%`, `context=1024`, `horizon=72`)
- execution: completed-bar signal, `next_bar_open` fill
- symbol: BTCUSDT 5m

| days | raw OOS | Moirai OOS | Δ OOS | raw full | Moirai full | raw DD | Moirai DD | blocked full/oos | blocked pnl full/oos |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 60 | -1.10% | -1.10% | 0.00% | -9.88% | -9.88% | -14.64% | -14.64% | 0 / 0 | 0.00 / 0.00 |
| 365 | 9.00% | 10.80% | 1.80% | -13.07% | -11.64% | -27.51% | -27.51% | 1 / 1 | -140.53 / -140.53 |
| 730 | 11.95% | 13.79% | 1.84% | -17.00% | 3.23% | -41.40% | -41.40% | 2 / 1 | -1975.76 / -132.39 |
| 1300 | 8.46% | 10.24% | 1.79% | 84.82% | 87.86% | -52.41% | -52.41% | 1 / 1 | -295.55 / -295.55 |

结论短句：Moirai2 在 BTC OOS 赢了 3/4 个窗口。

如果要继续，优先看 730/1300d 的 OOS 和 DD；60d/365d 只能当烟测，不适合下结论。
