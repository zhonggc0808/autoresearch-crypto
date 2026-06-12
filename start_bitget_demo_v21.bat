@echo off
title ETH v2.1 Balanced - Bitget Demo
cd /d "D:\aiproject2\autoresearch-crypto"

echo ============================================
echo  ETH ChannelBreakout v2.1 Balanced
echo  Bitget Demo Sandbox (500 USDT, 1x)
echo ============================================
echo.

where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uv not found in PATH
    echo Try running from PowerShell: uv run python live_bitget_quant.py ...
    pause
    exit /b 1
)

echo Starting...
echo.
uv run python live_bitget_quant.py --symbol ETHUSDT --interval 5m --demo --capital 500 --leverage 1 --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt
pause
