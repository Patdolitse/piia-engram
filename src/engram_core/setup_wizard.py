"""Engram 安装向导 — engram setup / engram doctor 命令入口。"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 旧版 MCP server 名称，迁移时需要清理
LEGACY_SERVER_NAMES = ["piia-pkc", "piia_pkc", "piia-pkc-mcp"]

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
    """将 engram 写入指定工具的 MCP 配置（合并，不覆盖其他工具的配置）。
    同时自动清理已知的旧版 server 名称（piia-pkc 等）。
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_mcp_config(config_path)

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # 清理旧版 server 名称
    removed = [name for name in LEGACY_SERVER_NAMES if name in config["mcpServers"]]
    for name in removed:
        del config["mcpServers"][name]
    if removed:
        print(f"  [migrated] removed legacy server(s): {', '.join(removed)}")

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
    print()
    print("  遇到问题或有建议？欢迎反馈：")
    print("  https://github.com/Patdolitse/engram/issues")
    print("========================================\n")


def auto_migrate() -> None:
    """升级后首次启动时静默迁移旧配置，每个版本只运行一次。

    由 mcp_server.py 在 stdio 模式启动前调用。
    不向 stdout 输出任何内容（避免破坏 MCP 协议）。
    迁移日志写入 ~/.engram/migration.log。
    """
    try:
        import os as _os
        from datetime import datetime

        # 确定数据目录和哨兵文件
        data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
        sentinel = data_dir / ".migrated_version"

        # 读取当前版本
        try:
            from engram_core import __version__ as _ver  # type: ignore[import]
        except Exception:
            return

        # 已迁移过则跳过
        if sentinel.is_file() and sentinel.read_text(encoding="utf-8").strip() == _ver:
            return

        # 扫描所有工具配置，清理旧版名称
        log_lines: list[str] = []
        for _tool_id, cfg in _tool_configs().items():
            for config_path in cfg["config_paths"]:
                if not config_path.is_file():
                    continue
                config = _read_mcp_config(config_path)
                servers = config.get("mcpServers", {})
                stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
                if not stale:
                    continue
                for name in stale:
                    del servers[name]
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                log_lines.append(f"  {config_path}: removed {stale}")

        # 写哨兵（无论是否有迁移，都标记当前版本已处理过）
        data_dir.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(_ver, encoding="utf-8")

        # 写迁移日志（仅在有实际变更时）
        if log_lines:
            log_file = data_dir / "migration.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().isoformat()}] engram v{_ver} migration:\n")
                for line in log_lines:
                    f.write(line + "\n")
                f.write("  Restart affected AI tools to apply changes.\n")

    except Exception:
        pass  # 迁移失败不能导致 MCP server 崩溃


def run_doctor(fix: bool = False) -> int:
    """扫描所有已知配置文件，检查旧版 server 名称和失效路径。

    Args:
        fix: True 时自动修复发现的问题。

    Returns:
        发现的问题数量（0 = 健康）。
    """
    print("\n========================================")
    print("  Engram Doctor - Config Health Check")
    print("========================================\n")

    issues: list[tuple[Path, str]] = []  # (config_path, 描述)

    for tool_id, cfg in _tool_configs().items():
        for config_path in cfg["config_paths"]:
            if not config_path.is_file():
                continue
            config = _read_mcp_config(config_path)
            servers = config.get("mcpServers", {})

            # 检查旧版名称
            stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
            if stale:
                issues.append((config_path, f"包含旧版 server: {', '.join(stale)}"))

            # 检查 engram 条目路径是否有效
            engram_entry = servers.get("engram", {})
            if engram_entry:
                python_exe = engram_entry.get("command", "")
                server_script = (engram_entry.get("args") or [""])[0]
                if python_exe and not Path(python_exe).is_file():
                    issues.append((config_path, f"Python 路径不存在: {python_exe}"))
                if server_script and not Path(server_script).is_file():
                    issues.append((config_path, f"mcp_server.py 路径不存在: {server_script}"))

    if not issues:
        print("  [ok] All configs look healthy.\n")
        return 0

    print(f"  [!] Found {len(issues)} issue(s):\n")
    for path, desc in issues:
        print(f"  {path}")
        print(f"    -> {desc}")
    print()

    if not fix:
        print("  Run 'engram doctor --fix' to auto-repair the issues above.\n")
        return len(issues)

    # 自动修复
    python_path = _find_python()
    mcp_server_path = _find_mcp_server()
    if not python_path or not mcp_server_path:
        print("  [error] Cannot auto-fix: Python 3.10+ or mcp_server.py not found.")
        print("          Run 'engram setup' to complete installation first.\n")
        return len(issues)

    fixed = 0
    seen_paths: set[Path] = set()
    for path, _ in issues:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            _write_mcp_config(path, python_path, mcp_server_path)
            print(f"  [fixed] {path}")
            fixed += 1
        except Exception as exc:
            print(f"  [error] {path}: {exc}")

    remaining = len(issues) - fixed
    print(f"\n  Done: {fixed} config(s) updated. Restart your AI tools to apply changes.\n")
    return remaining


def main() -> None:
    """CLI 入口：engram setup / engram doctor [--fix]"""
    args = sys.argv[1:]
    if not args or args[0] == "setup":
        run_setup()
    elif args[0] == "doctor":
        fix = "--fix" in args
        sys.exit(run_doctor(fix=fix))
    elif args[0] == "stats":
        from engram_core.stats import run_stats
        run_stats()
    else:
        print("Engram CLI\n\nUsage:\n  engram setup            Interactive install wizard\n  engram doctor           Check config health (all AI tools)\n  engram doctor --fix     Auto-repair any issues found\n  engram stats            Show project growth metrics\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
