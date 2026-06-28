# exp_0137 ETH/BTC cross-asset overlap diagnostic

- research-only
- no live/checkpoint/config/oracle/production strategy change
- no BTC live route
- no SOL in this round
- no portfolio allocator
- verdict: `REJECT`

## Scope

- ETH sleeve: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`
- BTC sleeve: core Donchian `m=375`, `min_hold_bars=432`
- overlap: `2022-11-20 03:40:00` to `2026-06-12 02:55:00`
- overlap split: `2025-05-18 05:20:00`

## Sleeves

| name | panel | full | OOS | DD | roll12 | trades | trades/year | fee10 OOS | fee10 DD | years W/L/F |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ETH_v22_moirai_full2600d | panel_a_standalone_full_available | 42285.03% | 629.72% | -51.12% | -23.48% | 236 | 35.13 | 544.59% | -51.44% | 8/0/0 |
| BTC_core_donchian_full1300d | panel_a_standalone_full_available | 88.32% | 40.19% | -63.19% | -43.54% | 396 | 111.26 | 20.35% | -66.23% | 3/2/0 |
| ETH_v22_moirai_overlap | panel_b_eth_btc_overlap | 1278.01% | 229.87% | -35.98% | 7.57% | 137 | 38.49 | 206.85% | -37.05% | 4/1/0 |
| BTC_core_donchian_overlap | panel_b_eth_btc_overlap | 88.70% | 40.64% | -63.19% | -43.54% | 396 | 111.26 | 20.74% | -66.23% | 3/2/0 |

## Correlation

| daily | roll90 mean | roll90 max | roll180 mean | roll180 max | monthly |
|---:|---:|---:|---:|---:|---:|
| 0.25 | 0.21 | 0.73 | 0.20 | 0.56 | 0.27 |

## DD Overlap

| anchor | other | start | end | anchor DD | other return | DD overlap | worst30 overlap | worst90 overlap |
|---|---|---|---|---:|---:|---:|---:|---:|
| ETH_v22_moirai_overlap | BTC_core_donchian_overlap | 2024-03-11 00:00:00 | 2024-09-17 00:00:00 | -34.05% | -42.43% | 0.82 | 0.67 | 0.92 |
| BTC_core_donchian_overlap | ETH_v22_moirai_overlap | 2024-02-16 00:00:00 | 2024-06-29 00:00:00 | -62.13% | 10.68% | 0.82 | 0.67 | 0.92 |

## Top20 Overlap

| ETH overlap count | BTC overlap count | month overlap | quarter overlap | ETH pnl overlap | BTC pnl overlap |
|---:|---:|---:|---:|---:|---:|
| 9 | 12 | 10 | 9 | 66.53% | 65.83% |

## Fixed Combos

| combo | full | OOS | DD | DD improve | roll12 | worst90 | fee10 OOS | fee10 DD | OOS gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| combo_80_20_ETH_BTC | 983.04% | 185.58% | -35.42% | 1.58% | 1.92% | -28.09% | 161.73% | -36.43% | True |
| combo_70_30_ETH_BTC | 811.27% | 163.90% | -37.37% | -3.86% | -1.45% | -30.31% | 139.95% | -38.66% | False |
| combo_60_40_ETH_BTC | 656.81% | 142.95% | -39.49% | -9.74% | -5.14% | -32.80% | 119.15% | -41.10% | False |

## Stage Gates

| combo | BTC sleeve | DD >=15% | OOS kept | roll12 better | ETH DD BTC ok | top20 misaligned | fee10 stable | observe | pass |
|---|---|---|---|---|---|---|---|---|---|
| combo_80_20_ETH_BTC | True | False | True | False | False | False | True | False | False |
| combo_70_30_ETH_BTC | True | False | False | False | False | False | True | False | False |
| combo_60_40_ETH_BTC | True | False | False | False | False | False | True | False | False |

## Read

- ETH same-window OOS baseline is `229.87%` with DD `-35.98%`.
- Final verdict is based only on Panel B overlap metrics, not ETH 2600d full-window numbers.
- If REJECT: do not proceed to BTC shadow sleeve.
- If SHADOW_CANDIDATE: next step is independent BTC shadow sleeve only, not live capital allocation and not signal ensemble.

## Evidence

- sleeves: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_sleeves.csv`
- correlations: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_correlations.csv`
- DD overlap: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_dd_overlap.csv`
- top winners: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_top_winners.csv`
- top overlap: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_top_overlap.csv`
- combos: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_combos.csv`
- stage gates: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic_stage_gates.csv`
- json: `research_workspace\diagnostics\exp_0137_cross_asset_eth_btc_overlap_diagnostic.json`
