param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [string]$Scenario = ''
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($WorkRoot)
if (Test-Path -LiteralPath $root) { throw 'WorkRoot must be a new directory' }
New-Item -ItemType Directory -Path $root | Out-Null
Get-ChildItem Env: | Where-Object { $_.Name -match '^(ENGRAM_|CURSOR_|CLAUDE_|CODEX_|ANTHROPIC_|OPENAI_|GIT_)' } | ForEach-Object {
    Remove-Item -LiteralPath ('Env:' + $_.Name)
}
foreach ($name in @('ENGRAM_DIR','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','CLAUDE_CONFIG_DIR',
    'CODEX_HOME','TEMP','TMP','TMPDIR','ENGRAM_CACHE_DIR','XDG_CACHE_HOME','XDG_CONFIG_HOME')) {
    $directory = Join-Path $root $name.ToLowerInvariant()
    New-Item -ItemType Directory -Path $directory | Out-Null
    [Environment]::SetEnvironmentVariable($name, $directory, 'Process')
}
$env:DO_NOT_TRACK = '1'
$env:ENGRAM_NO_UPDATE_CHECK = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path (Split-Path $PSScriptRoot -Parent) 'src'
$env:GIT_CONFIG_NOSYSTEM = '1'
$env:GIT_CONFIG_GLOBAL = Join-Path $env:HOME 'gitconfig'
$env:GIT_TERMINAL_PROMPT = '0'
$arguments = @((Join-Path $PSScriptRoot 'continuity_matrix.py'), '--work-dir', (Join-Path $root 'fixtures'),
    '--output', (Join-Path $root 'results.json'))
if ($Scenario) { $arguments += @('--scenario', $Scenario) }
& $Python @arguments
exit $LASTEXITCODE
