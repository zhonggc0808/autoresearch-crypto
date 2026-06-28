# exp_0136 v2.2 multitimeframe diversification diagnostic

- research-only; no live/checkpoint/config/oracle/production strategy change
- question: do core Donchian sleeves on 15m/1h diversify trend return, drawdown, and top winners versus the 5m v2.2+Moirai line?
- non-baseline sleeves are core Donchian only: no regime split, BB confirmation, MTG, BCD, Moirai gate, or daily EMA permission
- execution: completed bar signal, next bar open fill, normal fee/slippage plus 10bp fee stress
- resample: 15m/1h from 5m using completed OHLCV bars
- verdict: `REJECT`

## Sleeves

| sleeve | TF | lookback | min hold | full | OOS | DD | roll12 | trades | trades/year | OOS trades | max idle d | top20 gross | worst20 gross |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m_v22_moirai_baseline | 5min | 375 | 432 | 42285.03% | 629.72% | -51.12% | -23.48% | 236 | 35.13 | 103 | 81.0 | 67.45% | 59.88% |
| 5m_core_donchian | 5min | 375 | 432 | 365.20% | 584.85% | -84.84% | -70.69% | 734 | 109.26 | 219 | 3.3 | 32.06% | 25.99% |
| 15m_core_donchian | 15min | 125 | 144 | 24.75% | 263.07% | -93.24% | -83.43% | 708 | 105.39 | 215 | 3.3 | 41.57% | 32.39% |
| 1h_core_donchian | 1h | 32 | 36 | -53.27% | 185.00% | -97.47% | -90.21% | 644 | 95.86 | 187 | 3.3 | 55.86% | 38.19% |
| 1h_core_donchian_fast | 1h | 32 | 18 | -36.99% | 55.13% | -97.41% | -81.98% | 774 | 115.21 | 227 | 3.3 | 52.71% | 27.82% |

## Correlations

| left | right | daily | roll90 mean | roll180 mean | monthly |
|---|---|---:|---:|---:|---:|
| 5m_v22_moirai_baseline | 15m_core_donchian | 0.42 | 0.39 | 0.39 | 0.36 |
| 5m_v22_moirai_baseline | 1h_core_donchian | 0.38 | 0.35 | 0.35 | 0.44 |
| 5m_v22_moirai_baseline | 1h_core_donchian_fast | 0.40 | 0.37 | 0.37 | 0.39 |
| 5m_core_donchian | 15m_core_donchian | 0.89 | 0.90 | 0.90 | 0.88 |
| 15m_core_donchian | 1h_core_donchian | 0.80 | 0.80 | 0.80 | 0.85 |

## Fixed Combos

| combo | full | OOS | DD | DD improve | roll12 | OOS gate | combo gate |
|---|---:|---:|---:|---:|---:|---|---|
| combo_70_20_10_v22_15m_1h | 10189.64% | 529.38% | -58.16% | -13.77% | -46.32% | True | False |
| combo_60_20_20_v22_15m_1h | 5514.85% | 482.69% | -64.35% | -25.89% | -54.97% | False | False |
| combo_80_0_20_v22_1h_fast | 15618.67% | 473.41% | -50.60% | 1.02% | -35.56% | False | False |

## Stage Gates

| sleeve | OOS > 0 | sample ok | corr ok | DD desync | top20 misaligned | combo pass | stage2 ready |
|---|---|---|---|---|---|---|---|
| 15m_core_donchian | True | True | True | False | True | False | False |
| 1h_core_donchian | True | True | True | False | True | False | False |
| 1h_core_donchian_fast | True | True | True | True | True | False | False |

## Reporting Contract

- sleeve metrics: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic.csv`
- correlations: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_correlations.csv`
- DD overlap: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_dd_overlap.csv`
- top winners: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_top_winners.csv`
- top overlap: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_top_overlap.csv`
- combos: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_combos.csv`
- stage gates: `research_workspace\diagnostics\exp_0136_v22_multitimeframe_diversification_diagnostic_stage_gates.csv`
- regime-permission result: intentionally excluded for non-baseline sleeves in Stage 1.
- safe-execution result: all sleeve signal changes are evaluated at next bar open.
- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.
