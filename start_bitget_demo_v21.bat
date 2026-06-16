@echo off
title ETH v2.1 Balanced - Bitget Demo
cd /d "C:\Users\Public\Documents\aiproject\autoresearch-crypto"

echo ============================================
echo  ETH ChannelBreakout v2.1 Balanced
echo  Bitget Demo Sandbox (4500 USDT, 2x)
echo ============================================
echo.

set "UV_EXE=C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe"
if not exist "%UV_EXE%" (
    echo [ERROR] uv.exe not found at %UV_EXE%
    pause
    exit /b 1
)

echo Starting...
echo.
"%UV_EXE%" run python live_bitget_quant.py --symbol ETHUSDT --interval 5m --demo --capital 4500 --leverage 2 --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt
pause
