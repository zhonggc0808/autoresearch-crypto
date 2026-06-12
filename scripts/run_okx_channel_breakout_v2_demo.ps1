[CmdletBinding()]
param(
    [string]$Symbol = "ETH-USDT-SWAP",
    [string]$Interval = "5m",
    [double]$Capital = 4500.0,
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
    "live_okx_quant.py",
    "--symbol", $Symbol,
    "--interval", $Interval,
    "--demo",
    "--strategy-profile", "channel_breakout_v2",
    "--checkpoint", $Checkpoint,
    "--capital", $Capital.ToString("0.####", $Culture),
    "--leverage", $Leverage.ToString("0.####", $Culture),
    "--margin-mode", $MarginMode
)

if ($Once) {
    $ArgsList += "--once"
}
if ($SignalOnly) {
    $ArgsList += "--signal-only"
}
if ($NotifyEmailTo) {
    $ArgsList += @("--notify-email-to", $NotifyEmailTo)
}

Write-Host "OKX demo ChannelBreakout v2"
Write-Host "Symbol:   $Symbol"
Write-Host "Interval: $Interval"
Write-Host "Capital:  $($Capital.ToString("0.####", $Culture)) USDT"
Write-Host "Leverage: $($Leverage.ToString("0.####", $Culture))x"
Write-Host "Notional: $($Notional.ToString("0.####", $Culture)) USDT"
Write-Host "Mode:     Demo trading"
Write-Host "Profile:  channel_breakout_v2"
Write-Host "Checkpoint: $Checkpoint"

& $UvPath @ArgsList
exit $LASTEXITCODE
