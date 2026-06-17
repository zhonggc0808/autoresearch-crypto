# Risk Overlay Final Report

Generated: 2026-06-17T23:51:21.036975
Checkpoint: checkpoints/channel_breakout_375_432.pt
Data: data\crypto\ETHUSDT_5m_2600d.parquet
OOS: 2024-06-06 14:25:00 to 2026-06-12 02:55:00
Git commit: 6361ad1
Script version: 2026-06-17.risk_overlay.v1
Fee/slippage: commission=0.0002, slippage=0.0002
MC simulations: 2000
MC seed: 2202
MC block bars: 288

Stage A rows: 44
Stage B rows: 10
Stage C rows: 3

## Stage C

| Config | Return | MaxDD | MC DD<-30% | MC loss |
|---|---:|---:|---:|---:|
| combo_conservative | 93.2% | -13.6% | 4.3% | 2.4% |
| combo_balanced | 102.7% | -14.2% | 5.5% | 2.1% |
| combo_full | 93.3% | -13.9% | 5.5% | 3.0% |

## Tail Loss

| Config | Top 20 loss share | Top 50 loss share |
|---|---:|---:|
| combo_conservative | 32.0% | 65.3% |
| combo_balanced | 32.3% | 65.3% |
| combo_full | 32.1% | 64.6% |

## Damaged Winners

| Config | Damaged / Big Winners | Share |
|---|---:|---:|
| combo_conservative | 1 / 21 | 4.8% |
| combo_balanced | 1 / 21 | 4.8% |
| combo_full | 1 / 21 | 4.8% |

## Gate 0/1 Evidence

- Gate 0 source: `channel_breakout_375_432_v2_oos_2600d_mae_mfe_report.md`.
- Gate 0 registered overlays: adverse partial, time-in-loss, squeeze, and break-even grids; no hard -4% full-stop promoted.
- Gate 1 source: `channel_breakout_375_432_v2_oos_2600d_fixed_size_baseline.md`.
- Gate 1 selected safe=0.400x, balanced=0.425x; 0.450x remains borderline.

## Acceptance Check

| Criterion | Evidence | Status |
|---|---|---|
| Signal unchanged | Overlays use `stop_config` on existing signals only | pass |
| MC DD<-30% reduced | combo_balanced 5.5% vs fixed baseline 11.6% | pass |
| Return/Sharpe preserved | return 102.7%, Sharpe 1.65 | pass |
| Tail loss reduced | top20 32.3% vs 45.9%; top50 65.3% vs 77.9% | pass |
| Damaged big winners <15% | 4.8% | pass |
| Deterministic before MC | Stage A=44, Stage B=10 | pass |
| Event + logical ledger | `event_pnl == logical_pnl` invariant checked | pass |
| Close-fill execution | stop events use current close fill in simulator | pass |

Recommendation: combo_balanced.
Gate 0/1 assumptions are data-reviewed for this research recommendation.
Production promotion still requires human approval.
