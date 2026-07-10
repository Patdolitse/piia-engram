param(
    [string]$TaskName = "EngramAnchorSmoke",
    [string]$InstallDir = "",
    [string]$PythonExe = "",
    [string]$RunnerScript = "",
    [Parameter(Mandatory=$true)][string]$RepoRoot,
    [Parameter(Mandatory=$true)][string]$EngramDir,
    [Parameter(Mandatory=$true)][string]$EvidenceDir,
    [string]$At = "10:00"
)

$ErrorActionPreference = "Stop"

function Resolve-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction Stop
    return $cmd.Source
}

function Assert-DurablePython([string]$Path) {
    $lower = $Path.ToLowerInvariant()
    if ($lower.Contains("\.codex\") -or $lower.Contains("\codex-runtimes\")) {
        throw "PythonExe must be a durable Python install, not a Codex runtime path."
    }
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "piia-engram\anchor-live-smoke"
}
if (-not $PythonExe) {
    $PythonExe = Resolve-CommandPath "python"
}
Assert-DurablePython $PythonExe
if (-not $RunnerScript) {
    $RunnerScript = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\run_anchor_live_smoke.py"
}
if (-not (Test-Path -LiteralPath $RunnerScript)) {
    throw "RunnerScript not found."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$backupDir = Join-Path $InstallDir "backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$wrapper = Join-Path $InstallDir "run-anchor-live-smoke.cmd"
if (Test-Path -LiteralPath $wrapper) {
    Copy-Item -LiteralPath $wrapper -Destination (Join-Path $backupDir "run-anchor-live-smoke.$stamp.cmd") -Force
}
$installedRunner = Join-Path $InstallDir "run_anchor_live_smoke.py"
if (Test-Path -LiteralPath $installedRunner) {
    Copy-Item -LiteralPath $installedRunner -Destination (Join-Path $backupDir "run_anchor_live_smoke.$stamp.py") -Force
}
Copy-Item -LiteralPath $RunnerScript -Destination $installedRunner -Force
& $PythonExe $installedRunner --help *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Runner preflight failed with the selected PythonExe."
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath (Join-Path $backupDir "$TaskName.$stamp.xml") -Encoding UTF8
}

$lines = @(
    "@echo off",
    "chcp 65001 >nul",
    ('"{0}" "{1}" --repo-root "{2}" --engram-dir "{3}" --history-dir "{4}" --markdown-log "{5}"' -f $PythonExe, $installedRunner, $RepoRoot, $EngramDir, $EvidenceDir, (Join-Path $EvidenceDir "SMOKE_LOG.md"))
)
Set-Content -LiteralPath $wrapper -Value $lines -Encoding ASCII

$manifest = [ordered]@{
    schema = "anchor_live_smoke_task_install.v1"
    installed_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    task_name = $TaskName
    python_sha256 = Get-FileSha256 $PythonExe
    runner_sha256 = Get-FileSha256 $installedRunner
    wrapper_sha256 = Get-FileSha256 $wrapper
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $InstallDir "manifest.json") -Encoding UTF8

$action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument ("/d /c " + '"' + $wrapper + '"')
$trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Parse($At))
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Output "installed"
