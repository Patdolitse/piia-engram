"""Engram 安装向导 — engram setup / engram doctor 命令入口。"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 旧版 MCP server 名称，迁移时需要清理
LEGACY_SERVER_NAMES = ["piia-pkc", "piia_pkc", "piia-pkc-mcp"]

# ---------------------------------------------------------------------------
# 智能扫描 + 分流导入
# ---------------------------------------------------------------------------

# 用户身份类关键词
_USER_KEYWORDS = re.compile(
    r"(语言|language|中文|english|角色|role|我是|i am|偏好|prefer|always|never"
    r"|禁止|必须|风格|style|tone|沟通|communicate|所有沟通|技术水平|technical.?level"
    r"|工作方式|work.?style|习惯|habit)",
    re.IGNORECASE,
)

# 项目规则类关键词
_PROJECT_KEYWORDS = re.compile(
    r"(这个.?repo|this.?repo|测试|test|build|deploy|ci/cd|ci |cd "
    r"|pre.?commit|hook|lint|tailwind|webpack|vite|docker|makefile"
    r"|\.env|package\.json|tsconfig|eslint|prettier|migration"
    r"|数据库|database|schema|endpoint|路由|route|api)",
    re.IGNORECASE,
)


def _scan_rule_files(cwd: Path | None = None) -> list[dict]:
    """扫描全局和项目级规则文件，返回 [{path, scope, lines}]。

    scope: "global" (全局文件，倾向用户身份) / "project" (项目文件，倾向项目规则)
    """
    home = Path.home()
    current_dir = cwd or Path.cwd()
    found: list[dict] = []

    # 全局文件
    global_candidates = [
        home / ".claude" / "CLAUDE.md",
    ]
    # Cursor 全局规则目录
    cursor_rules_dir = home / ".cursor" / "rules"
    if cursor_rules_dir.is_dir():
        global_candidates.extend(sorted(cursor_rules_dir.glob("*.mdc"))[:5])

    # Claude Code 项目级指令（全局目录下的各项目）
    claude_projects = home / ".claude" / "projects"
    if claude_projects.is_dir():
        for proj_claude in sorted(claude_projects.glob("*/CLAUDE.md"))[:10]:
            global_candidates.append(proj_claude)

    for path in global_candidates:
        entry = _read_rule_file(path, "global")
        if entry:
            found.append(entry)

    # 项目文件（CWD）
    project_candidates = [
        current_dir / "CLAUDE.md",
        current_dir / ".cursorrules",
        current_dir / "AGENTS.md",
        current_dir / ".github" / "copilot-instructions.md",
    ]
    for path in project_candidates:
        entry = _read_rule_file(path, "project")
        if entry:
            found.append(entry)

    return found


def _read_rule_file(path: Path, scope: str) -> dict | None:
    """读取单个规则文件，返回 {path, scope, lines} 或 None。"""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    lines = text.splitlines()[:200]  # 最多 200 行
    content_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    if len(content_lines) < 2:
        return None  # 太少，跳过

    return {"path": path, "scope": scope, "lines": lines}


def _classify_line(line: str, scope: str) -> str:
    """将一行内容分类为 "user" / "project" / "skip"。

    Args:
        line: 文本行
        scope: "global" (全局文件) / "project" (项目文件)
    """
    stripped = line.strip()

    # 跳过：空行、纯标记、过短
    if not stripped or stripped.startswith("#") or stripped.startswith("---"):
        return "skip"
    if len(stripped) < 8:
        return "skip"
    # 跳过 frontmatter / code fences
    if stripped.startswith("```") or stripped.startswith("<!--"):
        return "skip"

    has_user = bool(_USER_KEYWORDS.search(stripped))
    has_project = bool(_PROJECT_KEYWORDS.search(stripped))

    if has_user and not has_project:
        return "user"
    if has_project and not has_user:
        return "project"
    if has_user and has_project:
        # 歧义：看文件来源
        return "user" if scope == "global" else "project"

    # 无关键词命中：看来源文件默认倾向
    return "user" if scope == "global" else "project"


def _import_with_split(
    rule_files: list[dict],
    engram,
) -> dict:
    """将扫描到的规则文件按分流规则导入 Engram。

    Returns: {user_count, project_count, skipped, files}
    """
    user_rules: list[str] = []
    project_rules: list[str] = []
    skipped = 0

    for rf in rule_files:
        scope = rf["scope"]
        for line in rf["lines"]:
            category = _classify_line(line, scope)
            if category == "user":
                user_rules.append(line.strip())
            elif category == "project":
                project_rules.append(line.strip())
            else:
                skipped += 1

    # 写入用户偏好
    if user_rules:
        # 提取特定偏好
        prefs_update: dict = {}
        remaining_user: list[str] = []

        for rule in user_rules:
            rule_lower = rule.lower()
            if any(kw in rule_lower for kw in ["语言", "language", "中文", "english", "沟通"]):
                # 语言偏好 → profile
                if "中文" in rule:
                    prefs_update["language"] = "中文"
                elif "english" in rule_lower:
                    prefs_update["language"] = "English"
                remaining_user.append(rule)
            elif any(kw in rule_lower for kw in ["角色", "role", "我是", "i am"]):
                remaining_user.append(rule)
            else:
                remaining_user.append(rule)

        if prefs_update:
            engram.update_profile(prefs_update)

        # 剩余用户规则存为 lesson（domain=user_preference）
        for rule in remaining_user:
            engram.add_lesson(rule, domain="user_preference", source_tool="engram_setup")

    # 写入项目规则
    for rule in project_rules:
        engram.add_lesson(rule, domain="project_rules", source_tool="engram_setup")

    return {
        "user_count": len(user_rules),
        "project_count": len(project_rules),
        "skipped": skipped,
        "files": [str(rf["path"]) for rf in rule_files],
    }

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


def _configure_utf8_stdio() -> None:
    """Prefer UTF-8 output so Windows setup can print Chinese and status icons."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not reconfigure:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (TypeError, ValueError, OSError):
            pass


def _get_engram_class():
    """Import Engram from package or local script execution context."""
    try:
        from engram_core.core import Engram
    except ImportError:  # pragma: no cover - direct script fallback
        from core import Engram  # type: ignore
    return Engram


def _run_seed_knowledge_onboarding(
    data_dir: str | None = None,
    cwd: Path | None = None,
) -> dict:
    """Guide first-time users to save enough seed context for get_user_context."""
    Engram = _get_engram_class()
    root = Path(data_dir).expanduser().resolve() if data_dir else Path.home() / ".engram"
    root.mkdir(parents=True, exist_ok=True)
    engram = Engram(root=root)
    current_dir = cwd or Path.cwd()

    print("Step 4/4 — 录入种子知识（可直接回车跳过）")
    role = _prompt("  你的角色是什么？（如：全栈开发者、产品经理、学生）", "")
    tech_stack = _prompt("  你常用什么编程语言/技术栈？", "")
    language = _prompt("  你偏好 AI 用什么语言跟你沟通？（中文/English/其他）", "")

    profile_updates: dict[str, str] = {}
    if role:
        profile_updates["role"] = role
    if language:
        profile_updates["language"] = language
    if tech_stack:
        profile_updates["tech_stack"] = tech_stack
        existing_profile = engram.get_profile()
        if not existing_profile.get("description"):
            profile_updates["description"] = f"常用技术栈：{tech_stack}"
    if profile_updates:
        engram.update_profile(profile_updates)

    lessons_added = 0
    first_lesson = _prompt("  你有没有一条 AI 工具总是忘记的规则或偏好？录入一条试试", "")
    lesson_inputs = [first_lesson] if first_lesson else []
    while lesson_inputs and len(lesson_inputs) < 3:
        next_lesson = _prompt("  还有吗？（直接回车跳过）", "")
        if not next_lesson:
            break
        lesson_inputs.append(next_lesson)

    for lesson in lesson_inputs:
        result = engram.add_lesson(lesson, domain="setup", source_tool="engram_setup")
        if result.get("status") != "duplicate":
            lessons_added += 1

    # Step 4.5 — 智能扫描 + 分流导入
    print("\nStep 4.5 — 智能导入规则文件")
    rule_files = _scan_rule_files(cwd=current_dir)
    import_result: dict = {"user_count": 0, "project_count": 0, "skipped": 0, "files": []}

    if rule_files:
        print(f"\n  扫描到 {len(rule_files)} 个规则文件：")
        for rf in rule_files:
            scope_label = "全局" if rf["scope"] == "global" else "项目"
            content_count = sum(1 for l in rf["lines"] if l.strip() and not l.strip().startswith("#"))
            print(f"  [{scope_label}] {rf['path']} ({content_count} 行有效内容)")

        # 预览分流
        user_preview = project_preview = skip_preview = 0
        for rf in rule_files:
            for line in rf["lines"]:
                cat = _classify_line(line, rf["scope"])
                if cat == "user":
                    user_preview += 1
                elif cat == "project":
                    project_preview += 1
                else:
                    skip_preview += 1

        print(f"\n  分流预览：")
        print(f"    用户身份: {user_preview} 条")
        print(f"    项目规则: {project_preview} 条")
        print(f"    跳过:     {skip_preview} 条")

        if _yn("\n  导入这些内容？", default=True):
            import_result = _import_with_split(rule_files, engram)
            print(f"\n  ✅ 已导入: {import_result['user_count']} 条身份 + {import_result['project_count']} 条项目规则")
        else:
            print("  跳过导入。")
    else:
        print("  未发现规则文件（CLAUDE.md / .cursorrules 等）。")

    total_imported = import_result["user_count"] + import_result["project_count"]

    print("\n========================================")
    print("  Engram 初始化完成！")
    print("========================================\n")
    if role or tech_stack or language:
        identity_parts = [role or "-", tech_stack or "-", language or "-"]
        print(f"  身份：{' | '.join(identity_parts)}")
    else:
        print("  身份：未填写")
    print(f"  经验：已录入 {lessons_added} 条")
    if total_imported > 0:
        print(f"  导入：{total_imported} 条规则（{import_result['user_count']} 条身份 + {import_result['project_count']} 条项目）")
    print()
    print("  验证方法：打开你的 AI 工具，说这句话：")
    print()
    print("    请同步 Engram 上下文，然后告诉我你现在知道我什么。")
    print()
    print("  如果 AI 能说出你的角色、语言偏好、技术栈，")
    print("  就说明 Engram 已经在工作了。\n")

    return {
        "profile": profile_updates,
        "lessons_added": lessons_added,
        "imported_files": import_result["files"],
        "import_user_count": import_result["user_count"],
        "import_project_count": import_result["project_count"],
    }


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------

def run_setup() -> None:
    """交互式安装向导主流程。"""
    _configure_utf8_stdio()
    print("\n========================================")
    print("  Engram 安装向导")
    print("========================================\n")

    # Step 1 — Python 检测
    print("Step 1/4 — 检测 Python")
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
    print("Step 2/4 — 数据目录")
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
    print("Step 3/4 — 配置 AI 工具")
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

    selected_data_dir = data_dir or default_data_dir
    _run_seed_knowledge_onboarding(selected_data_dir)

    # 完成 — 验证提示已在 _run_seed_knowledge_onboarding 中输出
    print("  重启你的 AI 工具（Claude Code / Cursor）即可使用。")
    print()
    print("  遇到问题或有建议？欢迎反馈：")
    print("  https://github.com/Patdolitse/engram/issues\n")


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
        print(
            "Engram CLI\n\n"
            "Usage:\n"
            "  engram setup            Interactive install wizard\n"
            "  engram doctor           Check config health (all AI tools)\n"
            "  engram doctor --fix     Auto-repair any issues found\n"
            "  engram stats            Show project growth metrics\n\n"
            "Tool tiers:\n"
            "  Default: all MCP tools are loaded.\n"
            "  Set ENGRAM_TOOLS=core to load only 核心工具 / core MCP tools.\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
