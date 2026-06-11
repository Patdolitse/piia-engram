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

# 单个规则文件最多读取的行数。够覆盖绝大多数 CLAUDE.md / AGENTS.md 全文，
# 同时给超大文件设一个安全上限，避免把巨型文档整体灌进来。
# 可用环境变量 ENGRAM_MAX_RULE_LINES 覆盖（极少数超大规则仓库场景）。
_MAX_RULE_LINES = 1500

# 单条分组 lesson 的 detail 字符上限。1500 行规则在极端情况下可能很大，
# 给个防御性上限，避免单条 lesson 撑爆 lessons.json（历史上有过损坏问题）。
_MAX_DETAIL_CHARS = 20000


def _max_rule_lines() -> int:
    """规则文件读取行数上限，支持 ENGRAM_MAX_RULE_LINES 覆盖，回退默认值。"""
    raw = os.environ.get("ENGRAM_MAX_RULE_LINES", "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return _MAX_RULE_LINES


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

    lines = text.splitlines()[:_max_rule_lines()]  # 读全文，超大文件设安全上限
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


def _src_label(path) -> str:
    """规则文件的来源标签：父目录名/文件名，避免存绝对路径，也便于区分同名文件。

    根目录文件（如 /CLAUDE.md）的 parent.name 为空，用 "." 兜底，
    避免生成形如 "/CLAUDE.md" 的伪绝对路径标签。
    """
    try:
        parent_name = path.parent.name or "."
        return f"{parent_name}/{path.name}"
    except AttributeError:
        return str(path)


def _build_grouped_detail(sections: dict[str, list[str]]) -> str:
    """把 {来源标签: [规则行]} 渲染成按来源分节的 markdown detail。

    对极端超长内容做防御性截断，避免单条 lesson 撑爆 lessons.json。
    截断在「行边界」进行（而非任意字符位置），保证不会把某条规则切成半行、
    也不会在 markdown 列表项中间断开。注意 Python 字符串切片按 Unicode 码点
    计数，本身不会切坏多字节字符，这里在行边界截断是为了语义完整。
    """
    parts: list[str] = []
    for label, rules in sections.items():
        parts.append(f"## {label}")
        parts.extend(f"- {r}" for r in rules)
        parts.append("")
    detail = "\n".join(parts).strip()
    if len(detail) > _MAX_DETAIL_CHARS:
        # 在不超过上限的前提下，回退到最后一个换行边界，避免切半行规则
        clipped = detail[:_MAX_DETAIL_CHARS]
        last_nl = clipped.rfind("\n")
        if last_nl > 0:
            clipped = clipped[:last_nl]
        detail = clipped.rstrip() + "\n\n…(truncated)"
    return detail


def _upsert_grouped_lesson(engram, summary: str, domain: str, detail: str) -> int:
    """以 upsert 方式写入一条「setup 导入」分组 lesson。

    为什么不直接 add_lesson：add_lesson 自带基于 *summary* 的相似度去重，
    而分组 lesson 用的是固定模板 summary。第二次 `engram setup` 时新内容会
    被判为与上次完全重复而被丢弃 → 规则更新永远无法落地（真 bug）。
    因此这里按 source_tool=engram_setup + domain 找已存在的导入 lesson：
    有就 update_lesson 刷新第一条（canonical）的 summary/detail（绕开 summary
    去重、反映最新规则），没有才 add_lesson 新增。

    迁移兼容：早期版本逐行导入会在同一 domain 留下多条 engram_setup lesson。
    本函数更新 canonical 那条后，会把同 domain 其余 engram_setup 碎片标记为
    outdated（status != "active" → 后续 get_lessons 不再返回），避免新旧并存
    造成的陈旧碎片污染。归档而非删除，保留可审计的历史。

    Returns: 1 表示已落地一条分组 lesson。
    """
    try:
        existing = engram.get_lessons(
            domain=domain,
            source_tool="engram_setup",
            limit=None,
            _update_access=False,   # 只查不计访问
            _migrate_fields=False,  # 只读，不回写旧知识文件
        )
    except Exception:
        existing = []

    existing_ids = [les["id"] for les in existing if les.get("id")]

    if existing_ids:
        canonical_id, *stragglers = existing_ids
        engram.update_lesson(canonical_id, {"summary": summary, "detail": detail})
        # 归档同 domain 残留的旧逐行导入碎片，避免新旧并存
        for straggler_id in stragglers:
            try:
                engram.update_lesson(straggler_id, {"status": "outdated"})
            except Exception:
                pass
    else:
        engram.add_lesson(
            summary,
            domain=domain,
            detail=detail,
            source_tool="engram_setup",
        )
    return 1


def _import_with_split(
    rule_files: list[dict],
    engram,
) -> dict:
    """将扫描到的规则文件按分流规则导入 Engram。

    去碎片化：不再逐行存 lesson（那会制造大量低质量碎片），而是把所有
    user 类规则汇成 *一条* user_preference lesson、所有 project 类规则汇成
    *一条* project_rules lesson，detail 里按来源文件分节保留 provenance。
    语言偏好仍单独提取写入 profile。

    Returns: {user_count, project_count, skipped, files,
              user_lessons, project_lessons}
    """
    user_sections: dict[str, list[str]] = {}
    project_sections: dict[str, list[str]] = {}
    skipped = 0
    user_count = 0
    project_count = 0
    prefs_update: dict = {}

    for rf in rule_files:
        scope = rf["scope"]
        label = _src_label(rf["path"])
        for line in rf["lines"]:
            category = _classify_line(line, scope)
            stripped = line.strip()
            if category == "user":
                user_sections.setdefault(label, []).append(stripped)
                user_count += 1
                # 语言偏好 → profile（不影响其进入分组 lesson）
                lower = stripped.lower()
                if "中文" in stripped:
                    prefs_update["language"] = "中文"
                elif "english" in lower:
                    prefs_update["language"] = "English"
            elif category == "project":
                project_sections.setdefault(label, []).append(stripped)
                project_count += 1
            else:
                skipped += 1

    if prefs_update:
        engram.update_profile(prefs_update)

    user_lessons = 0
    project_lessons = 0

    if user_sections:
        user_lessons = _upsert_grouped_lesson(
            engram,
            _t("用户身份与偏好（Engram 从规则文件导入）",
               "User identity & preferences (imported by Engram from rule files)"),
            "user_preference",
            _build_grouped_detail(user_sections),
        )

    if project_sections:
        project_lessons = _upsert_grouped_lesson(
            engram,
            _t("项目规则（Engram 从规则文件导入）",
               "Project rules (imported by Engram from rule files)"),
            "project_rules",
            _build_grouped_detail(project_sections),
        )

    return {
        "user_count": user_count,
        "project_count": project_count,
        "skipped": skipped,
        "files": [str(rf["path"]) for rf in rule_files],
        "user_lessons": user_lessons,
        "project_lessons": project_lessons,
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
    - verified: True = evidence-tracked setup path, False = expected/community setup path
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
            "本机已安装 Piia Engram（MCP 记忆层）。\n\n"
            "- **会话开始**：调用 `get_resume_brief` 接续上一轮工作（跨会话/跨工具连续性）\n"
            "- **对话开头（首次/新项目）**：调用 `get_user_context` 了解用户身份和偏好\n"
            "- **学到经验/踩坑**：调用 `add_lesson` 存入\n"
            "- **做出决策**：调用 `add_decision` 记录选择和理由\n"
            "- **对话结束**：调用 `wrap_up_session` 保存上下文\n"
            "- **用户问起历史对话**（“我刚才/之前问过什么”、“上次聊到哪”）：调用 `get_recent_context` 查找\n"
            "- **搜索历史知识**：调用 `search_knowledge`\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "Piia Engram (MCP memory layer) is installed on this machine.\n\n"
            "- **Session start**: call `get_resume_brief` to resume from the last session (cross-session / cross-tool continuity)\n"
            "- **Conversation start (first time / new project)**: call `get_user_context` to learn user identity and preferences\n"
            "- **Lessons learned**: call `add_lesson` to save\n"
            "- **Decisions made**: call `add_decision` to record choice and reasoning\n"
            "- **End of conversation**: call `wrap_up_session` to save context\n"
            "- **User asks about past conversations** (\"what did I just ask\", \"where did we leave off\"): call `get_recent_context`\n"
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
            "本机已安装 Piia Engram（MCP 记忆层）。\n\n"
            "- 会话开始调用 `get_resume_brief` 接续上一轮工作（跨工具连续性的关键）\n"
            "- 首次对话或新项目调用 `get_user_context` 了解用户\n"
            "- 学到经验时调用 `add_lesson`\n"
            "- 做决策时调用 `add_decision`\n"
            "- 对话结束调用 `wrap_up_session`\n"
            "- 用户问起历史对话（“我刚才/之前问过什么”）调用 `get_recent_context`\n"
            "- 搜索知识用 `search_knowledge`\n"
        ),
        "snippet_en": (
            "---\n"
            "description: Engram memory layer — AI remembers user identity and experience\n"
            "globs:\n"
            "alwaysApply: true\n"
            "---\n\n"
            "Piia Engram (MCP memory layer) is installed.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (key to cross-tool continuity)\n"
            "- First conversation or new project: call `get_user_context` to learn user\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- End of conversation: call `wrap_up_session`\n"
            "- User asks about past conversations (\"what did I just ask\"): call `get_recent_context`\n"
            "- Search knowledge: call `search_knowledge`\n"
        ),
    },
    "codex": {
        "path_fn": lambda home: home / ".codex" / "AGENTS.md",
        "snippet_zh": (
            "\n{marker}\n"
            "## Engram 记忆层\n\n"
            "本机已安装 Piia Engram（MCP 记忆层）。\n\n"
            "- 会话开始：调用 `get_resume_brief` 接续上一轮工作（跨工具连续性）\n"
            "- 首次/新项目：调用 `get_user_context` 了解用户身份和偏好\n"
            "- 学到经验/踩坑：调用 `add_lesson` 存入\n"
            "- 做出决策：调用 `add_decision` 记录\n"
            "- 任务结束：调用 `wrap_up_session` 保存上下文\n"
            "- 用户问起历史对话（“我刚才/之前问过什么”、“上次聊到哪”）：调用 `get_recent_context` 查找\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "Piia Engram (MCP memory layer) is installed.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (cross-tool continuity)\n"
            "- First time / new project: call `get_user_context` to learn user identity and preferences\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- Task end: call `wrap_up_session` to save context\n"
            "- User asks about past conversations (\"what did I just ask\", \"where did we leave off\"): call `get_recent_context`\n"
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
            "本机已安装 Piia Engram（MCP 记忆层）。\n\n"
            "- 会话开始：调用 `get_resume_brief` 接续上一轮工作（跨工具连续性）\n"
            "- 首次/新项目：调用 `get_user_context` 了解用户身份和偏好\n"
            "- 学到经验/踩坑：调用 `add_lesson` 存入\n"
            "- 做出决策：调用 `add_decision` 记录\n"
            "- 任务结束：调用 `wrap_up_session` 保存上下文\n"
            "- 用户问起历史对话（“我刚才/之前问过什么”）：调用 `get_recent_context` 查找\n"
            "- 搜索历史知识：调用 `search_knowledge`\n"
            "{marker_end}\n"
        ),
        "snippet_en": (
            "\n{marker}\n"
            "## Engram Memory Layer\n\n"
            "Piia Engram (MCP memory layer) is installed on this machine.\n\n"
            "- Session start: call `get_resume_brief` to resume the previous session (cross-tool continuity)\n"
            "- First time / new project: call `get_user_context` to learn user identity and preferences\n"
            "- Lessons learned: call `add_lesson`\n"
            "- Decisions made: call `add_decision`\n"
            "- Task end: call `wrap_up_session` to save context\n"
            "- User asks about past conversations (\"what did I just ask\"): call `get_recent_context`\n"
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
    # (e.g. "/usr/bin/python3" or "C:/Python312/python.exe").
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
    extra_env: dict[str, str] | None = None,
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

    # 重跑 setup 不得静默关闭用户已启用的增强检索（env 块整体重建会丢键）。
    preserved_search = (extra_env or {}).get("ENGRAM_SEARCH") or existing_env.get("ENGRAM_SEARCH")
    if preserved_search:
        env["ENGRAM_SEARCH"] = str(preserved_search)

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
    extra_env: dict[str, str] | None = None,
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
    # 重跑 setup 不得静默关闭用户已启用的增强检索（env 段整体重建会丢键）。
    preserved_search = (extra_env or {}).get("ENGRAM_SEARCH") or existing_env.get("ENGRAM_SEARCH")
    if preserved_search:
        engram_block.append(f'ENGRAM_SEARCH = {toml_string(str(preserved_search))}')

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
    extra_env: dict[str, str] | None = None,
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
            extra_env=extra_env,
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
        extra_env=extra_env,
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
        rule_total = import_result["user_count"] + import_result["project_count"]
        grouped_lessons = import_result["user_lessons"] + import_result["project_lessons"]
        print(_t(f"\n  ✅ 已读取: {import_result['user_count']} 条身份规则 + {import_result['project_count']} 条项目规则",
                 f"\n  ✅ Read: {import_result['user_count']} identity + {import_result['project_count']} project rules"))
        if grouped_lessons > 0:
            # 关键：N 条规则去碎片化后归整为 grouped_lessons 条记忆，不是丢了规则——
            # 规则按来源文件分节保留在这几条记忆的 detail 里（极长文件可能截断）。
            print(_t(f"  📦 已归整为 {grouped_lessons} 条记忆（{rule_total} 条规则去碎片化合并，按来源文件分节保留出处）。",
                     f"  📦 Consolidated into {grouped_lessons} memory entr{'y' if grouped_lessons == 1 else 'ies'} "
                     f"({rule_total} rules merged, kept under their source-file sections)."))
            print(_t("  🔒 提示：规则原文已存入本地记忆。若文件含密钥/令牌/隐私，请运行 'engram review' 删除。",
                     "  🔒 Note: rule text is stored verbatim in local memory. If files contain secrets/tokens/private info, run 'engram review' to remove."))
            print(_t("  ✍️  导入内容仅作起点 — 如需纠正或删除，运行 'engram review' 复核。",
                     "  ✍️  Imports are just a starting point — run 'engram review' to correct or remove anything."))
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

def _vector_deps_available() -> bool:
    """语义向量层可选依赖（piia-engram[vector]）是否已安装。"""
    try:
        import fastembed  # noqa: F401
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


def _run_hybrid_search_offer(python_path: str) -> bool:
    """可选步骤：一键启用增强检索（hybrid）。返回是否启用。

    启用时设置本进程 ENGRAM_SEARCH=hybrid（让 setup 期间的 Engram 调用与
    收尾 reindex 走 hybrid 路径）；持久化由调用方把它写进各客户端 MCP
    配置的 env 块完成。缺少向量依赖时 hybrid 自动降级为关键词+全文，
    依赖安装是可选的。
    """
    print(_t("可选 — 增强检索（hybrid：关键词 + 全文 + 语义向量）",
             "Optional — Enhanced search (hybrid: keyword + full-text + semantic vectors)"))
    print(_t("    跨语言召回更好：中文存的知识，英文搜索也能命中。随时可还原为默认检索。",
             "    Better cross-lingual recall (store in one language, find in another). Reversible anytime."))
    ans = _prompt(_t("  启用增强检索？ 1=启用  2=暂不（默认）",
                     "  Enable enhanced search? 1=Yes  2=Not now (default)"), "2")
    if ans.strip() != "1":
        print(_t("  ℹ️  保持默认关键词检索。以后可设 ENGRAM_SEARCH=hybrid 并运行 'engram reindex' 开启（见 docs/hybrid-search.md）。",
                 "  ℹ️  Keeping default keyword search. Enable later via ENGRAM_SEARCH=hybrid + 'engram reindex' (see docs/hybrid-search.md)."))
        return False

    os.environ["ENGRAM_SEARCH"] = "hybrid"
    if not _vector_deps_available():
        ins = _prompt(_t(
            "  语义向量依赖未安装。 1=现在安装（pip install 'piia-engram[vector]'）  2=跳过（先用关键词+全文）",
            "  Vector deps not installed. 1=Install now (pip install 'piia-engram[vector]')  2=Skip (keyword + full-text for now)",
        ), "1")
        if ins.strip() == "1":
            print(_t("  ⏳ 正在安装（首次会下载向量模型依赖，可能需要几分钟）…",
                     "  ⏳ Installing (first run downloads vector model deps; may take a few minutes)…"))
            try:
                rc = subprocess.call(
                    [python_path, "-m", "pip", "install", "piia-engram[vector]"]
                )
            except OSError as exc:
                rc = 1
                _safe_print(f"  ⚠️  pip launch failed: {exc}")
            if rc == 0:
                print(_t("  ✅ 语义向量依赖已安装", "  ✅ Vector dependencies installed"))
            else:
                print(_t("  ⚠️  安装未完成 — 增强检索仍可用（关键词+全文）。稍后可重试：pip install 'piia-engram[vector]'",
                         "  ⚠️  Install incomplete — enhanced search still works (keyword + full-text). Retry later: pip install 'piia-engram[vector]'"))
        else:
            print(_t("  ℹ️  已跳过 — 增强检索先以关键词+全文运行；装上 'piia-engram[vector]' 后自动升级为语义检索。",
                     "  ℹ️  Skipped — enhanced search runs as keyword + full-text; installs of 'piia-engram[vector]' upgrade it automatically."))
    print(_t("  ✅ 增强检索已启用", "  ✅ Enhanced search enabled"))
    return True


def _run_hybrid_reindex() -> None:
    """Setup 收尾：为已启用的增强检索构建持久化索引（含种子知识）。"""
    try:
        eng = _get_engram_class()()
        result = eng.rebuild_index()
    except Exception as exc:
        _safe_print(_t(f"  ⚠️  索引构建失败（不影响使用，检索会自动重建）：{exc}",
                       f"  ⚠️  Index build failed (search auto-rebuilds later): {exc}"))
        return
    if result.get("skipped") == "corpus_encrypted":
        print(_t("  ℹ️  已启用库加密 — 不落盘明文索引，检索回退为关键词模式。",
                 "  ℹ️  Corpus encryption enabled — no plaintext index on disk; search falls back to keyword mode."))
        return
    vec = result.get("vector_enabled")
    print(_t(
        f"  ✅ 检索索引已构建（{result.get('indexed', 0)} 条；语义向量层：{'开' if vec else '关'}）",
        f"  ✅ Search index built ({result.get('indexed', 0)} entries; vector layer: {'on' if vec else 'off'})",
    ))


def _apply_external_configs(
    tools: list[dict],
    python_path: str,
    mcp_server_path: str,
    selected_data_dir: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Write MCP config + inject instruction snippets/hooks for each detected
    tool. Returns ``(success_names, failed_names)``.

    The caller owns the user-consent decision (interactive confirm or the
    ``--apply-external-config`` flag); this function assumes the write is
    authorized and always passes ``authorized_external_write=True``. Every
    write goes through the backup-protected ``_write_*`` helpers.
    """
    success: list[str] = []
    failed: list[str] = []
    configured_tool_ids: list[str] = []
    for tool in tools:
        try:
            _write_tool_mcp_config(
                tool,
                python_path,
                mcp_server_path,
                selected_data_dir,
                file_safety_root=selected_data_dir,
                authorized_external_write=True,
                extra_env=extra_env,
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

    return success, failed


def run_setup(advanced: bool = False, apply_external_config: bool = False) -> None:
    """交互式安装向导主流程。

    Streamlined flow: auto-detect tools and initialize Engram. When AI clients
    are detected, setup lists the exact config files it will touch and asks for
    a one-keystroke confirm before writing (every write is backup-protected).
    Choosing "No" keeps all external config files unchanged. Passing
    ``apply_external_config=True`` (CLI flag ``--apply-external-config``) skips
    the prompt and writes directly — useful for non-interactive/CI runs.

    Args:
        advanced: If True, show full interactive privacy preferences.
        apply_external_config: If True, skip the consent prompt and mutate
            detected AI client configs directly (still backup-protected). If
            False, ask the user first; on "No" nothing external is changed.
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
    print(_t("  Piia Engram 安装向导", "  Piia Engram Setup Wizard"))
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

    # 可选 — 增强检索（一键开 hybrid；选择需在写客户端配置前定下来）
    print()
    hybrid_enabled = _run_hybrid_search_offer(python_path)
    extra_env = {"ENGRAM_SEARCH": "hybrid"} if hybrid_enabled else None
    print()

    # 工具检测 — 默认交互确认后写入；--apply-external-config 跳过确认直写。
    tools = _detect_tools()
    success: list[str] = []
    failed: list[str] = []
    external_config_written = False
    if not tools:
        print(_t("  ⚠️  未检测到 AI 工具（Claude Code / Cursor / Claude Desktop）",
                 "  ⚠️  No AI tools detected (Claude Code / Cursor / Claude Desktop)"))
        print(_t("  安装后重新运行 'engram setup' 即可。\n",
                 "  Re-run 'engram setup' after installing.\n"))
    else:
        detected_names = ", ".join(tool["name"] for tool in tools)
        should_write = apply_external_config
        if not apply_external_config:
            # 默认路径：列出工具与将写入的文件，征得同意后写入（写前自动备份）。
            print(_t(
                f"  🔎 已检测到 AI 工具：{detected_names}",
                f"  🔎 Detected AI tools: {detected_names}",
            ))
            print(_t(
                "  即将把 Engram 写入以下客户端的 MCP 配置（写入前自动备份）：",
                "  Engram will be added to these clients' MCP config (auto-backed-up first):",
            ))
            for tool in tools:
                print(f"      - {tool['config_path']}")
            ans = _prompt(_t(
                "  自动写入以上配置？ 1=是，自动配置（推荐）  2=否，仅只读检查",
                "  Write these configs now? 1=Yes, auto-configure (recommended)  2=No, read-only",
            ), "1")
            should_write = ans.strip() != "2"
        if should_write:
            success, failed = _apply_external_configs(
                tools, python_path, mcp_server_path, selected_data_dir,
                extra_env=extra_env,
            )
            external_config_written = True
            if failed:
                print(_t(
                    "  ℹ️  部分客户端写入失败，可稍后重试或手动添加 MCP 配置。",
                    "  ℹ️  Some clients failed; retry later or add the MCP config manually.",
                ))
        else:
            print(_t(
                "  ℹ️  已跳过写入，未更改任何外部配置文件。",
                "  ℹ️  Skipped — no external config files were changed.",
            ))
            print(_t(
                "      之后想自动配置可运行：engram setup --apply-external-config",
                "      To auto-configure later, run: engram setup --apply-external-config",
            ))
    print()

    # Step 2 — 录入身份信息
    _run_seed_knowledge_onboarding(
        selected_data_dir,
        external_config_applied=external_config_written,
    )

    # Step 3 — 隐私偏好
    if advanced:
        _run_privacy_preferences(selected_data_dir)
    else:
        _run_privacy_defaults(selected_data_dir)

    # 增强检索收尾：等种子知识录入后再建索引，索引才包含初始知识
    if hybrid_enabled:
        _run_hybrid_reindex()
        if not external_config_written:
            print(_t(
                "  ⚠️  本次未写入客户端配置 — 要让 AI 工具用上增强检索，请在各工具 MCP 配置的 env 里加 ENGRAM_SEARCH=hybrid，"
                "或运行 engram setup --apply-external-config",
                "  ⚠️  Client configs were not written — to use enhanced search in your AI tools, add ENGRAM_SEARCH=hybrid "
                "to each tool's MCP config env, or run: engram setup --apply-external-config",
            ))
        print()

    # 完成
    print(_t("  重启你的 AI 工具即可使用：",
             "  Setup next step:"))
    if external_config_written:
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
        external_config_mode="apply" if external_config_written else "read_only",
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



# ---------------------------------------------------------------------------
# Split command modules — re-exported so piia_engram.setup_wizard keeps its
# historical surface (tests and mcp_server import/patch these names here).
# ---------------------------------------------------------------------------
from .doctor import (  # noqa: E402,F401 — re-exports
    _detect_installed_tools,
    _file_sha256_12,
    _servers_from_config,
    _shared_instruction_candidates,
    _claude_hook_rows,
    _build_config_integrity_report,
    _print_config_integrity_report,
    _entry_args,
    _classify_engram_entry,
    _probe_mcp_entry,
    _validate_engram_entry,
    run_doctor,
    _run_functional_checks,
)
from .cli_commands import (  # noqa: E402,F401 — re-exports
    _format_session_size,
    _parse_sessions_limit,
    _SESSION_SCAN_LIMIT,
    _run_continuity_checks,
    _print_sessions_usage,
    run_sessions,
    _print_review_usage,
    _review_title,
    _review_quality_summary,
    _review_quality_score,
    _clean_review_inline,
    _truncate_review_text,
    _print_review_quality_detail,
    _review_items,
    _print_review_list,
    _print_review_item,
    _require_yes,
    _print_playbook_usage,
    _arg_value,
    run_playbook,
    run_review,
    _run_telemetry_cli,
    _run_privacy_report,
    _build_feedback_report,
    run_feedback,
    _run_reindex,
    _run_repair_encoding,
    _run_recover_json,
    _run_backup_plan,
    _render_import_result_text,
    _run_import_backup,
    _run_export_agents_md,
    _run_recall,
    _run_portrait,
    _run_telemetry_validate,
    _run_release_check,
    _run_dashboard,
    _run_integrity,
    _run_lifecycle,
    _run_lifecycle_apply,
    _run_lifecycle_restore,
    _run_merge,
    _parse_merge_opts,
    _run_merge_apply,
    _run_reconcile,
    _governance_root,
    run_grants,
    run_trust,
    run_revoke,
    run_audit,
    run_verify_ledger,
    _print_status_usage,
    run_status,
    _print_preview_usage,
    run_preview,
    _print_continuity_usage,
    run_continuity,
    _print_management_usage,
    _run_management_action_cli,
    run_management,
)


def main() -> None:
    """CLI entry: setup / doctor / repair-encoding / telemetry / governance."""
    _configure_utf8_stdio()
    args = sys.argv[1:]
    # Non-intrusive update reminder (stderr only, opt-out, 24h-cached, fail-silent).
    # `doctor` prints its own richer version line, so skip the generic notice there
    # to avoid a double-print. Never reached by the separate MCP-server entry point.
    if not (args and args[0] == "doctor"):
        try:
            from piia_engram.update_check import maybe_print_update_notice

            maybe_print_update_notice()
        except Exception:
            pass
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
    elif args[0] == "preview":
        sys.exit(run_preview(args[1:]))
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
    elif args[0] == "portrait":
        sys.exit(_run_portrait(args[1:]))
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
    elif args[0] == "watcher":
        from piia_engram.watcher.install import run_watcher_cli

        sys.exit(run_watcher_cli(args[1:]))
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
            "  engram preview          Show what a simulated AI caller would receive (--as/--level/--html)\n"
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
            "  engram portrait         Lean user portrait snapshot + growth since last (--list/--no-save/--json)\n"
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
            "  engram watcher install  Auto-capture sessions from hook-less AI tools (per-user autostart)\n"
            "  engram watcher status   Show watcher install + last-scan status\n"
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
            "  Set ENGRAM_TOOLS=all to unlock all 53 tools.\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
