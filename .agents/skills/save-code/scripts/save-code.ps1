[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [string]$Remote = "origin",

    [string]$Branch = "",

    [string[]]$Paths = @(),

    [switch]$DryRun,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AdditionalPaths = @()
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

if ($AdditionalPaths.Count -gt 0) {
    $Paths = @($Paths) + @($AdditionalPaths)
}

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }

    return @($output)
}

function Get-WorktreeChangedPaths {
    $lines = Get-GitOutput -Arguments @("status", "--porcelain=v1", "--untracked-files=all")
    $paths = New-Object System.Collections.Generic.List[string]

    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -lt 4) {
            continue
        }

        $pathPart = $line.Substring(3).Trim()
        if ($pathPart -match " -> ") {
            foreach ($part in ($pathPart -split " -> ")) {
                $paths.Add($part.Trim('"'))
            }
        }
        else {
            $paths.Add($pathPart.Trim('"'))
        }
    }

    return @($paths | Sort-Object -Unique)
}

function Normalize-RepoPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $normalized = $Path.Replace("\", "/").Trim()
    while ($normalized.StartsWith("./")) {
        $normalized = $normalized.Substring(2)
    }

    return $normalized
}

function Get-HighRiskMatches {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CandidatePaths
    )

    $riskItems = [System.Collections.ArrayList]::new()

    foreach ($candidatePath in $CandidatePaths) {
        if ([string]::IsNullOrWhiteSpace($candidatePath)) {
            continue
        }

        $normalized = Normalize-RepoPath -Path $candidatePath
        $reason = $null

        if ($normalized -match '(^|/)live_[^/]*_quant\.py$') {
            $reason = "live trading entry point"
        }
        elseif ($normalized -match '^dex/live(/|$)') {
            $reason = "shared live runtime"
        }
        elseif ($normalized -match '^configs/live(/|$)') {
            $reason = "live config"
        }
        elseif ($normalized -match '^checkpoints(/|$)') {
            $reason = "checkpoint"
        }
        elseif ($normalized -match '^\.env') {
            $reason = "environment or credential file"
        }
        elseif ($normalized -eq "dex/strategies/channel_breakout.py") {
            $reason = "protected ChannelBreakout strategy"
        }
        elseif ($normalized -eq "scripts/research_oracle.py") {
            $reason = "protected research oracle"
        }

        if ($null -ne $reason) {
            [void]$riskItems.Add([pscustomobject]@{
                Path = $normalized
                Reason = $reason
            })
        }
    }

    return @($riskItems)
}

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)
    Write-Host ""
    Write-Host "== $Title =="
}

$insideWorkTree = (Get-GitOutput -Arguments @("rev-parse", "--is-inside-work-tree") | Select-Object -First 1).Trim()
if ($insideWorkTree -ne "true") {
    throw "Not inside a Git worktree."
}

$prefix = (Get-GitOutput -Arguments @("rev-parse", "--show-prefix") | Select-Object -First 1)
if (-not [string]::IsNullOrEmpty($prefix)) {
    $topLevel = (Get-GitOutput -Arguments @("rev-parse", "--show-toplevel") | Select-Object -First 1).Trim()
    throw "Run this script from the repository root: $topLevel"
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
    $currentBranch = Get-GitOutput -Arguments @("branch", "--show-current") | Select-Object -First 1
    if ($null -ne $currentBranch) {
        $Branch = $currentBranch.Trim()
    }
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
    throw "Current branch could not be determined. Refusing to push from a detached HEAD."
}

Invoke-GitChecked -Arguments @("remote", "get-url", $Remote) | Out-Null

Write-Section -Title "Git status"
$statusShort = @(Get-GitOutput -Arguments @("status", "--short", "--untracked-files=all"))
if ($statusShort.Count -eq 0) {
    Write-Host "(clean)"
}
else {
    $statusShort | ForEach-Object { Write-Host $_ }
}

$hasExplicitPaths = $null -ne $Paths -and $Paths.Count -gt 0
if ($hasExplicitPaths) {
    $candidatePaths = @($Paths)
}
else {
    $candidatePaths = @(Get-WorktreeChangedPaths)
}

Write-Section -Title "Selected scope"
if ($candidatePaths.Count -eq 0) {
    Write-Host "(no changed paths)"
}
else {
    $candidatePaths | ForEach-Object { Write-Host $_ }
}

if (-not $hasExplicitPaths) {
    if ($DryRun) {
        Write-Host "[DRY RUN] No -Paths supplied; would require interactive confirmation before staging all current changes."
    }
    else {
        $confirmation = Read-Host "No -Paths supplied. Type STAGE ALL CHANGES to stage every current change"
        if ($confirmation -ne "STAGE ALL CHANGES") {
            throw "Scope confirmation failed. No files were staged."
        }
    }
}

$highRiskMatches = @(Get-HighRiskMatches -CandidatePaths $candidatePaths)
if ($highRiskMatches.Count -gt 0) {
    Write-Section -Title "High-risk paths detected"
    foreach ($riskItem in $highRiskMatches) {
        Write-Host "$($riskItem.Path) [$($riskItem.Reason)]"
    }

    if ($DryRun) {
        Write-Host "[DRY RUN] Would require exact confirmation phrase: APPROVE LIVE-RISK SAVE"
    }
    else {
        $approval = Read-Host "Type APPROVE LIVE-RISK SAVE to continue"
        if ($approval -ne "APPROVE LIVE-RISK SAVE") {
            throw "Live-risk approval phrase did not match. No files were staged."
        }
    }
}
else {
    Write-Section -Title "High-risk paths"
    Write-Host "(none detected in selected scope)"
}

$pushTarget = "$Remote/$Branch"

if ($DryRun) {
    Write-Section -Title "Dry run"
    Write-Host "Would stage: $(if ($hasExplicitPaths) { ($Paths -join ', ') } else { 'all current changes' })"
    Write-Host "Would commit with message: $Message"
    Write-Host "Would push to: $pushTarget"
    Write-Host "Would archive committed HEAD to: ..\autoresearch-crypto-runtime-YYYYMMDD-HHMMSS-<shortsha>.tar.gz"
    Write-Host "Dry run complete. No commit, push, or archive was created."
    exit 0
}

Write-Section -Title "Stage"
if ($hasExplicitPaths) {
    $addArgs = @("add", "--") + $Paths
    Invoke-GitChecked -Arguments $addArgs
}
else {
    Invoke-GitChecked -Arguments @("add", "--all")
}

Write-Section -Title "Staged diff"
Invoke-GitChecked -Arguments @("diff", "--cached", "--name-status")

& git diff --cached --quiet --exit-code
$diffExitCode = $LASTEXITCODE
if ($diffExitCode -eq 0) {
    throw "Staged diff is empty. Nothing to commit."
}
elseif ($diffExitCode -ne 1) {
    throw "git diff --cached --quiet failed with exit code $diffExitCode"
}

Write-Section -Title "Commit"
Invoke-GitChecked -Arguments @("commit", "-m", $Message)

$commitSha = (Get-GitOutput -Arguments @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
$shortSha = (Get-GitOutput -Arguments @("rev-parse", "--short", "HEAD") | Select-Object -First 1).Trim()

Write-Section -Title "Push"
Invoke-GitChecked -Arguments @("push", $Remote, $Branch)

Write-Section -Title "Archive"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$parentDir = Split-Path -Parent (Get-Location).Path
$archiveName = "autoresearch-crypto-runtime-$timestamp-$shortSha.tar.gz"
$archivePath = Join-Path $parentDir $archiveName
Invoke-GitChecked -Arguments @("archive", "--format=tar.gz", "--output", $archivePath, "HEAD")

Write-Section -Title "Saved"
Write-Host "Commit SHA: $commitSha"
Write-Host "Push target: $pushTarget"
Write-Host "Archive path: $archivePath"
