param(
    [string]$Password,
    [switch]$Generate,
    [switch]$Apply,
    [string]$WorkerDir = "worker",
    [string]$ConfigHome = "",
    [int]$Bytes = 24
)

$ErrorActionPreference = "Stop"

function New-DashboardPassword {
    param([int]$LengthBytes)

    $buffer = New-Object byte[] $LengthBytes
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try {
        $rng.GetBytes($buffer)
    } finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($buffer).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

if (-not $Password) {
    if (-not $Generate) {
        throw "Provide -Password or use -Generate."
    }
    $Password = New-DashboardPassword -LengthBytes $Bytes
}

if ($Password.Length -lt 20) {
    throw "Dashboard password must be at least 20 characters."
}

if ($Password -notmatch '^[A-Za-z0-9_-]+$') {
    throw "Dashboard password may only contain letters, numbers, underscore, and hyphen."
}

Write-Host "New DASH_PASSWORD:"
Write-Host $Password
Write-Host ""
Write-Host "Owner handoff: record this password in the private credential store before applying it."

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry run only. Re-run with -Apply to write Cloudflare Worker secret DASH_PASSWORD."
    exit 0
}

if ($ConfigHome) {
    $env:XDG_CONFIG_HOME = $ConfigHome
}

$resolvedWorkerDir = Resolve-Path -LiteralPath $WorkerDir
Push-Location $resolvedWorkerDir
try {
    $cmd = "<NUL set /p dummy=$Password | npx wrangler secret put DASH_PASSWORD"
    cmd.exe /c $cmd
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "DASH_PASSWORD rotation submitted to Wrangler. Existing dashboard sessions are invalidated."
