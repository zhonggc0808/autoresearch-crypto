[CmdletBinding()]
param(
    [string]$Symbol = "ETH-USDT-SWAP",
    [string]$Interval = "5m",
    [double]$Capital = 3000.0,
    [double]$Leverage = 3.0,
    [ValidateSet("cross", "isolated")]
    [string]$MarginMode = "cross",
    [switch]$Once,
    [switch]$SignalOnly,
    [switch]$TimesfmGate,
    [switch]$DisableExitOverlays,
    [string]$NotifyEmailTo = ""
)

$ErrorActionPreference = "Stop"
$Culture = [System.Globalization.CultureInfo]::InvariantCulture

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

$Profile = "channel_breakout_v2_2_m375_bbm375_1p5"
$Checkpoint = Join-Path $ProjectRoot "checkpoints\channel_breakout_v2_2_m375_bbm375_1p5.json"
if (-not (Test-Path $Checkpoint)) {
    throw "Missing checkpoint profile: $Checkpoint"
}
$TimesfmCandidate = Join-Path $ProjectRoot "configs\live\moirai2_gate_exp_0093.json"
$TimesfmModel = Join-Path $ProjectRoot "research_workspace\diagnostics\moirai_2_small"
if ($TimesfmGate -and -not (Test-Path $TimesfmCandidate)) {
    throw "Missing forecast gate candidate: $TimesfmCandidate"
}
if ($TimesfmGate -and -not (Test-Path $TimesfmModel)) {
    throw "Missing forecast gate local model: $TimesfmModel"
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
$ArgsList = @("run")
if ($TimesfmGate) {
    $ArgsList += @("--with", "uni2ts")
}
$ArgsList += @(
    "python",
    "live_okx_quant.py",
    "--symbol", $Symbol,
    "--interval", $Interval,
    "--demo",
    "--strategy-profile", $Profile,
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
if ($TimesfmGate) {
    $ArgsList += @(
        "--timesfm-candidate", $TimesfmCandidate,
        "--timesfm-model", $TimesfmModel
    )
}
if ($DisableExitOverlays) {
    $ArgsList += "--disable-exit-overlays"
}
if ($NotifyEmailTo) {
    $ArgsList += @("--notify-email-to", $NotifyEmailTo)
}

Write-Host "OKX demo ChannelBreakout v2.2 M375 + BBM375/1.5"
Write-Host "Symbol:   $Symbol"
Write-Host "Interval: $Interval"
Write-Host "Capital:  $($Capital.ToString("0.####", $Culture)) USDT max configured margin"
Write-Host "Leverage: $($Leverage.ToString("0.####", $Culture))x"
Write-Host "Notional: $($Notional.ToString("0.####", $Culture)) USDT max"
Write-Host "Sizing:   clamp to exchange available USDT with 2% reserve"
Write-Host "Mode:     Demo trading"
Write-Host "Profile:  $Profile"
Write-Host "Overlays: $(-not $DisableExitOverlays)"
Write-Host "Gate:     $(if ($TimesfmGate) { 'Moirai2 exp_0093' } else { 'disabled' })"

& $UvPath @ArgsList
exit $LASTEXITCODE
