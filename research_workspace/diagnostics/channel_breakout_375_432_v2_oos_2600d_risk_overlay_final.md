# Risk Overlay Final Report

Generated: 2026-06-17T23:31:39.288281
Checkpoint: checkpoints/channel_breakout_375_432.pt
Data: data\crypto\ETHUSDT_5m_2600d.parquet
OOS: 2024-06-06 14:25:00 to 2026-06-12 02:55:00
Git commit: 845a3f4
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

Recommendation: combo_balanced.
Promotion still requires human review of Gate 0/1 assumptions before production use.
