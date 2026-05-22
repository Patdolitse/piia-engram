"""Engram 安装向导 — engram setup / engram doctor 命令入口。"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 旧版 MCP server 名称，迁移时需要清理
LEGACY_SERVER_NAMES = ["piia-pkc", "piia_pkc", "piia-pkc-mcp"]

# ---------------------------------------------------------------------------
# i18n — 双语支持（中文/English）
# ---------------------------------------------------------------------------

_lang = "zh"  # 默认中文，setup 开始时由用户选择


def _t(zh: str, en: str) -> str:
    """根据当前语言返回对应文案。"""
    return zh if _lang == "zh" else en


def _safe_print(text: str) -> None:
    """Print with fallback for consoles that can't handle certain Unicode chars (e.g. Windows GBK)."""
    try:
        print(text)
    except UnicodeEncodeError:
        # Strip chars the console encoding can't handle
        import sys
        enc = sys.stdout.encoding or "ascii"
        safe = text.encode(enc, errors="ignore").decode(enc)
        print(safe)

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
    # CJK characters carry more information per char than ASCII
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in stripped)
    min_len = 4 if has_cjk else 8
    if len(stripped) < min_len:
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
        except Exception as exc:
            logger.warning("read config failed (%s): %s", config_path, exc)
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


def _choice(message: str, options: list[str], allow_custom: bool = True) -> str:
    """数字菜单选择。支持自定义输入。

    Args:
        message: 提示语
        options: 预设选项列表
        allow_custom: 是否允许自定义输入

    Returns: 选中的选项文本，或空字符串（跳过）
    """
    print(f"  {message}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    if allow_custom:
        print(f"    {len(options) + 1}. 其他（自行输入）")
    print(f"    0. 跳过")

    answer = _prompt("  请选择").strip()
    if not answer or answer == "0":
        return ""

    try:
        idx = int(answer)
        if 1 <= idx <= len(options):
            return options[idx - 1]
        if allow_custom and idx == len(options) + 1:
            return _prompt("  请输入")
    except ValueError:
        # 直接输入了文本而非数字，也接受
        return answer

    return ""


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

    print(_t("Step 4/4 — 录入种子知识（输入 0 跳过）\n",
             "Step 4/4 — Seed knowledge (enter 0 to skip)\n"))
    role = _choice(_t("你的角色是什么？", "What is your role?"), [
        _t("全栈开发者", "Full-stack developer"),
        _t("后端开发者", "Backend developer"),
        _t("前端开发者", "Frontend developer"),
        _t("产品经理", "Product manager"),
        _t("数据科学家", "Data scientist"),
        _t("学生", "Student"),
    ])
    print()
    tech_stack = _choice(_t("你常用什么编程语言/技术栈？", "Primary language / tech stack?"), [
        "Python",
        "TypeScript / JavaScript",
        "Go",
        "Java",
        "Rust",
        "Python + TypeScript",
    ])
    print()
    language = _choice(_t("你偏好 AI 用什么语言跟你沟通？",
                          "Preferred language for AI communication?"), [
        "中文",
        "English",
        _t("日本语", "Japanese"),
    ])

    profile_updates: dict[str, str] = {}
    if role:
        profile_updates["role"] = role
    if language:
        profile_updates["language"] = language
    if tech_stack:
        profile_updates["tech_stack"] = tech_stack
        existing_profile = engram.get_profile()
        if not existing_profile.get("description"):
            profile_updates["description"] = _t(
                f"常用技术栈：{tech_stack}",
                f"Primary tech stack: {tech_stack}",
            )
    if profile_updates:
        engram.update_profile(profile_updates)

    lessons_added = 0
    first_lesson = _prompt(_t(
        "  你有没有一条 AI 工具总是忘记的规则或偏好？录入一条试试",
        "  Any rule or preference your AI tools keep forgetting? Enter one to try",
    ), "")
    lesson_inputs = [first_lesson] if first_lesson else []
    while lesson_inputs and len(lesson_inputs) < 3:
        next_lesson = _prompt(_t("  还有吗？（直接回车跳过）",
                                 "  More? (Enter to skip)"), "")
        if not next_lesson:
            break
        lesson_inputs.append(next_lesson)

    for lesson in lesson_inputs:
        result = engram.add_lesson(lesson, domain="setup", source_tool="engram_setup")
        if result.get("status") != "duplicate":
            lessons_added += 1

    # Step 4.5 — 智能扫描 + 分流导入
    print(_t("\nStep 4.5 — 智能导入规则文件",
             "\nStep 4.5 — Smart rule file import"))
    rule_files = _scan_rule_files(cwd=current_dir)
    import_result: dict = {"user_count": 0, "project_count": 0, "skipped": 0, "files": []}

    if rule_files:
        print(_t(f"\n  扫描到 {len(rule_files)} 个规则文件：",
                 f"\n  Found {len(rule_files)} rule file(s):"))
        for rf in rule_files:
            scope_label = _t("全局", "global") if rf["scope"] == "global" else _t("项目", "project")
            content_count = sum(1 for l in rf["lines"] if l.strip() and not l.strip().startswith("#"))
            print(_t(f"  [{scope_label}] {rf['path']} ({content_count} 行有效内容)",
                     f"  [{scope_label}] {rf['path']} ({content_count} content lines)"))

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

        print(_t("\n  分流预览：", "\n  Classification preview:"))
        print(_t(f"    用户身份: {user_preview} 条",
                 f"    User identity: {user_preview}"))
        print(_t(f"    项目规则: {project_preview} 条",
                 f"    Project rules: {project_preview}"))
        print(_t(f"    跳过:     {skip_preview} 条",
                 f"    Skipped:       {skip_preview}"))

        if _yn(_t("\n  导入这些内容？", "\n  Import these?"), default=True):
            import_result = _import_with_split(rule_files, engram)
            print(_t(f"\n  ✅ 已导入: {import_result['user_count']} 条身份 + {import_result['project_count']} 条项目规则",
                     f"\n  ✅ Imported: {import_result['user_count']} identity + {import_result['project_count']} project rules"))
        else:
            print(_t("  跳过导入。", "  Skipped."))
    else:
        print(_t("  未发现规则文件（CLAUDE.md / .cursorrules 等）。",
                 "  No rule files found (CLAUDE.md / .cursorrules etc.)."))

    total_imported = import_result["user_count"] + import_result["project_count"]

    print("\n========================================")
    print(_t("  Engram 初始化完成！", "  Engram setup complete!"))
    print("========================================\n")
    if role or tech_stack or language:
        identity_parts = [role or "-", tech_stack or "-", language or "-"]
        print(_t(f"  身份：{' | '.join(identity_parts)}",
                 f"  Identity: {' | '.join(identity_parts)}"))
    else:
        print(_t("  身份：未填写", "  Identity: not set"))
    print(_t(f"  经验：已录入 {lessons_added} 条",
             f"  Lessons: {lessons_added} recorded"))
    if total_imported > 0:
        print(_t(f"  导入：{total_imported} 条规则（{import_result['user_count']} 条身份 + {import_result['project_count']} 条项目）",
                 f"  Imported: {total_imported} rules ({import_result['user_count']} identity + {import_result['project_count']} project)"))
    print()
    print(_t("  验证方法：打开你的 AI 工具，说这句话：",
             "  To verify: open your AI tool and say:"))
    print()
    print(_t("    请同步 Engram 上下文，然后告诉我你现在知道我什么。",
             "    Sync Engram context, then tell me what you know about me."))
    print()
    print(_t("  如果 AI 能说出你的角色、语言偏好、技术栈，",
             "  If the AI mentions your role, language, and tech stack,"))
    print(_t("  就说明 Engram 已经在工作了。\n",
             "  Engram is working.\n"))

    # --- First-day aha moment: show identity card preview ---
    try:
        from .core import Engram as _Engram
    except ImportError:
        try:
            from core import Engram as _Engram  # type: ignore
        except ImportError:
            _Engram = None  # type: ignore
    if _Engram is not None:
        try:
            _e = _Engram()
            card = _e.export_identity_card()
            if card and len(card.strip().splitlines()) > 5:
                print("----------------------------------------")
                print(_t("  [CARD] AI identity card preview:\n",
                         "  [CARD] AI identity card preview:\n"))
                for line in card.strip().splitlines():
                    _safe_print(f"  {line}")
                print()
                print("----------------------------------------")
                print(_t("  Tip: AI tools can see this via get_identity_card.",
                         "  Tip: AI tools can see this via get_identity_card."))
                print()
        except Exception:
            pass  # Non-critical — skip silently

    return {
        "profile": profile_updates,
        "lessons_added": lessons_added,
        "imported_files": import_result["files"],
        "import_user_count": import_result["user_count"],
        "import_project_count": import_result["project_count"],
    }


# ---------------------------------------------------------------------------
# Privacy & data preferences (telemetry opt-in + reconcile authorization)
# ---------------------------------------------------------------------------

def _run_privacy_preferences(data_dir: str) -> None:
    """Ask user about auto-reconcile and anonymous usage statistics."""
    from engram_core.telemetry import _load_config, _save_config, set_enabled

    cfg = _load_config()

    print(_t("\nStep 5 — 隐私与数据偏好", "\nStep 5 — Privacy & data preferences"))
    print(_t("  你的数据默认只留在本机。以下两项可选功能需要你明确同意。\n",
             "  Your data stays local by default. The following optional features require your explicit consent.\n"))

    # --- Reconcile authorization ---
    print(_t("  [1] 跨工具记忆同步",
             "  [1] Cross-tool memory sync"))
    print(_t("      Engram 可以在每次启动时自动扫描其他 AI 工具的配置文件",
             "      Engram can scan other AI tools' config files on each startup"))
    print(_t("      （如 ~/.claude/projects/*/memory/*.md、CLAUDE.md、.cursorrules 等）",
             "      (e.g. ~/.claude/projects/*/memory/*.md, CLAUDE.md, .cursorrules)"))
    print(_t("      并导入其中的规则和记忆到 Engram。",
             "      and import rules and memories into Engram."))
    print(_t("      扫描结果会显示在 get_user_context 输出中。\n",
             "      Results appear in get_user_context output.\n"))

    reconcile_authorized = _yn(
        _t("  允许 Engram 扫描其他 AI 工具的文件？",
           "  Allow Engram to scan other AI tools' files?"),
        default=True,
    )
    cfg["reconcile_authorized"] = reconcile_authorized
    if reconcile_authorized:
        print(_t("  ✅ 已授权跨工具同步\n", "  ✅ Cross-tool sync authorized\n"))
    else:
        print(_t("  ℹ️  已关闭跨工具同步。可设置 ENGRAM_RECONCILE=1 重新开启。\n",
                 "  ℹ️  Cross-tool sync disabled. Set ENGRAM_RECONCILE=1 to re-enable.\n"))

    # --- Anonymous usage statistics ---
    print(_t("  [2] 匿名使用统计",
             "  [2] Anonymous usage statistics"))
    print(_t("      帮助我们了解哪些功能被使用、哪些需要改进。",
             "      Help us understand which features are used and need improvement."))
    print(_t("      每天最多记录一次，内容如下：",
             "      Logged at most once per day:"))
    print(_t("        • 工具调用计数（只有工具名和次数，无参数和内容）",
             "        • Tool call counts (names + counts only, no arguments or content)"))
    print(_t("        • 知识条目总数（只有数字，无内容）",
             "        • Knowledge entry totals (counts only, no content)"))
    print(_t("        • Engram 版本号",
             "        • Engram version"))
    print(_t("      绝不发送：知识内容、prompt、文件路径、邮箱、IP 地址",
             "      Never sent: knowledge content, prompts, file paths, email, IP"))
    print(_t("      当前为 Phase 1：仅记录到本地文件，不发送网络请求。",
             "      Current Phase 1: logged to local file only, no network requests."))
    print(_t(f"      日志位置：{data_dir}/telemetry.log",
             f"      Log location: {data_dir}/telemetry.log"))
    print(_t("      查看将记录的内容：engram telemetry preview",
             "      Preview what's logged: engram telemetry preview"))
    print(_t("      随时关闭：engram telemetry off\n",
             "      Disable anytime: engram telemetry off\n"))

    telemetry_enabled = _yn(
        _t("  开启匿名使用统计？",
           "  Enable anonymous usage statistics?"),
        default=False,
    )
    set_enabled(telemetry_enabled)
    if telemetry_enabled:
        print(_t("  ✅ 已开启（仅本地日志，Phase 1 不发送网络请求）\n",
                 "  ✅ Enabled (local log only, Phase 1 sends no network requests)\n"))
    else:
        print(_t("  ℹ️  未开启。可随时运行 engram telemetry on 改变。\n",
                 "  ℹ️  Not enabled. Run engram telemetry on to change anytime.\n"))

    # Save reconcile pref to same config file
    cfg_all = _load_config()
    cfg_all["reconcile_authorized"] = reconcile_authorized
    _save_config(cfg_all)


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------

def run_setup() -> None:
    """交互式安装向导主流程。"""
    global _lang
    _configure_utf8_stdio()

    # 语言选择（最先，决定后续所有文案）
    print("\n  Language / 语言选择:")
    print("    1. 中文")
    print("    2. English")
    lang_answer = _prompt("  Choose / 请选择", "1").strip()
    _lang = "en" if lang_answer == "2" else "zh"
    print()

    print("========================================")
    print(_t("  PIIA Engram 安装向导", "  PIIA Engram Setup Wizard"))
    print("========================================\n")

    # Step 1 — Python 检测
    print(_t("Step 1/4 — 检测 Python", "Step 1/4 — Detecting Python"))
    python_path = _find_python()
    if not python_path:
        print(_t("❌ 未找到可用的 Python 3.10+。", "❌ Python 3.10+ not found."))
        print(_t("   请安装 Python 后重新运行：https://python.org/downloads/",
                 "   Please install Python and re-run: https://python.org/downloads/"))
        sys.exit(1)
    print(f"  ✅ Python: {python_path}\n")

    # mcp_server.py 路径
    mcp_server_path = _find_mcp_server()
    if not mcp_server_path:
        print(_t("❌ 未找到 mcp_server.py，请确认已正确安装（pip install piia-engram）。",
                 "❌ mcp_server.py not found. Please ensure piia-engram is installed."))
        sys.exit(1)

    # Step 2 — 数据目录
    print(_t("Step 2/4 — 数据目录", "Step 2/4 — Data directory"))
    default_data_dir = str(Path.home() / ".engram")
    print(_t(f"  知识库默认存储位置: {default_data_dir}",
             f"  Default storage: {default_data_dir}"))
    custom_dir = _prompt(_t("  自定义路径（直接回车使用默认）",
                            "  Custom path (Enter for default)"), "")
    data_dir: str | None = None
    if custom_dir:
        data_dir = str(Path(custom_dir).expanduser().resolve())
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        print(_t(f"  ✅ 将使用: {data_dir}", f"  ✅ Using: {data_dir}"))
    else:
        print(_t(f"  ✅ 将使用: {default_data_dir}", f"  ✅ Using: {default_data_dir}"))
    print()

    # Step 3 — 工具检测与配置
    print(_t("Step 3/4 — 配置 AI 工具", "Step 3/4 — Configure AI tools"))
    tools = _detect_tools()
    if not tools:
        print(_t("  ⚠️  未检测到支持 MCP 的 AI 工具（Claude Code / Cursor / Claude Desktop）。",
                 "  ⚠️  No MCP-compatible AI tools detected (Claude Code / Cursor / Claude Desktop)."))
        print(_t("  安装工具后重新运行 'engram setup' 即可完成配置。\n",
                 "  Install a supported tool and re-run 'engram setup'.\n"))
    else:
        for tool in tools:
            print(_t(f"  ✅ 检测到 {tool['name']}: {tool['config_path']}",
                     f"  ✅ Found {tool['name']}: {tool['config_path']}"))
        print()
        if _yn(_t("  为所有检测到的工具配置 Engram？",
                   "  Configure Engram for all detected tools?")):
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
                print(_t(f"  ✅ {name} 已配置", f"  ✅ {name} configured"))
            for name in failed:
                print(_t(f"  ❌ {name} 配置失败", f"  ❌ {name} failed"))

    selected_data_dir = data_dir or default_data_dir
    _run_seed_knowledge_onboarding(selected_data_dir)

    # Step 5 — Privacy & data preferences
    _run_privacy_preferences(selected_data_dir)

    # 完成
    print(_t("  重启你的 AI 工具（Claude Code / Cursor）即可使用。",
             "  Restart your AI tool (Claude Code / Cursor) to get started."))
    print()
    print(_t("  觉得有用？来聊聊你怎么用的：",
             "  Find Engram useful? Share how you use it:"))
    print("  https://github.com/Patdolitse/engram/discussions\n")
    print(_t("  遇到问题？",
             "  Issues?"))
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

    except Exception as exc:
        logger.warning("migration failed: %s", exc)


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

    # Info: remind about ENGRAM_TOOLS default change (v3.13+)
    for _tool_id, cfg in _tool_configs().items():
        for config_path in cfg["config_paths"]:
            if not config_path.is_file():
                continue
            config = _read_mcp_config(config_path)
            engram_entry = config.get("mcpServers", {}).get("engram", {})
            if engram_entry and not engram_entry.get("env", {}).get("ENGRAM_TOOLS"):
                print(f"  [info] {config_path}")
                print("    -> Engram now defaults to 10 core tools (was 43).")
                print('    -> Add "ENGRAM_TOOLS": "all" to env if you need all 43 tools.')
                print()

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


def _run_telemetry_cli(sub_args: list[str]) -> None:
    """Handle `engram telemetry <subcommand>`."""
    from engram_core.telemetry import (
        get_status, is_enabled, preview_payload, set_enabled,
    )

    sub = sub_args[0] if sub_args else "status"

    if sub == "status":
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        print(f"\n  Anonymous usage statistics: {state}")
        print(f"  Phase: {status['phase']}")
        print(f"  Config: {status['config_path']}")
        print(f"  Log: {status['log_path']}")
        if status["enabled"]:
            print(f"  Opted in: {status['opted_in_at']}")
        print()

    elif sub == "preview":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    elif sub in ("off", "disable"):
        set_enabled(False)
        print("\n  ✅ Anonymous usage statistics disabled.")
        print("  No data will be logged or sent.\n")

    elif sub in ("on", "enable"):
        set_enabled(True)
        print("\n  ✅ Anonymous usage statistics enabled (Phase 1: local log only).")
        print("  Run 'engram telemetry preview' to see what will be logged.\n")

    elif sub == "--show-payload":
        # Alias for preview (ChatGPT Pro recommendation)
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    else:
        print(
            "\nUsage:\n"
            "  engram telemetry status       Show current status\n"
            "  engram telemetry preview      Show what data will be logged\n"
            "  engram telemetry on           Enable anonymous usage statistics\n"
            "  engram telemetry off          Disable anonymous usage statistics\n"
        )


def _run_privacy_report() -> None:
    """Handle `engram privacy` — show what data Engram stores and where."""
    import os as _os
    data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    print("\n========================================")
    print("  Engram Privacy Report")
    print("========================================\n")

    # 1. Data directory
    print(f"  [DIR] Data directory: {data_dir}")
    if data_dir.exists():
        files = list(data_dir.iterdir())
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        print(f"        Files: {len([f for f in files if f.is_file()])}")
        print(f"        Total size: {total_size / 1024:.1f} KB")
    else:
        print("        (not created yet)")
    print()

    # 2. Identity data
    identity_file = data_dir / "identity.json"
    print("  [ID]  Identity data:")
    if identity_file.is_file():
        size = identity_file.stat().st_size
        print(f"        {identity_file} ({size / 1024:.1f} KB)")
        print("        Contains: profile, preferences, work_style, quality_standards, trust_boundaries")
        try:
            raw = identity_file.read_text(encoding="utf-8")
            encrypted_count = raw.count("enc:v")
            if encrypted_count > 0:
                print(f"        [ENCRYPTED] {encrypted_count} fields encrypted")
            else:
                print("        [PLAIN] No encrypted fields (set ENGRAM_KEY to enable)")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 3. Knowledge base
    knowledge_file = data_dir / "knowledge.json"
    print("  [KB]  Knowledge base:")
    if knowledge_file.is_file():
        size = knowledge_file.stat().st_size
        print(f"        {knowledge_file} ({size / 1024:.1f} KB)")
        try:
            import json as _j
            kdata = _j.loads(knowledge_file.read_text(encoding="utf-8"))
            lessons = kdata.get("lessons", [])
            decisions = kdata.get("decisions", [])
            print(f"        Lessons: {len(lessons)}")
            print(f"        Decisions: {len(decisions)}")
        except Exception:
            pass
    else:
        print("        (not created yet)")
    print()

    # 4. Telemetry
    print("  [STAT] Anonymous usage statistics:")
    try:
        from engram_core.telemetry import get_status
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        print(f"        Status: {state}")
        print(f"        Config: {status['config_path']}")
        log_path = Path(status["log_path"])
        if log_path.is_file():
            log_size = log_path.stat().st_size
            log_lines = len(log_path.read_text(encoding="utf-8").strip().splitlines())
            print(f"        Log: {log_path} ({log_size / 1024:.1f} KB, {log_lines} entries)")
        else:
            print("        Log: (no entries yet)")
        print("        Collected: tool names + counts, knowledge totals, version, daily anonymous ID")
        print("        NOT collected: text content, prompts, file paths, PII, IP, OS info")
        print("        Network: Phase 1 = local only, NO network requests")
    except ImportError:
        print("        (telemetry module not available)")
    print()

    # 5. Reconcile
    print("  [SYNC] Cross-tool sync:")
    try:
        from engram_core.reconcile import ReconcileMixin
        authorized = ReconcileMixin._reconcile_authorized()
        print(f"        Status: {'ON' if authorized else 'OFF'}")
        print("        Scans: ~/.claude/projects/*/memory/*.md, CLAUDE.md, .cursorrules, etc.")
        print("        Control: ENGRAM_RECONCILE=0 to disable")
    except ImportError:
        print("        (reconcile module not available)")
    print()

    # 6. Network
    print("  [NET]  Network requests:")
    print("        Core Engram: ZERO network requests (local files only)")
    print("        Optional: read_web_content (user-initiated only, via local Reader service)")
    print("        Optional: telemetry Phase 2 (NOT implemented, requires re-consent)")
    print()

    # 7. How to delete
    print("  [DEL]  Delete all data:")
    print(f"        rm -rf {data_dir}")
    print("        (This removes ALL Engram data permanently)")
    print()


def main() -> None:
    """CLI 入口：engram setup / engram doctor [--fix] / engram telemetry <sub> / engram privacy"""
    args = sys.argv[1:]
    if not args or args[0] == "setup":
        run_setup()
    elif args[0] == "doctor":
        fix = "--fix" in args
        sys.exit(run_doctor(fix=fix))
    elif args[0] == "stats":
        from engram_core.stats import run_stats, log_stats
        if "--log" in args:
            log_stats()
        else:
            run_stats()
    elif args[0] == "telemetry":
        _run_telemetry_cli(args[1:])
    elif args[0] == "privacy":
        _run_privacy_report()
    else:
        print(
            "Engram CLI\n\n"
            "Usage:\n"
            "  engram setup            Interactive install wizard\n"
            "  engram doctor           Check config health (all AI tools)\n"
            "  engram doctor --fix     Auto-repair any issues found\n"
            "  engram stats            Show project growth metrics\n"
            "  engram stats --log      Append stats snapshot to local log\n"
            "  engram telemetry        Manage anonymous usage statistics\n"
            "  engram privacy          Show what data Engram stores\n\n"
            "Tool tiers:\n"
            "  Default: 10 核心工具 / core MCP tools.\n"
            "  Set ENGRAM_TOOLS=all to unlock all 43 tools.\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
