# exp_0137 cross-asset core Donchian sanity

- research-only; no live/checkpoint/config/oracle/production strategy change
- question: do BTC/SOL core Donchian sleeves clear a minimal standalone sanity gate before a full cross-asset exp0137?
- strategy: core Donchian `m=375`, `min_hold_bars=432`; no Moirai, no BB, no regime split, no MTG/BCD
- data: each asset uses its longest available dated 5m parquet
- execution: completed-bar signal, next 5m open fill
- sanity gate: `max_dd > -85.00%` and `OOS_return > 0`
- verdict: `OBSERVE_READY_FOR_FULL_EXP0137`

## Results

| symbol | data | full | OOS | DD | trades | trades/year | OOS trades | DD gate | OOS > 0 | pass |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| BTCUSDT | 1300d | 88.32% | 40.19% | -63.19% | 396 | 111.26 | 127 | True | True | True |
| SOLUSDT | 730d | 11.70% | 23.41% | -69.35% | 240 | 120.08 | 68 | True | True | True |

## Read

- Both assets must pass before running a full cross-asset exp0137.
- If either asset fails, do not optimize this core Donchian cross-asset line from these parameters.
- This diagnostic does not authorize live/demo routing or checkpoint promotion.

## Evidence

- CSV: `research_workspace\diagnostics\exp_0137_cross_asset_core_donchian_sanity.csv`
- JSON: `research_workspace\diagnostics\exp_0137_cross_asset_core_donchian_sanity.json`
