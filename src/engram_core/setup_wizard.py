"""Engram 安装向导 — engram setup 命令入口。"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 工具检测配置
# ---------------------------------------------------------------------------

def _tool_configs() -> dict:
    """返回各工具的配置路径（运行时构建，确保 Path.home() 正确）。"""
    home = Path.home()
    is_mac = platform.system() == "Darwin"
    is_win = platform.system() == "Windows"

    return {
        "claude_code": {
            "name": "Claude Code",
            "config_paths": [home / ".claude" / ".mcp.json"],
        },
        "cursor": {
            "name": "Cursor",
            "config_paths": [home / ".cursor" / "mcp.json"],
        },
        "claude_desktop": {
            "name": "Claude Desktop",
            "config_paths": (
                [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
                if is_mac
                else [Path(os.environ.get("APPDATA", home)) / "Claude" / "claude_desktop_config.json"]
                if is_win
                else []
            ),
        },
    }


# ---------------------------------------------------------------------------
# 辅助函数（可单独测试）
# ---------------------------------------------------------------------------

def _find_python() -> str | None:
    """找到可用的 Python 3.10+ 可执行路径。优先用当前 Python。"""
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/opt/homebrew/bin/python3",    # Mac Apple Silicon Homebrew
        "/usr/local/bin/python3",        # Mac Intel Homebrew
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [
                    candidate, "-c",
                    "import sys; assert sys.version_info >= (3, 10); print(sys.executable)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue
    return None


def _find_mcp_server() -> str | None:
    """找到已安装的 mcp_server.py 绝对路径。"""
    spec = importlib.util.find_spec("engram_core")
    if spec and spec.origin:
        path = Path(spec.origin).parent / "mcp_server.py"
        if path.is_file():
            return str(path)
    return None


def _detect_tools() -> list[dict]:
    """检测已安装的 AI 工具，返回可配置的工具列表。"""
    detected = []
    for tool_id, cfg in _tool_configs().items():
        for config_path in cfg["config_paths"]:
            # 配置文件已存在，或父目录存在（工具已装但未配置 MCP）
            if config_path.exists() or config_path.parent.exists():
                detected.append(
                    {
                        "id": tool_id,
                        "name": cfg["name"],
                        "config_path": config_path,
                    }
                )
                break
    return detected


def _read_mcp_config(config_path: Path) -> dict:
    """读取现有 MCP 配置，不存在或解析失败时返回空结构。"""
    if config_path.is_file():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_mcp_config(
    config_path: Path,
    python_path: str,
    mcp_server_path: str,
    data_dir: str | None = None,
) -> None:
    """将 engram 写入指定工具的 MCP 配置（合并，不覆盖其他工具的配置）。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_mcp_config(config_path)

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    entry: dict = {
        "command": python_path,
        "args": [mcp_server_path],
    }
    if data_dir:
        entry["env"] = {"ENGRAM_DIR": data_dir}

    config["mcpServers"]["engram"] = entry

    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 向导交互
# ---------------------------------------------------------------------------

def _prompt(message: str, default: str = "") -> str:
    """带默认值的输入提示。Ctrl+C 或 EOF 时退出。"""
    display = f"{message} [{default}]: " if default else f"{message}: "
    try:
        value = input(display).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value if value else default


def _yn(message: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _prompt(f"{message} [{hint}]").lower()
    if not answer:
        return default
    return answer.startswith("y")


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------

def run_setup() -> None:
    """交互式安装向导主流程。"""
    print("\n========================================")
    print("  Engram 安装向导")
    print("========================================\n")

    # Step 1 — Python 检测
    print("Step 1/3 — 检测 Python")
    python_path = _find_python()
    if not python_path:
        print("❌ 未找到可用的 Python 3.10+。")
        print("   请安装 Python 后重新运行：https://python.org/downloads/")
        sys.exit(1)
    print(f"  ✅ Python: {python_path}\n")

    # mcp_server.py 路径
    mcp_server_path = _find_mcp_server()
    if not mcp_server_path:
        print("❌ 未找到 mcp_server.py，请确认 engram 已正确安装（pip install engram）。")
        sys.exit(1)

    # Step 2 — 数据目录
    print("Step 2/3 — 数据目录")
    default_data_dir = str(Path.home() / ".engram")
    print(f"  知识库默认存储位置: {default_data_dir}")
    custom_dir = _prompt("  自定义路径（直接回车使用默认）", "")
    data_dir: str | None = None
    if custom_dir:
        data_dir = str(Path(custom_dir).expanduser().resolve())
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        print(f"  ✅ 将使用: {data_dir}")
    else:
        print(f"  ✅ 将使用: {default_data_dir}")
    print()

    # Step 3 — 工具检测与配置
    print("Step 3/3 — 配置 AI 工具")
    tools = _detect_tools()
    if not tools:
        print("  ⚠️  未检测到支持 MCP 的 AI 工具（Claude Code / Cursor / Claude Desktop）。")
        print("  安装工具后重新运行 'engram setup' 即可完成配置。\n")
    else:
        for tool in tools:
            print(f"  ✅ 检测到 {tool['name']}: {tool['config_path']}")
        print()
        if _yn("  为所有检测到的工具配置 Engram？"):
            success = []
            failed = []
            for tool in tools:
                try:
                    _write_mcp_config(
                        tool["config_path"],
                        python_path,
                        mcp_server_path,
                        data_dir,
                    )
                    success.append(tool["name"])
                except Exception as exc:
                    failed.append(f"{tool['name']} ({exc})")
            print()
            for name in success:
                print(f"  ✅ {name} 已配置")
            for name in failed:
                print(f"  ❌ {name} 配置失败")

    # 完成
    print("\n========================================")
    print("  Engram 安装完成！")
    print("  重启你的 AI 工具（Claude Code / Cursor）即可使用。")
    print("  AI 对话开始时会自动调用 get_user_context 加载你的身份。")
    print("========================================\n")


def main() -> None:
    """CLI 入口：engram setup 或 engram（无参数时显示帮助）。"""
    args = sys.argv[1:]
    if not args or args[0] == "setup":
        run_setup()
    else:
        print("Engram CLI\n\n用法: engram setup\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
