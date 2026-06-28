# exp_0132 market-signal TP attribution compression

- input: `exp_0131` matrix/exits/live-case outputs
- purpose: compress 86 variants into A/B/C action buckets before any further search
- attribution proxy: `early_exit_pnl_proxy = current_return_at_trigger * base_entry_notional`; compare against baseline trade final PnL
- live/checkpoint/config action: no change

## Bucket Counts

| category | count | action |
|---|---:|---|
| A long pass, no live-case hit | 10 | OBSERVE; inspect exits only |
| B live-case hit, failed long-window gate | 34 | rejected_for_live; explanation only |
| C overtrigger or winner damage | 13 | REJECT/archive |
| D low-signal or neutral rejects | 29 | ignore unless new evidence |

## A. Long-Window Pass But No Live-Case Hit

| variant | exits | dOOS | DD improve | top20 cut | valuable exits | missed winners | net delta proxy | action |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1h_strict_engulf_v2p0_macd4h | 6 | 13.46% | 4.42% | 1 | 4 | 2 | -48547.48 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r75_v1p5_macd2h | 2 | 8.46% | 0.00% | 0 | 2 | 0 | 11227.51 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r75_v2p0_macd2h | 1 | 8.46% | 0.00% | 0 | 1 | 0 | 9128.23 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r75_v2p0_then_macd4h_12h | 3 | 7.44% | 0.00% | 1 | 1 | 2 | -18639.11 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r75_v1p5_macd4h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r75_v2p0_macd4h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r85_v1p5_macd4h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r85_v1p5_then_macd4h_12h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r85_v2p0_macd4h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |
| 2h_wick_r85_v2p0_then_macd4h_12h | 1 | 0.00% | 0.00% | 1 | 1 | 0 | 61909.45 | OBSERVE_REVIEW_EXITS |

Representative `1h_strict_engulf_v2p0_macd4h`:

- exits: `6`
- valuable exits by proxy: `4`
- missed winners by proxy: `2`
- saved losers: `0`
- net delta proxy vs baseline trades: `-48547.48`
- read: long-window metrics are interesting, but the six exits are mostly winner cuts rather than clear saved losers.

Representative exits:

| time | side | base pnl | early pnl proxy | delta proxy | quality | top20 | reasons |
|---|---|---:|---:|---:|---|---:|---|
| 2020-08-21 12:00:00 | long | 15339.94 | 16247.70 | 907.77 | improved_winner | False | 1h_bearish_strict_engulf+macd_bear_cross_4h |
| 2020-10-20 12:00:00 | long | 37135.16 | 202.73 | -36932.43 | missed_winner | False | 1h_bearish_strict_engulf+macd_bear_cross_4h |
| 2021-04-04 00:00:00 | long | 71695.47 | 16239.13 | -55456.34 | missed_winner | False | 1h_bearish_strict_engulf+macd_bear_cross_4h |
| 2023-02-24 16:00:00 | long | 2902.58 | 5186.76 | 2284.19 | improved_winner | False | 1h_bearish_strict_engulf+macd_bear_cross_4h |
| 2025-02-12 20:00:00 | short | 95432.27 | 109496.27 | 14064.00 | improved_winner | False | 1h_bullish_strict_engulf+macd_bull_cross_4h |
| 2025-05-11 16:00:00 | long | 289292.05 | 315877.40 | 26585.34 | improved_winner | True | 1h_bearish_strict_engulf+macd_bear_cross_4h |

## B. Live-Case Hit But Long-Window Gate Failed

| variant | live trigger | close | dOOS | DD improve | roll12 | exits | top20 cut | year W/L/F | net delta proxy | action |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2h_wick_r75_v2p0_then_macd2h_12h | 2026-06-26 22:00:00 | 1564.52 | 99.66% | 7.49% | 14.50% | 24 | 7 | 3/5/0 | 289903.71 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5_then_macd2h_12h | 2026-06-26 22:00:00 | 1564.52 | 72.12% | 7.49% | -3.76% | 29 | 8 | 3/5/0 | 302325.04 | REJECTED_FOR_LIVE |
| 1h_loose_engulf_v1p5_macd2h | 2026-06-26 22:00:00 | 1564.52 | 42.26% | 2.52% | -12.91% | 27 | 9 | 3/5/0 | 242146.97 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5 | 2026-06-26 12:00:00 | 1552.80 | 41.32% | -7.32% | -16.37% | 65 | 12 | 2/6/0 | 318629.75 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v2p0_then_macd2h_24h | 2026-06-26 22:00:00 | 1564.52 | 24.30% | 7.49% | 14.05% | 36 | 8 | 1/7/0 | -34935.55 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v2p0 | 2026-06-26 12:00:00 | 1552.80 | 22.91% | -7.32% | -12.39% | 51 | 10 | 1/7/0 | 127154.00 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5_then_macd4h_24h | 2026-06-27 08:00:00 | 1579.03 | 10.89% | 0.00% | 3.99% | 15 | 4 | 4/4/0 | 357137.78 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v2p0_then_macd4h_24h | 2026-06-27 08:00:00 | 1579.03 | 7.98% | 0.00% | 3.99% | 13 | 4 | 4/3/1 | 349051.93 | REJECTED_FOR_LIVE |
| 1h_loose_engulf_v2p0_macd2h | 2026-06-26 22:00:00 | 1564.52 | 2.96% | -6.74% | -21.13% | 23 | 7 | 3/5/0 | -15935.75 | REJECTED_FOR_LIVE |
| 1h_strict_engulf_v2p0 | 2026-06-24 20:00:00 | 1679.79 | 2.83% | -21.76% | -40.70% | 104 | 16 | 1/7/0 | -22919.70 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5_then_macd2h_24h | 2026-06-26 22:00:00 | 1564.52 | -5.03% | 7.49% | -3.76% | 45 | 10 | 2/6/0 | 14426.28 | REJECTED_FOR_LIVE |
| 1h_strict_engulf_v2p0_oi0 | 2026-06-24 20:00:00 | 1679.79 | -6.23% | -21.26% | -35.90% | 68 | 12 | 1/6/1 | -407171.18 | REJECTED_FOR_LIVE |
| 1h_strict_engulf_v1p5_macd2h | 2026-06-26 22:00:00 | 1564.52 | -16.21% | 4.42% | -0.78% | 17 | 5 | 6/2/0 | 14628.05 | REJECTED_FOR_LIVE |
| 1h_strict_engulf_v2p0_oiN0p5 | 2026-06-26 22:00:00 | 1564.52 | -17.68% | -21.26% | -35.90% | 58 | 11 | 1/6/1 | -233201.12 | REJECTED_FOR_LIVE |
| 1h_strict_engulf_v2p0_macd2h | 2026-06-26 22:00:00 | 1564.52 | -32.57% | 4.42% | -0.78% | 15 | 4 | 5/2/1 | -64275.70 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5_oi0 | 2026-06-26 12:00:00 | 1552.80 | -48.15% | -5.74% | -19.08% | 42 | 11 | 2/5/1 | -166708.60 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v2p0_oi0 | 2026-06-26 12:00:00 | 1552.80 | -70.15% | -5.74% | -10.50% | 35 | 9 | 2/5/1 | -330934.35 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v1p5_oiN0p5 | 2026-06-26 12:00:00 | 1552.80 | -75.49% | -5.74% | -19.08% | 36 | 10 | 2/5/1 | -360174.57 | REJECTED_FOR_LIVE |
| 2h_wick_r75_v2p0_oiN0p5 | 2026-06-26 12:00:00 | 1552.80 | -84.51% | -5.74% | -10.50% | 31 | 9 | 2/5/1 | -455142.81 | REJECTED_FOR_LIVE |
| 2h_wick_r60_v2p0_oiN0p5 | 2026-06-26 12:00:00 | 1552.80 | -208.68% | 2.20% | -10.50% | 63 | 15 | 1/6/1 | -741471.06 | REJECTED_FOR_LIVE |

Representative `2h_wick_r75_v2p0_then_macd2h_12h`:

- would have exited the 2026-06-23 Bitget short at `2026-06-26 22:00:00` near `1564.52`
- current-return-at-trigger was `10.52%` in the live-case CSV
- long-window top20 cuts: `7`
- year W/L/F: `3/5/0`
- read: useful for explaining the live short, not suitable as a strategy candidate.

## C. Overtrigger Or Top-Winner Damage

| variant | exits | dOOS | roll12 | top20 cut | live hit | action |
|---|---:|---:|---:|---:|---:|---|
| 2h_wick_r85_v1p5 | 30 | 165.38% | -20.36% | 6 | False | REJECT |
| 2h_wick_r85_v1p5_then_macd2h_24h | 21 | 167.56% | -6.66% | 6 | False | REJECT |
| 2h_wick_r85_v2p0 | 24 | 135.13% | -21.38% | 5 | False | REJECT |
| 2h_wick_r85_v2p0_then_macd2h_24h | 18 | 145.59% | -6.66% | 5 | False | REJECT |
| 2h_wick_r85_v1p5_oi0 | 17 | 143.20% | -14.44% | 5 | False | REJECT |
| 1h_loose_engulf_v1p5_macd4h | 15 | -157.80% | -27.23% | 5 | False | REJECT |
| 2h_wick_r85_v2p0_oi0 | 14 | 117.19% | -14.44% | 4 | False | REJECT |
| 2h_wick_r85_v1p5_then_macd2h_12h | 12 | 99.01% | -6.66% | 3 | False | REJECT |
| 2h_wick_r85_v1p5_oiN0p5 | 12 | 34.29% | 3.29% | 3 | False | REJECT |
| 1h_loose_engulf_v2p0_macd4h | 11 | -242.83% | -4.54% | 3 | False | REJECT |
| 2h_wick_r85_v2p0_then_macd2h_12h | 10 | 94.22% | -6.66% | 3 | False | REJECT |
| 2h_wick_r85_v2p0_oiN0p5 | 10 | 22.38% | 3.29% | 3 | False | REJECT |
| 1h_strict_engulf_v1p5_macd4h | 8 | 19.67% | -19.35% | 2 | False | REJECT |

## Conclusion

- Do not expand the wick/engulf/MACD matrix further.
- Keep A variants as OBSERVE only; their exit-level evidence is not yet strong enough to promote.
- Mark B variants as `rejected_for_live`: they explain the 2026-06-23 short but fail long-window winner-damage controls.
- Archive C variants as REJECT, especially r60 wick and loose/fast MACD families with high top20 cuts.
- If market-signal TP is revisited, use a new direction: arm the market signal only after unusually profitable trades, such as ATR-normalized open profit or entry-risk multiple, rather than adding more shape combinations.

Summary CSV: `research_workspace\diagnostics\exp_0132_v22_moirai_market_signal_tp_attribution.csv`
Exit attribution CSV: `research_workspace\diagnostics\exp_0132_v22_moirai_market_signal_tp_attribution_exit_attribution.csv`
