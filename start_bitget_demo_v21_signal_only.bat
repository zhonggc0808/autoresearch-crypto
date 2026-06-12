@echo off
echo ============================================
echo  ETH ChannelBreakout v2.1 Balanced
echo  Bitget Demo -- SIGNAL ONLY (no trading)
echo ============================================
cd /d %~dp0
uv run python live_bitget_quant.py ^
  --symbol ETHUSDT ^
  --interval 5m ^
  --demo ^
  --signal-only ^
  --once ^
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt
pause
