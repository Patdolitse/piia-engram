#!/usr/bin/env bash
# Engram 一键安装脚本 (Mac/Linux)
# 用法: curl -fsSL https://raw.githubusercontent.com/Patdolitse/engram/main/install.sh | bash
set -e

echo ""
echo "========================================"
echo "  Engram 安装程序 (Mac/Linux)"
echo "========================================"
echo ""

# 检测 Python 3.10+
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || true)
        if [ "$version" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python 3.10+。"
    echo ""
    if command -v brew &>/dev/null; then
        echo "  请运行: brew install python"
    else
        echo "  请访问: https://python.org/downloads/"
    fi
    exit 1
fi

echo "✅ Python: $($PYTHON --version)"
echo ""
echo "正在安装 Engram..."
$PYTHON -m pip install --upgrade piia-engram

echo ""
$PYTHON -m engram_core.setup_wizard setup
