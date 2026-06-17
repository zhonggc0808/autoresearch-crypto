# MAE/MFE Diagnostic Report

Generated: 2026-06-17T23:28:50.101893
Checkpoint: checkpoints/channel_breakout_375_432.pt
Data: data\crypto\ETHUSDT_5m_2600d.parquet
OOS: 2024-06-06 14:25:00 to 2026-06-12 02:55:00
Git commit: 845a3f4
Script version: 2026-06-17.mae_mfe.v2
Fee/slippage: commission=0.0002, slippage=0.0002
Closed trades: 217

## Q1: Big Winners MAE

| Threshold | Count | Share |
|---|---:|---:|
| MAE < -4% | 0 | 0.0% |
| MAE < -5% | 0 | 0.0% |
| MAE < -6% | 0 | 0.0% |
| MAE < -7% | 0 | 0.0% |

Sample: 21 big winners

## Q2: Big Losers MAE

| Threshold | Count | Share |
|---|---:|---:|
| MAE < -4% | 18 | 78.3% |
| MAE < -5% | 17 | 73.9% |
| MAE < -6% | 14 | 60.9% |
| MAE < -7% | 12 | 52.2% |

Sample: 23 big losers

## Q3: Big Losers Time To MAE
- Median time_to_mae_bars: 494.00 bars
- Median time_to_mae_hours: 41.17 hours

## Q4: Big Winners Time To MFE
- Median time_to_mfe_bars: 1526.00 bars
- Median time_to_mfe_hours: 127.17 hours

## Q5: Horizon Unrealized PnL
### 48h
- Unrealized < 0%: 53 trades, median final pnl -356.99
- Unrealized < -1%: 38 trades, median final pnl -500.90
- Unrealized < -2%: 25 trades, median final pnl -957.12
- Unrealized < -3%: 16 trades, median final pnl -973.42

### 72h
- Unrealized < 0%: 18 trades, median final pnl -647.53
- Unrealized < -1%: 14 trades, median final pnl -987.23
- Unrealized < -2%: 9 trades, median final pnl -1605.61
- Unrealized < -3%: 7 trades, median final pnl -1781.81

### 96h
- Unrealized < 0%: 10 trades, median final pnl -330.36
- Unrealized < -1%: 5 trades, median final pnl -356.99
- Unrealized < -2%: 3 trades, median final pnl -1605.61
- Unrealized < -3%: 1 trades, median final pnl -5149.16

## Q6: Break-Even Stop Killed Big Winners
- trigger 3.0%, stop 0.0%: 1/21 (4.8%)
- trigger 3.0%, stop 0.5%: 3/21 (14.3%)
- trigger 4.0%, stop 0.0%: 0/21 (0.0%)
- trigger 4.0%, stop 0.5%: 0/21 (0.0%)

## Q7: Top Loss Contribution
- Top 20 losses: 50.7%
- Top 50 losses: 81.4%

## Gate 0
- Big winners MAE < -5%: 0.0%
- Big winners MAE < -7%: 0.0%
- Big losers median time_to_mae_hours: 41.2
- Tight BE killed big winners: 4.8%
