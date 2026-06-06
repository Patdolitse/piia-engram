"""Engram 安装向导 — engram setup / engram doctor 命令入口。"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import locale
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

logger = logging.getLogger(__name__)

# 旧版 MCP server 名称，迁移时需要清理
LEGACY_SERVER_NAMES = ["piia-pkc", "piia_pkc", "piia-pkc-mcp"]


def _module_src_dir(mcp_server_path: str) -> str:
    """Return the source root containing the piia_engram package."""
    if "\\" in mcp_server_path or re.match(r"^[A-Za-z]:[\\/]", mcp_server_path):
        return str(PureWindowsPath(mcp_server_path).parent.parent)
    return str(Path(mcp_server_path).parent.parent)

# ---------------------------------------------------------------------------
# i18n — 双语支持（中文/English）
# ---------------------------------------------------------------------------

from piia_engram.i18n import set_lang as _set_lang, get_lang as _get_lang, t as _t

# Backward compat: _lang is still readable but writes go through i18n module.
_lang = "zh"  # 默认中文，setup 开始时由用户选择


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


def _is_utf8_encoding(encoding: str | None) -> bool:
    name = (encoding or "").strip().lower().replace("_", "-")
    return name in {"utf-8", "utf8", "utf-8-sig", "cp65001"} or name.startswith("utf-8")


def _run_terminal_encoding_check(
    *,
    stdout_encoding: str | None = None,
    stderr_encoding: str | None = None,
    preferred_encoding: str | None = None,
    filesystem_encoding: str | None = None,
    pythonioencoding: str | None = None,
) -> int:
    """Report terminal/display encoding without treating it as data corruption."""
    stdout_encoding = stdout_encoding if stdout_encoding is not None else sys.stdout.encoding
    stderr_encoding = stderr_encoding if stderr_encoding is not None else sys.stderr.encoding
    preferred_encoding = (
        preferred_encoding
        if preferred_encoding is not None
        else locale.getpreferredencoding(False)
    )
    filesystem_encoding = (
        filesystem_encoding
        if filesystem_encoding is not None
        else sys.getfilesystemencoding()
    )
    pythonioencoding = (
        pythonioencoding
        if pythonioencoding is not None
        else os.environ.get("PYTHONIOENCODING")
    )

    print()
    _safe_print("  -- Terminal encoding --\n")
    stdout_label = stdout_encoding or "unknown"
    stderr_label = stderr_encoding or "unknown"
    stdio_is_utf8 = _is_utf8_encoding(stdout_encoding) and _is_utf8_encoding(stderr_encoding)
    if stdio_is_utf8:
        print(f"    [ok] stdout/stderr: {stdout_label} / {stderr_label}")
    else:
        print(f"    [--] stdout/stderr: {stdout_label} / {stderr_label}")
        print("         Terminal may display UTF-8 text as mojibake.")
        print("         This does not mean Engram files are corrupted.")

    if pythonioencoding and _is_utf8_encoding(pythonioencoding):
        print(f"    [ok] PYTHONIOENCODING={pythonioencoding}")
    elif pythonioencoding:
        print(f"    [--] PYTHONIOENCODING={pythonioencoding}")
        print("         Set PYTHONIOENCODING=utf-8 for subprocess-heavy workflows.")
    elif stdio_is_utf8:
        print("    [ok] PYTHONIOENCODING not set (stdout/stderr already UTF-8)")
    else:
        print("    [--] PYTHONIOENCODING not set")
        print("         Set PYTHONIOENCODING=utf-8 for subprocess-heavy workflows.")

    runtime_status = (
        "[ok]"
        if _is_utf8_encoding(preferred_encoding)
        and _is_utf8_encoding(filesystem_encoding)
        else "[--]"
    )
    print(
        f"    {runtime_status} Runtime encodings: "
        f"preferred={preferred_encoding or 'unknown'}, "
        f"filesystem={filesystem_encoding or 'unknown'}"
    )
    return 0

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
    """返回各工具的 MCP 配置路径（运行时构建，确保 Path.home() 正确）。

    每个条目包含:
    - name: 工具显示名
    - config_paths: 配置文件路径列表
    - format: "json" | "toml"（默认 json）
    - verified: True = 团队实测验证过, False = 社区级支持（路径来自官方文档，未实测）
    - server_key: MCP servers 在配置中的顶层 key（默认 "mcpServers"）
    """
    home = Path.home()
    is_mac = platform.system() == "Darwin"
    is_win = platform.system() == "Windows"
    appdata = Path(os.environ.get("APPDATA", "")) if is_win else None
    vscode_storage = (appdata / "Code" / "User") if appdata else (
        home / "Library" / "Application Support" / "Code" / "User" if is_mac
        else home / ".config" / "Code" / "User"
    )

    configs: dict = {
        # ── 已验证（团队实测） ─────────────────────────────
        "claude_code": {
            "name": "Claude Code",
            "config_paths": [home / ".claude" / ".mcp.json"],
            "verified": True,
        },
        "cursor": {
            "name": "Cursor",
            "config_paths": [home / ".cursor" / "mcp.json"],
            "verified": True,
        },
        "claude_desktop": {
            "name": "Claude Desktop",
            "config_paths": (
                [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
                if is_mac
                else [appdata / "Claude" / "claude_desktop_config.json"]
                if appdata
                else []
            ),
            "verified": True,
        },
        "codex": {
            "name": "Codex",
            "config_paths": [home / ".codex" / "config.toml"],
            "format": "toml",
            "server_key": "mcp_servers",
            "verified": True,
        },

        # ── 社区级支持（路径来自官方文档，未实测） ──────────
        "windsurf": {
            "name": "Windsurf",
            "config_paths": [home / ".codeium" / "windsurf" / "mcp_config.json"],
            "verified": False,
        },
        "trae": {
            "name": "Trae",
            # Trae stores its global MCP servers in ~/.trae/mcp.json
            # (standard "mcpServers" JSON, same on Windows/macOS/Linux).
            "config_paths": [home / ".trae" / "mcp.json"],
            "verified": False,
        },
        "codebuddy": {
            "name": "CodeBuddy",
            # Tencent CodeBuddy stores user-scope MCP servers in
            # ~/.codebuddy/mcp.json (standard "mcpServers" JSON).
            "config_paths": [home / ".codebuddy" / "mcp.json"],
            "verified": False,
        },
        "copilot_vscode": {
            "name": "GitHub Copilot (VS Code)",
            "config_paths": [vscode_storage / "mcp.json"] if vscode_storage else [],
            "server_key": "servers",
            "verified": False,
        },
        "cline": {
            "name": "Cline",
            "config_paths": [
                vscode_storage / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
            ] if vscode_storage else [],
            "verified": False,
        },
        "roo_code": {
            "name": "Roo Code",
            "config_paths": [
                vscode_storage / "globalStorage" / "rooveterinaryinc.roo-cline" / "settings" / "cline_mcp_settings.json",
            ] if vscode_storage else [],
            "verified": False,
        },
        "amazon_q": {
            "name": "Amazon Q Developer",
            "config_paths": [home / ".aws" / "amazonq" / "mcp.json"],
            "verified": False,
        },
        "augment": {
            "name": "Augment Code",
            "config_paths": [home / ".augment" / "settings.json"],
            "verified": False,
        },
        "zed": {
            "name": "Zed",
            "config_paths": (
                [home / ".config" / "zed" / "settings.json"] if not is_win
                else [appdata / "Zed" / "settings.json"] if appdata
                else []
            ),
            "server_key": "context_servers",
            "verified": False,
        },
    }
    return configs


# ---------------------------------------------------------------------------
# Client instruction snippets — injected into each tool's native instruction
# file so the AI knows to call Engram proactively without relying solely on
# MCP server instructions (which not all clients read reliably).
# ---------------------------------------------------------------------------

_INSTRUCTION_MARKER = "<!-- piia-engram:auto-injected v=2 -->"
_INSTRUCTION_MARKER_END = "<!-- /piia-engram -->"

# v3.31 P0: every cross-tool snippet MUST instruct AI to call
# `get_resume_brief` at session start. This is the marker doctor uses
# to detect stale (v=1) snippets that lack the auto-resume directive.
_SNIPPET_FRESHNESS_TOKEN = "get_resume_brief"

# Map: tool_id → (instruction_file_path_fn, snippet)
# instruction_file_path_fn takes Path.home() and returns the file path

_INSTRUCTION_SNIPPETS: dict[str, dict] = {
    "claude_code": {
        "path_fn": lambda home: home / ".claude" / "CLAUDE.md",
        "snippet_zh": (
            "\n{marker}\n"
            "## Engram 记忆层\n\n"
            "本机已安装 PIIA Engram（MCP 记忆层）。\n\n"
            "- **会话开始**：调用 `get_resume_brief` 接续上一轮工作（跨会话/跨工具连续性）\n"
            "- **对话开头（首次/新项目）**：调用 `get_user_context` 了解用户身份和偏好\n"
            "- **学到经验/踩坑**：调用 `add_lesson` 存入\n"
            "- **做出决策**：调用 `add_decision` 记录选择和理由\n"
            "- **对话结束**：调用 `wrap_up_session` 保存上下文\n"
            "- **搜索历史知识**：调用 `search_knowledge`\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "PIIA Engram (MCP memory layer) is installed on this machine.\n\n"
            "- **Session start**: call `get_resume_brief` to resume from the last session (cross-session / cross-tool continuity)\n"
            "- **Conversation start (first time / new project)**: call `get_user_context` to learn user identity and preferences\n"
            "- **Lessons learned**: call `add_lesson` to save\n"
            "- **Decisions made**: call `add_decision` to record choice and reasoning\n"
            "- **End of conversation**: call `wrap_up_session` to save context\n"
            "- **Search past knowledge**: call `search_knowledge`\n"
            "{marker_end}\n"
        ),
    },
    "cursor": {
        "path_fn": lambda home: home / ".cursor" / "rules" / "engram.mdc",
        "snippet_zh": (
            "---\n"
            "description: Engram 记忆层 — AI 记住用户身份和经验\n"
            "globs:\n"
            "alwaysApply: true\n"
            "---\n\n"
            "本机已安装 PIIA Engram（MCP 记忆层）。\n\n"
            "- 会话开始调用 `get_resume_brief` 接续上一轮工作（跨工具连续性的关键）\n"
            "- 首次对话或新项目调用 `get_user_context` 了解用户\n"
            "- 学到经验时调用 `add_lesson`\n"
            "- 做决策时调用 `add_decision`\n"
            "- 对话结束调用 `wrap_up_session`\n"
            "- 搜索知识用 `search_knowledge`\n"
        ),
        "snippet_en": (
            "---\n"
            "description: Engram memory layer — AI remembers user identity and experience\n"
            "globs:\n"
            "alwaysApply: true\n"
            "---\n\n"
            "PIIA Engram (MCP memory layer) is installed.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (key to cross-tool continuity)\n"
            "- First conversation or new project: call `get_user_context` to learn user\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- End of conversation: call `wrap_up_session`\n"
            "- Search knowledge: call `search_knowledge`\n"
        ),
    },
    "codex": {
        "path_fn": lambda home: home / ".codex" / "AGENTS.md",
        "snippet_zh": (
            "\n{marker}\n"
            "## Engram 记忆层\n\n"
            "本机已安装 PIIA Engram（MCP 记忆层）。\n\n"
            "- 会话开始：调用 `get_resume_brief` 接续上一轮工作（跨工具连续性）\n"
            "- 首次/新项目：调用 `get_user_context` 了解用户身份和偏好\n"
            "- 学到经验/踩坑：调用 `add_lesson` 存入\n"
            "- 做出决策：调用 `add_decision` 记录\n"
            "- 任务结束：调用 `wrap_up_session` 保存上下文\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "PIIA Engram (MCP memory layer) is installed.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (cross-tool continuity)\n"
            "- First time / new project: call `get_user_context` to learn user identity and preferences\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- Task end: call `wrap_up_session` to save context\n"
            "{marker_end}\n"
        ),
    },
    "windsurf": {
        # Windsurf reads ~/.codeium/windsurf/memories/global_rules.md as
        # the global rules file (mirrors Cursor's ~/.cursor/rules/*.mdc).
        # Markdown format with the same marker block we use for CLAUDE.md /
        # AGENTS.md so doctor can detect & replace stale snippets.
        "path_fn": lambda home: home / ".codeium" / "windsurf" / "memories" / "engram.md",
        "snippet_zh": (
            "\n{marker}\n"
            "## Engram 记忆层\n\n"
            "本机已安装 PIIA Engram（MCP 记忆层）。\n\n"
            "- 会话开始：调用 `get_resume_brief` 接续上一轮工作（跨工具连续性）\n"
            "- 首次/新项目：调用 `get_user_context` 了解用户身份和偏好\n"
            "- 学到经验/踩坑：调用 `add_lesson` 存入\n"
            "- 做出决策：调用 `add_decision` 记录\n"
            "- 任务结束：调用 `wrap_up_session` 保存上下文\n"
            "- 搜索历史知识：调用 `search_knowledge`\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "PIIA Engram (MCP memory layer) is installed on this machine.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (cross-tool continuity)\n"
            "- First time / new project: call `get_user_context` to learn user identity and preferences\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- Task end: call `wrap_up_session` to save context\n"
            "- Search past knowledge: call `search_knowledge`\n"
            "{marker_end}\n"
        ),
    },
}


def _inject_instruction_snippet(
    tool_id: str,
    lang: str = "zh",
    *,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """Inject Engram instruction snippet into a tool's native instruction file.

    Returns the file path on success, or None if skipped/failed.
    Uses marker comments to detect existing snippets and update them.
    Cursor uses .mdc files (no marker needed — entire file is ours).
    """
    snippet_info = _INSTRUCTION_SNIPPETS.get(tool_id)
    if not snippet_info:
        return None

    home = Path.home()
    target_path: Path = snippet_info["path_fn"](home)
    snippet_key = "snippet_zh" if lang == "zh" else "snippet_en"
    snippet = snippet_info[snippet_key]

    # Format markers into snippet
    snippet = snippet.format(
        marker=_INSTRUCTION_MARKER,
        marker_end=_INSTRUCTION_MARKER_END,
    )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if tool_id == "cursor":
            # Cursor .mdc: entire file is ours, just overwrite
            _write_config_text_with_backup(
                target_path,
                snippet,
                backup_root=file_safety_root,
                authorized_external_write=authorized_external_write,
            )
            return str(target_path)

        # For CLAUDE.md / AGENTS.md: append or replace marked section
        existing = ""
        if target_path.is_file():
            existing = target_path.read_text(encoding="utf-8")

        if _INSTRUCTION_MARKER in existing:
            # Replace existing snippet
            start = existing.index(_INSTRUCTION_MARKER)
            end_marker_pos = existing.find(_INSTRUCTION_MARKER_END, start)
            if end_marker_pos >= 0:
                end = end_marker_pos + len(_INSTRUCTION_MARKER_END)
                # Include trailing newline if present
                if end < len(existing) and existing[end] == "\n":
                    end += 1
                existing = existing[:start] + existing[end:]

        # Append snippet
        new_content = existing.rstrip("\n") + "\n" + snippet
        _write_config_text_with_backup(
            target_path,
            new_content,
            backup_root=file_safety_root,
            authorized_external_write=authorized_external_write,
        )
        return str(target_path)

    except Exception as exc:
        logger.warning("instruction injection failed for %s: %s", tool_id, exc)
        return None


def _remove_instruction_snippet(
    tool_id: str,
    *,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> bool:
    """Remove Engram instruction snippet from a tool's native instruction file.

    Returns True if removed, False if not found or failed.
    """
    snippet_info = _INSTRUCTION_SNIPPETS.get(tool_id)
    if not snippet_info:
        return False

    home = Path.home()
    target_path: Path = snippet_info["path_fn"](home)

    try:
        if tool_id == "cursor":
            if target_path.is_file():
                if file_safety_root is not None:
                    from piia_engram.file_safety import delete_external_config_file

                    delete_external_config_file(
                        Path(file_safety_root),
                        target_path,
                        tool="setup",
                        authorized=authorized_external_write,
                    )
                else:
                    target_path.unlink()
                return True
            return False

        if not target_path.is_file():
            return False

        content = target_path.read_text(encoding="utf-8")
        if _INSTRUCTION_MARKER not in content:
            return False

        start = content.index(_INSTRUCTION_MARKER)
        end_marker_pos = content.find(_INSTRUCTION_MARKER_END, start)
        if end_marker_pos < 0:
            return False

        end = end_marker_pos + len(_INSTRUCTION_MARKER_END)
        if end < len(content) and content[end] == "\n":
            end += 1
        # Also remove leading newline if present
        if start > 0 and content[start - 1] == "\n":
            start -= 1

        new_content = content[:start] + content[end:]
        _write_config_text_with_backup(
            target_path,
            new_content,
            backup_root=file_safety_root,
            authorized_external_write=authorized_external_write,
        )
        return True

    except Exception as exc:
        logger.warning("instruction removal failed for %s: %s", tool_id, exc)
        return False


_HOOK_MODULES = {
    "auto_save_on_stop": "piia_engram.hooks.auto_save_on_stop",
    "auto_inject_resume_brief": "piia_engram.hooks.auto_inject_resume_brief",
    "auto_absorb_compact": "piia_engram.hooks.auto_absorb_compact",
}


def _quote_for_shell(value: str) -> str:
    """Cross-platform shell quoting for a path or argument.

    **Strategy**: skip quoting entirely when the value has no shell-
    sensitive characters — an unquoted path works identically in
    ``cmd.exe``, PowerShell, and POSIX shells.  Only when quoting IS
    needed (spaces, CJK, ``&``, ``|``, etc.) do we fall back to
    double-quote wrapping, which is correct for ``cmd.exe`` and POSIX
    but *not* for PowerShell when used in the executable (first-token)
    position.

    **Claude Code hook runner context**: Node.js ``child_process.exec()``
    defaults to ``cmd.exe`` on Windows, so the double-quote fallback is
    correct for the hook use-case today.

    .. warning::

       **PowerShell limitation (H2)**: If the executable path contains
       spaces, the generated command ``"C:\\Program Files\\...\\python.exe"
       -m module`` will fail in PowerShell because PS treats a quoted
       first token as a string expression, not a command invocation.
       PowerShell requires ``& "path" -m module``.  This is NOT a
       problem for Claude Code hooks (cmd.exe), but would break if a
       future hook runner switches to PowerShell.  In that case,
       ``_build_engram_hook_command`` should prefix ``& `` on Windows.
    """
    if not value:
        return '""'
    # Fast path: no quoting needed — works in ALL shells including
    # PowerShell.  Covers the common case of paths without spaces
    # (e.g. "E:/codex-runtimes/.../python.exe").
    _SHELL_SENSITIVE = set(' \t"&|<>()^!%')
    if not any(c in _SHELL_SENSITIVE for c in value):
        return value
    # Slow path: must quote.  Use double quotes (cmd.exe + POSIX).
    out = value.replace('"', '\\"')
    return f'"{out}"'


def _build_engram_hook_command(
    python_path: str,
    *,
    module: str,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Build the ``command`` string for a hook entry.

    Uses ``"{python}" -m piia_engram.hooks.<module>`` so we don't have
    to ship the script outside the wheel and don't have to quote a
    script path. Env hints are passed as ``--env KEY=VAL`` pairs that
    the hook module parses from ``sys.argv`` — that's the only env
    transport that works identically on Windows cmd, PowerShell, and
    POSIX shells without an inline ``KEY=VAL prog`` prefix (which
    Windows shells don't understand).
    """
    parts: list[str] = [_quote_for_shell(python_path), "-m", module]
    if extra_env:
        for key, value in extra_env.items():
            parts.append("--env")
            parts.append(_quote_for_shell(f"{key}={value}"))
    return " ".join(parts)


def _inject_claude_code_hook_for_event(
    python_path: str,
    *,
    event: str,
    module: str,
    status_message: str,
    timeout: int = 30,
    extra_env: dict[str, str] | None = None,
    marker_keywords: tuple[str, ...] = ("piia_engram",),
    async_hook: bool = True,
    force_rewrite: bool = False,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """Register a per-event hook in ``~/.claude/settings.json``.

    Generic core used by Stop / PreCompact / SessionStart / PostCompact
    wiring.

    Args:
        python_path: Absolute path to the python interpreter to invoke.
        event: Claude Code hook event name (``Stop``, ``PreCompact``, etc.).
        module: Dotted python module to run via ``python -m``.
        status_message: ``statusMessage`` field shown in Claude Code UI.
        timeout: Hook timeout in seconds.
        extra_env: Extra env hints; transported as ``--env KEY=VAL`` argv.
        marker_keywords: Strings used to detect "already registered".
        async_hook: ``True`` for fire-and-forget Stop / PreCompact /
            PostCompact; ``False`` for SessionStart, where
            ``additionalContext`` must be written before the first user
            turn is processed.
        force_rewrite: If ``False`` (default), skip when any existing hook
            matches ``marker_keywords`` — backward-compatible idempotent
            behaviour, preserves any user-customised hook. If ``True``,
            *replace* the matching hook's ``command`` (and timeout /
            statusMessage / async) with the freshly built one, so doctor
            ``--fix`` can upgrade stale hooks left behind by older
            versions (e.g. script-path style → ``python -m`` style, or
            hooks whose env markers no longer satisfy the current
            doctor's strict-match check).

    Returns:
        Path to the settings file on success (either newly added or
        rewritten); ``None`` if a matching hook already existed and
        ``force_rewrite`` is False, or on failure.
    """
    try:
        settings_path = Path.home() / ".claude" / "settings.json"

        engram_command = _build_engram_hook_command(
            python_path, module=module, extra_env=extra_env,
        )

        settings: dict = {}
        if settings_path.is_file():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

        hooks = settings.setdefault("hooks", {})
        event_groups = hooks.setdefault(event, [])

        # Look for a matching existing hook.
        existing_hook: dict | None = None
        for event_group in event_groups:
            for hook in event_group.get("hooks", []):
                cmd = hook.get("command", "")
                if any(kw in cmd for kw in marker_keywords):
                    existing_hook = hook
                    break
            if existing_hook is not None:
                break

        if existing_hook is not None:
            if not force_rewrite:
                logger.info(
                    "Engram %s hook already registered (matched marker)", event,
                )
                return None  # Already registered, no rewrite requested

            # Rewrite in place: update the command (and refresh metadata)
            # so doctor --fix can upgrade old hooks left behind by earlier
            # Engram versions.
            existing_cmd = existing_hook.get("command", "")
            if existing_cmd == engram_command:
                logger.info(
                    "Engram %s hook already up to date (no rewrite needed)",
                    event,
                )
                return None  # Same command — nothing to do
            existing_hook["command"] = engram_command
            existing_hook["timeout"] = timeout
            existing_hook["statusMessage"] = status_message
            if async_hook:
                existing_hook["async"] = True
            else:
                existing_hook.pop("async", None)
            logger.info("Engram %s hook rewritten in place", event)
        else:
            # No matching hook — append fresh.
            engram_hook: dict = {
                "type": "command",
                "command": engram_command,
                "timeout": timeout,
                "statusMessage": status_message,
            }
            if async_hook:
                engram_hook["async"] = True

            if event_groups:
                event_groups[0].setdefault("hooks", []).append(engram_hook)
            else:
                event_groups.append({"hooks": [engram_hook]})

        _write_config_text_with_backup(
            settings_path,
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            backup_root=file_safety_root,
            authorized_external_write=authorized_external_write,
        )
        return str(settings_path)

    except Exception as exc:
        logger.warning(
            "Failed to register Engram %s hook: %s", event, exc,
        )
        return None


def _inject_claude_code_hook(
    python_path: str,
    *,
    force_rewrite: bool = False,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """Register Engram Stop hook in Claude Code settings.json.

    Pass ``force_rewrite=True`` (used by ``doctor --fix``) to upgrade an
    older hook in place; default keeps backward-compatible idempotent
    skip behaviour.
    """
    return _inject_claude_code_hook_for_event(
        python_path,
        event="Stop",
        module=_HOOK_MODULES["auto_save_on_stop"],
        status_message="Engram 会话自动保存...",
        timeout=30,
        marker_keywords=("auto_save_on_stop", "piia_engram.hooks.auto_save_on_stop"),
        async_hook=True,
        force_rewrite=force_rewrite,
        file_safety_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


def _inject_claude_code_precompact_hook(
    python_path: str,
    *,
    force_rewrite: bool = False,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """v3.30: register PreCompact hook with asymmetric threshold.

    Fires before Claude Code auto-compacts the transcript, calling the
    auto_save_on_stop module with MIN_TURNS_TO_FLUSH=5 (vs 10 for the
    Stop hook). Asymmetric thresholds prevent short sessions from
    triggering a flush at every minor compaction while still protecting
    long sessions from losing pre-compact state.

    Sets ``CLAUDE_INVOKED_BY=engram_precompact`` so the script can
    detect re-entry (the Claude Agent SDK inside the script would
    otherwise re-fire SessionEnd/PreCompact in an infinite loop).

    Pass ``force_rewrite=True`` to upgrade an old script-path style hook
    to the current ``python -m`` form.
    """
    return _inject_claude_code_hook_for_event(
        python_path,
        event="PreCompact",
        module=_HOOK_MODULES["auto_save_on_stop"],
        status_message="Engram pre-compact 兜底保存...",
        timeout=30,
        extra_env={
            "ENGRAM_MIN_TURNS_TO_FLUSH": "5",
            "CLAUDE_INVOKED_BY": "engram_precompact",
        },
        marker_keywords=("CLAUDE_INVOKED_BY=engram_precompact",),
        async_hook=True,
        force_rewrite=force_rewrite,
        file_safety_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


def _inject_claude_code_sessionstart_hook(
    python_path: str,
    *,
    force_rewrite: bool = False,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """v3.30: register SessionStart hook for resume_brief auto-inject.

    Fires when Claude Code starts a new session. The hook script calls
    ``mcp__engram__get_resume_brief`` and emits the result via the
    ``hookSpecificOutput.additionalContext`` JSON protocol, which Claude
    Code splices into the system prompt for the first user turn.

    This is the "last mile" — without it the AI has to be *told* to call
    get_resume_brief, defeating the "user does zero work" goal.
    """
    return _inject_claude_code_hook_for_event(
        python_path,
        event="SessionStart",
        module=_HOOK_MODULES["auto_inject_resume_brief"],
        status_message="Engram 接续简报注入...",
        timeout=15,
        extra_env={"CLAUDE_INVOKED_BY": "engram_session_start"},
        marker_keywords=(
            "auto_inject_resume_brief",
            "CLAUDE_INVOKED_BY=engram_session_start",
        ),
        # SessionStart must be synchronous: Claude Code only splices
        # ``hookSpecificOutput.additionalContext`` into the first user
        # turn if the hook returned before that turn was assembled.
        # Marking the hook async lets the first turn start before the
        # brief is written, defeating the whole point of the mechanism.
        async_hook=False,
        force_rewrite=force_rewrite,
        file_safety_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


def _inject_claude_code_postcompact_hook(
    python_path: str,
    *,
    force_rewrite: bool = False,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> str | None:
    """v3.30: register PostCompact hook to absorb compact summary.

    Fires AFTER Claude Code compacts the transcript. The hook script
    reads the compacted transcript, extracts the AI-generated summary
    from its head, and appends it to the per-project daily log as a
    "compact" event. Also feeds the summary into extract_session_insights
    for staging-tier auto-extraction.

    Lightweight command-type hook that doesn't spin up a full AI
    conversation just to extract knowledge from the summary.

    Sets ``CLAUDE_INVOKED_BY=engram_postcompact`` for recursion guard.
    """
    return _inject_claude_code_hook_for_event(
        python_path,
        event="PostCompact",
        module=_HOOK_MODULES["auto_absorb_compact"],
        status_message="Engram 压缩摘要吸收中...",
        timeout=30,
        extra_env={
            "CLAUDE_INVOKED_BY": "engram_postcompact",
        },
        marker_keywords=(
            "auto_absorb_compact",
            "piia_engram.hooks.auto_absorb_compact",
        ),
        async_hook=True,
        force_rewrite=force_rewrite,
        file_safety_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


# Tool-specific restart instructions (key = tool config key from _tool_configs)
_RESTART_HINTS: dict[str, tuple[str, str]] = {
    "claude_code": (
        "关闭并重开 VS Code 终端（或命令行窗口）",
        "Close and reopen your VS Code terminal (or command-line window)",
    ),
    "cursor": (
        "Ctrl+Shift+P → Reload Window",
        "Ctrl+Shift+P → Reload Window",
    ),
    "claude_desktop": (
        "完全退出 Claude Desktop 再重新打开",
        "Quit Claude Desktop completely and reopen it",
    ),
    "codex": (
        "关闭 Codex 终端窗口后重新启动",
        "Close the Codex terminal window and restart it",
    ),
    "copilot_vscode": (
        "Ctrl+Shift+P → Reload Window",
        "Ctrl+Shift+P → Reload Window",
    ),
    "windsurf": (
        "关闭并重开 Windsurf 窗口",
        "Close and reopen your Windsurf window",
    ),
    "trae": (
        "Ctrl+Shift+P → Reload Window",
        "Ctrl+Shift+P → Reload Window",
    ),
    "codebuddy": (
        "在 CodeBuddy 的 MCP 设置里点'更新'，或重开 CodeBuddy 窗口",
        "Click 'Update' in CodeBuddy's MCP settings, or reopen the CodeBuddy window",
    ),
}


def _print_restart_hints(configured_tools: list[str] | None = None) -> None:
    """Print tool-specific restart instructions for configured tools."""
    if not configured_tools:
        # Detect configured tools
        configs = _tool_configs()
        configured_tools = []
        for key, tool in configs.items():
            for path in tool.get("config_paths", []):
                if Path(path).is_file():
                    configured_tools.append(key)
                    break

    if not configured_tools:
        print(_t("  重启你的 AI 工具即可使用。",
                 "  Restart your AI tool to apply changes."))
        return

    hints_shown = False
    for key in configured_tools:
        if key in _RESTART_HINTS:
            zh_hint, en_hint = _RESTART_HINTS[key]
            name = _tool_configs().get(key, {}).get("name", key)
            print(f"    {name}: {_t(zh_hint, en_hint)}")
            hints_shown = True

    if not hints_shown:
        print(_t("  重启你的 AI 工具即可使用。",
                 "  Restart your AI tool to apply changes."))


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
    spec = importlib.util.find_spec("piia_engram")
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
                        "format": cfg.get("format", "json"),
                        "server_key": cfg.get("server_key", "mcpServers"),
                    }
                )
                break
    return detected


def _read_mcp_config(config_path: Path, fmt: str = "json") -> dict:
    """读取现有 MCP 配置，不存在或解析失败时返回空结构。

    Args:
        config_path: 配置文件路径。
        fmt: "json" 或 "toml"。
    """
    if not config_path.is_file():
        return {}
    try:
        raw = config_path.read_text(encoding="utf-8")
        if fmt == "toml":
            return _parse_toml(raw)
        return json.loads(raw)
    except Exception as exc:
        logger.warning("read config failed (%s): %s", config_path, exc)
    return {}


def _read_mcp_config_for_write(config_path: Path, fmt: str = "json") -> dict:
    """Read an existing MCP config for mutation; refuse unsafe overwrites.

    The public read helper is intentionally forgiving for doctor/reporting.
    Setup writes need the opposite behavior: if a user's existing config cannot
    be parsed, leave it byte-for-byte intact and surface a clear failure.
    """
    if not config_path.is_file():
        return {}
    raw = config_path.read_text(encoding="utf-8")
    try:
        config = _parse_toml(raw) if fmt == "toml" else json.loads(raw)
    except Exception as exc:
        raise ValueError(
            f"Cannot parse existing {fmt.upper()} config at {config_path}; "
            "refusing to overwrite it. Please fix the config syntax or move "
            "the file aside, then re-run 'engram setup'."
        ) from exc
    if not isinstance(config, dict):
        raise ValueError(
            f"Existing {fmt.upper()} config at {config_path} is not an object; "
            "refusing to overwrite it."
        )
    return config


def _backup_existing_config(config_path: Path) -> Path | None:
    """Create a sibling backup for an existing user config file."""
    if not config_path.is_file():
        return None
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_path = config_path.with_name(f"{config_path.name}.engram-backup.{stamp}")
    counter = 1
    while backup_path.exists():
        backup_path = config_path.with_name(
            f"{config_path.name}.engram-backup.{stamp}.{counter}"
        )
        counter += 1
    shutil.copy2(config_path, backup_path)
    return backup_path


def _write_config_text_with_backup(
    config_path: Path,
    text: str,
    *,
    backup_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> None:
    """Write config text, backing up an existing file before mutation.

    When ``backup_root`` is provided, the write is treated as an explicit
    external client-config mutation and is routed through the central file
    safety layer so backups and metadata-only ledger entries live under the
    selected Engram root.
    """
    if backup_root is not None:
        from piia_engram.file_safety import write_external_config_text

        write_external_config_text(
            Path(backup_root),
            config_path,
            text,
            tool="setup",
            authorized=authorized_external_write,
        )
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.is_file():
        try:
            existing = config_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            existing = None
        if existing == text:
            return
        _backup_existing_config(config_path)
    config_path.write_text(text, encoding="utf-8")


def _parse_toml(text: str) -> dict:
    """解析 TOML 文本，兼容 Python 3.10（无 tomllib）。"""
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ImportError:
        pass
    try:
        import tomli  # third-party fallback
        return tomli.loads(text)
    except ImportError:
        pass
    # 最后手段：只提取 [mcp_servers.*] 段（覆盖 doctor 的核心需求）
    return _parse_toml_mcp_minimal(text)


def _parse_toml_mcp_minimal(text: str) -> dict:
    """从 TOML 文本中最小化提取 mcp_servers 配置。

    只处理 doctor 需要的字段：command, args, env。
    不是通用 TOML 解析器，仅用于 Python 3.10 无 tomllib 的回退。
    """
    import re as _re
    servers: dict = {}
    current_server: str | None = None
    current_section: str | None = None  # "root" | "env"

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # [mcp_servers.NAME]
        m = _re.match(r'^\[mcp_servers\.([a-zA-Z0-9_-]+)\]$', line)
        if m:
            current_server = m.group(1)
            current_section = "root"
            servers[current_server] = {"command": "", "args": [], "env": {}}
            continue

        # [mcp_servers.NAME.env]
        m = _re.match(r'^\[mcp_servers\.([a-zA-Z0-9_-]+)\.env\]$', line)
        if m:
            current_server = m.group(1)
            current_section = "env"
            if current_server not in servers:
                servers[current_server] = {"command": "", "args": [], "env": {}}
            continue

        # Other section header → stop tracking current server
        if line.startswith("["):
            current_server = None
            current_section = None
            continue

        if not current_server:
            continue

        # key = value
        m = _re.match(r'^(\w+)\s*=\s*(.+)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # Unquote string values
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]

        if current_section == "env":
            servers[current_server]["env"][key] = val
        elif key == "command":
            servers[current_server]["command"] = val
        elif key == "args":
            # Parse simple TOML array: ["a", "b"]
            arr_m = _re.findall(r'"([^"]*)"', val)
            servers[current_server]["args"] = arr_m

    return {"mcp_servers": servers}


def _write_mcp_config(
    config_path: Path,
    python_path: str,
    mcp_server_path: str,
    data_dir: str | None = None,
    server_key: str = "mcpServers",
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> None:
    """将 engram 写入指定工具的 MCP 配置（合并，不覆盖其他工具的配置）。
    同时自动清理已知的旧版 server 名称（piia-pkc 等）。
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = _read_mcp_config_for_write(config_path, fmt="json")

    servers = config.get(server_key)
    if servers is None:
        config[server_key] = {}
        servers = config[server_key]
    if not isinstance(servers, dict):
        raise ValueError(
            f"Existing config key '{server_key}' at {config_path} is not an object; "
            "refusing to overwrite it."
        )
    existing_engram = servers.get("engram")
    existing_env = (
        existing_engram.get("env", {})
        if isinstance(existing_engram, dict)
        and isinstance(existing_engram.get("env"), dict)
        else {}
    )

    # 清理旧版 server 名称
    removed = [name for name in LEGACY_SERVER_NAMES if name in servers]
    for name in removed:
        del servers[name]
    if removed:
        print(f"  [migrated] removed legacy server(s): {', '.join(removed)}")

    # Always use `-m piia_engram.mcp_server` (module invocation).
    # Direct .py paths fail with "ImportError: attempted relative import
    # with no known parent package" in all clients that spawn a subprocess.
    env: dict[str, str] = {"PYTHONIOENCODING": "utf-8", "ENGRAM_TOOLS": "all"}

    # If piia_engram is NOT importable from the default sys.path (e.g.
    # editable install via a different Python, or manual source checkout),
    # inject PYTHONPATH so `-m` can still resolve the package.
    spec = importlib.util.find_spec("piia_engram")
    if not spec and mcp_server_path:
        src_dir = _module_src_dir(mcp_server_path)
        env["PYTHONPATH"] = src_dir

    preserved_data_dir = data_dir or existing_env.get("ENGRAM_DIR")
    if preserved_data_dir:
        env["ENGRAM_DIR"] = str(preserved_data_dir)

    entry: dict = {
        "command": python_path,
        "args": ["-m", "piia_engram.mcp_server"],
        "env": env,
    }

    servers["engram"] = entry

    _write_config_text_with_backup(
        config_path,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        backup_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


def _write_mcp_config_toml(
    config_path: Path,
    python_path: str,
    mcp_server_path: str,
    data_dir: str | None = None,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> None:
    """修复 TOML 格式配置文件中的 engram MCP 条目（如 Codex config.toml）。

    策略：原地替换 [mcp_servers.engram] 段，保留文件其余内容不动。
    """
    existing_env: dict = {}
    if config_path.is_file():
        existing_config = _read_mcp_config_for_write(config_path, fmt="toml")
        existing_servers = existing_config.get("mcp_servers", {})
        existing_engram = (
            existing_servers.get("engram", {})
            if isinstance(existing_servers, dict)
            else {}
        )
        if isinstance(existing_engram, dict) and isinstance(existing_engram.get("env"), dict):
            existing_env = existing_engram["env"]

    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.is_file() else []
    new_lines: list[str] = []
    skip_until_next_section = False

    def toml_string(value: str) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    # 构建新的 engram 条目
    # 优先用 -m 模块调用（不依赖绝对路径）
    engram_block = [
        '[mcp_servers.engram]',
        f'command = {toml_string(python_path)}',
        f'args = ["-m", "piia_engram.mcp_server"]',
        '',
        '[mcp_servers.engram.env]',
        'PYTHONIOENCODING = "utf-8"',
        'ENGRAM_TOOLS = "all"',
    ]
    # 如果 mcp_server_path 不是通过 -m 可达的（不在 site-packages），加 PYTHONPATH
    spec = importlib.util.find_spec("piia_engram")
    if not spec:
        # piia_engram 不在默认路径，需要 PYTHONPATH
        src_dir = _module_src_dir(mcp_server_path)
        engram_block.append(f'PYTHONPATH = {toml_string(src_dir)}')
    preserved_data_dir = data_dir or existing_env.get("ENGRAM_DIR")
    if preserved_data_dir:
        engram_block.append(f'ENGRAM_DIR = {toml_string(str(preserved_data_dir))}')

    inserted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 检测 [mcp_servers.engram] 段开始
        if stripped == '[mcp_servers.engram]':
            skip_until_next_section = True
            if not inserted:
                new_lines.extend(engram_block)
                inserted = True
            i += 1
            continue

        # 检测 [mcp_servers.engram.env] 子段（也要跳过）
        if stripped == '[mcp_servers.engram.env]':
            skip_until_next_section = True
            i += 1
            continue

        # 遇到其他段头，结束跳过
        if stripped.startswith('[') and skip_until_next_section:
            skip_until_next_section = False

        if not skip_until_next_section:
            new_lines.append(line)

        i += 1

    # 如果原文件没有 engram 段，追加到末尾
    if not inserted:
        new_lines.append('')
        new_lines.extend(engram_block)

    _write_config_text_with_backup(
        config_path,
        '\n'.join(new_lines) + '\n',
        backup_root=file_safety_root,
        authorized_external_write=authorized_external_write,
    )


def _write_tool_mcp_config(
    tool: dict,
    python_path: str,
    mcp_server_path: str,
    data_dir: str | None = None,
    file_safety_root: str | Path | None = None,
    authorized_external_write: bool = False,
) -> None:
    """Write an MCP config using the target client's declared format."""
    if tool.get("format", "json") == "toml":
        _write_mcp_config_toml(
            tool["config_path"],
            python_path,
            mcp_server_path,
            data_dir,
            file_safety_root=file_safety_root,
            authorized_external_write=authorized_external_write,
        )
        return
    _write_mcp_config(
        tool["config_path"],
        python_path,
        mcp_server_path,
        data_dir,
        server_key=tool.get("server_key", "mcpServers"),
        file_safety_root=file_safety_root,
        authorized_external_write=authorized_external_write,
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


def _candidate_engram_roots(default_data_dir: str) -> list[str]:
    """Return install/root choices for the Engram data folder."""
    choices: list[str] = [str(Path(default_data_dir).expanduser())]
    if platform.system() == "Windows":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive_root = Path(f"{letter}:\\")
            try:
                if not drive_root.exists():
                    continue
            except OSError:
                continue
            candidate = str(drive_root / ".engram")
            if candidate not in choices:
                choices.append(candidate)
    return choices


def _choose_data_dir(default_data_dir: str) -> str:
    """Ask where the Engram root should live."""
    choices = _candidate_engram_roots(default_data_dir)
    print(_t("\n  Engram 数据文件夹：", "\n  Engram data folder:"))
    for idx, choice in enumerate(choices, start=1):
        label = _t("默认", "default") if idx == 1 else _t("磁盘", "drive")
        print(f"    {idx}. {choice} ({label})")
    print(_t("    c. 自定义路径", "    c. Custom path"))

    answer = _prompt(
        _t("  选择数据位置，或直接输入自定义路径", "  Choose data location, or type a custom path"),
        "1",
    ).strip()
    if not answer:
        return choices[0]
    if answer.lower() in {"c", "custom", "自定义"}:
        custom = _prompt(_t("  请输入 Engram 数据文件夹路径", "  Enter Engram data folder path")).strip()
        return str(Path(custom).expanduser()) if custom else choices[0]
    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(choices):
            return choices[index - 1]
    return str(Path(answer).expanduser())


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
        from piia_engram.core import Engram
    except ImportError:  # pragma: no cover - direct script fallback
        from core import Engram  # type: ignore
    return Engram


# ---------------------------------------------------------------------------
# Cold-start: environment probing
# ---------------------------------------------------------------------------

def _probe_environment(cwd: Path | None = None) -> dict:
    """Silently extract identity signals from the user's dev environment.

    Returns a dict with discovered signals:
      name, email, language_hint, tech_stack_hint, commit_style
    All fields are optional — any failure is silently ignored.
    """
    signals: dict = {}
    current_dir = cwd or Path.cwd()

    # 1. Git config → name, email
    for key, field in [("user.name", "name"), ("user.email", "email")]:
        try:
            r = subprocess.run(
                ["git", "config", "--global", key],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                signals[field] = r.stdout.strip().decode("utf-8", errors="replace")
        except Exception:
            pass

    # 2. Git log → language hint, commit style
    try:
        r = subprocess.run(
            ["git", "log", "--format=%s", "-50"],
            capture_output=True, timeout=5,
            cwd=str(current_dir),
        )
        if r.returncode == 0 and r.stdout.strip():
            msgs = r.stdout.strip().decode("utf-8", errors="replace").splitlines()
            # Language detection: if >40% messages contain CJK → zh
            cjk_count = sum(1 for m in msgs if any('\u4e00' <= c <= '\u9fff' for c in m))
            if msgs and cjk_count / len(msgs) > 0.4:
                signals["language_hint"] = "中文"
            # Conventional commits detection
            conventional = sum(1 for m in msgs if re.match(r'^(feat|fix|chore|docs|refactor|test|ci|build)[:(]', m))
            if msgs and conventional / len(msgs) > 0.3:
                signals["commit_style"] = "conventional commits"
    except Exception:
        pass

    # 3. Project file detection → tech stack hint
    tech_hints: list[str] = []
    detectors = [
        ("pyproject.toml", "Python"), ("setup.py", "Python"),
        ("requirements.txt", "Python"), ("Pipfile", "Python"),
        ("package.json", "TypeScript / JavaScript"),
        ("tsconfig.json", "TypeScript"),
        ("Cargo.toml", "Rust"), ("go.mod", "Go"),
        ("pom.xml", "Java"), ("build.gradle", "Java"),
        ("Gemfile", "Ruby"),
    ]
    for filename, tech in detectors:
        if (current_dir / filename).exists() and tech not in tech_hints:
            tech_hints.append(tech)
    if tech_hints:
        signals["tech_stack_hint"] = " + ".join(tech_hints[:3])

    return signals


# ---------------------------------------------------------------------------
# Cold-start: seed templates
# ---------------------------------------------------------------------------

_SEED_TEMPLATES: dict[str, list[dict]] = {
    "Python": [
        {"summary": "Pin dependency versions in pyproject.toml or requirements.txt", "domain": "python"},
        {"summary": "Use virtual environments (venv/conda) to isolate project dependencies", "domain": "python"},
    ],
    "TypeScript / JavaScript": [
        {"summary": "Enable strict mode in tsconfig.json for better type safety", "domain": "javascript"},
        {"summary": "Prefer named exports over default exports for refactoring ease", "domain": "javascript"},
    ],
    "TypeScript": [
        {"summary": "Enable strict mode in tsconfig.json for better type safety", "domain": "javascript"},
    ],
    "Go": [
        {"summary": "Always check returned errors — never use blank identifier for errors", "domain": "go"},
    ],
    "Rust": [
        {"summary": "Prefer Result over unwrap in production code paths", "domain": "rust"},
    ],
    "Java": [
        {"summary": "Use try-with-resources for auto-closeable objects", "domain": "java"},
    ],
    "_universal": [
        {"summary": "Write commit messages explaining why, not just what", "domain": "git"},
        {"summary": "Add comments for non-obvious business logic, not for self-documenting code", "domain": "best-practices"},
        {"summary": "Test edge cases, not just happy paths", "domain": "testing"},
    ],
}


def _apply_seed_templates(engram, tech_stack: str) -> int:
    """Inject starter lessons based on detected tech stack. Returns count added."""
    templates: list[dict] = list(_SEED_TEMPLATES.get("_universal", []))

    # Match tech stack keywords
    for key, items in _SEED_TEMPLATES.items():
        if key.startswith("_"):
            continue
        if key.lower() in tech_stack.lower():
            templates.extend(items)

    added = 0
    for t in templates:
        try:
            result = engram.add_lesson(
                t["summary"], domain=t["domain"],
                source_tool="engram_setup", tier="staging",
            )
            if result.get("status") != "duplicate":
                added += 1
        except Exception:
            pass
    return added


def _run_seed_knowledge_onboarding(
    data_dir: str | None = None,
    cwd: Path | None = None,
    external_config_applied: bool = True,
) -> dict:
    """Guide first-time users to save enough seed context for get_user_context."""
    Engram = _get_engram_class()
    root = Path(data_dir).expanduser().resolve() if data_dir else Path.home() / ".engram"
    root.mkdir(parents=True, exist_ok=True)
    engram = Engram(root=root)
    current_dir = cwd or Path.cwd()

    # --- Environment probing (zero-interaction) ---
    print(_t("  🔍 正在探测开发环境…", "  🔍 Probing dev environment…"))
    env_signals = _probe_environment(cwd=current_dir)

    probed_parts: list[str] = []
    if env_signals.get("name"):
        probed_parts.append(_t(f"姓名: {env_signals['name']}", f"Name: {env_signals['name']}"))
    if env_signals.get("tech_stack_hint"):
        probed_parts.append(_t(f"技术栈: {env_signals['tech_stack_hint']}", f"Tech: {env_signals['tech_stack_hint']}"))
    if env_signals.get("language_hint"):
        probed_parts.append(_t(f"语言: {env_signals['language_hint']}", f"Language: {env_signals['language_hint']}"))
    if env_signals.get("commit_style"):
        probed_parts.append(_t(f"提交风格: {env_signals['commit_style']}", f"Commits: {env_signals['commit_style']}"))

    if probed_parts:
        print(_t("  ✅ 自动探测到：", "  ✅ Auto-detected:"))
        for p in probed_parts:
            _safe_print(f"     {p}")
        print()
    else:
        print(_t("  （未探测到额外信息）\n", "  (no extra signals detected)\n"))

    # Auto-fill name from git config
    if env_signals.get("name"):
        existing_profile = engram.get_profile()
        if not existing_profile.get("name"):
            engram.update_profile({"name": env_signals["name"]})

    print(_t("Step 2/3 — 录入种子知识（输入 0 跳过）\n",
             "Step 2/3 — Seed knowledge (enter 0 to skip)\n"))

    # Pre-select tech stack options: put detected stack first
    tech_options = [
        "Python",
        "TypeScript / JavaScript",
        "Go",
        "Java",
        "Rust",
        "Python + TypeScript",
    ]
    detected_tech = env_signals.get("tech_stack_hint", "")
    # Move detected tech to front if it matches an option
    for i, opt in enumerate(tech_options):
        if detected_tech and opt.lower() in detected_tech.lower():
            tech_options.insert(0, tech_options.pop(i))
            break

    role = _choice(_t("你的角色是什么？", "What is your role?"), [
        _t("全栈开发者", "Full-stack developer"),
        _t("后端开发者", "Backend developer"),
        _t("前端开发者", "Frontend developer"),
        _t("产品经理", "Product manager"),
        _t("数据科学家", "Data scientist"),
        _t("学生", "Student"),
    ])
    print()
    tech_stack = _choice(_t("你常用什么编程语言/技术栈？", "Primary language / tech stack?"), tech_options)
    print()

    # Pre-select language: put detected language first
    lang_options = [
        "中文",
        "English",
        _t("日本语", "Japanese"),
    ]
    if env_signals.get("language_hint") == "中文":
        pass  # already first
    elif env_signals.get("language_hint"):
        # Non-Chinese detected, move English first
        lang_options = ["English", "中文", _t("日本语", "Japanese")]

    language = _choice(_t("你偏好 AI 用什么语言跟你沟通？",
                          "Preferred language for AI communication?"), lang_options)

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

    # --- Seed templates (auto-inject best practices) ---
    effective_tech = tech_stack or env_signals.get("tech_stack_hint", "")
    seed_count = 0
    if effective_tech:
        seed_count = _apply_seed_templates(engram, effective_tech)
        if seed_count:
            print(_t(f"\n  🌱 已注入 {seed_count} 条通用最佳实践（基于 {effective_tech}）",
                     f"\n  🌱 Injected {seed_count} starter best practices (based on {effective_tech})"))
            print(_t("     这些标记为 staging——使用 3 次后自动晋升为 verified。",
                     "     These are marked staging — review confirms what becomes verified."))

    # Step 4.5 — 智能扫描 + 分流导入
    print(_t("\n  智能导入规则文件",
             "\n  Smart rule file import"))
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

        import_result = _import_with_split(rule_files, engram)
        print(_t(f"\n  ✅ 已导入: {import_result['user_count']} 条身份 + {import_result['project_count']} 条项目规则",
                 f"\n  ✅ Imported: {import_result['user_count']} identity + {import_result['project_count']} project rules"))
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
    if seed_count > 0:
        print(_t(f"  种子：{seed_count} 条最佳实践（staging 层级）",
                 f"  Seeds: {seed_count} best practices (staging tier)"))
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
                print(_t("  Tip: export this for non-MCP tools with get_identity_card.",
                         "  Tip: export this for non-MCP tools with get_identity_card."))
                print()
        except Exception:
            pass  # Non-critical — skip silently

    # --- Refresh quick_context.md so all tools can read it immediately ---
    try:
        _e2 = Engram(root=root)
        _e2.refresh_quick_context()
        print(_t("  📄 quick_context.md 已刷新 — 所有 AI 工具都可以立即读取。",
                 "  📄 quick_context.md refreshed — all AI tools can read it immediately."))
        print()
    except Exception:
        pass

    # --- Post-setup checklist ---
    print("========================================")
    print("  Next steps:")
    print("========================================\n")
    if external_config_applied:
        print("  1. Restart your AI tool:")
        _print_restart_hints()
        print('  2. Say to AI: "Sync Engram context"')
        print("  3. Confirm AI knows your role and preferences")
        print("  4. Run 'engram doctor' anytime to check health\n")
    else:
        print("  1. Engram data is initialized; external AI tool configs were not changed.")
        print("  2. To connect AI tools automatically, run: engram setup --apply-external-config")
        print("  3. Or manually add the Engram MCP entry using the detected tool paths above.")
        print("  4. Run 'engram doctor' anytime to check health\n")

    return {
        "profile": profile_updates,
        "lessons_added": lessons_added,
        "seed_count": seed_count,
        "env_signals": env_signals,
        "imported_files": import_result["files"],
        "import_user_count": import_result["user_count"],
        "import_project_count": import_result["project_count"],
    }


# ---------------------------------------------------------------------------
# Privacy & data preferences (telemetry opt-in + reconcile authorization)
# ---------------------------------------------------------------------------

def _run_privacy_preferences(data_dir: str) -> None:
    """Ask user about auto-reconcile and anonymous usage statistics."""
    from piia_engram.telemetry import (
        _load_config, _save_config, set_enabled, set_remote_enabled,
    )

    cfg = _load_config()

    print(_t("\nStep 5 — 隐私与数据偏好", "\nStep 5 — Privacy & data preferences"))
    print(_t("  你的数据默认只留在本机。以下可选功能需要你明确同意。\n",
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
    print(_t("        • Engram 版本号 / 操作系统 / Python 版本",
             "        • Engram version / OS / Python version"))
    print(_t("      绝不发送：知识内容、prompt、文件路径、邮箱、IP 地址",
             "      Never sent: knowledge content, prompts, file paths, email, IP"))
    print(_t(f"      本地日志位置：{data_dir}/telemetry.log",
             f"      Local log location: {data_dir}/telemetry.log"))
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
        print(_t("  ✅ 已开启本地统计\n",
                 "  ✅ Local statistics enabled\n"))
        # --- Remote sending (Phase 2) ---
        print(_t("  [2b] 远程匿名统计（帮助开发者改进 Engram）",
                 "  [2b] Remote anonymous statistics (help improve Engram)"))
        print(_t("      同样的匿名数据，每日发送一次到 Engram 开发团队。",
                 "      Same anonymous data, sent once daily to the Engram team."))
        print(_t("      数据通过 HTTPS 发送到 Cloudflare Worker，不经过任何第三方。",
                 "      Data sent via HTTPS to Cloudflare Worker, no third parties."))
        print(_t("      发送失败不会影响任何功能（静默跳过）。",
                 "      Send failures are silently ignored (never affects functionality)."))
        print(_t("      随时关闭：engram telemetry remote off\n",
                 "      Disable anytime: engram telemetry remote off\n"))

        remote_enabled = _yn(
            _t("  同时开启远程发送？",
               "  Also enable remote sending?"),
            default=False,
        )
        set_remote_enabled(remote_enabled)
        if remote_enabled:
            print(_t("  ✅ 远程统计已开启\n",
                     "  ✅ Remote statistics enabled\n"))
        else:
            print(_t("  ℹ️  仅本地统计。可随时运行 engram telemetry remote on 开启远程。\n",
                     "  ℹ️  Local only. Run engram telemetry remote on to enable remote anytime.\n"))
    else:
        set_remote_enabled(False)
        print(_t("  ℹ️  未开启。可随时运行 engram telemetry on 改变。\n",
                 "  ℹ️  Not enabled. Run engram telemetry on to change anytime.\n"))

    # Save reconcile pref to same config file
    cfg_all = _load_config()
    cfg_all["reconcile_authorized"] = reconcile_authorized
    _save_config(cfg_all)


def _run_privacy_defaults(data_dir: str) -> None:
    """Set reconcile=on by default, then ask about telemetry."""
    from piia_engram.telemetry import (
        _load_config, _save_config, set_enabled, set_feedback_enabled,
        set_remote_enabled,
    )

    cfg = _load_config()
    cfg["reconcile_authorized"] = True
    _save_config(cfg)

    # --- Ask about telemetry — one question, all-or-nothing ---
    print(_t("  [匿名使用统计]",
             "  [Anonymous Usage Statistics]"))
    print(_t("      帮助我们了解哪些功能被使用、哪些需要改进。",
             "      Help us understand which features are used and need improvement."))
    print(_t("      包含：工具调用次数、知识条目数、每周治理概况",
             "      Includes: tool call counts, knowledge totals, weekly governance summary"))
    print(_t("      绝不包含：知识内容、prompt、文件路径、邮箱、IP",
             "      Never includes: knowledge content, prompts, file paths, email, IP"))
    print(_t("      随时关闭：engram telemetry off\n",
             "      Disable anytime: engram telemetry off\n"))

    enabled = _yn(
        _t("  开启匿名使用统计？",
           "  Enable anonymous usage statistics?"),
        default=True,
    )
    set_enabled(enabled)
    set_remote_enabled(enabled)
    set_feedback_enabled(enabled)
    if enabled:
        print(_t("  ✅ 已开启（含每周匿名反馈报告）\n",
                 "  ✅ Enabled (including weekly anonymous feedback reports)\n"))
    else:
        print(_t("  ℹ️  未开启。可随时运行 engram telemetry on 改变。\n",
                 "  ℹ️  Not enabled. Run engram telemetry on to change anytime.\n"))


# ---------------------------------------------------------------------------
# 向导主流程
# ---------------------------------------------------------------------------

def run_setup(advanced: bool = False, apply_external_config: bool = False) -> None:
    """交互式安装向导主流程。

    Streamlined flow: auto-detect tools and initialize Engram. External AI
    client config files are read-only by default; automatic mutation requires
    ``apply_external_config=True`` or the CLI flag ``--apply-external-config``.

    Args:
        advanced: If True, show full interactive privacy preferences.
        apply_external_config: If True, mutate detected AI client configs with
            backup protection. If False, only detect and print guidance.
    """
    global _lang
    _configure_utf8_stdio()

    # 语言选择（最先，决定后续所有文案）
    print("\n  Language / 语言选择:")
    print("    1. 中文")
    print("    2. English")
    lang_answer = _prompt("  Choose / 请选择", "1").strip()
    _lang = "en" if lang_answer == "2" else "zh"
    _set_lang(_lang)
    print()

    print("========================================")
    print(_t("  PIIA Engram 安装向导", "  PIIA Engram Setup Wizard"))
    print("========================================\n")

    # Step 1 — 自动检测环境
    print(_t("Step 1/3 — 检测环境", "Step 1/3 — Detecting environment"))
    python_path = _find_python()
    if not python_path:
        print(_t("❌ 未找到可用的 Python 3.10+。", "❌ Python 3.10+ not found."))
        print(_t("   请安装 Python 后重新运行：https://python.org/downloads/",
                 "   Please install Python and re-run: https://python.org/downloads/"))
        sys.exit(1)
    print(f"  ✅ Python: {python_path}")

    mcp_server_path = _find_mcp_server()
    if not mcp_server_path:
        print(_t("❌ 未找到 mcp_server.py，请确认已正确安装（pip install piia-engram）。",
                 "❌ mcp_server.py not found. Please ensure piia-engram is installed."))
        sys.exit(1)

    # 数据目录 — 优先读 ENGRAM_DIR 环境变量
    default_data_dir = os.environ.get("ENGRAM_DIR") or str(Path.home() / ".engram")
    selected_data_dir = _choose_data_dir(default_data_dir)
    # Keep setup-time Engram() calls aligned with the folder the user selected.
    os.environ["ENGRAM_DIR"] = selected_data_dir
    print(_t(f"  ✅ 数据目录: {selected_data_dir}",
             f"  ✅ Data dir: {selected_data_dir}"))

    # 工具检测 — 默认只读；显式授权时才自动配置外部客户端文件。
    tools = _detect_tools()
    success: list[str] = []
    failed: list[str] = []
    if not tools:
        print(_t("  ⚠️  未检测到 AI 工具（Claude Code / Cursor / Claude Desktop）",
                 "  ⚠️  No AI tools detected (Claude Code / Cursor / Claude Desktop)"))
        print(_t("  安装后重新运行 'engram setup' 即可。\n",
                 "  Re-run 'engram setup' after installing.\n"))
    elif not apply_external_config:
        detected_names = ", ".join(tool["name"] for tool in tools)
        print(_t(
            f"  🔎 已检测到 AI 工具（只读检查）：{detected_names}",
            f"  🔎 Detected AI tools (read-only check): {detected_names}",
        ))
        print(_t(
            "  ℹ️  默认不会更改 Claude/Codex/Zed/Cursor 等外部配置文件。",
            "  ℹ️  By default, setup does not modify external Claude/Codex/Zed/Cursor config files.",
        ))
        print(_t(
            "      如需自动配置并创建备份，请运行：engram setup --apply-external-config",
            "      To auto-configure with backups, run: engram setup --apply-external-config",
        ))
    else:
        configured_tool_ids = []
        for tool in tools:
            try:
                _write_tool_mcp_config(
                    tool,
                    python_path,
                    mcp_server_path,
                    selected_data_dir,
                    file_safety_root=selected_data_dir,
                    authorized_external_write=True,
                )
                success.append(tool["name"])
                configured_tool_ids.append(tool["id"])
            except Exception as exc:
                failed.append(f"{tool['name']} ({exc})")
        for name in success:
            print(_t(f"  ✅ {name} 已配置", f"  ✅ {name} configured"))
        for name in failed:
            print(_t(f"  ❌ {name} 配置失败", f"  ❌ {name} failed"))

        # Inject instruction snippets into each tool's native instruction file
        # so AI proactively calls Engram (not relying solely on MCP instructions)
        injected = []
        for tool_id in configured_tool_ids:
            result = _inject_instruction_snippet(
                tool_id,
                _lang,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
            )
            if result:
                injected.append(result)
        if injected:
            print()
            print(_t("  📝 已注入 AI 指令（确保 AI 主动调用 Engram）：",
                     "  📝 Injected AI instructions (ensures AI calls Engram proactively):"))
            for path in injected:
                print(f"    {path}")

        # Register Claude Code Stop hook for session auto-save
        if "claude_code" in configured_tool_ids:
            hook_result = _inject_claude_code_hook(
                python_path,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
            )
            if hook_result:
                print(_t(f"  🔗 已注册 Claude Code 会话结束 Hook：{hook_result}",
                         f"  🔗 Registered Claude Code Stop hook: {hook_result}"))
            # v3.30 mechanism (4): PreCompact hook with MIN_TURNS=5 — fires
            # before Claude Code auto-compacts the transcript, so long
            # sessions don't lose pre-compact state.
            pre_result = _inject_claude_code_precompact_hook(
                python_path,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
            )
            if pre_result:
                print(_t(f"  🔗 已注册 PreCompact 兜底 Hook（v3.30）",
                         f"  🔗 Registered PreCompact safety-net hook (v3.30)"))
            # v3.30 mechanism (6): SessionStart auto-inject resume brief.
            ss_result = _inject_claude_code_sessionstart_hook(
                python_path,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
            )
            if ss_result:
                print(_t(f"  🔗 已注册 SessionStart 接续简报 Hook（v3.30）",
                         f"  🔗 Registered SessionStart resume-brief hook (v3.30)"))
            # v3.30 R4: PostCompact hook — absorb compact summary into daily log.
            pc_result = _inject_claude_code_postcompact_hook(
                python_path,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
            )
            if pc_result:
                print(_t(f"  🔗 已注册 PostCompact 摘要吸收 Hook（v3.30）",
                         f"  🔗 Registered PostCompact summary-absorb hook (v3.30)"))
    print()

    # Step 2 — 录入身份信息
    _run_seed_knowledge_onboarding(
        selected_data_dir,
        external_config_applied=apply_external_config,
    )

    # Step 3 — 隐私偏好
    if advanced:
        _run_privacy_preferences(selected_data_dir)
    else:
        _run_privacy_defaults(selected_data_dir)

    # 完成
    print(_t("  重启你的 AI 工具即可使用：",
             "  Setup next step:"))
    if apply_external_config:
        _print_restart_hints()
    else:
        print("  External AI tool configs are unchanged.")
        print("  To connect tools automatically, run: engram setup --apply-external-config")
    print()
    print(_t("  觉得有用？来聊聊你怎么用的：",
             "  Find Engram useful? Share how you use it:"))
    print("  https://github.com/Patdolitse/piia-engram/discussions\n")
    print(_t("  遇到问题？",
             "  Issues?"))
    print("  https://github.com/Patdolitse/piia-engram/issues\n")

    # Save setup report for activation funnel tracking (local only)
    _save_setup_report(
        selected_data_dir,
        tools,
        success,
        failed,
        external_config_mode="apply" if apply_external_config else "read_only",
    )


def _save_setup_report(
    data_dir: str,
    detected_tools: list[dict],
    success: list[str],
    failed: list[str],
    external_config_mode: str = "apply",
) -> None:
    """Save a local setup report for activation funnel tracking.

    Appends to ~/.engram/setup_report.jsonl (one JSON line per setup run).
    No network calls — purely local for later analysis.
    """
    try:
        from datetime import datetime, timezone

        try:
            from importlib.metadata import version as _pkg_version
            ver = _pkg_version("piia-engram")
        except Exception:
            ver = "unknown"

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": ver,
            "os": platform.system(),
            "python": platform.python_version(),
            "tools_detected": [t.get("name", t.get("id", "?")) for t in detected_tools],
            "tools_configured": success,
            "tools_failed": failed,
            "external_config_mode": external_config_mode,
            "language": _lang,
            "status": "success" if not failed else "partial",
        }

        report_path = Path(data_dir) / "setup_report.jsonl"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Non-critical — never fail setup over reporting


def auto_migrate() -> None:
    """升级后首次启动时静默迁移旧配置，每个版本只运行一次。

    由 mcp_server.py 在 stdio 模式启动前调用。
    不向 stdout 输出任何内容（避免破坏 MCP 协议）。
    迁移日志写入 ~/.engram/migration.log。
    """
    try:
        import os as _os
        from datetime import datetime, timezone

        # 确定数据目录和哨兵文件
        data_dir = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
        sentinel = data_dir / ".migrated_version"

        # 读取当前版本
        try:
            from piia_engram import __version__ as _ver  # type: ignore[import]
        except Exception:
            return

        # 已迁移过则跳过
        if sentinel.is_file() and sentinel.read_text(encoding="utf-8").strip() == _ver:
            return

        # 扫描所有工具配置，清理旧版名称
        # Do not mutate external client files from MCP startup. External
        # config writes are explicit setup/doctor actions.
        log_lines: list[str] = []
        for _tool_id, cfg in _tool_configs().items():
            fmt = cfg.get("format", "json")
            server_key = cfg.get("server_key", "mcpServers")
            for config_path in cfg["config_paths"]:
                if not config_path.is_file():
                    continue
                config = _read_mcp_config(config_path, fmt=fmt)
                servers = config.get(server_key, {})
                stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
                if not stale:
                    continue
                log_lines.append(
                    f"  {config_path}: detected legacy server(s) {stale}; "
                    "external config left unchanged"
                )

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
                f.write(
                    "  Run 'engram setup --apply-external-config' or "
                    "'engram doctor --fix' to update external client configs.\n"
                )

        # Telemetry: stays off by default — user opts in via `engram setup`
        # or `engram telemetry on`. Local-first trust > data collection.

    except Exception as exc:
        logger.warning("migration failed: %s", exc)


def _detect_installed_tools() -> list[dict]:
    """扫描系统中实际安装的 AI 工具。

    不仅检查配置文件是否存在，还检查工具本身是否安装（配置目录存在）。
    返回 [{tool_id, name, config_path, format, verified, status, config, servers}]。
    - status: "configured" (有 engram 条目), "installed" (工具在但没配 engram)
    - verified: True = 团队实测过, False = 社区级支持
    """
    results = []
    for tool_id, cfg in _tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        verified = cfg.get("verified", False)
        for config_path in cfg["config_paths"]:
            # 检查工具是否安装（配置目录存在 = 工具装了）
            tool_dir = config_path.parent
            if not tool_dir.exists():
                continue

            config = _read_mcp_config(config_path, fmt=fmt)

            # 按工具的 server_key 取 MCP servers 段
            servers = config.get(server_key, {})
            # TOML 回退：也检查下划线变体
            if not servers and server_key == "mcpServers":
                servers = config.get("mcp_servers", {})
            has_engram = "engram" in servers

            results.append({
                "tool_id": tool_id,
                "name": cfg["name"],
                "config_path": config_path,
                "format": fmt,
                "server_key": server_key,
                "verified": verified,
                "status": "configured" if has_engram else "installed",
                "config": config,
                "servers": servers,
            })
            break  # 每个工具只取第一个匹配的路径
    return results


def _file_sha256_12(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except (OSError, PermissionError):
        return ""


def _servers_from_config(config: dict, server_key: str) -> dict:
    servers = config.get(server_key, {})
    if isinstance(servers, dict) and servers:
        return servers
    if server_key == "mcpServers":
        fallback = config.get("mcp_servers", {})
    elif server_key == "mcp_servers":
        fallback = config.get("mcpServers", {})
    else:
        fallback = {}
    return fallback if isinstance(fallback, dict) else {}


def _shared_instruction_candidates(home: Path) -> list[Path]:
    data_dir = Path(os.environ.get("ENGRAM_DIR", "") or home / ".engram")
    candidates = [
        data_dir / "shared_instructions.md",
        home / ".piia" / "shared_instructions.md",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def _claude_hook_rows(home: Path) -> list[dict]:
    settings_path = home / ".claude" / "settings.json"
    settings_exists = settings_path.is_file()
    settings: dict = {}
    if settings_exists:
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    hook_specs = (
        (
            "Stop",
            "Stop",
            ("auto_save_on_stop", "piia_engram.hooks.auto_save_on_stop"),
            "any",
        ),
        (
            "PreCompact",
            "PreCompact",
            ("CLAUDE_INVOKED_BY=engram_precompact",
             "piia_engram.hooks.auto_save_on_stop"),
            "all",
        ),
        (
            "SessionStart",
            "SessionStart (resume brief inject)",
            ("auto_inject_resume_brief",
             "piia_engram.hooks.auto_inject_resume_brief"),
            "any",
        ),
        (
            "PostCompact",
            "PostCompact (summary absorb)",
            ("auto_absorb_compact",
             "piia_engram.hooks.auto_absorb_compact"),
            "any",
        ),
    )

    rows: list[dict] = []
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else {}
    for event, label, markers, match_mode in hook_specs:
        registered = False
        matcher = all if match_mode == "all" else any
        for event_group in hooks.get(event, []) if isinstance(hooks, dict) else []:
            for hook in event_group.get("hooks", []):
                cmd = str(hook.get("command", ""))
                if matcher(marker in cmd for marker in markers):
                    registered = True
                    break
            if registered:
                break
        rows.append({
            "event": event,
            "label": label,
            "settings_path": str(settings_path),
            "settings_exists": settings_exists,
            "registered": registered,
            "sha256_12": _file_sha256_12(settings_path) if settings_exists else "",
        })
    return rows


def _build_config_integrity_report(cwd: Path | None = None) -> dict:
    """Build a metadata-only portability/integrity report for local AI config.

    The report intentionally includes hashes, counts, booleans, and paths, but
    never returns instruction file bodies or project rule lines.
    """
    mcp_configs: list[dict] = []
    for tool_id, cfg in _tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        for raw_path in cfg.get("config_paths", []):
            path = Path(raw_path)
            exists = path.is_file()
            config = _read_mcp_config(path, fmt=fmt) if exists else {}
            servers = _servers_from_config(config, server_key)
            mcp_configs.append({
                "tool_id": tool_id,
                "name": cfg.get("name", tool_id),
                "path": str(path),
                "format": fmt,
                "server_key": server_key,
                "verified": bool(cfg.get("verified", False)),
                "parent_exists": path.parent.exists(),
                "exists": exists,
                "configured": "engram" in servers,
                "legacy_servers": [
                    name for name in LEGACY_SERVER_NAMES if name in servers
                ],
                "sha256_12": _file_sha256_12(path) if exists else "",
            })

    home = Path.home()
    instruction_files: list[dict] = []
    for tool_id, info in _INSTRUCTION_SNIPPETS.items():
        path = Path(info["path_fn"](home))
        exists = path.is_file()
        content = ""
        if exists:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                content = ""
        if tool_id == "cursor":
            has_marker = bool(content.strip()) and (
                "engram" in content.lower() or _SNIPPET_FRESHNESS_TOKEN in content
            )
        else:
            has_marker = _INSTRUCTION_MARKER in content or (
                "<!-- piia-engram:auto-injected -->" in content
            )
        instruction_files.append({
            "tool_id": tool_id,
            "path": str(path),
            "exists": exists,
            "has_marker": has_marker,
            "has_resume_brief": _SNIPPET_FRESHNESS_TOKEN in content,
            "line_count": len(content.splitlines()) if content else 0,
            "sha256_12": _file_sha256_12(path) if exists else "",
        })

    shared_instruction_files: list[dict] = []
    for path in _shared_instruction_candidates(home):
        exists = path.is_file()
        line_count = 0
        if exists:
            try:
                line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            except (OSError, PermissionError):
                line_count = 0
        shared_instruction_files.append({
            "path": str(path),
            "exists": exists,
            "line_count": line_count,
            "sha256_12": _file_sha256_12(path) if exists else "",
        })

    claude_hooks = _claude_hook_rows(home)

    project_rules: list[dict] = []
    for rule in _scan_rule_files(cwd=cwd):
        path = Path(rule["path"])
        lines = list(rule.get("lines", []))
        content_line_count = len([
            line for line in lines
            if line.strip() and not line.strip().startswith("#")
        ])
        project_rules.append({
            "path": str(path),
            "scope": rule.get("scope", ""),
            "line_count": len(lines),
            "content_line_count": content_line_count,
            "sha256_12": _file_sha256_12(path),
        })

    summary = {
        "mcp_config_paths": len(mcp_configs),
        "mcp_configs_found": sum(1 for row in mcp_configs if row["exists"]),
        "mcp_configs_configured": sum(1 for row in mcp_configs if row["configured"]),
        "instruction_files": len(instruction_files),
        "instruction_files_found": sum(1 for row in instruction_files if row["exists"]),
        "instruction_files_fresh": sum(
            1 for row in instruction_files
            if row["exists"] and row["has_resume_brief"]
        ),
        "project_rule_files": sum(1 for row in project_rules if row["scope"] == "project"),
        "legacy_server_configs": sum(1 for row in mcp_configs if row["legacy_servers"]),
        "shared_instruction_files_found": sum(
            1 for row in shared_instruction_files if row["exists"]
        ),
        "claude_hooks_registered": sum(1 for row in claude_hooks if row["registered"]),
        "claude_hooks_total": len(claude_hooks),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "mcp_configs": mcp_configs,
        "instruction_files": instruction_files,
        "shared_instruction_files": shared_instruction_files,
        "claude_hooks": claude_hooks,
        "project_rules": project_rules,
        "live_store_modified": False,
    }


def _print_config_integrity_report(report: dict) -> None:
    _safe_print("\n  -- Config Integrity --\n")
    summary = report.get("summary", {})
    mcp_total = int(summary.get("mcp_config_paths", 0) or 0)
    mcp_found = int(summary.get("mcp_configs_found", 0) or 0)
    mcp_configured = int(summary.get("mcp_configs_configured", 0) or 0)
    instruction_total = int(summary.get("instruction_files", 0) or 0)
    instruction_found = int(summary.get("instruction_files_found", 0) or 0)
    instruction_fresh = int(summary.get("instruction_files_fresh", 0) or 0)
    project_rules = int(summary.get("project_rule_files", 0) or 0)
    legacy_configs = int(summary.get("legacy_server_configs", 0) or 0)
    shared_found = int(summary.get("shared_instruction_files_found", 0) or 0)
    hooks_registered = int(summary.get("claude_hooks_registered", 0) or 0)
    hooks_total = int(summary.get("claude_hooks_total", 0) or 0)

    mcp_status = "[ok]" if legacy_configs == 0 else "[--]"
    print(
        f"    {mcp_status} MCP configs: "
        f"{mcp_found}/{mcp_total} files found, {mcp_configured} configured"
    )
    snippet_status = "[ok]" if instruction_found == instruction_fresh else "[--]"
    print(
        f"    {snippet_status} Instruction files: "
        f"{instruction_found}/{instruction_total} found, {instruction_fresh} fresh"
    )
    print(f"    [ok] Project rule files: {project_rules} found")
    print(f"    [ok] Shared instructions: {shared_found} found")
    hook_status = "[ok]" if hooks_registered == hooks_total else "[--]"
    print(f"    {hook_status} Claude hooks: {hooks_registered}/{hooks_total} registered")
    print("    [ok] Report is metadata-only (hashes + counts; no rule bodies)")


def _entry_args(entry: dict) -> list[str]:
    """Return MCP entry args as strings."""
    args = entry.get("args", [])
    if args is None:
        return []
    if not isinstance(args, list):
        return []
    return [str(arg) for arg in args]


def _classify_engram_entry(entry: dict) -> dict:
    """Classify an MCP ``engram`` entry and build a safe ``--help`` probe."""
    command = str(entry.get("command") or "").strip()
    raw_args = entry.get("args", [])
    args = _entry_args(entry)

    if not command:
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry is missing command",
            "probe_argv": None,
        }
    if raw_args is not None and not isinstance(raw_args, list):
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry args must be a list",
            "probe_argv": None,
        }

    if command == "uvx":
        if args[:3] == ["--from", "piia-engram", "piia-engram-mcp"]:
            return {
                "severity": "ok",
                "style": "recommended-uvx",
                "message": "Entry point style: recommended uvx zero-install",
                "probe_argv": [command, *args, "--help"],
            }
        return {
            "severity": "warn",
            "style": "uvx-other",
            "message": (
                "uvx entry should use: "
                "--from piia-engram piia-engram-mcp"
            ),
            "probe_argv": None,
        }

    if command == "piia-engram-mcp":
        return {
            "severity": "ok",
            "style": "recommended-console-script",
            "message": "Entry point style: recommended installed console script",
            "probe_argv": [command, *args, "--help"],
        }

    for index, arg in enumerate(args):
        if arg == "-m" and index + 1 < len(args):
            module_name = args[index + 1]
            if module_name == "piia_engram.mcp_server":
                return {
                    "severity": "ok",
                    "style": "compatible-python-module",
                    "message": "Entry point style: compatible python module",
                    "probe_argv": [command, *args, "--help"],
                }
            if "engram_core" in module_name:
                return {
                    "severity": "warn",
                    "style": "legacy-module",
                    "message": (
                        f"Uses old module name '{module_name}', use "
                        f"'{module_name.replace('engram_core', 'piia_engram')}'"
                    ),
                    "probe_argv": None,
                }

    if any(str(arg).endswith("mcp_server.py") for arg in args):
        return {
            "severity": "warn",
            "style": "legacy-script-path",
            "message": (
                "Uses direct mcp_server.py path; use "
                '["-m", "piia_engram.mcp_server"] or piia-engram-mcp'
            ),
            "probe_argv": None,
        }

    return {
        "severity": "warn",
        "style": "unknown",
        "message": (
            "Unknown MCP entry style; expected piia-engram-mcp, uvx, "
            "or python -m piia_engram.mcp_server"
        ),
        "probe_argv": None,
    }


def _probe_mcp_entry(entry: dict, *, timeout: int = 5) -> str | None:
    """Run a bounded ``--help`` probe for safe MCP entry shapes."""
    classification = _classify_engram_entry(entry)
    probe_argv = classification.get("probe_argv")
    if not probe_argv:
        return None
    try:
        result = subprocess.run(
            probe_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"MCP launch probe timed out after {timeout}s"
    except Exception as exc:
        return f"MCP launch probe failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        return f"MCP launch probe exited with code {result.returncode}{suffix}"
    return None


def _validate_engram_entry(servers: dict, config_path: Path) -> list[str]:
    """验证 engram MCP 条目的所有路径是否有效。

    Returns:
        问题描述列表（空 = 健康）。
    """
    issues = []
    engram = servers.get("engram", {})
    if not engram:
        return issues

    # Entry-point style + bounded launchability probe. The probe only runs
    # for shapes where `_classify_engram_entry` can build a safe `--help`.
    classification = _classify_engram_entry(engram)
    if classification["severity"] in ("warn", "error"):
        issues.append(classification["message"])
    if classification.get("probe_argv"):
        probe_issue = _probe_mcp_entry(engram)
        if probe_issue:
            issues.append(probe_issue)

    # 1. Python 可执行路径
    python_exe = engram.get("command", "")
    non_path_commands = {"npx", "node", "uvx", "piia-engram-mcp"}
    if python_exe and python_exe not in non_path_commands:
        exe_path = Path(python_exe.replace("\\\\", "\\"))
        if not exe_path.is_file():
            issues.append(f"Python 路径不存在: {python_exe}")

    # 2. 服务端脚本/模块路径
    args = engram.get("args") or []
    uses_module_invocation = "-m" in args
    for arg in args:
        if arg.startswith("-"):
            # -m module.name — 不是文件路径，跳过
            continue
        p = Path(arg.replace("\\\\", "\\"))
        # 只验证看起来像路径的参数（含 / 或 \ 或 .py）
        if ("/" in arg or "\\" in arg or arg.endswith(".py")):
            if not p.is_file():
                issues.append(f"脚本路径不存在: {arg}")
            if not uses_module_invocation and arg.endswith(".py"):
                # Direct .py invocation causes ImportError on relative imports.
                issues.append(
                    f"使用直接 .py 路径调用 '{arg}'，会导致 ImportError。"
                    f"应改为 args: [\"-m\", \"piia_engram.mcp_server\"]"
                )

    # 3. 检查 -m 模块调用是否指向旧模块名
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            module_name = args[i + 1]
            if "engram_core" in module_name:
                issues.append(
                    f"使用旧模块名 '{module_name}'，应改为 "
                    f"'{module_name.replace('engram_core', 'piia_engram')}'"
                )

    # 4. 环境变量中的路径
    env = engram.get("env", {})
    for key, val in env.items():
        if key in ("ENGRAM_DIR", "PYTHONPATH") and val:
            p = Path(val.replace("\\\\", "\\"))
            if not p.exists():
                issues.append(f"环境变量 {key} 路径不存在: {val}")

    return issues


def run_doctor(fix: bool = False) -> int:
    """扫描系统中所有已安装的 AI 工具，检查 Engram MCP 配置健康状况。

    流程：
    1. 扫描系统中装了哪些 AI 工具
    2. 检查哪些已配置 Engram、哪些还没配
    3. 验证已配置的条目路径是否有效（stale path 检测）
    4. 可选自动修复

    Args:
        fix: True 时自动修复发现的问题。

    Returns:
        发现的问题数量（0 = 健康）。
    """
    _configure_utf8_stdio()
    print("\n========================================")
    print("  Engram Doctor - Config Health Check")
    print("========================================\n")

    # ── 第一步：扫描已安装的 AI 工具 ──
    tools = _detect_installed_tools()

    if not tools:
        print("  [!] No supported AI tools detected on this system.\n")
        print("  Verified: Claude Code, Claude Desktop, Cursor, Codex")
        print("  Community: Windsurf, Copilot, Cline, Roo Code, Amazon Q, Augment, Zed\n")
        return 0

    print(f"  Detected {len(tools)} AI tool(s):\n")

    verified_tools = [t for t in tools if t.get("verified")]
    community_tools = [t for t in tools if not t.get("verified")]
    configured_count = 0
    unconfigured: list[dict] = []

    if verified_tools:
        print("  Verified (team tested):")
        for t in verified_tools:
            if t["status"] == "configured":
                _safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                _safe_print(f"    [--] {t['name']} — Engram NOT configured")
                unconfigured.append(t)

    if community_tools:
        if verified_tools:
            print()
        print("  Community-supported (untested by our team):")
        for t in community_tools:
            if t["status"] == "configured":
                _safe_print(f"    [ok] {t['name']} — Engram configured")
                configured_count += 1
            else:
                _safe_print(f"    [--] {t['name']} — installed, Engram not configured")
                unconfigured.append(t)
    print()

    # ── 第二步：验证已配置的条目 ──
    issues: list[tuple[dict, str]] = []  # (tool_info, 描述)

    for t in tools:
        if t["status"] != "configured":
            continue
        servers = t["servers"]

        # 旧版 server 名称
        stale = [n for n in LEGACY_SERVER_NAMES if n in servers]
        if stale:
            issues.append((t, f"包含旧版 server: {', '.join(stale)}"))

        # 路径验证（核心：stale path 检测）
        path_issues = _validate_engram_entry(servers, t["config_path"])
        for desc in path_issues:
            issues.append((t, desc))

    # ── 第三步：报告 ──
    if unconfigured:
        print(f"  [info] {len(unconfigured)} tool(s) detected but Engram not configured:")
        for t in unconfigured:
            print(f"    - {t['name']} ({t['config_path']})")
        print("    Run 'engram setup' to configure them.\n")

    if not issues:
        if configured_count > 0:
            print("  [ok] All configured tools look healthy.\n")
        func_issues = _run_functional_checks(fix=fix)
        return func_issues

    print(f"  [!] Found {len(issues)} issue(s):\n")
    for t, desc in issues:
        print(f"  {t['name']} ({t['config_path']})")
        print(f"    -> {desc}")
    print()

    if not fix:
        print("  Run 'engram doctor --fix' to auto-repair.\n")
        return len(issues)

    # ── 第四步：自动修复 ──
    python_path = _find_python()
    mcp_server_path = _find_mcp_server()
    if not python_path or not mcp_server_path:
        print("  [error] Cannot auto-fix: Python 3.10+ or mcp_server.py not found.")
        print("          Run 'engram setup' to complete installation first.\n")
        return len(issues)

    fixed = 0
    file_safety_root = Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    seen_paths: set[Path] = set()
    for t, _ in issues:
        path = t["config_path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            _write_tool_mcp_config(
                t,
                python_path,
                mcp_server_path,
                file_safety_root=file_safety_root,
                authorized_external_write=True,
            )
            print(f"  [fixed] {t['name']} ({path})")
            fixed += 1
        except Exception as exc:
            print(f"  [error] {t['name']} ({path}): {exc}")

    remaining = len(issues) - fixed
    print(f"\n  Done: {fixed} config(s) updated.")
    print(_t("  重启以下工具生效：", "  Restart the following tools to apply:"))
    _print_restart_hints()
    print()
    func_issues = _run_functional_checks(fix=fix)
    return remaining + func_issues


def _run_functional_checks(*, fix: bool = False) -> int:
    """运行功能性验证：MCP server 能否启动、知识库能否读写、quick_context 是否可用。

    Args:
        fix: If True, attempt to auto-repair issues (e.g. refresh stale quick_context.md).

    Returns:
        发现的问题数量（0 = 健康）。
    """
    _safe_print("  -- Functional Checks --\n")
    problems = 0

    # 1. 核心库导入
    try:
        from piia_engram.core import Engram  # noqa: F811

        print("    [ok] piia_engram.core importable")
    except Exception as exc:
        print(f"    [!!] piia_engram.core import failed: {exc}")
        problems += 1
        return problems  # 后续检查都依赖 core

    # 2. Engram 实例化（读取 ~/.engram/）
    try:
        eng = Engram()
        print(f"    [ok] Engram initialized ({eng.root})")
    except Exception as exc:
        print(f"    [!!] Engram init failed: {exc}")
        problems += 1
        return problems

    # 3. 身份数据读取
    try:
        profile = eng.get_profile()
        role = profile.get("role", "")
        if role:
            print(f"    [ok] Identity loaded (role: {role})")
        else:
            print("    [--] Identity empty — run 'engram setup' to create your profile")
    except Exception as exc:
        print(f"    [!!] Profile read failed: {exc}")
        problems += 1

    # 4. quick_context.md 可用性 + 过期检测
    qc = eng.root / "quick_context.md"
    if qc.exists() and qc.stat().st_size > 0:
        # Check staleness: compare mtime with identity/knowledge files
        qc_mtime = qc.stat().st_mtime
        stale = False
        source_dirs = [eng.root / "identity", eng.root / "knowledge"]
        for src_dir in source_dirs:
            if not src_dir.is_dir():
                continue
            for src_file in src_dir.iterdir():
                if src_file.is_file() and src_file.stat().st_mtime > qc_mtime:
                    stale = True
                    break
            if stale:
                break
        if stale:
            if fix:
                try:
                    eng.refresh_quick_context()
                    print(f"    [fixed] quick_context.md refreshed ({qc.stat().st_size} bytes)")
                except Exception as exc:
                    print(f"    [!!] quick_context.md refresh failed: {exc}")
                    problems += 1
            else:
                print("    [--] quick_context.md is stale (identity/knowledge updated since last generation)")
                print("         Run 'engram doctor --fix' to regenerate.")
        else:
            print(f"    [ok] quick_context.md ready ({qc.stat().st_size} bytes)")
    else:
        if fix:
            try:
                eng.refresh_quick_context()
                print(f"    [fixed] quick_context.md generated ({qc.stat().st_size} bytes)")
            except Exception as exc:
                print(f"    [!!] quick_context.md generation failed: {exc}")
                problems += 1
        else:
            print("    [--] quick_context.md missing or empty — cold-start will be slower")

    # 5. MCP server 工具注册
    try:
        from piia_engram import mcp_server  # noqa: F811

        tool_count = len(mcp_server.mcp._tool_manager._tools)
        print(f"    [ok] MCP server: {tool_count} tools registered")
    except Exception as exc:
        print(f"    [!!] MCP server import failed: {exc}")
        problems += 1

    problems += _run_terminal_encoding_check()

    try:
        _print_config_integrity_report(_build_config_integrity_report(cwd=Path.cwd()))
    except Exception as exc:
        print(f"    [!!] Config integrity check failed: {exc}")
        problems += 1

    problems += _run_continuity_checks(eng)

    # 6. AI instruction snippet injection status
    # v3.31 P0: doctor now checks BOTH presence AND freshness. A snippet
    # that lacks _SNIPPET_FRESHNESS_TOKEN ("get_resume_brief") was injected
    # by v3.30 or earlier and is missing the cross-tool resume directive;
    # doctor --fix overwrites it with the current snippet.
    print()
    _safe_print("  -- AI Instruction Snippets --\n")
    home = Path.home()
    snippet_found = False
    missing_snippets: list[str] = []  # path missing OR file lacks snippet
    stale_snippets: list[str] = []    # snippet present but missing freshness token
    for tool_id, info in _INSTRUCTION_SNIPPETS.items():
        target_path = info["path_fn"](home)
        if not target_path.is_file():
            _safe_print(f"    [--] {tool_id}: no instruction file")
            missing_snippets.append(tool_id)
            continue
        try:
            content = target_path.read_text(encoding="utf-8")
        except Exception:
            _safe_print(f"    [--] {tool_id}: file exists but unreadable")
            continue

        # Cursor mdc is entirely ours; everything else uses the marker.
        if tool_id == "cursor":
            present = bool(content.strip())
        else:
            present = _INSTRUCTION_MARKER in content or (
                # back-compat: also match v=1 marker before bump
                "<!-- piia-engram:auto-injected -->" in content
            )

        if not present:
            _safe_print(f"    [--] {tool_id}: file exists but no Engram snippet")
            missing_snippets.append(tool_id)
            continue

        if _SNIPPET_FRESHNESS_TOKEN not in content:
            _safe_print(
                f"    [stale] {tool_id}: snippet missing "
                f"'{_SNIPPET_FRESHNESS_TOKEN}' directive (pre-v3.31)"
            )
            stale_snippets.append(tool_id)
        else:
            _safe_print(f"    [ok] {tool_id}: snippet up to date in {target_path}")
            snippet_found = True

    refresh_targets = missing_snippets + stale_snippets

    if refresh_targets and fix:
        # Detect language from existing identity
        lang = "zh"
        try:
            profile = eng.get_profile()
            pref_lang = profile.get("language", "")
            if pref_lang and "en" in pref_lang.lower():
                lang = "en"
        except Exception:
            pass
        fixed_snippets = []
        for tool_id in refresh_targets:
            result = _inject_instruction_snippet(
                tool_id,
                lang=lang,
                file_safety_root=eng.root,
                authorized_external_write=True,
            )
            if result:
                action = "refreshed" if tool_id in stale_snippets else "fixed"
                fixed_snippets.append(f"[{action}] {tool_id}: {result}")
        if fixed_snippets:
            print()
            for s in fixed_snippets:
                _safe_print(f"    {s}")
    elif refresh_targets and not fix:
        print()
        if stale_snippets:
            print(
                "    [info] Stale snippets detected — missing the "
                "cross-tool resume directive."
            )
        else:
            print("    [info] Missing AI instruction snippets.")
        print("           Run 'engram doctor --fix' to update them.")
        print("           Without the latest snippet, AI may not auto-resume across tools.")
    elif not snippet_found:
        print()
        print("    [info] No AI instruction snippets found.")
        print("           Run 'engram setup' to inject them.")

    # ── Claude Code Hooks (Stop / PreCompact / SessionStart) ──
    # v3.30 M7: doctor must check all three events the setup wizard
    # registers, not only Stop. Otherwise users who upgrade from
    # v3.29.x and run ``engram doctor --fix`` end up missing PreCompact
    # (mechanism 4) and SessionStart (mechanism 6) silently.
    print()
    _safe_print("  -- Claude Code Hooks --\n")
    settings_path = Path.home() / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}

    hook_specs = (
        # (event, human label, markers, match_mode, injector)
        # match_mode: "any" = any marker hit → ok (Stop, SessionStart)
        #             "all" = ALL markers must hit → ok (PreCompact)
        (
            "Stop",
            "Stop",
            ("auto_save_on_stop", "piia_engram.hooks.auto_save_on_stop"),
            "any",
            _inject_claude_code_hook,
        ),
        (
            "PreCompact",
            "PreCompact",
            # M3 fix: require BOTH the module AND the env marker.
            # A hook with just the module name (no CLAUDE_INVOKED_BY=
            # engram_precompact) is a misconfigured Stop-style hook that
            # would use the wrong flush threshold.
            ("CLAUDE_INVOKED_BY=engram_precompact",
             "piia_engram.hooks.auto_save_on_stop"),
            "all",
            _inject_claude_code_precompact_hook,
        ),
        (
            "SessionStart",
            "SessionStart (resume brief inject)",
            ("auto_inject_resume_brief",
             "piia_engram.hooks.auto_inject_resume_brief"),
            "any",
            _inject_claude_code_sessionstart_hook,
        ),
        (
            "PostCompact",
            "PostCompact (summary absorb)",
            ("auto_absorb_compact",
             "piia_engram.hooks.auto_absorb_compact"),
            "any",
            _inject_claude_code_postcompact_hook,
        ),
    )

    python_path_for_fix: str | None = None
    for event, label, markers, match_mode, injector in hook_specs:
        found = False
        matcher = all if match_mode == "all" else any
        for event_group in settings.get("hooks", {}).get(event, []):
            for hook in event_group.get("hooks", []):
                cmd = hook.get("command", "")
                if matcher(m in cmd for m in markers):
                    found = True
                    break
            if found:
                break

        if found:
            _safe_print(f"    [ok] {label} hook registered")
            continue

        _safe_print(f"    [--] No Engram {label} hook in Claude Code settings")
        if fix:
            if python_path_for_fix is None:
                python_path_for_fix = _find_python() or ""
            if python_path_for_fix:
                # v3.30.1: pass force_rewrite=True so doctor --fix can
                # upgrade stale hooks (e.g. script-path style → -m form,
                # or hooks whose env markers no longer satisfy doctor's
                # strict-match check). Without force_rewrite, idempotent
                # skip would let "PreCompact present but stale" survive.
                result = injector(
                    python_path_for_fix,
                    force_rewrite=True,
                    file_safety_root=eng.root,
                    authorized_external_write=True,
                )
                if result:
                    _safe_print(f"    [fixed] {label} hook registered: {result}")
                else:
                    _safe_print(
                        f"    [info] {label} hook already up to date"
                    )
            else:
                _safe_print("    [info] Cannot fix: Python not found")
        else:
            print(
                f"           Run 'engram doctor --fix' or 'engram setup' "
                f"to register the {label} hook."
            )

    print()
    _safe_print("  -- Encoding health --\n")
    try:
        from piia_engram.encoding_repair import repair_engram_root, scan_engram_root

        if fix:
            repair_report = repair_engram_root(eng.root, apply=True)
            if repair_report.repairable_count:
                _safe_print(
                    "    [fixed] Encoding health: repaired "
                    f"{repair_report.repairable_count} text field(s) in "
                    f"{len(repair_report.changed_files)} file(s)"
                )
                if repair_report.backup_dir is not None:
                    _safe_print(f"           Backup: {repair_report.backup_dir}")
            if repair_report.suspect_count:
                _safe_print(
                    "    [!!] Encoding health: "
                    f"{repair_report.suspect_count} suspect mojibake field(s) "
                    "need manual review"
                )
                problems += 1
            elif not repair_report.repairable_count:
                print("    [ok] Encoding health: no mojibake detected")
        else:
            scan_report = scan_engram_root(eng.root)
            if scan_report.repairable_count or scan_report.suspect_count:
                _safe_print(
                    "    [!!] Encoding health: found "
                    f"{scan_report.repairable_count} repairable mojibake field(s) "
                    f"and {scan_report.suspect_count} suspect mojibake field(s)"
                )
                for finding in scan_report.findings[:3]:
                    _safe_print(f"         - {finding.relative_path}:{finding.json_path}")
                print("           Run 'engram doctor --fix' or 'engram repair-encoding --apply'.")
                problems += 1
            else:
                print("    [ok] Encoding health: no mojibake detected")
    except Exception as exc:
        print(f"    [!!] Encoding health check failed: {exc}")
        problems += 1

    print()
    return problems


def _format_session_size(size_bytes: int) -> str:
    """Human-readable byte count for the small sessions table."""
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        size = 0
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _parse_sessions_limit(raw: str | None, *, default: int = 20) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 200))


_SESSION_SCAN_LIMIT = 100_000


def _run_continuity_checks(eng) -> int:
    """Doctor section for cross-tool session continuity.

    This is intentionally informational for the empty-state case: a clean
    fresh install may have no saved sessions yet, so that must not make doctor
    exit nonzero.
    """
    print()
    _safe_print("  -- Continuity --\n")
    problems = 0

    try:
        all_sessions = eng.list_agent_sessions(limit=_SESSION_SCAN_LIMIT)
        recent = all_sessions[:1]
    except Exception as exc:
        print(f"    [!!] Agent session listing failed: {exc}")
        return 1

    if not recent:
        print("    [--] No saved agent sessions yet")
        print("         Run an AI session, then wrap up or stop the tool to create one.")
    else:
        latest = recent[0]
        tools = sorted({str(s.get("tool", "")) for s in all_sessions if s.get("tool")})
        _safe_print(
            "    [ok] Agent sessions: "
            f"{len(all_sessions)} saved across {len(tools)} tool(s); "
            f"latest {latest.get('tool', '?')}/{latest.get('session_id', '?')} "
            f"at {latest.get('modified_at', '?')}"
        )

    try:
        brief = eng.get_resume_brief(token_budget=400)
        included = brief.get("sections_included", []) if isinstance(brief, dict) else []
        print(f"    [ok] Resume brief builds ({len(included)} section(s))")
    except Exception as exc:
        print(f"    [!!] Resume brief failed: {exc}")
        problems += 1

    return problems


def _print_sessions_usage() -> None:
    print(
        "Usage:\n"
        "  engram sessions [--tool TOOL] [--limit N]\n"
        "  engram sessions show <session_id> [--tool TOOL]\n"
    )


def run_sessions(argv: list[str] | None = None) -> int:
    """List or show saved cross-tool agent sessions."""
    _configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_sessions_usage()
        return 0

    from piia_engram.core import Engram  # local import keeps setup startup light

    eng = Engram()

    if args and args[0] == "show":
        if len(args) < 2:
            _print_sessions_usage()
            return 2
        session_id = args[1]
        tool = ""
        i = 2
        while i < len(args):
            if args[i] == "--tool" and i + 1 < len(args):
                tool = args[i + 1]
                i += 2
            else:
                print(f"Unknown sessions option: {args[i]}")
                _print_sessions_usage()
                return 2

        metadata = eng.list_agent_sessions(tool=tool, limit=_SESSION_SCAN_LIMIT)
        match = next((s for s in metadata if s.get("session_id") == session_id), None)
        if match is None:
            print(f"Session not found: {session_id}")
            return 1

        session_path = eng.root / "contexts" / str(match.get("tool", "")) / f"{session_id}.md"
        try:
            content = session_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Session not readable: {session_id} ({exc})")
            return 1

        _safe_print(f"# Session {match.get('tool', '?')}/{session_id}")
        _safe_print(f"Modified: {match.get('modified_at', '?')}\n")
        _safe_print(content)
        return 0

    tool = ""
    limit = 20
    i = 0
    while i < len(args):
        if args[i] == "--tool" and i + 1 < len(args):
            tool = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = _parse_sessions_limit(args[i + 1])
            i += 2
        else:
            print(f"Unknown sessions option: {args[i]}")
            _print_sessions_usage()
            return 2

    sessions = eng.list_agent_sessions(tool=tool, limit=limit)
    if not sessions:
        if tool:
            print(f"No saved agent sessions found for tool: {tool}")
        else:
            print("No saved agent sessions yet.")
        print("Run an AI session, then wrap up or stop the tool to create one.")
        return 0

    title = f"Recent agent sessions ({len(sessions)})"
    if tool:
        title += f" for {tool}"
    print(title)
    print("modified_at           tool          session_id                 size")
    print("-------------------   -----------   ------------------------   --------")
    for s in sessions:
        _safe_print(
            f"{s.get('modified_at', '?'):<21} "
            f"{s.get('tool', '?'):<13} "
            f"{s.get('session_id', '?'):<26} "
            f"{_format_session_size(s.get('size_bytes', 0))}"
        )
    print("\nUse 'engram sessions show <session_id>' to print a session.")
    return 0


def _print_review_usage() -> None:
    print(
        "Usage:\n"
        "  engram review [--limit N] [--sort recent|quality|quality-desc] [--low-quality]\n"
        "  engram review show <id>\n"
        "  engram review approve <id> --yes\n"
        "  engram review archive <id> --yes\n"
    )


def _review_title(item_type: str, item: dict) -> str:
    if item_type == "decision":
        title = item.get("question") or item.get("title") or ""
        choice = item.get("choice") or ""
        return f"{title} -> {choice}" if choice else str(title)
    return str(item.get("summary") or item.get("title") or "")


def _review_quality_summary(item: dict) -> str:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict) or not extraction:
        return "-"
    parts: list[str] = []
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        parts.append(f"q={score:.2f}")
    method = str(extraction.get("method") or "").strip()
    if method:
        parts.append(_truncate_review_text(method, 16))
    return " ".join(parts) if parts else "-"


def _review_quality_score(item: dict) -> float | None:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict):
        return None
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        return float(score)
    return None


def _clean_review_inline(value: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value or ""))
    return " ".join(text.split())


def _truncate_review_text(value: str, limit: int = 180) -> str:
    text = _clean_review_inline(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _print_review_quality_detail(item: dict) -> None:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict) or not extraction:
        return
    method = str(extraction.get("method") or "").strip()
    source_tool = str(extraction.get("source_tool") or "").strip()
    source = _truncate_review_text(method, 48) if method else "unknown"
    if source_tool:
        source = f"{source} via {_truncate_review_text(source_tool, 48)}"
    _safe_print(f"source: {source}")

    quality_parts: list[str] = []
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        quality_parts.append(f"q={score:.2f}")
    signals = extraction.get("quality_signals")
    if isinstance(signals, list) and signals:
        quality_parts.append("signals=" + ",".join(_truncate_review_text(s, 32) for s in signals[:6]))
    flags = extraction.get("quality_flags")
    if isinstance(flags, list) and flags:
        quality_parts.append("flags=" + ",".join(_truncate_review_text(f, 32) for f in flags[:6]))
    if quality_parts:
        _safe_print("quality: " + "; ".join(quality_parts))

    evidence = str(extraction.get("evidence_span") or "").strip()
    if evidence:
        _safe_print(f"evidence: {_truncate_review_text(evidence)}")


def _review_items(
    eng,
    *,
    limit: int = 20,
    sort: str = "recent",
    low_quality_only: bool = False,
) -> list[dict]:
    """Return staging lessons/decisions for the terminal review queue.

    The explicit ``_update_access=False`` is part of the contract: listing the
    queue must not mutate access counters or timestamps.
    """
    rows: list[dict] = []
    for item in eng.get_lessons(limit=None, _update_access=False):
        if item.get("tier") == "staging":
            rows.append({"type": "lesson", "item": item})
    for item in eng.get_decisions(limit=None, _update_access=False):
        if item.get("tier") == "staging":
            rows.append({"type": "decision", "item": item})

    if low_quality_only:
        rows = [
            row for row in rows
            if (score := _review_quality_score(row.get("item") or {})) is None or score < 0.70
        ]

    def sort_key(row: dict) -> str:
        item = row.get("item") or {}
        return str(item.get("timestamp") or item.get("created_at") or item.get("id") or "")

    def quality_sort_key(row: dict, missing_score: float) -> tuple[float, str]:
        score = _review_quality_score(row.get("item") or {})
        return (score if score is not None else missing_score, sort_key(row))

    if sort == "quality":
        rows.sort(key=lambda row: quality_sort_key(row, 99.0))
    elif sort == "quality-desc":
        rows.sort(key=lambda row: quality_sort_key(row, -1.0), reverse=True)
    else:
        rows.sort(key=sort_key, reverse=True)
    return rows[:limit]


def _print_review_list(rows: list[dict]) -> None:
    if not rows:
        print("No staging knowledge needs review.")
        return
    print(f"Staging knowledge review queue ({len(rows)})")
    print("type       id                         domain        quality          title")
    print("---------  -------------------------  ------------  ---------------  ------------------------------")
    for row in rows:
        item_type = row["type"]
        item = row["item"]
        title = _review_title(item_type, item)
        if len(title) > 70:
            title = title[:67] + "..."
        quality = _review_quality_summary(item)
        _safe_print(
            f"{item_type:<9}  "
            f"{str(item.get('id', '?')):<25}  "
            f"{str(item.get('domain', ''))[:12]:<12}  "
            f"{quality:<15}  "
            f"{title}"
        )
    print("\nUse 'engram review show <id>' to inspect one item.")
    print("Use 'engram review approve <id> --yes' or 'engram review archive <id> --yes'.")


def _print_review_item(item_type: str, item: dict) -> None:
    print(f"type: {item_type}")
    print(f"id: {item.get('id', '')}")
    print(f"tier: {item.get('tier', '')}")
    print(f"status: {item.get('status', '')}")
    if item.get("domain"):
        print(f"domain: {item.get('domain')}")
    if item_type == "decision":
        _safe_print(f"question: {item.get('question') or item.get('title') or ''}")
        _safe_print(f"choice: {item.get('choice', '')}")
        if item.get("reasoning"):
            _safe_print(f"reasoning: {item.get('reasoning')}")
    else:
        _safe_print(f"summary: {item.get('summary') or item.get('title') or ''}")
        if item.get("detail"):
            _safe_print(f"detail: {item.get('detail')}")
    _print_review_quality_detail(item)


def _require_yes(args: list[str], action: str) -> bool:
    if "--yes" in args:
        return True
    print(f"Refusing to {action} without explicit --yes.")
    return False


def _print_playbook_usage() -> None:
    print(
        "Engram Playbook CLI\n\n"
        "Usage:\n"
        "  engram playbook install <builtin-name> [--yes] [--project <folder>]\n\n"
        "Default is dry-run. Pass --yes to write a built-in Playbook."
    )


def _arg_value(args: list[str], *names: str) -> str:
    for name in names:
        if name in args:
            idx = args.index(name)
            if idx + 1 >= len(args):
                return ""
            return args[idx + 1]
    return ""


def run_playbook(argv: list[str] | None = None) -> int:
    """Local CLI for installing built-in Playbook templates."""
    _configure_utf8_stdio()
    args = list(argv or [])
    if not args or args[0] in ("-h", "--help"):
        _print_playbook_usage()
        return 0
    if args[0] != "install" or len(args) < 2:
        _print_playbook_usage()
        return 2

    project_folder = _arg_value(args, "--project", "--project-folder")
    if ("--project" in args or "--project-folder" in args) and not project_folder:
        print("--project requires a folder path")
        return 2

    Engram = _get_engram_class()
    eng = Engram()
    confirm = "--yes" in args
    result = eng.install_builtin_playbook(
        args[1],
        project_folder=project_folder or None,
        dry_run=not confirm,
        confirm=confirm,
    )
    _safe_print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("error") else 0


def run_review(argv: list[str] | None = None) -> int:
    """Terminal review queue for staging lessons and decisions."""
    _configure_utf8_stdio()
    args = list(argv or [])
    if args and args[0] in ("-h", "--help"):
        _print_review_usage()
        return 0

    from piia_engram.core import Engram

    eng = Engram()

    if args and args[0] == "show":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_type, item = eng._find_item_by_id(args[1])
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {args[1]}")
            return 1
        _print_review_item(item_type, item)
        return 0

    if args and args[0] == "approve":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_id = args[1]
        if not _require_yes(args[2:], "approve review item"):
            return 2
        item_type, item = eng._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {item_id}")
            return 1
        if item.get("tier") != "staging":
            print(f"Review item is not staging: {item_id}")
            return 1
        result = eng.promote_knowledge(item_id)
        if result.get("status") != "promoted":
            print(f"Review item could not be promoted: {item_id}")
            return 1
        print(f"Promoted review item: {item_id}")
        return 0

    if args and args[0] == "archive":
        if len(args) < 2:
            _print_review_usage()
            return 2
        item_id = args[1]
        if not _require_yes(args[2:], "archive review item"):
            return 2
        item_type, item = eng._find_item_by_id(item_id)
        if item is None or item_type not in {"lesson", "decision"}:
            print(f"Review item not found: {item_id}")
            return 1
        result = eng.archive_knowledge(item_id)
        if result.get("error"):
            print(result["error"])
            return 1
        print(f"Archived review item: {item_id}")
        return 0

    limit = 20
    sort = "recent"
    low_quality_only = False
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = _parse_sessions_limit(args[i + 1])
            i += 2
        elif args[i] == "--sort" and i + 1 < len(args):
            sort = args[i + 1]
            if sort not in {"recent", "quality", "quality-desc"}:
                print(f"Invalid review sort: {sort}")
                _print_review_usage()
                return 2
            i += 2
        elif args[i] == "--low-quality":
            low_quality_only = True
            i += 1
        else:
            print(f"Unknown review option: {args[i]}")
            _print_review_usage()
            return 2

    _print_review_list(_review_items(
        eng, limit=limit, sort=sort, low_quality_only=low_quality_only,
    ))
    return 0


def _run_telemetry_cli(sub_args: list[str]) -> None:
    """Handle `engram telemetry <subcommand>`."""
    from piia_engram.telemetry import (
        get_status, is_enabled, preview_payload, set_enabled,
        set_remote_enabled,
    )

    sub = sub_args[0] if sub_args else "status"

    if sub == "status":
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        remote_state = "ON" if status.get("remote_enabled") else "OFF"
        print(f"\n  Anonymous usage statistics: {state}")
        print(f"  Remote sending: {remote_state}")
        print(f"  Phase: {status['phase']}")
        print(f"  Config: {status['config_path']}")
        print(f"  Log: {status['log_path']}")
        if status["enabled"]:
            print(f"  Opted in: {status['opted_in_at']}")
        if status.get("remote_enabled"):
            print(f"  Remote opted in: {status.get('remote_opted_in_at', '(unknown)')}")
            print(f"  Endpoint: {status.get('endpoint', '(unknown)')}")
        print()

    elif sub == "preview":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    elif sub in ("off", "disable"):
        set_enabled(False)
        set_remote_enabled(False)
        print("\n  ✅ Anonymous usage statistics disabled (local + remote).")
        print("  No data will be logged or sent.\n")

    elif sub in ("on", "enable"):
        set_enabled(True)
        print("\n  ✅ Anonymous usage statistics enabled.")
        print("  Run 'engram telemetry preview' to see what will be logged.")
        print("  Run 'engram telemetry remote on' to also enable remote sending.\n")

    elif sub == "remote":
        remote_sub = sub_args[1] if len(sub_args) > 1 else "status"
        if remote_sub in ("on", "enable"):
            if not is_enabled():
                set_enabled(True)
                print("\n  ✅ Local statistics also enabled (required for remote).")
            set_remote_enabled(True)
            print("  ✅ Remote anonymous statistics enabled.")
            print("  Data will be sent via HTTPS to Cloudflare Worker.\n")
        elif remote_sub in ("off", "disable"):
            set_remote_enabled(False)
            print("\n  ✅ Remote sending disabled. Local logging continues if enabled.\n")
        else:
            status = get_status()
            remote_state = "ON" if status.get("remote_enabled") else "OFF"
            print(f"\n  Remote sending: {remote_state}")
            if status.get("remote_enabled"):
                print(f"  Endpoint: {status.get('endpoint', '(unknown)')}")
            print()

    elif sub == "feedback":
        from piia_engram.telemetry import is_feedback_enabled, set_feedback_enabled
        fb_sub = sub_args[1] if len(sub_args) > 1 else "status"
        if fb_sub in ("on", "enable"):
            if not is_enabled():
                set_enabled(True)
                print("\n  ✅ Local statistics also enabled (required for feedback).")
            set_remote_enabled(True)
            set_feedback_enabled(True)
            print("  ✅ Weekly anonymous feedback reports enabled.")
            print("  Reports are sent automatically during wrap_up_session.\n")
        elif fb_sub in ("off", "disable"):
            set_feedback_enabled(False)
            print("\n  ✅ Feedback reports disabled. Other telemetry settings unchanged.\n")
        else:
            fb_state = "ON" if is_feedback_enabled() else "OFF"
            print(f"\n  Weekly feedback reports: {fb_state}")
            print("  Toggle: engram telemetry feedback on/off\n")

    elif sub == "--show-payload":
        print("\n  Next payload (if enabled):\n")
        print(preview_payload())
        print()

    else:
        print(
            "\nUsage:\n"
            "  engram telemetry status         Show current status\n"
            "  engram telemetry preview        Show what data will be logged\n"
            "  engram telemetry on             Enable anonymous usage statistics\n"
            "  engram telemetry off            Disable anonymous usage statistics\n"
            "  engram telemetry remote on      Enable remote sending (Phase 2)\n"
            "  engram telemetry remote off     Disable remote sending\n"
            "  engram telemetry feedback on    Enable weekly feedback reports\n"
            "  engram telemetry feedback off   Disable weekly feedback reports\n"
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
        from piia_engram.telemetry import get_status
        status = get_status()
        state = "ON" if status["enabled"] else "OFF"
        remote_state = "ON" if status.get("remote_enabled") else "OFF"
        print(f"        Local: {state}")
        print(f"        Remote: {remote_state}")
        print(f"        Phase: {status['phase']}")
        print(f"        Config: {status['config_path']}")
        log_path = Path(status["log_path"])
        if log_path.is_file():
            log_size = log_path.stat().st_size
            log_lines = len(log_path.read_text(encoding="utf-8").strip().splitlines())
            print(f"        Log: {log_path} ({log_size / 1024:.1f} KB, {log_lines} entries)")
        else:
            print("        Log: (no entries yet)")
        print("        Collected: tool names + counts, knowledge totals, version, daily anonymous ID")
        print("        NOT collected: text content, prompts, file paths, PII, IP")
        if status.get("remote_enabled"):
            print(f"        Endpoint: {status.get('endpoint', '(unknown)')}")
        print("        Optional: telemetry Phase 2 (remote to Cloudflare Worker, requires re-consent)")
    except ImportError:
        print("        (telemetry module not available)")
    print()

    # 5. Reconcile
    print("  [SYNC] Cross-tool sync:")
    try:
        from piia_engram.reconcile import ReconcileMixin
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


# ---------------------------------------------------------------------------
# engram feedback — 内测反馈报告
# ---------------------------------------------------------------------------


def _build_feedback_report(data_dir: str | None = None) -> dict:
    """Build an anonymous usage/governance report from local Engram data.

    Reads knowledge files and computes governance metrics without any
    network calls. No lesson/decision content is included — only counts,
    distributions, and timing statistics.

    Returns a dict suitable for JSON export.
    """
    from datetime import datetime, timezone

    root = Path(data_dir) if data_dir else Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    knowledge_dir = root / "knowledge"
    playbooks_dir = root / "playbooks"
    contexts_dir = root / "contexts"

    report: dict = {
        "report_type": "engram_beta_feedback",
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Version
    try:
        from importlib.metadata import version as _pkg_version
        report["engram_version"] = _pkg_version("piia-engram")
    except Exception:
        report["engram_version"] = "unknown"

    report["os"] = platform.system()
    report["python"] = platform.python_version()

    # Lessons
    lessons_path = knowledge_dir / "lessons.json"
    lessons: list[dict] = []
    if lessons_path.is_file():
        try:
            lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_lessons = [l for l in lessons if l.get("tier") == "staging"]
    verified_lessons = [l for l in lessons if l.get("tier") != "staging"]

    # Decisions
    decisions_path = knowledge_dir / "decisions.json"
    decisions: list[dict] = []
    if decisions_path.is_file():
        try:
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_decisions = [d for d in decisions if d.get("tier") == "staging"]
    verified_decisions = [d for d in decisions if d.get("tier") != "staging"]

    # Playbooks
    playbooks_index = playbooks_dir / "_index.json"
    playbooks: list[dict] = []
    if playbooks_index.is_file():
        try:
            playbooks = json.loads(playbooks_index.read_text(encoding="utf-8"))
        except Exception:
            pass
    staging_playbooks = [p for p in playbooks if p.get("tier") == "staging"]
    verified_playbooks = [p for p in playbooks if p.get("tier") != "staging"]

    total_staging = len(staging_lessons) + len(staging_decisions) + len(staging_playbooks)
    total_verified = len(verified_lessons) + len(verified_decisions) + len(verified_playbooks)
    total = total_staging + total_verified

    report["knowledge"] = {
        "total": total,
        "staging": total_staging,
        "verified": total_verified,
        "promotion_rate": round(total_verified / total, 2) if total > 0 else None,
        "lessons": {"staging": len(staging_lessons), "verified": len(verified_lessons)},
        "decisions": {"staging": len(staging_decisions), "verified": len(verified_decisions)},
        "playbooks": {"staging": len(staging_playbooks), "verified": len(verified_playbooks)},
    }

    # Domain distribution (top 10, no content)
    domain_counts: dict[str, int] = {}
    for item in lessons + decisions:
        domain = item.get("domain", "")
        if domain:
            for d in domain.split(","):
                d = d.strip()
                if d:
                    domain_counts[d] = domain_counts.get(d, 0) + 1
    top_domains = sorted(domain_counts.items(), key=lambda x: -x[1])[:10]
    report["top_domains"] = {k: v for k, v in top_domains}

    # Source tool distribution
    tool_counts: dict[str, int] = {}
    for item in lessons + decisions:
        src = item.get("source_tool", "unknown")
        tool_counts[src] = tool_counts.get(src, 0) + 1
    report["source_tools"] = tool_counts

    # Timing: days since first knowledge, avg staging age
    now = datetime.now(timezone.utc)
    all_items = lessons + decisions + playbooks
    created_dates: list[datetime] = []
    staging_ages: list[float] = []
    for item in all_items:
        ts = item.get("created_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # Ensure timezone-aware for comparison
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            created_dates.append(dt)
            if item.get("tier") == "staging":
                staging_ages.append((now - dt).total_seconds() / 86400)
        except Exception:
            pass

    if created_dates:
        report["first_knowledge_date"] = min(created_dates).strftime("%Y-%m-%d")
        report["days_with_knowledge"] = (now - min(created_dates)).days
    report["avg_staging_age_days"] = round(sum(staging_ages) / len(staging_ages), 1) if staging_ages else None

    # Session contexts count
    session_count = 0
    if contexts_dir.is_dir():
        try:
            session_count = sum(1 for f in contexts_dir.iterdir() if f.suffix == ".json")
        except Exception:
            pass
    report["session_count"] = session_count

    # MCP tool call log (from telemetry.log if exists)
    telemetry_log = root / "telemetry.log"
    tool_call_totals: dict[str, int] = {}
    if telemetry_log.is_file():
        try:
            for line in telemetry_log.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    for tool_name, counts in entry.get("tool_calls", {}).items():
                        if isinstance(counts, dict):
                            n = counts.get("success", 0) + counts.get("error", 0)
                        else:
                            n = int(counts)
                        tool_call_totals[tool_name] = tool_call_totals.get(tool_name, 0) + n
                except Exception:
                    continue
        except Exception:
            pass
    if tool_call_totals:
        top_tools = sorted(tool_call_totals.items(), key=lambda x: -x[1])[:15]
        report["top_mcp_tools"] = {k: v for k, v in top_tools}

    # Configured AI tools (from setup_report)
    setup_report = root / "setup_report.jsonl"
    if setup_report.is_file():
        try:
            lines = setup_report.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                last = json.loads(lines[-1])
                report["configured_tools"] = last.get("tools_configured", [])
        except Exception:
            pass

    # Beta event tracking aggregate
    try:
        from piia_engram.beta_tracker import aggregate_events
        beta = aggregate_events()
        if beta:
            report["beta_events"] = beta
    except Exception:
        pass

    return report


def run_feedback(*, dry_run: bool = False) -> None:
    """Generate and display an anonymous beta feedback report.

    The report contains only counts and distributions — no knowledge content,
    no file paths, no personal information. Users can copy-paste it.

    Args:
        dry_run: If True, show the exact payload that would be sent but do not send.
    """
    _configure_utf8_stdio()

    print("\n  ========================================")
    print("  PIIA Engram 内测反馈报告 / Beta Feedback Report")
    print("  ========================================\n")

    report = _build_feedback_report()

    # Pretty print
    k = report.get("knowledge", {})
    print(f"  Engram 版本: {report.get('engram_version', '?')}")
    print(f"  OS: {report.get('os', '?')} | Python: {report.get('python', '?')}")
    print(f"  使用天数: {report.get('days_with_knowledge', '?')} 天")
    print(f"  会话数: {report.get('session_count', 0)}")
    print()

    print("  ── 知识治理 ──")
    print(f"  总知识数: {k.get('total', 0)} (staging: {k.get('staging', 0)}, verified: {k.get('verified', 0)})")
    pr = k.get("promotion_rate")
    if pr is not None:
        print(f"  确认率 (promotion rate): {pr:.0%}")
    avg_age = report.get("avg_staging_age_days")
    if avg_age is not None:
        print(f"  Staging 平均滞留: {avg_age} 天")
    print(f"    Lessons:   staging={k.get('lessons', {}).get('staging', 0)}, verified={k.get('lessons', {}).get('verified', 0)}")
    print(f"    Decisions: staging={k.get('decisions', {}).get('staging', 0)}, verified={k.get('decisions', {}).get('verified', 0)}")
    print(f"    Playbooks: staging={k.get('playbooks', {}).get('staging', 0)}, verified={k.get('playbooks', {}).get('verified', 0)}")
    print()

    if report.get("top_domains"):
        print("  ── 领域分布 ──")
        for d, c in report["top_domains"].items():
            print(f"    {d}: {c}")
        print()

    if report.get("source_tools"):
        print("  ── 来源工具 ──")
        for t, c in report["source_tools"].items():
            print(f"    {t}: {c}")
        print()

    if report.get("configured_tools"):
        print(f"  ── 已配置工具 ──")
        print(f"    {', '.join(report['configured_tools'])}")
        print()

    beta = report.get("beta_events", {})
    if beta:
        print("  ── 行为埋点 ──")
        print(f"  总事件数: {beta.get('total_events', 0)}")
        if beta.get("tracking_days"):
            print(f"  追踪天数: {beta['tracking_days']} 天")
        ec = beta.get("event_counts", {})
        if ec:
            for ev_name, ev_count in sorted(ec.items(), key=lambda x: -x[1]):
                print(f"    {ev_name}: {ev_count}")
        prom = beta.get("promotions", {})
        if prom:
            print(f"  晋升总数: {prom.get('total', 0)}")
            for m, c in prom.get("methods", {}).items():
                print(f"    方式 {m}: {c}")
        cs = beta.get("cold_starts", {})
        if cs:
            print(f"  冷启动级别: {cs}")
        rec = beta.get("reconcile", {})
        if rec:
            print(f"  跨工具同步: {rec.get('sync_count', 0)} 次, 导入 {rec.get('total_imported', 0)} 条")
        print()

    # --dry-run: show exactly what would be sent, then stop
    if dry_run:
        print("  ── Dry-run: 以下是将要发送的完整 payload ──")
        print("  (实际运行时不会发送，仅展示)\n")
        preview = report.copy()
        try:
            from piia_engram.telemetry import _daily_id, _load_config
            cfg = _load_config()
            local_uuid = cfg.get("local_uuid", "")
            if local_uuid:
                preview["daily_id"] = _daily_id(local_uuid)
            else:
                preview["daily_id"] = "<would be generated at send time>"
        except Exception:
            preview["daily_id"] = "<would be generated at send time>"
        preview_json = json.dumps(preview, ensure_ascii=False, indent=2)
        print(f"  ```json\n{preview_json}\n  ```\n")
        print("  此 payload 只包含计数和分布，不含任何知识内容或个人信息。")
        print("  确认无误后，运行 engram feedback（不加 --dry-run）即可发送。")
        return

    # Auto-send if feedback reporting is opted in
    try:
        from piia_engram.telemetry import is_feedback_enabled, send_feedback
        if is_feedback_enabled():
            print("  ── 自动上报 ──")
            ok = send_feedback(report)
            if ok:
                print("  ✅ 反馈已匿名发送到 Engram 开发团队。")
                print("     关闭自动上报: engram telemetry feedback off\n")
            else:
                print("  ⚠️  自动上报失败（网络问题？），报告仅保留在本地。\n")
        else:
            print("  ── 自动上报未开启 ──")
            print("  开启后每周自动发送: engram telemetry feedback on\n")
    except Exception:
        pass

    # JSON for copy-paste
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    print("  ── 可复制 JSON（备用，粘贴到反馈帖即可）──")
    print(f"  ```json\n{report_json}\n  ```")
    print()
    print("  此报告不含任何知识内容、文件路径或个人信息。")
    print("  This report contains no knowledge content, file paths, or personal info.\n")


def _run_reindex() -> None:
    """Rebuild the v4.0 hybrid search index from the JSON knowledge store."""
    from piia_engram.core import Engram

    eng = Engram()
    result = eng.rebuild_index()
    # Corpus encryption refuses to persist a plaintext index. Say so explicitly
    # instead of the misleading "[ok] reindexed 0 entries" (Codex a5 round-3 O3).
    if result.get("skipped") == "corpus_encrypted":
        tail = " (existing plaintext index purged)" if result.get("purged") else ""
        print("[ok] corpus encryption enabled; persistent search index "
              f"skipped{tail}.")
        return
    vec = "on" if result.get("vector_enabled") else "off (install piia-engram[vector] for semantic search)"
    print(f"[ok] reindexed {result.get('indexed', 0)} entries — vector layer: {vec}")


def _run_repair_encoding(args: list[str]) -> int:
    """Scan or repair high-confidence mojibake in the active Engram root."""
    from piia_engram.core import Engram
    from piia_engram.encoding_repair import (
        repair_engram_root,
        scan_engram_root,
        summarize_findings,
    )

    apply = "--apply" in args or "--fix" in args
    no_backup = "--no-backup" in args
    summary_only = "--summary" in args
    eng = Engram()

    if summary_only and not apply:
        report = scan_engram_root(eng.root)
        summary = summarize_findings(report)
        # Metadata-only output: counts and generic reason codes, never bodies
        # or paths — safe to paste into an audit/report.
        print("Encoding scan summary (metadata-only, no bodies/paths):")
        print(f"  files_with_findings: {summary['files_with_findings']}")
        print(f"  repairable: {summary['repairable_count']}  "
              f"suspect: {summary['suspect_count']}  "
              f"total: {summary['total_findings']}")
        if summary["reasons"]:
            print("  reasons:")
            for reason, count in summary["reasons"].items():
                print(f"    {reason}: {count}")
        return 0 if summary["suspect_count"] == 0 else 1

    if apply:
        if no_backup:
            print(
                "[!!] Encoding repair: --no-backup disables automatic backup; "
                "use only if you already have a separate backup."
            )
        report = repair_engram_root(eng.root, apply=True, backup=not no_backup)
        if not report.findings:
            print("[ok] Encoding repair: no mojibake detected.")
            return 0
        if report.repairable_count:
            print(
                "[fixed] Encoding repair: repaired "
                f"{report.repairable_count} field(s) in {len(report.changed_files)} file(s)."
            )
            if report.backup_dir is not None:
                print(f"        Backup: {report.backup_dir}")
        if report.suspect_count:
            print(f"[!!] {report.suspect_count} suspect field(s) need manual review.")
            return 1
        return 0

    report = scan_engram_root(eng.root)
    if not report.findings:
        print("[ok] Encoding repair dry-run: no mojibake detected.")
        print(
            "     This confirms stored Engram data is clean. If text still looks "
            "garbled in a terminal, check display encoding instead."
        )
        print("     PowerShell tip: use Get-Content -Encoding utf8 for UTF-8 files.")
        return 0

    print(
        "[!!] Encoding repair dry-run: found "
        f"{report.repairable_count} repairable mojibake field(s) "
        f"and {report.suspect_count} suspect field(s)."
    )
    for finding in report.findings[:20]:
        print(f"  - {finding.relative_path}:{finding.json_path} ({finding.reason})")
    print("Run 'engram repair-encoding --apply' to repair with a backup.")
    return 1


def _run_recover_json(args: list[str]) -> int:
    """Dry-run recovery scan for JSON files backed up as ``*.corrupt``."""
    import os as _os
    from piia_engram.recovery import (
        analyze_json_recovery_candidates,
        analyze_recovery_retention_plan,
        write_recovery_candidate,
    )

    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram recover-json lessons\n"
            "  engram recover-json lessons --write-candidate PATH\n"
        )
        return 0

    dataset = args[0]
    output_path = None
    if "--write-candidate" in args:
        idx = args.index("--write-candidate")
        if idx + 1 >= len(args):
            print("ERROR: --write-candidate requires a destination path")
            return 2
        output_path = args[idx + 1]

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    report = analyze_json_recovery_candidates(root, dataset=dataset)
    active = report.get("active") or {}
    best = report.get("best_candidate")

    print(f"JSON recovery dry-run: dataset={dataset}")
    print(
        "Active: "
        f"{active.get('file_name', f'{dataset}.json')} "
        f"status={active.get('json_status')} "
        f"entries={active.get('entries')} "
        f"bom={active.get('starts_bom')}"
    )
    print("Candidates:")
    for item in report["files"]:
        if item.get("role") != "backup":
            continue
        marker = "*" if best and item["file_name"] == best["file_name"] else "-"
        print(
            f"  {marker} {item['file_name']} "
            f"status={item['json_status']} "
            f"entries={item['entries']} "
            f"date_max={item['date_max']} "
            f"sha256={item['sha256_12']}"
        )
    if best:
        print(f"Best candidate: {best['file_name']} entries={best['entries']}")
    else:
        print("Best candidate: none")
    retention = analyze_recovery_retention_plan(root, dataset=dataset)
    if retention.get("primary_candidate"):
        print(
            "Retention plan: "
            f"union_ids={retention['union_ids']} "
            f"overlap_ids={retention['overlap_ids']} "
            f"primary_only_ids={retention['primary_only_ids']} "
            f"secondary_only_ids={retention['secondary_only_ids']} "
            f"overflow_ids={retention['overflow_ids']} "
            f"active_merge_safe={str(retention['active_merge_safe']).lower()}"
        )
        print(f"Recommendation: {retention['recommendation']}")
    print("Live store modified: false")

    if output_path:
        result = write_recovery_candidate(root, dataset=dataset, output_path=output_path)
        print(
            "Wrote recovery candidate: "
            f"{result['output_path']} "
            f"entries={result['entries']} "
            "live_store_modified=false"
        )
    return 0


def _run_backup_plan(args: list[str]) -> int:
    """Print a metadata-only local backup plan (what to copy before upgrading).

    Read-only and local-only: it enumerates Engram-owned files under the active
    root, never reads stored knowledge bodies, and never touches files outside
    the Engram directory. Pass ``--json`` for machine-readable output.
    """
    import os as _os
    from piia_engram.recovery import build_backup_plan

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    plan = build_backup_plan(root)

    if "--json" in args:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(f"Engram backup plan (metadata only): {plan['root']}")
    if not plan["exists"]:
        print("  (no Engram root found at this path yet — nothing to back up)")
        return 0
    print(f"  total: {plan['total_files']} files, {plan['total_bytes']} bytes")
    print("  groups:")
    for group in plan["groups"]:
        print(f"    - {group['name']}: {group['files']} files, {group['bytes']} bytes")
    if plan["knowledge_datasets"]:
        print("  knowledge datasets:")
        for ds in plan["knowledge_datasets"]:
            print(
                f"    - {ds['file_name']}: entries={ds['entries']} "
                f"bytes={ds['bytes']} sha256={ds['sha256_12']}"
            )
    print(f"  external files included: {plan['external_files_included']} "
          f"(excluded: {plan['external_paths_excluded']})")
    print(f"  {plan['restore_hint']}")
    print("  live store modified: false")
    return 0


def _render_import_result_text(payload: dict) -> str:
    """Render import preview/apply output without exposing stored values."""
    status = payload.get("status", "unknown")
    mode = payload.get("mode", "")
    dry_run = bool(payload.get("dry_run"))
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    conflicts = payload.get("conflicts", []) if isinstance(payload.get("conflicts"), list) else []

    title = "Engram import preview" if dry_run else "Engram import apply"
    lines = [
        f"{title} - {status}",
        f"  mode: {mode or 'merge'}",
        f"  dry_run: {'true' if dry_run else 'false'}",
        "  metadata_only: true",
    ]
    if payload.get("requires_confirmation"):
        lines.append("  requires_confirmation: true")
        lines.append("  re-run with --apply --yes to mutate the local store")
    if summary:
        lines.append("  summary:")
        for section, counts in sorted(summary.items()):
            lines.append(
                f"    - {section}: incoming={counts.get('incoming', 0)} "
                f"add={counts.get('would_add', 0)} "
                f"skip={counts.get('would_skip', 0)} "
                f"conflicts={counts.get('conflicts', 0)}"
            )
    if conflicts:
        lines.append(f"  conflicts: {len(conflicts)} (metadata only; values withheld)")
    if payload.get("error"):
        lines.append(f"  error: {payload['error']}")
    if dry_run and not payload.get("requires_confirmation"):
        lines.append("  run 'engram import <backup.json> --apply --yes' to apply")
    return "\n".join(lines)


def _run_import_backup(args: list[str]) -> int:
    """Preview/apply a full Engram JSON backup import.

    Default is read-only preview. Mutation requires both ``--apply`` and
    ``--yes``; overwrite mode is explicit via ``--overwrite``.
    """
    import os as _os
    from piia_engram.core import Engram

    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram import <backup.json> [--json]\n"
            "  engram import <backup.json> --apply --yes [--json]\n"
            "  engram import <backup.json> --apply --yes --materialize-version-chain [--json]\n"
            "  engram import <backup.json> --overwrite --apply --yes [--json]\n\n"
            "Default is metadata-only preview. --overwrite maps to merge=False."
        )
        return 0 if args and args[0] in {"-h", "--help"} else 2

    json_output = "--json" in args
    apply = "--apply" in args
    confirm = "--yes" in args
    overwrite = "--overwrite" in args
    materialize_version_chain = "--materialize-version-chain" in args
    known_flags = {
        "--json",
        "--apply",
        "--yes",
        "--overwrite",
        "--materialize-version-chain",
    }
    paths = []
    for arg in args:
        if arg in known_flags:
            continue
        if arg.startswith("--"):
            payload = {"error": f"Unknown import option: {arg}"}
            if json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(_render_import_result_text(payload))
            return 2
        paths.append(arg)

    if len(paths) != 1:
        payload = {"error": "Usage: engram import <backup.json> [--json]"}
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_import_result_text(payload))
        return 2

    backup_path = paths[0]
    merge = not overwrite
    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)

    if apply and not confirm:
        payload = eng.import_all(backup_path, merge=merge, dry_run=True)
        payload["requires_confirmation"] = True
        payload["confirmation_hint"] = "re-run with --apply --yes to mutate the local store"
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(_render_import_result_text(payload))
        return 1

    payload = eng.import_all(
        backup_path,
        merge=merge,
        dry_run=not apply,
        materialize_version_chain=materialize_version_chain and merge,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_import_result_text(payload))
    return 1 if payload.get("error") else 0


def _run_export_agents_md(args: list[str]) -> int:
    """Export verified, non-sensitive knowledge as an AGENTS.md / CLAUDE.md block.

    Local + owner-run (CLI): loads the user's own store and renders the curated,
    committable digest via ``agents_md_export.build_agents_md_export`` — which is
    verified-only, sensitivity-screened, and summary/metadata-only by
    construction. Prints to stdout by default; ``--out PATH`` writes the block to
    an explicit destination and REFUSES to overwrite an existing file (so it can
    never clobber a hand-maintained AGENTS.md).
    """
    import os as _os
    from piia_engram.agents_md_export import build_agents_md_export
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram export-agents-md [--scope global|project] [--project NAME]\n"
            "                          [--max-sensitivity public|personal|work]\n"
            "                          [--out PATH]\n"
        )
        return 0

    def _opt(flag: str, default: str = "") -> str:
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    scope = _opt("--scope", "global")
    if scope not in {"global", "project"}:
        print("ERROR: --scope must be 'global' or 'project'")
        return 2
    project = _opt("--project", "")
    if scope == "project" and not project:
        print("ERROR: --scope project requires --project NAME")
        return 2
    max_sensitivity = _opt("--max-sensitivity", "work")
    out_path = _opt("--out", "")

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False)
    decisions = eng.get_decisions(limit=None, _update_access=False)
    block = build_agents_md_export(
        lessons=lessons,
        decisions=decisions,
        scope=scope,
        project=project,
        max_sensitivity=max_sensitivity,
    )

    if out_path:
        dest = Path(out_path).expanduser().resolve()
        if dest.exists():
            print(f"ERROR: refusing to overwrite an existing file: {dest}")
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(block, encoding="utf-8")
        print(f"Wrote AGENTS.md export: {dest}")
        return 0
    print(block)
    return 0


def _run_recall(args: list[str]) -> int:
    """Print a single-call recall digest for the owner (engram recall).

    Local + owner-run (CLI = ``private-self``): composes existing governed read
    methods (profile slice, recent context, relevant lessons, optional keyword
    search) via ``recall_service.gather_recall``, collapses superseded knowledge
    to its current head, and renders a metadata/summary-only digest. It adds no
    new agent-facing surface — the MCP recall tool stays deferred per
    docs/specs/recall-surface-v1.md §6.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.recall_service import gather_recall, render_recall_text

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram recall [--project NAME] [--query TEXT] [--budget N]\n"
            "                [--no-freshness] [--no-collapse] [--json]\n"
        )
        return 0

    def _opt(flag: str, default: str = "") -> str:
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                return args[idx + 1]
        return default

    project = _opt("--project", "")
    query = _opt("--query", "")
    try:
        budget = int(_opt("--budget", "2000"))
    except ValueError:
        print("ERROR: --budget must be an integer")
        return 2

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    payload = gather_recall(
        eng,
        project_folder=project,
        query=query,
        token_budget=budget,
        include_freshness="--no-freshness" not in args,
        collapse_versions="--no-collapse" not in args,
    )

    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(render_recall_text(payload))
    return 0


def _run_telemetry_validate(args: list[str]) -> int:
    """Validate telemetry payload/schema/migration consistency (read-only, no network).

    Static local check: confirms the client payload contract, worker schema, and
    v1.1 migration agree, the migration is additive/forward-only, and no
    content-bearing field exists on either side. Performs NO remote action.
    """
    from piia_engram.telemetry_validation import (
        render_readiness_text,
        render_validation_text,
        validate_remote_readiness,
        validate_telemetry_contract,
    )

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram telemetry-validate [--json]\n"
            "  engram telemetry-validate --remote-readiness [--json]\n"
            "\n"
            "  --remote-readiness  Pre-deploy checklist (payload/schema/migration\n"
            "                      sequencing, dashboard wording, opt-in defaults,\n"
            "                      no content fields). Read-only; performs no remote\n"
            "                      D1/worker action.\n"
        )
        return 0

    worker_dir = Path.cwd() / "worker"
    if "--remote-readiness" in args:
        report = validate_remote_readiness(worker_dir)
        if "--json" in args:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_readiness_text(report))
        return 0 if report.get("ok") else 1

    report = validate_telemetry_contract(worker_dir)
    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_validation_text(report))
    return 0 if report.get("ok") else 1


def _run_release_check(args: list[str]) -> int:
    """Print a read-only release readiness report (engram release-check).

    Aggregates required-file presence, English-first release notes, publish
    allowlist, a public-doc private-term scan, and release-evidence completeness.
    Performs NO build/tag/publish — it only reads the working tree. Exits
    non-zero when not ready so scripts/CI can gate on it.
    """
    from piia_engram.release_readiness import (
        build_release_readiness,
        render_release_readiness_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram release-check [--json]\n")
        return 0

    # Maintainer command: run from the repo root (the working tree to ship).
    root = Path.cwd()
    report = build_release_readiness(root)
    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_release_readiness_text(report))
    return 0 if report.get("ready") else 1


def _run_dashboard(args: list[str]) -> int:
    """Print the non-technical owner control dashboard (engram dashboard).

    Read-only and metadata-only: aggregates recall trust, lifecycle proposals,
    integrity status, and export/telemetry readiness into one bilingual view.
    Surfaces proposals + the commands to act on them; performs no destructive
    action. ``--html`` writes a fully-escaped local HTML page.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.integrity import scan_integrity
    from piia_engram.owner_dashboard import (
        build_owner_dashboard,
        render_dashboard_html,
        render_dashboard_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram dashboard [--json] [--html [PATH]]\n")
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False) or []
    decisions = eng.get_decisions(limit=None, _update_access=False) or []
    integrity_report = scan_integrity(root)
    telemetry_status = {}
    try:
        from piia_engram import telemetry as _tel
        telemetry_status = _tel.get_status()
    except Exception:
        telemetry_status = {}

    # Readiness reports — all read-only, metadata-only, computed here so the
    # dashboard surface stays pure. Each is best-effort and degrades to None.
    merge_report = None
    try:
        merge_report = eng.suggest_merges()
    except Exception:
        merge_report = None
    reconcile_report = None
    try:
        from piia_engram.reconcile_proposal import build_reconcile_proposal
        candidates = eng.collect_memory_candidates()
        reconcile_report = build_reconcile_proposal(
            candidates, list(lessons) + list(decisions), source="memory_files",
        )
    except Exception:
        reconcile_report = None
    version_report = None
    try:
        from piia_engram.governance_store import RelationStore
        from piia_engram.version_chain import build_version_report
        version_report = build_version_report(RelationStore(root).all_edges())
    except Exception:
        version_report = None

    dashboard = build_owner_dashboard(
        lessons=list(lessons), decisions=list(decisions),
        integrity_report=integrity_report, telemetry_status=telemetry_status,
        merge_report=merge_report, reconcile_report=reconcile_report,
        version_report=version_report,
    )

    if "--json" in args:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
        return 0
    if "--html" in args:
        idx = args.index("--html")
        out = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("-") else ""
        dest = Path(out).expanduser().resolve() if out else (root / "reports" / "dashboard.html")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_dashboard_html(dashboard), encoding="utf-8")
        print(f"Wrote dashboard: {dest}")
        return 0
    print(render_dashboard_text(dashboard))
    return 0


def _run_integrity(args: list[str]) -> int:
    """Print a metadata-only integrity scan + self-heal proposals (engram integrity).

    Read-only and proposal-only: checks JSON validity, duplicate ids, store/index
    drift, governance-ledger chain, and relation/version-chain health, then
    suggests owner commands to fix any problems. It NEVER repairs, rebuilds, or
    overwrites anything — acting on a proposal is an explicit owner command.
    """
    import os as _os
    from piia_engram.integrity import (
        build_self_heal_proposals,
        render_integrity_text,
        scan_integrity,
    )

    if args and args[0] in {"-h", "--help"}:
        print("Usage:\n  engram integrity [--json]\n")
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    report = scan_integrity(root)
    proposals = build_self_heal_proposals(report)

    if "--json" in args:
        print(json.dumps({"report": report, "proposals": proposals},
                         ensure_ascii=False, indent=2))
        return 0
    print(render_integrity_text(report, proposals))
    # Exit non-zero when problems are found so scripts/CI can detect drift.
    return 0 if report.get("healthy") else 1


def _run_lifecycle(args: list[str]) -> int:
    """Print a metadata-only memory lifecycle / decay proposal (engram lifecycle).

    Read-only and proposal-only: it scores active lessons + decisions by
    freshness/access/tier/quality metadata and reports archive/prune *candidates*
    with reasons. It NEVER archives, prunes, or deletes — acting on a proposal is
    a separate, explicit, owner-confirmed step. See
    docs/runbooks/memory-lifecycle.md.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.lifecycle import build_lifecycle_proposal, render_lifecycle_text

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram lifecycle [--json]                      Metadata-only decay/archive proposal\n"
            "  engram lifecycle apply [--id ID ...] [--commit] [--yes] [--json]\n"
            "                                                 Owner-confirmed soft archive of candidates\n"
            "                                                 (default = dry-run preview; --commit --yes to apply)\n"
            "  engram lifecycle restore <id> [--yes] [--json] Undo a lifecycle soft archive\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")

    if args and args[0] == "apply":
        return _run_lifecycle_apply(Engram(root=root), args[1:])
    if args and args[0] == "restore":
        return _run_lifecycle_restore(Engram(root=root), args[1:])

    eng = Engram(root=root)
    lessons = eng.get_lessons(limit=None, _update_access=False) or []
    decisions = eng.get_decisions(limit=None, _update_access=False) or []
    report = build_lifecycle_proposal(list(lessons) + list(decisions))

    if "--json" in args:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(render_lifecycle_text(report))
    return 0


def _run_lifecycle_apply(eng, args: list[str]) -> int:
    """Owner-confirmed lifecycle archive apply (dry-run by default).

    ``--commit`` opts out of the safe dry-run preview; an actual mutation also
    requires ``--yes``. Without ``--yes`` a ``--commit`` invocation fails closed
    (reports ``requires_confirmation`` and changes nothing). ``--id`` (repeatable)
    narrows the action to a specific candidate subset.
    """
    from piia_engram.lifecycle_apply import (
        apply_lifecycle_archive,
        render_lifecycle_apply_text,
    )

    json_output = "--json" in args
    confirm = "--yes" in args
    commit = "--commit" in args
    ids: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"--json", "--yes", "--commit"}:
            i += 1
            continue
        if arg == "--id":
            if i + 1 >= len(args):
                print("Missing value for --id")
                return 2
            ids.append(args[i + 1])
            i += 2
            continue
        print(f"Unknown lifecycle apply option: {arg}")
        return 2

    payload = apply_lifecycle_archive(
        eng,
        ids=ids or None,
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_lifecycle_apply_text(payload))
    # Fail-closed (confirmation required) is a non-zero exit so scripts notice.
    return 1 if payload.get("requires_confirmation") else 0


def _run_lifecycle_restore(eng, args: list[str]) -> int:
    """Undo a lifecycle soft archive (owner-confirmed)."""
    json_output = "--json" in args
    confirm = "--yes" in args
    item_id = ""
    for arg in args:
        if arg in {"--json", "--yes"}:
            continue
        if arg.startswith("--"):
            print(f"Unknown lifecycle restore option: {arg}")
            return 2
        if not item_id:
            item_id = arg
    if not item_id:
        print("Usage: engram lifecycle restore <id> [--yes] [--json]")
        return 2

    if not confirm:
        payload = {
            "schema": 1,
            "action": "lifecycle_restore",
            "id": item_id,
            "requires_confirmation": True,
            "changed": False,
            "status": "confirmation_required",
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"Lifecycle restore for {item_id} requires confirmation - "
                "re-run with --yes to apply."
            )
        return 1

    result = eng.restore_lifecycle_archive(item_id)
    payload = {
        "schema": 1,
        "action": "lifecycle_restore",
        "id": item_id,
        "requires_confirmation": False,
        "changed": bool(result.get("changed")),
        "status": "restored" if result.get("changed") else (
            "not_found" if result.get("error") else "noop"
        ),
        "from_tier": result.get("from_tier", ""),
        "to_tier": result.get("to_tier", ""),
        "error": result.get("error"),
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"Lifecycle restore {item_id}: {payload['status']} "
            f"({payload['from_tier'] or 'none'} -> {payload['to_tier'] or 'none'})"
        )
    return 0 if result.get("error") is None else 1


def _run_merge(args: list[str]) -> int:
    """Near-duplicate merge proposal + owner-confirmed apply (engram merge).

    ``engram merge`` (no subcommand) prints the metadata-only merge preview
    (read-only). ``engram merge apply`` previews/applies the same plan via the
    reversible soft-archive ``merge_knowledge`` primitive: dry-run by default,
    ``--commit --yes`` to actually fold each secondary into its primary. Never
    hard-deletes and exposes no agent-facing apply tool.
    """
    import os as _os
    from piia_engram.core import Engram

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram merge [--threshold T] [--limit N] [--json]\n"
            "                                       Metadata-only near-duplicate suggestions\n"
            "  engram merge apply [--pair PRIMARY:SECONDARY ...] [--threshold T]\n"
            "                     [--limit N] [--commit] [--yes] [--json]\n"
            "                                       Owner-confirmed soft-archive merge\n"
            "                                       (default = dry-run preview; --commit --yes to apply)\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)

    if args and args[0] == "apply":
        return _run_merge_apply(eng, args[1:])

    from piia_engram.merge_apply import apply_merge, render_merge_apply_text

    threshold, limit, _ = _parse_merge_opts(args)
    payload = apply_merge(eng, threshold=threshold, limit=limit, dry_run=True)
    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(render_merge_apply_text(payload))
    print("  run 'engram merge apply --commit --yes' to fold them")
    return 0


def _parse_merge_opts(args: list[str]) -> tuple[float, int, list[tuple[str, str]]]:
    """Parse shared merge options: --threshold, --limit, --pair PRIMARY:SECONDARY."""
    threshold = 0.45
    limit = 10
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--threshold" and i + 1 < len(args):
            try:
                threshold = float(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if arg == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if arg == "--pair" and i + 1 < len(args):
            raw = args[i + 1]
            if ":" in raw:
                p, s = raw.split(":", 1)
                if p and s:
                    pairs.append((p, s))
            i += 2
            continue
        i += 1
    return threshold, limit, pairs


def _run_merge_apply(eng, args: list[str]) -> int:
    """Owner-confirmed near-duplicate merge apply (dry-run by default)."""
    from piia_engram.merge_apply import apply_merge, render_merge_apply_text

    json_output = "--json" in args
    confirm = "--yes" in args
    commit = "--commit" in args
    threshold, limit, pairs = _parse_merge_opts(args)

    payload = apply_merge(
        eng,
        pairs=pairs or None,
        threshold=threshold,
        limit=limit,
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_merge_apply_text(payload))
    return 1 if payload.get("requires_confirmation") else 0


def _run_reconcile(args: list[str]) -> int:
    """Reconcile proposal + owner-confirmed import-only apply (engram reconcile).

    ``engram reconcile`` (no subcommand) scans external AI memory files and
    prints a metadata-only classification (import / duplicate / conflict / skip),
    importing nothing. ``engram reconcile apply`` imports ONLY the novel
    (``import``) candidates via the existing write API: dry-run by default,
    ``--commit --yes`` to actually import. Duplicates and conflicts are never
    applied (conflict resolution is deferred); no agent-facing tool is exposed.
    """
    import os as _os
    from piia_engram.core import Engram
    from piia_engram.reconcile_apply import (
        apply_reconcile,
        preview_reconcile_conflicts,
        render_reconcile_conflicts_text,
        render_reconcile_apply_text,
    )

    if args and args[0] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  engram reconcile [--json]                 Metadata-only import proposal\n"
            "  engram reconcile conflicts [--json]       Metadata-only conflict preview\n"
            "  engram reconcile apply [--commit] [--yes] [--json]\n"
            "                                            Owner-confirmed import-only apply\n"
            "                                            (default = dry-run preview; --commit --yes to import)\n"
        )
        return 0

    root = Path(_os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    eng = Engram(root=root)
    candidates = eng.collect_memory_candidates()

    json_output = "--json" in args
    apply = bool(args) and args[0] == "apply"
    conflicts = bool(args) and args[0] == "conflicts"
    confirm = "--yes" in args
    commit = apply and "--commit" in args

    if conflicts:
        payload = preview_reconcile_conflicts(
            eng, candidates,
            source="memory_files",
        )
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_reconcile_conflicts_text(payload))
        return 0

    payload = apply_reconcile(
        eng, candidates,
        source="memory_files",
        confirm=confirm,
        dry_run=not commit,
    )
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_reconcile_apply_text(payload))
    return 1 if payload.get("requires_confirmation") else 0


def _governance_root():
    from piia_engram.core import Engram
    return Engram().root


def run_grants(root) -> int:
    """List agent trust grants + revocations (engram grants)."""
    from piia_engram.governance_store import GrantStore
    data = GrantStore(root).list_grants()
    print("Agent grants (explicit):")
    if data["grants"]:
        for a, lvl in sorted(data["grants"].items()):
            print(f"  {a}: {lvl}")
    else:
        print("  (none — agents are auto-classified by default)")
    print("Revoked:")
    if data["revoked"]:
        for a in sorted(data["revoked"]):
            print(f"  {a}")
    else:
        print("  (none)")
    return 0


def run_trust(root, agent: str, level: str) -> int:
    """Grant an agent a trust level (engram trust <agent> <level>)."""
    from piia_engram.governance_store import GrantStore
    try:
        GrantStore(root).set_grant(agent, level)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 2
    print(f"[ok] {agent} → {level}")
    return 0


def run_revoke(root, agent: str) -> int:
    """Revoke an agent (engram revoke <agent>)."""
    from piia_engram.governance_store import GrantStore
    GrantStore(root).revoke(agent)
    print(f"[ok] revoked {agent}.")
    print("     Note: stops FUTURE disclosure only — cannot recall context "
          "already sent to an AI tool.")
    return 0


def run_audit(root, limit: int = 20) -> int:
    """Show recent disclosure receipts + ledger integrity (engram audit)."""
    from piia_engram.governance import GovernanceLedger, default_ledger_path
    led = GovernanceLedger(default_ledger_path(root))
    # Codex round-5 P2: verify() FIRST. records() does an unguarded json.loads
    # per line, so on a corrupt ledger it would raise and traceback. verify()
    # reports the break gracefully, so we bail before ever touching records().
    ok, msg = led.verify()
    if not ok:
        print(f"ledger integrity: BROKEN — {msg}")
        return 1
    recs = led.records()
    if not recs:
        print("(no disclosures recorded yet)")
        return 0
    for r in recs[-limit:]:
        ev = r.get("event", {})
        print(f"  #{r.get('seq')} {r.get('ts')}  {ev.get('agent_id', '?')} "
              f"[{ev.get('trust_level', '?')}] returned={ev.get('returned_count', '?')} "
              f"excluded_sensitivity={ev.get('excluded_by_sensitivity', '?')}")
    print("ledger integrity: OK")
    return 0


def run_verify_ledger(root) -> int:
    """Verify the governance ledger hash chain (engram verify-ledger)."""
    from piia_engram.governance import GovernanceLedger, default_ledger_path
    ok, msg = GovernanceLedger(default_ledger_path(root)).verify()
    print(f"[{'ok' if ok else 'FAIL'}] governance ledger: {msg}")
    return 0 if ok else 1


def _print_status_usage() -> None:
    print(
        "Usage:\n"
        "  engram status [--no-probe]\n"
        "  engram status --html [--output PATH] [--no-probe]\n"
    )


def run_status(argv: list[str] | None = None) -> int:
    """Print a redacted first-run health summary."""
    from piia_engram.status_report import build_status, render_status_text, write_status_html

    args = list(argv or [])
    html_output = False
    no_probe = False
    output: Path | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_status_usage()
            return 0
        if arg == "--html":
            html_output = True
        elif arg == "--no-probe":
            no_probe = True
        elif arg == "--output":
            if i + 1 >= len(args):
                print("Missing value for --output")
                _print_status_usage()
                return 2
            output = Path(args[i + 1]).expanduser()
            i += 1
        else:
            print(f"Unknown status option: {arg}")
            _print_status_usage()
            return 2
        i += 1

    if output is not None and not html_output:
        print("--output only applies with --html")
        _print_status_usage()
        return 2
    status = build_status(probe=not no_probe)
    if html_output:
        path = write_status_html(status, output)
        print(f"Engram status HTML written to: {path}")
    else:
        print(render_status_text(status), end="")
    return 0


def _print_continuity_usage() -> None:
    print(
        "Usage:\n"
        "  engram continuity [--project PATH] [--limit N]\n"
        "  engram continuity --json [--project PATH] [--limit N]\n"
    )


def run_continuity(argv: list[str] | None = None) -> int:
    """Print a metadata-only cross-tool continuity proof."""
    from piia_engram.continuity_report import (
        build_continuity_report,
        render_continuity_text,
    )
    from piia_engram.core import Engram

    args = list(argv or [])
    project_folder = os.getcwd()
    limit = 500
    json_output = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_continuity_usage()
            return 0
        if arg == "--json":
            json_output = True
        elif arg == "--project":
            if i + 1 >= len(args):
                print("Missing value for --project")
                _print_continuity_usage()
                return 2
            project_folder = args[i + 1]
            i += 1
        elif arg == "--limit":
            if i + 1 >= len(args):
                print("Missing value for --limit")
                _print_continuity_usage()
                return 2
            try:
                limit = int(args[i + 1])
            except ValueError:
                print("--limit must be an integer")
                return 2
            i += 1
        else:
            print(f"Unknown continuity option: {arg}")
            _print_continuity_usage()
            return 2
        i += 1

    report = build_continuity_report(
        Engram(),
        project_folder=project_folder,
        session_limit=limit,
    )
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_continuity_text(report), end="")
    return 0


def _print_management_usage() -> None:
    print(
        "Usage:\n"
        "  engram management [--project PATH] [--review-limit N] [--playbook-limit N]\n"
        "                    [--review-kind all|lesson|decision] [--quality all|low|ok|missing]\n"
        "                    [--playbook-state all|active|archived|deleted|staging] [--scope all|global|project|shared]\n"
        "  engram management --json [same options]\n"
        "  engram management action review approve|archive <id> [--yes] [--json]\n"
        "  engram management action playbook archive|delete|restore <id> [--yes] [--json]\n"
        "  engram management action playbook_scope accept_global|accept_project|accept_shared|skip <id> [--project PATH] [--yes] [--json]\n"
    )


def _run_management_action_cli(args: list[str]) -> int:
    from piia_engram.core import Engram
    from piia_engram.management_actions import (
        render_management_action_text,
        run_management_action,
    )

    if len(args) < 4:
        _print_management_usage()
        return 2
    target, action, item_id = args[1], args[2], args[3]
    tail = args[4:]
    json_output = "--json" in tail
    confirm = "--yes" in tail
    project_folder = ""
    project_folders: list[str] = []
    reason = ""
    i = 0
    while i < len(tail):
        arg = tail[i]
        if arg in {"--json", "--yes"}:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(tail):
                print("Missing value for --reason")
                _print_management_usage()
                return 2
            reason = tail[i + 1]
            i += 2
            continue
        if arg == "--project":
            if i + 1 >= len(tail):
                print("Missing value for --project")
                _print_management_usage()
                return 2
            project_folder = tail[i + 1]
            project_folders.append(project_folder)
            i += 2
            continue
        print(f"Unknown management action option: {arg}")
        _print_management_usage()
        return 2

    result = run_management_action(
        Engram(),
        target=target,
        action=action,
        item_id=item_id,
        confirm=confirm,
        project_folder=project_folder,
        project_folders=project_folders,
        reason=reason,
    )
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_management_action_text(result), end="")
    return 0 if result.get("error") is None else 1


def run_management(argv: list[str] | None = None) -> int:
    """Print a metadata-only management projection for GUI consumers."""
    from piia_engram.core import Engram
    from piia_engram.management_view import (
        build_management_view,
        render_management_text,
    )

    args = list(argv or [])
    if args and args[0] == "action":
        return _run_management_action_cli(args)
    project_folder = os.getcwd()
    review_limit = 50
    playbook_limit = 50
    json_output = False
    review_kind = "all"
    quality_status = "all"
    playbook_state = "all"
    scope_type = "all"
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            _print_management_usage()
            return 0
        if arg == "--json":
            json_output = True
        elif arg == "--project":
            if i + 1 >= len(args):
                print("Missing value for --project")
                _print_management_usage()
                return 2
            project_folder = args[i + 1]
            i += 1
        elif arg == "--review-limit":
            if i + 1 >= len(args):
                print("Missing value for --review-limit")
                _print_management_usage()
                return 2
            try:
                review_limit = int(args[i + 1])
            except ValueError:
                print("--review-limit must be an integer")
                return 2
            i += 1
        elif arg == "--playbook-limit":
            if i + 1 >= len(args):
                print("Missing value for --playbook-limit")
                _print_management_usage()
                return 2
            try:
                playbook_limit = int(args[i + 1])
            except ValueError:
                print("--playbook-limit must be an integer")
                return 2
            i += 1
        elif arg == "--review-kind":
            if i + 1 >= len(args):
                print("Missing value for --review-kind")
                _print_management_usage()
                return 2
            review_kind = args[i + 1]
            i += 1
        elif arg == "--quality":
            if i + 1 >= len(args):
                print("Missing value for --quality")
                _print_management_usage()
                return 2
            quality_status = args[i + 1]
            i += 1
        elif arg == "--playbook-state":
            if i + 1 >= len(args):
                print("Missing value for --playbook-state")
                _print_management_usage()
                return 2
            playbook_state = args[i + 1]
            i += 1
        elif arg == "--scope":
            if i + 1 >= len(args):
                print("Missing value for --scope")
                _print_management_usage()
                return 2
            scope_type = args[i + 1]
            i += 1
        else:
            print(f"Unknown management option: {arg}")
            _print_management_usage()
            return 2
        i += 1

    view = build_management_view(
        Engram(),
        project_folder=project_folder,
        review_limit=review_limit,
        playbook_limit=playbook_limit,
        review_kind=review_kind,
        quality_status=quality_status,
        playbook_state=playbook_state,
        scope_type=scope_type,
    )
    if json_output:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        print(render_management_text(view), end="")
    return 0


def main() -> None:
    """CLI entry: setup / doctor / repair-encoding / telemetry / governance."""
    _configure_utf8_stdio()
    args = sys.argv[1:]
    if not args or args[0] == "setup":
        run_setup(
            advanced="--advanced" in args,
            apply_external_config="--apply-external-config" in args,
        )
    elif args[0] == "doctor":
        fix = "--fix" in args
        sys.exit(run_doctor(fix=fix))
    elif args[0] == "sessions":
        sys.exit(run_sessions(args[1:]))
    elif args[0] == "review":
        sys.exit(run_review(args[1:]))
    elif args[0] == "playbook":
        sys.exit(run_playbook(args[1:]))
    elif args[0] == "status":
        sys.exit(run_status(args[1:]))
    elif args[0] == "continuity":
        sys.exit(run_continuity(args[1:]))
    elif args[0] == "management":
        sys.exit(run_management(args[1:]))
    elif args[0] == "stats":
        from piia_engram.stats import run_stats, log_stats
        if "--log" in args:
            log_stats()
        else:
            run_stats()
    elif args[0] == "telemetry":
        _run_telemetry_cli(args[1:])
    elif args[0] == "privacy":
        _run_privacy_report()
    elif args[0] == "feedback":
        run_feedback(dry_run="--dry-run" in args)
    elif args[0] == "reindex":
        _run_reindex()
    elif args[0] == "repair-encoding":
        sys.exit(_run_repair_encoding(args[1:]))
    elif args[0] == "recover-json":
        sys.exit(_run_recover_json(args[1:]))
    elif args[0] == "backup-plan":
        sys.exit(_run_backup_plan(args[1:]))
    elif args[0] == "import":
        sys.exit(_run_import_backup(args[1:]))
    elif args[0] == "export-agents-md":
        sys.exit(_run_export_agents_md(args[1:]))
    elif args[0] == "recall":
        sys.exit(_run_recall(args[1:]))
    elif args[0] == "lifecycle":
        sys.exit(_run_lifecycle(args[1:]))
    elif args[0] == "merge":
        sys.exit(_run_merge(args[1:]))
    elif args[0] == "reconcile":
        sys.exit(_run_reconcile(args[1:]))
    elif args[0] == "integrity":
        sys.exit(_run_integrity(args[1:]))
    elif args[0] == "dashboard":
        sys.exit(_run_dashboard(args[1:]))
    elif args[0] == "release-check":
        sys.exit(_run_release_check(args[1:]))
    elif args[0] == "telemetry-validate":
        sys.exit(_run_telemetry_validate(args[1:]))
    elif args[0] == "grants":
        sys.exit(run_grants(_governance_root()))
    elif args[0] == "trust":
        if len(args) < 3:
            print("usage: engram trust <agent_id> <trusted-local|read-only-external|private-self>")
            sys.exit(2)
        sys.exit(run_trust(_governance_root(), args[1], args[2]))
    elif args[0] == "revoke":
        if len(args) < 2:
            print("usage: engram revoke <agent_id>")
            sys.exit(2)
        sys.exit(run_revoke(_governance_root(), args[1]))
    elif args[0] == "audit":
        sys.exit(run_audit(_governance_root()))
    elif args[0] == "verify-ledger":
        sys.exit(run_verify_ledger(_governance_root()))
    else:
        print(
            "Engram CLI\n\n"
            "Usage:\n"
            "  engram setup            Interactive setup (read-only for external client configs)\n"
            "  engram setup --apply-external-config  Auto-configure AI clients with backups\n"
            "  engram setup --advanced Full interactive setup with privacy prompts\n"
            "  engram doctor           Check config health (all AI tools)\n"
            "  engram doctor --fix     Auto-repair any issues found\n"
            "  engram status           Show a redacted install + memory health summary\n"
            "  engram status --html    Write a local redacted status page\n"
            "  engram continuity       Prove cross-tool handoff readiness (metadata only)\n"
            "  engram management       Show a metadata-only review/playbook management view\n"
            "  engram sessions         List saved cross-tool agent sessions\n"
            "  engram sessions show <id>  Print one saved session\n"
            "  engram review           List staging knowledge awaiting review\n"
            "  engram review show <id> Inspect one review item\n"
            "  engram review approve <id> --yes  Promote staging item\n"
            "  engram review archive <id> --yes  Archive review item\n"
            "  engram playbook install <builtin-name> [--yes]\n"
            "  engram feedback         Generate anonymous beta feedback report\n"
            "  engram feedback --dry-run  Preview payload without sending\n"
            "  engram reindex          Rebuild the hybrid search index from JSON\n"
            "  engram repair-encoding  Dry-run mojibake scan (use --apply to fix)\n"
            "  engram recover-json <dataset>  Dry-run metadata scan for corrupt JSON backups\n"
            "  engram backup-plan      Metadata-only local backup plan (--json for raw)\n"
            "  engram import <backup.json>  Metadata-only import preview (--apply --yes to write)\n"
            "  engram export-agents-md Export verified, non-sensitive knowledge as an AGENTS.md block\n"
            "  engram recall           Single-call owner recall digest (--project/--query/--json)\n"
            "  engram lifecycle        Metadata-only decay/archive proposal (never deletes)\n"
            "  engram integrity        Read-only integrity scan + self-heal proposals\n"
            "  engram dashboard        Non-technical owner control view (--html/--json)\n"
            "  engram release-check    Read-only release readiness report (no publish)\n"
            "  engram telemetry-validate  Static payload/schema/migration consistency check\n"
            "  engram grants           List agent trust grants + revocations\n"
            "  engram trust <a> <lvl>  Grant an agent a trust level\n"
            "  engram revoke <agent>   Revoke an agent (future disclosure only)\n"
            "  engram audit            Show recent disclosure receipts + ledger check\n"
            "  engram verify-ledger    Verify the governance ledger hash chain\n"
            "  engram stats            Show project growth metrics\n"
            "  engram stats --log      Append stats snapshot to local log\n"
            "  engram telemetry        Manage anonymous usage statistics\n"
            "  engram privacy          Show what data Engram stores\n\n"
            "Export & identity (run these as MCP tools in your AI client):\n"
            "  get_identity_card       Owner-gated Markdown identity card export\n"
            "  export_knowledge_report Readable Markdown report of active knowledge\n"
            "  export_engram           Full local JSON backup (treat as sensitive)\n\n"
            "Tool tiers:\n"
            "  Default: 17 核心工具 / core MCP tools.\n"
            "  Set ENGRAM_TOOLS=all to unlock all 84 tools.\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
