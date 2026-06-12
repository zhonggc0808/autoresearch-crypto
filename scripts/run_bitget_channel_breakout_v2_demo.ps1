[CmdletBinding()]
param(
    [string]$Symbol = "ETH/USDT:USDT",
    [string]$Interval = "5m",
    [double]$Capital = 100.0,
    [double]$Leverage = 5.0,
    [ValidateSet("cross", "isolated")]
    [string]$MarginMode = "cross",
    [switch]$Once,
    [switch]$SignalOnly,
    [string]$NotifyEmailTo = ""
)

$ErrorActionPreference = "Stop"
$Culture = [System.Globalization.CultureInfo]::InvariantCulture

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

$Checkpoint = Join-Path $ProjectRoot "checkpoints\channel_breakout_375_432.pt"
if (-not (Test-Path $Checkpoint)) {
    throw "Missing checkpoint: $Checkpoint"
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($UvCommand) {
    $UvPath = $UvCommand.Source
} else {
    $UvPath = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\Scripts\uv.exe"
    if (-not (Test-Path $UvPath)) {
        throw "uv was not found in PATH or at $UvPath"
    }
}

$Notional = $Capital * $Leverage
$ArgsList = @(
    "run",
    "python",
    "live_bitget_quant.py",
    "--symbol", $Symbol,
    "--interval", $Interval,
    "--demo",
    "--checkpoint", $Checkpoint,
    "--capital", $Capital.ToString("0.####", $Culture),
    "--leverage", $Leverage.ToString("0.####", $Culture)
)

if ($Once) {
    $ArgsList += "--once"
}
if ($SignalOnly) {
    Write-Host "Bitget 暂不支持 --signal-only 模式，此脚本不支持该参数" -ForegroundColor Yellow
    exit 1
}
if ($NotifyEmailTo) {
    $ArgsList += @("--notify-email-to", $NotifyEmailTo)
}

Write-Host "Bitget Sandbox ChannelBreakout v2"
Write-Host "Symbol:   $Symbol"
Write-Host "Interval: $Interval"
Write-Host "Capital:  $($Capital.ToString("0.####", $Culture)) USDT"
Write-Host "Leverage: $($Leverage.ToString("0.####", $Culture))x"
Write-Host "Notional: $($Notional.ToString("0.####", $Culture)) USDT"
Write-Host "Mode:     Bitget Sandbox (demo trading)"
Write-Host "Checkpoint: $Checkpoint"
Write-Host ""
Write-Host "注意: Bitget K线单次限制200根，长窗口策略首次拉取较慢" -ForegroundColor Yellow
Write-Host ""

& $UvPath @ArgsList
exit $LASTEXITCODE
