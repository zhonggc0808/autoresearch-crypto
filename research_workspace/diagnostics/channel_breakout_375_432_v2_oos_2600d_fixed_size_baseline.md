# Fixed Size Matrix - Baseline Selection

Generated: 2026-06-17T23:29:05.957497
Checkpoint: checkpoints/channel_breakout_375_432.pt
Data: data\crypto\ETHUSDT_5m_2600d.parquet
OOS: 2024-06-06 14:25:00 to 2026-06-12 02:55:00
Git commit: 845a3f4
Script version: 2026-06-17.fixed_size.v2
Fee/slippage: commission=0.0002, slippage=0.0002
MC simulations: 2000
MC block bars: 288

| Size | Return | MaxDD | Sharpe | Trades | MC DD<-30% | MC loss |
|---:|---:|---:|---:|---:|---:|---:|
| 1.000 | 515.5% | -33.0% | 2.13 | 217 | 93.4% | 2.7% |
| 0.500 | 176.0% | -18.2% | 1.87 | 217 | 21.6% | 1.8% |
| 0.475 | 163.8% | -17.3% | 1.86 | 217 | 17.8% | 1.8% |
| 0.450 | 151.9% | -16.5% | 1.84 | 217 | 14.5% | 1.8% |
| 0.425 | 140.4% | -15.7% | 1.83 | 217 | 11.6% | 1.7% |
| 0.400 | 129.4% | -14.8% | 1.81 | 217 | 9.0% | 1.7% |

## Gate 1

- safe_baseline: 0.400x
- balanced_baseline: 0.425x
- aggressive_candidate: 0.450x (borderline; rerun 5000 sims or alternate seed before promotion)
- note: selection uses MC DD<-30% <15% and MC loss <5%
