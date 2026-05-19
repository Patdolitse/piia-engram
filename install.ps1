# Engram 一键安装脚本 (Windows PowerShell)
# 用法: irm https://raw.githubusercontent.com/Patdolitse/engram/main/install.ps1 | iex

Write-Host ""
Write-Host "========================================"
Write-Host "  Engram 安装程序 (Windows)"
Write-Host "========================================"
Write-Host ""

# 按优先级查找 Python 3.10+
$pythonCandidates = @(
    (Get-Command python3 -ErrorAction SilentlyContinue)?.Source,
    (Get-Command python -ErrorAction SilentlyContinue)?.Source,
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    # Codex App 捆绑 Python（fallback）
    "E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path $candidate -ErrorAction SilentlyContinue)) {
        $ver = & $candidate -c "import sys; print(sys.version_info >= (3,10))" 2>$null
        if ($ver -eq "True") {
            $python = $candidate
            break
        }
    }
}

if (-not $python) {
    Write-Host "❌ 未找到 Python 3.10+。"
    Write-Host "   请访问 https://python.org/downloads/ 安装 Python。"
    exit 1
}

Write-Host "✅ Python: $python"
Write-Host ""
Write-Host "正在安装 Engram..."
& $python -m pip install --upgrade engram

Write-Host ""
& $python -m engram_core.setup_wizard setup
