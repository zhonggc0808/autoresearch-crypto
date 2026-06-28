# exp_0135 Donchian internal return diagnostic

- diagnostic only; no trade action, live routing, checkpoint, config, oracle, or production strategy change
- lineage: native channel-structure diagnostic; market-signal exit line remains frozen
- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- signal: long `close <= donchian_upper_prev*(1-tol)`, short `close >= donchian_lower_prev*(1+tol)`
- Donchian: high/low rolling `375` shifted by one completed 5m bar
- tolerance: `[0, 50]` bp
- Bollinger context: close rolling `375`, std `1.5`, shifted by one completed 5m bar
- buckets: regime x direction x tol x also_inside_bb x is_top20
- event policy: rising-edge internal-return events only; same-bar baseline exits/reversals are skipped
- post-signal path: attribution-only future close path, never a live signal input
- baseline OOS/full/DD/trades: 629.72% / 42285.03% / -51.12% / 236
- events/buckets/verdict: `13271` / `40` / `REJECT`

## Direction Summary

| regime | direction | tol bp | events | unique trades | top20 | inside BB | avg delta | adv72 | adv288 | giveback capture |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BEAR | long | 0 | 877 | 91 | 200 | 3.76% | -3.26% | 50.74% | 50.97% | -0.05 |
| BEAR | long | 50 | 1692 | 91 | 326 | 27.36% | -2.09% | 52.01% | 51.90% | -0.01 |
| BEAR | short | 0 | 853 | 92 | 160 | 3.17% | 0.19% | 54.63% | 48.30% | 0.01 |
| BEAR | short | 50 | 1811 | 92 | 263 | 23.30% | -0.25% | 52.07% | 46.49% | -0.02 |
| BULL | long | 0 | 1969 | 26 | 639 | 4.67% | -27.53% | 51.35% | 47.54% | -0.40 |
| BULL | long | 50 | 3900 | 26 | 1239 | 34.79% | -22.19% | 50.54% | 49.90% | -0.30 |
| NEUTRAL | long | 0 | 336 | 18 | 146 | 2.38% | -9.64% | 52.98% | 55.36% | -0.15 |
| NEUTRAL | long | 50 | 643 | 19 | 286 | 26.28% | -7.81% | 49.77% | 55.21% | -0.10 |
| NEUTRAL | short | 0 | 411 | 22 | 66 | 4.62% | 3.03% | 50.85% | 57.18% | 0.08 |
| NEUTRAL | short | 50 | 779 | 21 | 95 | 27.86% | 2.08% | 49.04% | 53.27% | 0.02 |

## Stage Read

- verdict: `REJECT`
- reason: no stable regime_direction bucket met Stage-2 exploration criteria
- best direction bucket: `NEUTRAL short tol0bp`, avg delta `3.03%`, top20 events `66`, adv72 `50.85%`.
- best ex-post sub-bucket: `NEUTRAL short tol0bp inside_bb=False is_top20=False`, avg delta `4.54%`, adv72 `51.21%`.
- interpretation: positive non-top20 sub-buckets are ex-post only; `is_top20` cannot be known live, and follow-through is not strong enough to justify Stage 2.
- Stage 2 remains unauthorized here; this file only identifies whether any regime x direction bucket deserves review.

## Reporting Contract

- raw bucket result: `research_workspace\diagnostics\exp_0135_donchian_internal_return_diagnostic.csv`
- event-level result: `research_workspace\diagnostics\exp_0135_donchian_internal_return_diagnostic_events.csv`
- direction summary: `research_workspace\diagnostics\exp_0135_donchian_internal_return_diagnostic_direction.csv`
- regime-permission result: not applicable; no signal stream is modified.
- safe-execution result: not applicable; no execution signal is produced.
- OOS return, max drawdown, rolling 12m, trade count: inherited baseline context only.
- top-winner damage: diagnostic bucket field `is_top20`; no winner is cut because there is no trade action.
- worst-loser field: event-level `is_worst20`; no loser is blocked because there is no trade action.
- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.
