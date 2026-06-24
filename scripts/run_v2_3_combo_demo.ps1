[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("okx", "bitget", "binance")]
    [string]$Exchange = "okx",

    [string]$Symbol = "",
    [string]$Interval = "5m",
    [double]$Capital = 0.0,
    [double]$Leverage = 0.0,
    [ValidateSet("cross", "isolated")]
    [string]$MarginMode = "cross",
    [switch]$Once,
    [switch]$SignalOnly,
    [switch]$DisableExitOverlays,
    [string]$NotifyEmailTo = ""
)

$ErrorActionPreference = "Stop"
$Culture = [System.Globalization.CultureInfo]::InvariantCulture
$Profile = "channel_breakout_v2_3_combo_balanced"

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

switch ($Exchange) {
    "okx" {
        if (-not $Symbol) { $Symbol = "ETH-USDT-SWAP" }
        if ($Capital -le 0) { $Capital = 4000.0 }
        if ($Leverage -le 0) { $Leverage = 2.0 }
        $LiveScript = "live_okx_quant.py"
        $Mode = "OKX demo"
    }
    "bitget" {
        if (-not $Symbol) { $Symbol = "ETHUSDT" }
        if ($Capital -le 0) { $Capital = 4000.0 }
        if ($Leverage -le 0) { $Leverage = 2.0 }
        $LiveScript = "live_bitget_quant.py"
        $Mode = "Bitget Sandbox demo"
    }
    "binance" {
        if ($SignalOnly) {
            throw "Binance entrypoint does not support -SignalOnly."
        }
        if (-not $Symbol) { $Symbol = "ETHUSDT" }
        if ($Capital -le 0) { $Capital = 100.0 }
        if ($Leverage -le 0) { $Leverage = 1.0 }
        $LiveScript = "live_binance_quant.py"
        $Mode = "Binance Testnet demo"
    }
}

$Notional = $Capital * $Leverage
$ArgsList = @(
    "run",
    "python",
    $LiveScript,
    "--symbol", $Symbol,
    "--interval", $Interval,
    "--demo",
    "--strategy-profile", $Profile,
    "--capital", $Capital.ToString("0.####", $Culture),
    "--leverage", $Leverage.ToString("0.####", $Culture)
)

if ($Exchange -eq "okx") {
    $ArgsList += @("--margin-mode", $MarginMode)
}
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

Write-Host "ChannelBreakout v2.3 combo_balanced"
Write-Host "Exchange: $Exchange"
Write-Host "Symbol:   $Symbol"
Write-Host "Interval: $Interval"
Write-Host "Capital:  $($Capital.ToString("0.####", $Culture)) USDT"
Write-Host "Leverage: $($Leverage.ToString("0.####", $Culture))x"
Write-Host "Notional: $($Notional.ToString("0.####", $Culture)) USDT"
Write-Host "Mode:     $Mode"
Write-Host "Profile:  $Profile"
Write-Host "Risk:     combo_balanced"

& $UvPath @ArgsList
exit $LASTEXITCODE
