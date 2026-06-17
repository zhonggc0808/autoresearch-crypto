[CmdletBinding()]
param(
    [string]$Symbol = "ETHUSDT",
    [string]$Interval = "5m",
    [double]$Capital = 4500.0,
    [double]$Leverage = 2.0,
    [switch]$Once,
    [switch]$SignalOnly,
    [switch]$DisableExitOverlays,
    [string]$NotifyEmailTo = ""
)

$ErrorActionPreference = "Stop"
$Culture = [System.Globalization.CultureInfo]::InvariantCulture

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

$Checkpoint = Join-Path $ProjectRoot "checkpoints\channel_breakout_v2_2_mtg_bcd.json"
if (-not (Test-Path $Checkpoint)) {
    throw "Missing checkpoint profile: $Checkpoint"
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
    "--strategy-profile", "channel_breakout_v2_2_mtg_bcd",
    "--checkpoint", $Checkpoint,
    "--capital", $Capital.ToString("0.####", $Culture),
    "--leverage", $Leverage.ToString("0.####", $Culture)
)

if ($Once) {
    $ArgsList += "--once"
}
if ($SignalOnly) {
    $ArgsList += "--signal-only"
}
if ($DisableExitOverlays) {
    $ArgsList += "--disable-exit-overlays"
}
if ($NotifyEmailTo) {
    $ArgsList += @("--notify-email-to", $NotifyEmailTo)
}

Write-Host "Bitget Sandbox ChannelBreakout v2.2 MTG+BCD"
Write-Host "Symbol:   $Symbol"
Write-Host "Interval: $Interval"
Write-Host "Capital:  $($Capital.ToString("0.####", $Culture)) USDT"
Write-Host "Leverage: $($Leverage.ToString("0.####", $Culture))x"
Write-Host "Notional: $($Notional.ToString("0.####", $Culture)) USDT"
Write-Host "Mode:     Bitget Sandbox demo"
Write-Host "Profile:  channel_breakout_v2_2_mtg_bcd"
Write-Host "Overlays: $(-not $DisableExitOverlays)"

& $UvPath @ArgsList
exit $LASTEXITCODE
