"""Engram MCP Server.

Exposes Engram as an MCP server over stdio or SSE transport.
Any MCP-compatible AI tool can access the user's identity, preferences,
lessons, decisions, and skills.

Usage:
    python mcp_server.py
    python -m piia_engram.mcp_server --transport sse

Designed for local stdio transport and self-hosted remote SSE transport.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from piia_engram.beta_tracker import track_event as _beta

# Starlette imports are deferred to SSE mode — not needed for stdio.
# Importing eagerly can slow startup and fail in minimal Docker images.
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    _HAS_STARLETTE = True
except ImportError:
    _HAS_STARLETTE = False

# ---------------------------------------------------------------------------
# Sibling import setup (same pattern as local_llm_bridge.py)
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402
try:
    from .core import Engram, export_to_openclaw, import_from_openclaw  # noqa: E402
except ImportError:
    from core import Engram, export_to_openclaw, import_from_openclaw  # noqa: E402

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_engram = Engram()

# Anonymous usage statistics tracker (Phase 1: local log only)
try:
    from .telemetry import ToolCallTracker as _ToolCallTracker
except ImportError:
    try:
        from telemetry import ToolCallTracker as _ToolCallTracker  # type: ignore
    except ImportError:
        _ToolCallTracker = None  # type: ignore

_tracker = _ToolCallTracker() if _ToolCallTracker else None
_track_count = 0  # count calls for periodic flush
_FLUSH_EVERY = 10  # flush every N tool calls to avoid data loss


def _flush_telemetry(force: bool = False) -> None:
    """Flush telemetry data. Called periodically and on exit."""
    if _tracker is None:
        return
    try:
        from importlib.metadata import version as _pkg_version
        try:
            _ver = _pkg_version("piia-engram")
        except Exception:
            _ver = "dev"
        _tier = os.environ.get("ENGRAM_TOOLS", "core")
        _tracker.flush(engram_version=_ver, tools_tier=_tier, force=force)
    except Exception:
        pass  # never let telemetry affect MCP tools


# ---------------------------------------------------------------------------
# Session auto-tracking: record tool calls, auto-save on exit
# ---------------------------------------------------------------------------
from datetime import datetime as _dt


class _SessionTracker:
    """Track tool calls during this MCP server session.

    On process exit, automatically saves the accumulated operation log
    via save_agent_context so sessions are never lost — even when the
    AI tool forgets to call save_agent_context explicitly.
    """

    # Tools that indicate cold-start, not real work
    _COLD_START_TOOLS = frozenset({
        "get_user_context", "refresh_quick_context", "get_identity_card",
    })
    # Minimum non-cold-start calls to trigger auto-save
    _MIN_CALLS = 2

    _CHECKPOINT_EVERY = 20  # interim save every N real tool calls

    def __init__(self) -> None:
        self.session_id = f"auto-{_dt.now().strftime('%Y-%m-%dT%H-%M-%S')}"
        self.start_time = _dt.now()
        self.tool_name: str = ""        # detected connecting tool
        self.project_folder: str = ""   # detected project path
        self.calls: list[dict[str, str]] = []
        self.saved = False
        self._real_call_count = 0       # non-cold-start call counter
        self._checkpoint_seq = 0        # checkpoint sequence number

    def record(self, tool_called: str, args_summary: str = "") -> None:
        self.calls.append({
            "tool_called": tool_called,
            "timestamp": _dt.now().strftime("%H:%M:%S"),
            "args_summary": args_summary,
        })
        if tool_called not in self._COLD_START_TOOLS:
            self._real_call_count += 1
            if (self._real_call_count % self._CHECKPOINT_EVERY == 0
                    and self._real_call_count > 0):
                self._interim_save()

    def detect_tool(self, tool: str) -> None:
        if not self.tool_name and tool:
            self.tool_name = tool

    def detect_project(self, folder: str) -> None:
        if not self.project_folder and folder:
            self.project_folder = folder

    def _interim_save(self) -> None:
        """Save a mid-session checkpoint without marking session as done."""
        self._checkpoint_seq += 1
        tool = self.tool_name or "mcp_auto"
        duration = max(1, int((_dt.now() - self.start_time).total_seconds() / 60))

        seen: dict[str, None] = {}
        for c in self.calls:
            seen.setdefault(c["tool_called"], None)

        content = (
            f"[中间检查点 #{self._checkpoint_seq}] 会话时长: {duration} 分钟\n"
            f"工具调用次数: {len(self.calls)}\n"
            f"使用的工具: {', '.join(seen.keys())}\n"
        )
        actions = [
            {
                "tool_called": c["tool_called"],
                "arguments_summary": c.get("args_summary", ""),
                "result_summary": "",
            }
            for c in self.calls[-30:]
        ]
        try:
            _engram.save_agent_context(
                tool=tool,
                content=content,
                session_id=f"{self.session_id}-cp{self._checkpoint_seq}",
                project_folder=self.project_folder,
                actions=actions,
            )
        except Exception:
            pass  # silent — checkpoints are best-effort

    def auto_save(self) -> None:
        """Save accumulated session log. Called by atexit handler."""
        if self.saved:
            return
        # Check minimum work threshold
        real_calls = [
            c for c in self.calls
            if c["tool_called"] not in self._COLD_START_TOOLS
        ]
        if len(real_calls) < self._MIN_CALLS:
            return
        self.saved = True

        tool = self.tool_name or "mcp_auto"
        duration = max(1, int((_dt.now() - self.start_time).total_seconds() / 60))

        # Deduplicated tool list preserving order
        seen: dict[str, None] = {}
        for c in self.calls:
            seen.setdefault(c["tool_called"], None)
        unique_tools = list(seen.keys())

        content = (
            f"[MCP 自动记录] 会话时长: {duration} 分钟\n"
            f"工具调用次数: {len(self.calls)}\n"
            f"使用的工具: {', '.join(unique_tools)}\n"
        )

        # Keep last 50 actions to prevent oversized files
        actions = [
            {
                "tool_called": c["tool_called"],
                "arguments_summary": c.get("args_summary", ""),
                "result_summary": "",
            }
            for c in self.calls[-50:]
        ]

        try:
            _engram.save_agent_context(
                tool=tool,
                content=content,
                session_id=self.session_id,
                project_folder=self.project_folder,
                actions=actions,
            )
        except Exception as exc:
            logger.warning("session auto-save failed: %s", exc)

        # Auto-update project snapshot with current metrics
        if self.project_folder:
            try:
                project_info = _collect_project_info(self.project_folder)
                if project_info:
                    project_info["last_auto_snapshot"] = _dt.now().isoformat()
                    _engram.save_project_snapshot(
                        self.project_folder, project_info,
                    )
            except Exception as exc:
                logger.warning("project snapshot auto-update failed: %s", exc)


def _collect_project_info(project_folder: str) -> dict:
    """Collect lightweight project metrics from the filesystem.

    Returns a dict suitable for save_project_snapshot() merge.
    Returns empty dict if project_folder is invalid or not a Python project.
    Safe: no exceptions raised, no heavy deps, no blocking I/O.
    """
    if not project_folder:
        return {}

    root = Path(project_folder)
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return {}

    info: dict = {}

    # 1. Version from pyproject.toml
    try:
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            info["version"] = m.group(1)
    except Exception:
        pass

    # 2. Module count: .py files in src/ (excluding __pycache__)
    try:
        src_dir = root / "src"
        if src_dir.is_dir():
            info["module_count"] = sum(
                1 for p in src_dir.rglob("*.py")
                if "__pycache__" not in str(p)
            )
    except Exception:
        pass

    # 3. Test count: def test_ functions in tests/
    try:
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            tc = 0
            for tf in tests_dir.rglob("*.py"):
                if "__pycache__" in str(tf):
                    continue
                try:
                    for line in tf.read_text(encoding="utf-8").splitlines():
                        s = line.lstrip()
                        if s.startswith("def test_") or s.startswith("async def test_"):
                            tc += 1
                except Exception:
                    continue
            info["test_count"] = tc
    except Exception:
        pass

    # 4. MCP tool count: @mcp.tool() decorators
    try:
        for pkg_dir in (root / "src").iterdir():
            server_py = pkg_dir / "mcp_server.py"
            if server_py.is_file():
                info["mcp_tool_definitions"] = server_py.read_text(
                    encoding="utf-8",
                ).count("@mcp.tool()")
                break
    except Exception:
        pass

    return info


_session = _SessionTracker()

# Register atexit handler: auto-save session first, then flush telemetry
import atexit
atexit.register(lambda: (_session.auto_save(), _flush_telemetry(force=True)))


def _track(tool_name: str, success: bool = True, args_summary: str = "") -> None:
    """Record a tool call for telemetry and session auto-tracking.

    Flushes every _FLUSH_EVERY calls to avoid losing data when the
    MCP server process is killed without a clean wrap_up_session.
    """
    global _track_count
    if _tracker is not None:
        _tracker.record(tool_name, success=success)
        _track_count += 1
        if _track_count >= _FLUSH_EVERY:
            _track_count = 0
            _flush_telemetry(force=True)
    # Session auto-tracking
    _session.record(tool_name, args_summary)

IDENTITY_FIELDS = frozenset({
    "profile",
    "preferences",
    "trust_boundaries",
    "work_style",
    "quality_standards",
})

TOOL_TIER = os.environ.get("ENGRAM_TOOLS", "core").strip().lower() or "core"
TIER1_TOOLS = frozenset({
    # Session lifecycle (startup)
    "get_user_context",          # cold-start: load identity + context
    "wrap_up_session",           # session end: save insights + sync
    # Knowledge write (writeback)
    "memory_store",              # unified write endpoint (provider-compatible)
    "add_lesson",                # store reusable experience
    "add_decision",              # record decision + reasoning
    "add_playbook",              # record operational procedure
    # Knowledge read (retrieval)
    "search_knowledge",          # search across all knowledge
    "get_relevant_knowledge",    # project-aware knowledge retrieval
    # Identity
    "get_identity_card",         # export identity for non-MCP tools
    "update_identity",           # update profile/preferences/standards
    # Project context
    "get_project_context",       # current project state
    "save_project_snapshot",     # persist project state
    # Agent context recovery
    "get_recent_context",        # recover lost session context
})

mcp = FastMCP(
    "engram",
    instructions=(
        "Engram — the user's personal memory layer across all AI tools.\n\n"
        "Memory lifecycle (act on each phase without waiting for the user to ask):\n\n"
        "1. STARTUP  — get_user_context: inject user identity & context at conversation start.\n"
        "2. RETRIEVAL — search_knowledge / get_relevant_knowledge: look up past knowledge mid-conversation.\n"
        "3. WRITEBACK — memory_store (or add_lesson / add_decision / add_playbook): persist new knowledge.\n"
        "4. SESSION END — wrap_up_session: save session context & sync.\n\n"
        "Quick reference:\n"
        "- Conversation start → get_user_context(level='standard')\n"
        "- Need past knowledge → search_knowledge(query, filters_json='{\"tier\":\"verified\"}')\n"
        "- Learned something reusable → memory_store(kind='lesson', content_json=...)\n"
        "- Decision made → memory_store(kind='decision', content_json=...)\n"
        "- Conversation end → wrap_up_session\n"
    ),
)


def _apply_tool_tier() -> None:
    """Remove non-Tier-1 tools when ENGRAM_TOOLS=core (the default)."""
    if TOOL_TIER != "core":
        return

    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return

    for name in list(tools):
        if name in TIER1_TOOLS:
            continue
        try:
            mcp.remove_tool(name)
        except Exception:
            tools.pop(name, None)


def _json(obj: object) -> str:
    """Serialize to JSON string, handling empty results."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _user_lang() -> str:
    """Detect user language from profile. Returns 'zh' or 'en'."""
    from piia_engram.i18n import get_lang
    return get_lang()


def _validate_path(value: str, *, allow_empty: bool = False) -> str | None:
    """Light path hygiene for user-supplied filesystem paths.

    Engram is a local-first tool — the calling user already has full disk
    access, so this is NOT a sandboxing boundary. It only rejects the small
    set of inputs that silently break downstream system calls:

    - **Null bytes (\\x00)** — cause silent truncation in many C-level path
      APIs; treated as an attack signature.
    - **Empty / whitespace-only** — usually a programming bug, surface it loudly.

    Returns ``None`` when the value is valid; otherwise returns a human-readable
    error string the tool can return verbatim to the caller.
    """
    if value is None:
        return None if allow_empty else "路径参数缺失"
    if not isinstance(value, str):
        return f"路径参数必须是字符串（收到 {type(value).__name__}）"
    if "\x00" in value:
        return "路径包含 NUL 字节（不允许）"
    if not allow_empty and not value.strip():
        return "路径不能为空"
    return None


def _safe_err(exc: Exception) -> str:
    """Return a sanitized error message without internal filesystem paths."""
    msg = str(exc)
    # Strip any Windows/Unix absolute paths from the message
    msg = re.sub(r'[A-Za-z]:\\[\w\\. -]+', '<path>', msg)
    msg = re.sub(r'/[\w/. -]{3,}', '<path>', msg)
    return msg


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for transport configuration."""
    parser = argparse.ArgumentParser(description="Engram MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode: stdio (local) or sse (remote). Default: stdio.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (sse mode only). Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8767,
        help="Port to bind (sse mode only). Default: 8767.",
    )
    return parser.parse_args(argv[1:] if argv is not None else None)


if _HAS_STARLETTE:
    class TokenAuthMiddleware(BaseHTTPMiddleware):
        """Simple Bearer token auth for remote SSE mode."""

        def __init__(self, app, token: str):
            super().__init__(app)
            self.token = token

        async def dispatch(self, request: Request, call_next):
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer ") or not secrets.compare_digest(auth_header[7:], self.token):
                return JSONResponse(
                    {"error": "Unauthorized. Set ENGRAM_AUTH_TOKEN."},
                    status_code=401,
                )
            return await call_next(request)


# ===========================================================================
# READ TOOLS (19)
# ===========================================================================


@mcp.tool()
async def get_user_context(
    project_folder: Optional[str] = None,
    level: str = "standard",
    token_budget: Optional[int] = None,
    user_prompt: str = "",
) -> str:
    """获取用户的个性化上下文（冷启动，分层延迟可控）。 / Get tiered cold-start user context with latency control.

    **Lifecycle: startup** — 对话开始时调用，为 AI 注入用户身份和上下文。
    Lifecycle: startup — call at conversation start to inject user identity and context.

    用途：在每次新对话开始时调用，了解用户是谁、如何工作、学到了什么、质量标准是什么。
    Purpose: Call at the start of each new conversation to understand who the user is, how they work, what they have learned, and their quality bar.

    分层说明 / Tiered behaviour:
    - "quick": 仅身份画像 + 工作偏好（纯 JSON 读取，无文件扫描，最低延迟）。
      Profile + preferences only — pure JSON reads, no filesystem scans. Lowest latency.
    - "standard"（默认）: 加上质量标准、经验领域、相关教训/决策、项目快照。跳过昂贵的 reconcile。
      Default. Adds quality, domains, top lessons/decisions, project snapshot. Skips expensive reconciliation.
    - "full": 完整上下文，含冲突检测、过期/暂存提醒、自动同步副作用。仅在用户明确要求"全量回顾"时使用。
      Full context including conflict detection, stale/staging warnings, auto-sync side effects. Use only when the user explicitly asks for a comprehensive memory review.

    注意：默认 "standard" 已覆盖绝大多数冷启动需求；只有用户问"我们之前所有决定/经验"或要做记忆健康检查时才用 "full"。
    Note: "standard" covers most cold-start needs. Use "full" only when the user asks for a comprehensive memory review.

    Args:
        project_folder: 当前项目文件夹路径（可选）。 / Current project folder path (optional).
        level: "quick" | "standard" | "full"，默认 "standard"。 / Tier — defaults to "standard".
        token_budget: 上下文 token 预算（可选）。设定后按优先级裁剪 section，低优先级 section 先丢弃。不设则返回全量。
            Optional token budget. When set, sections are included by priority until budget is exhausted.
        user_prompt: 用户当前提问（可选）。传入后 Engram 可据此优化上下文相关性（未来增强）。
            Optional current user prompt. Passed through for future context-relevance optimization.
    """
    if project_folder:
        _session.detect_project(project_folder)
    try:
        context = _engram.generate_context(
            project_folder, level=level, max_tokens=token_budget,
        )
        _track("get_user_context", success=True)
        _beta("cold_start", level=level)
    except Exception as exc:
        _track("get_user_context", success=False)
        logger.warning("generate_context failed: %s", exc)
        return f"Engram 上下文加载失败: {_safe_err(exc)}"
    if not context:
        return (
            "Engram 为空——这是新用户。请帮助他们建立身份：\n"
            "1. 问用户的角色（开发者/PM/学生等）→ 调用 update_identity(field='profile', updates_json='{\"role\":\"...\"}')\n"
            "2. 问偏好的沟通语言 → update_identity(field='profile', updates_json='{\"language\":\"...\"}')\n"
            "3. 问技术栈 → update_identity(field='profile', updates_json='{\"tech_stack\":\"...\"}')\n"
            "4. 问有没有 AI 总是忘记的规则 → 调用 add_lesson(...)\n"
            "5. 完成后调用 refresh_quick_context() 持久化\n\n"
            "这只需要 30 秒，之后所有 AI 工具都能从第一条消息开始了解这位用户。\n"
            "或者建议用户在终端运行 `piia-engram` 完成引导式设置。"
        )
    if user_prompt:
        suffix = f"\n\n## 当前用户提问\n{user_prompt}"
        if token_budget is not None:
            # Rough token estimate: 1 token ≈ 3 chars for mixed CJK/English
            used = len(context) // 3
            suffix_cost = len(suffix) // 3
            if used + suffix_cost > token_budget:
                # Truncate prompt to fit remaining budget
                remaining = max(0, (token_budget - used) * 3 - len("\n\n## 当前用户提问\n"))
                if remaining > 20:
                    suffix = f"\n\n## 当前用户提问\n{user_prompt[:remaining]}…"
                else:
                    suffix = ""
        context += suffix
    return context


@mcp.tool()
async def refresh_quick_context(level: str = "standard") -> str:
    """刷新本地 `quick_context.md` 快照（跨工具 / 离线场景的快速通路）。 / Refresh the local quick_context.md snapshot (cross-tool / offline fast path).

    用途：把当前 Engram 状态固化为一份纯文本身份卡，写到 `~/.engram/quick_context.md`。任何 AI 工具（包括没接 Engram MCP 的）都可以直接 Read 这个文件作为冷启动上下文，无需 MCP 调用。
    Purpose: Persist the current Engram state as a plain-text identity card at `~/.engram/quick_context.md`. Any AI tool — even one without Engram MCP — can Read this file as cold-start context without an MCP round-trip.

    何时调用 / When to call:
    - 用户更新身份/偏好/质量标准后（让快照反映最新状态）
    - 添加重要的 lesson/decision 后
    - 第一次设置 Engram 时
    - 定期（例如每天一次）保持新鲜
    After identity/preference/quality updates, after significant lessons or decisions, on first setup, or on a periodic refresh.

    Args:
        level: 快照详细度 "quick" | "standard"(默认) | "full"。 / Snapshot tier — defaults to "standard".
    """
    try:
        path = _engram.refresh_quick_context(level=level)
        _track("refresh_quick_context", success=True)
        return f"已写入快照: {path} (level={level})"
    except Exception as exc:
        _track("refresh_quick_context", success=False)
        logger.warning("refresh_quick_context failed: %s", exc)
        return f"快照写入失败: {_safe_err(exc)}"


@mcp.tool()
async def get_identity_card() -> str:
    """导出用户的可携带 AI 身份卡（Markdown 格式）。 / Export the user's portable AI identity card as Markdown.

    用途：需要把用户身份、工作方式、质量标准、经验教训分享给其它 AI 工具时调用。
    Purpose: Call when another AI tool needs a self-contained summary of the user's identity, work style, quality standards, and lessons.

    注意：如果本会话只需要运行时上下文，用 get_user_context 更合适。
    Note: If the current session only needs runtime context, get_user_context is usually the better choice.
    """
    try:
        card = _engram.export_identity_card()
        _track("get_identity_card", success=True)
    except Exception as exc:
        _track("get_identity_card", success=False)
        logger.warning("export_identity_card failed: %s", exc)
        return f"身份卡生成失败: {_safe_err(exc)}"
    if not card:
        return "身份卡为空——尚未积累足够的知识。"
    return card


@mcp.tool()
async def get_profile(safe: bool = True) -> str:
    """获取用户身份画像。 / Get the user's identity profile.

    用途：需要读取角色、语言、技术水平、简介等用户画像字段时调用。
    Purpose: Call when you need profile fields such as role, language, technical level, or description.

    注意：默认遵守 trust_boundaries.restricted_fields 过滤敏感字段。设 safe=False 仅在用户明确要求时使用。
    Note: Respects trust_boundaries.restricted_fields by default. Set safe=False only when the user explicitly requests full profile access.

    Args:
        safe: 默认 True，按 trust_boundaries 过滤敏感字段。 / Default True; filters sensitive fields per trust_boundaries.
    """
    return _json(_engram.get_profile(safe=safe))


@mcp.tool()
async def get_work_style() -> str:
    """获取用户的工作偏好（工作模式、节奏、沟通风格）。 / Get the user's work style preferences: patterns, pace, and communication style.

    用途：需要单独读取旧版 work_style 偏好时调用。
    Purpose: Call when you specifically need the legacy work_style preference object.

    注意：新版偏好优先使用 get_preferences。
    Note: Prefer get_preferences for the newer preferences model.
    """
    return _json(_engram.get_work_style())


@mcp.tool()
async def get_preferences() -> str:
    """获取用户的工作偏好（v2.0，含工具偏好、工作模式、沟通风格）。 / Get the user's v2.0 preferences, including tool preferences, work patterns, and communication style.

    用途：需要读取用户如何协作、喜欢哪些工具、偏好什么工作方式时调用。
    Purpose: Call when you need to understand how the user collaborates, which tools they prefer, and how they like work to be done.

    注意：如果只需要完整冷启动上下文，用 get_user_context。
    Note: Use get_user_context when you need the full cold-start context rather than preferences alone.
    """
    return _json(_engram.get_preferences())


@mcp.tool()
async def get_trust_boundaries() -> str:
    """获取数据信任边界（哪些工具可以访问哪些 Engram 数据）。 / Get data trust boundaries that define which tools may access which Engram data.

    用途：需要判断敏感字段、共享边界或工具访问权限时调用。
    Purpose: Call when you need to inspect sensitive fields, sharing boundaries, or tool access permissions.

    注意：普通上下文读取会自动遵守安全画像逻辑；不要用本工具绕过隐私边界。
    Note: Normal context reads already respect safe-profile behavior; do not use this tool to bypass privacy boundaries.
    """
    return _json(_engram.get_trust_boundaries())


@mcp.tool()
async def get_quality_standards() -> str:
    """获取用户的质量标准和验收条件。 / Get the user's quality standards and acceptance criteria.

    用途：需要知道用户如何判断工作是否完成、测试和证据要求有多严格时调用。
    Purpose: Call when you need to know how the user judges completion, tests, evidence, and acceptance quality.

    注意：冷启动时 get_user_context 通常已经包含关键质量标准。
    Note: get_user_context usually includes the key quality standards during cold start.
    """
    return _json(_engram.get_quality_standards())


@mcp.tool()
async def get_lessons(
    domain: Optional[str] = None,
    source_tool: Optional[str] = None,
    limit: int = 50,
) -> str:
    """获取用户从过去项目中学到的经验教训。 / Get lessons the user learned from past projects.

    用途：用这些经验来避免重复过去的错误，可按领域、来源工具和数量过滤。
    Purpose: Call to avoid repeating past mistakes, optionally filtering by domain, source tool, and limit.

    注意：如果你只有项目路径、不知道关键词，用 get_relevant_knowledge 自动推荐。
    Note: If you only have a project path and no search keywords, use get_relevant_knowledge for automatic recommendations.

    Args:
        domain: 按领域过滤（如 'python'），支持多标签教训的包含匹配。 / Filter by domain, such as 'python'; supports contains matching for multi-label lessons.
        source_tool: 按来源工具过滤（如 'claude_code', 'codex'）。 / Filter by source tool, such as 'claude_code' or 'codex'.
        limit: 最多返回多少条（默认 50）。 / Maximum number of items to return (default 50).
    """
    lessons = _engram.get_lessons(domain=domain, source_tool=source_tool, limit=limit)
    if not lessons:
        return "尚无经验教训记录。"
    return _json(lessons)


@mcp.tool()
async def get_decisions(
    source_tool: Optional[str] = None,
    project: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = 30,
) -> str:
    """按时间列出用户做过的关键决策（不需要搜索词）。 / List the user's key decisions by time, without requiring a search query.

    用途：想浏览最近的决策记录，或按领域、项目、来源筛选时调用。
    Purpose: Call when browsing recent decisions or filtering decisions by domain, project, or source.

    注意：如果你有明确关键词想搜索决策内容，用 search_knowledge(scope="decisions") 更精准。
    Note: If you have explicit keywords for decision content, search_knowledge(scope="decisions") is more precise.

    Args:
        source_tool: 按来源工具过滤（如 'claude_code', 'codex'）。 / Filter by source tool, such as 'claude_code' or 'codex'.
        project: 按项目过滤（可选）。 / Filter by project (optional).
        domain: 按领域过滤（如 'architecture'），支持多标签决策的包含匹配。 / Filter by domain, such as 'architecture'; supports contains matching for multi-label decisions.
        limit: 最多返回多少条（默认 30）。 / Maximum number of items to return (default 30).
    """
    decisions = _engram.get_decisions(
        limit=limit,
        source_tool=source_tool,
        project=project,
        domain=domain,
    )
    if not decisions:
        return "尚无决策记录。"
    return _json(decisions)


@mcp.tool()
async def get_domains() -> str:
    """获取用户的技术领域经验图谱。 / Get the user's technical domain experience map.

    用途：查看用户在哪些技术、领域或主题上积累了经验。
    Purpose: Call to see which technologies, domains, or topics the user has experience in.

    注意：如果要读取某个领域里的具体经验，用 get_lessons(domain=...) 或 search_knowledge。
    Note: To read concrete knowledge within a domain, use get_lessons(domain=...) or search_knowledge.
    """
    domains = _engram.get_domains()
    if not domains:
        return "尚无领域经验记录。"
    return _json(domains)


@mcp.tool()
async def get_project_context(project_folder: str) -> str:
    """读取特定项目的知识快照（项目级，只含该项目的历史）。 / Read the knowledge snapshot for a specific project, containing only that project's history.

    用途：想了解某个项目之前的技术栈、已知问题、协作次数时调用。
    Purpose: Call when you need a project's previous tech stack, known issues, notes, or collaboration history.

    注意：如果想获取用户级完整身份上下文，用 get_user_context；如果想写入项目快照，用 save_project_snapshot。
    Note: Use get_user_context for full user-level context; use save_project_snapshot to write a project snapshot.

    Args:
        project_folder: 项目文件夹路径。 / Project folder path.
    """
    _session.detect_project(project_folder)
    try:
        snapshot = _engram.get_project_snapshot(project_folder)
        _track("get_project_context", success=True)
    except Exception as exc:
        _track("get_project_context", success=False)
        raise
    if not snapshot:
        return f"未找到项目知识记录: {project_folder}"
    return _json(snapshot)


@mcp.tool()
async def list_projects() -> str:
    """列出用户参与过的所有项目及基本信息。 / List all projects the user has worked on with basic metadata.

    用途：需要发现已有项目记录、确认项目路径或查看项目清单时调用。
    Purpose: Call when discovering saved project records, confirming project paths, or reviewing the project list.

    注意：读取单个项目详情用 get_project_context。
    Note: Use get_project_context to read details for one project.
    """
    projects = _engram.list_projects()
    if not projects:
        return "尚无项目记录。"
    return _json(projects)


@mcp.tool()
async def get_relevant_knowledge(project_folder: str, limit: int = 8) -> str:
    """按项目路径自动推荐最相关的经验教训（无需搜索词）。 / Automatically recommend the most relevant lessons for a project path, without search keywords.

    **Lifecycle: retrieval** — 在对话中需要项目相关的历史知识时调用。
    Lifecycle: retrieval — call mid-conversation when project-relevant past knowledge is needed.

    用途：你知道当前项目路径但不知道该搜什么词时调用，Engram 根据项目技术栈自动筛选。
    Purpose: Call when you know the current project path but not the right search terms; Engram filters by project tech stack.

    注意：如果用户给了明确搜索词，用 search_knowledge 更直接。
    Note: If the user provides explicit search keywords, search_knowledge is more direct.

    Args:
        project_folder: 当前项目文件夹路径。 / Current project folder path.
        limit: 最多返回多少条（默认 8）。 / Maximum number of items to return (default 8).
    """
    try:
        lessons = _engram.get_relevant_lessons(
            project_folder=project_folder, limit=limit
        )
        _track("get_relevant_knowledge", success=True)
    except Exception as exc:
        _track("get_relevant_knowledge", success=False)
        raise
    if not lessons:
        return "尚无相关经验教训。"
    return _json(lessons)


@mcp.tool()
async def get_knowledge_inheritance(description: str, limit: int = 10) -> str:
    """为新项目或任务生成可继承知识包。 / Build a knowledge inheritance pack for a new project or task.

    用途：根据自由文本描述，从现有 lessons 和 decisions 中找出最相关的可复用知识。
    Purpose: Call to rank existing lessons and decisions against a free-text description and return reusable knowledge.

    注意：本工具不需要已保存的项目快照；如果要同时创建项目档案，用 start_project。
    Note: This tool does not require a saved project snapshot; use start_project if you also want to create a project record.

    Args:
        description: 新项目或任务的自由文本描述。 / Free-text description of the new project or task.
        limit: 最多返回多少条（默认 10，上限 20）。 / Maximum number of items to return in total (default 10, max 20).
    """
    limit = min(int(limit), 20)
    return _json(_engram.get_knowledge_inheritance(description, limit=limit))


@mcp.tool()
async def search_knowledge(query: str, scope: str = "all", limit: int = 10,
                           filters_json: str = "") -> str:
    r"""搜索知识库（lessons/decisions/playbooks）。 / Search lessons, decisions, and playbooks by keyword.

    **Lifecycle: retrieval** — 在对话中需要检索历史知识时调用。
    Lifecycle: retrieval — call during conversation when past knowledge is needed.

    Call when the user asks to find knowledge about a specific topic,
    or recalls a procedure ('X how to' / 'X steps').

    If you only have a project path and no query, use get_relevant_knowledge;
    if you have an existing knowledge ID, use find_similar_knowledge.

    Args:
        query: Search query keywords.
        scope: Search scope: 'all', 'lessons', 'decisions', or 'playbooks'.
        limit: Maximum number of items to return (default 10).
        filters_json: Optional JSON string with filter criteria. Supported keys:
            - "domain": str — only items whose domain contains this value
            - "tier": str — only items matching this tier ('staging' or 'verified')
            - "date_after": str — ISO date string, only items created after this date
            Example: '{"tier": "verified", "domain": "python"}'
    """
    filters = None
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except (json.JSONDecodeError, TypeError):
            return "filters_json 格式错误，应为 JSON 字符串"
        if not isinstance(filters, dict):
            return "filters_json 应为 JSON 对象（{}）"
        _allowed_keys = {"domain", "tier", "date_after"}
        for k, v in filters.items():
            if k not in _allowed_keys:
                return f"filters 不支持的键: {k}。可用: {', '.join(sorted(_allowed_keys))}"
            if not isinstance(v, str):
                return f"filters['{k}'] 应为字符串"
        if "tier" in filters and filters["tier"] not in ("staging", "verified"):
            return "filters['tier'] 仅支持 'staging' 或 'verified'"
    try:
        result = _engram.search_knowledge(query, scope=scope, limit=limit, filters=filters)
        _track("search_knowledge", success=True)
    except Exception as exc:
        _track("search_knowledge", success=False)
        return f"搜索失败: {_safe_err(exc)}"
    return _json(result)


@mcp.tool()
async def get_knowledge_overview(section: str = "all", stale_days: int = 30) -> str:
    """获取统一的知识概览：摘要、健康报告和过期知识。 / Get a unified knowledge overview: digest, health report, and stale items.

    用途：需要快速了解知识库整体状态、健康度或待复查条目时调用。
    Purpose: Call when you need a quick view of knowledge-base status, health, or items needing review.

    注意：如果只想列出过期条目，用 get_stale_knowledge 更直接。
    Note: If you only want stale items, get_stale_knowledge is more direct.

    Args:
        section: 概览部分：'all'、'digest'、'health' 或 'stale'。 / Overview section: 'all', 'digest', 'health', or 'stale'.
        stale_days: 超过多少天算过期知识。 / Number of days after which knowledge is considered stale.
    """
    return _json(_engram.get_knowledge_overview(section, stale_days=stale_days))


@mcp.tool()
async def suggest_merges(threshold: float = 0.45, limit: int = 10) -> str:
    """扫描全库，推荐可合并的相似/重复知识条目。 / Scan all knowledge and recommend similar or duplicate items that can be merged.

    用途：定期维护时调用，一次性发现所有值得合并的近似条目，附带可直接执行的 merge 命令。
    Purpose: Call during periodic maintenance to discover all near-duplicate items with actionable merge commands.

    注意：如果已知某条的 ID 想查相似项，用 find_similar_knowledge 更直接；本工具是全库扫描。
    Note: If you already have an item ID, find_similar_knowledge is more direct; this tool scans the entire knowledge base.

    Args:
        threshold: 相似度阈值（0.2–1.0，默认 0.45）。 / Similarity threshold (0.2–1.0, default 0.45).
        limit: 最多返回多少组建议（默认 10，上限 30）。 / Maximum number of suggestions to return (default 10, max 30).
    """
    return _json(_engram.suggest_merges(threshold=threshold, limit=limit))


@mcp.tool()
async def get_related_knowledge(item_id: str) -> str:
    """获取与某条 lesson 或 decision 相连的所有知识。 / Get all knowledge items linked to a given lesson or decision.

    用途：已知一个知识 ID，想沿着知识关系图查看相关经验和决策时调用。
    Purpose: Call when you have a knowledge ID and want to follow the knowledge graph to related lessons and decisions.

    注意：如果想找内容相似但尚未显式关联的条目，用 find_similar_knowledge。
    Note: Use find_similar_knowledge to find similar items that are not explicitly linked.

    Args:
        item_id: lesson 或 decision 的 ID。 / ID of a lesson or decision.
    """
    return _json(_engram.get_related_knowledge(item_id))


@mcp.tool()
async def find_similar_knowledge(item_id: str, limit: int = 5) -> str:
    """根据已有知识条目 ID 查找内容相似的条目。 / Find content-similar knowledge items from an existing knowledge item ID.

    用途：你已经有一条 lesson 或 decision 的 ID，想看有没有类似或重复的条目。
    Purpose: Call when you already have a lesson or decision ID and want to find similar or duplicate items.

    注意：如果你没有 ID、只有关键词，用 search_knowledge。
    Note: If you do not have an ID and only have keywords, use search_knowledge.

    Args:
        item_id: 已有 lesson 或 decision 的 ID。 / ID of the existing lesson or decision.
        limit: 最多返回多少条相似项（默认 5）。 / Maximum number of similar items to return (default 5).
    """
    return _json(_engram.find_similar_knowledge(item_id, limit=limit))


@mcp.tool()
async def export_knowledge_report() -> str:
    """导出完整 Markdown 知识报告并返回内容。 / Export a full Markdown knowledge report and return its content.

    用途：需要把当前知识库整理成人可读报告，用于审阅、归档或分享时调用。
    Purpose: Call when the knowledge base should be rendered into a readable report for review, archiving, or sharing.

    注意：报告会保存到 ~/.engram/exports/，同时返回正文内容。
    Note: The report is saved under ~/.engram/exports/ and the content is returned as well.
    """
    return _engram.export_knowledge_report()


# ===========================================================================
# WRITE TOOLS (18)
# ===========================================================================


@mcp.tool()
async def memory_store(
    kind: str,
    content_json: str,
    source_tool: str = "",
) -> str:
    """统一知识写入入口 — 根据 kind 自动路由到 add_lesson / add_decision / add_playbook。
    Unified knowledge write endpoint — routes to add_lesson / add_decision / add_playbook based on kind.

    **Lifecycle: writeback** — 对话中产生值得长期保留的知识时调用。
    Lifecycle: writeback — call when the conversation produces knowledge worth persisting.

    这是 Provider 兼容的统一写入接口。如果你已经明确知道要写 lesson/decision/playbook，
    也可以直接调用对应的专用工具。本工具的优势在于：调用方不需要知道 Engram 内部的分类体系。
    This is a provider-compatible unified write interface. You may also call the specialized
    tools directly. The advantage here: callers don't need to know Engram's internal taxonomy.

    Args:
        kind: 知识类型 — 'lesson' | 'decision' | 'playbook'。 / Knowledge type.
        content_json: 知识内容 JSON 字符串。格式因 kind 而异：
            - lesson: {"summary": "...", "detail": "...", "domain": "..."}
            - decision: {"question": "...", "choice": "...", "reasoning": "..."}
            - playbook: {"title": "...", "triggers": "...", "steps_json": "[...]"}
            Content JSON string. Schema varies by kind (see above).
        source_tool: 调用来源工具（可选），如 'claude_code', 'cursor'。 / Source tool (optional).
    """
    try:
        content = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return "content_json 格式错误，应为 JSON 字符串"

    if not isinstance(content, dict):
        return "content_json 应为 JSON 对象（{}），不能是数组或标量"

    if source_tool:
        content["source_tool"] = source_tool

    kind = kind.strip().lower()

    # Schema validation per kind
    if kind == "lesson":
        if not content.get("summary", "").strip():
            return "lesson 必须包含非空的 summary 字段"
    elif kind == "decision":
        q = content.get("question", "") or content.get("title", "")
        if not q.strip() or not content.get("choice", "").strip():
            return "decision 必须包含非空的 question（或 title）和 choice 字段"
    elif kind == "playbook":
        if not content.get("title", "").strip():
            return "playbook 必须包含非空的 title 字段"
    elif kind:
        _track("memory_store", success=False)
        return f"不支持的 kind: {kind}。可用: lesson, decision, playbook"
    else:
        _track("memory_store", success=False)
        return "kind 不能为空。可用: lesson, decision, playbook"

    try:
        if kind == "lesson":
            result = _engram.add_lesson(content)
            label = content.get("summary", "")[:60]
            _track("memory_store", success=True)
            if result.get("status") == "duplicate":
                return _json(result)
            return f"教训已记录: {label}"
        elif kind == "decision":
            result = _engram.add_decision(content)
            label = f"{content.get('question', '')} → {content.get('choice', '')}"[:60]
            _track("memory_store", success=True)
            if result.get("status") == "duplicate":
                return _json(result)
            return f"决策已记录: {label}"
        else:  # playbook
            result = _engram.add_playbook(content)
            label = content.get("title", "")[:60]
            _track("memory_store", success=True)
            return f"Playbook 已记录: {label}"
    except Exception as exc:
        _track("memory_store", success=False)
        return f"memory_store 失败: {_safe_err(exc)}"


@mcp.tool()
async def add_lesson(
    summary: str,
    detail: str = "",
    domain: str = "",
    source_tool: str = "",
    source_url: str = "",
) -> str:
    """记录单条经验教训（你已经知道要记什么）。 / Record one lesson learned when you already know what to save.

    **Lifecycle: writeback** — 对话中学到可复用的经验时调用。
    Lifecycle: writeback — call when reusable experience is learned during conversation.

    用途：用户明确说出一条踩坑经验或技术发现时调用。
    Purpose: Call when the user explicitly states a lesson, pitfall, or technical finding.

    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。
    Note: If the user gives a session summary for automatic extraction, use extract_session_insights instead.

    Args:
        summary: 教训的一行摘要。 / One-line lesson summary.
        detail: 详细说明（可选）。 / Detailed explanation (optional).
        domain: 技术领域（可选），可填多个，逗号分隔，如 'python,testing'。 / Technical domain (optional); may contain multiple comma-separated labels such as 'python,testing'.
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。 / Source tool, such as 'claude_code' or 'codex' (optional but recommended).
        source_url: 如果教训来自外部内容，填写来源 URL（可选）。 / Source URL when the lesson comes from external content (optional).
    """
    lesson = {"summary": summary}
    if detail:
        lesson["detail"] = detail
    if domain:
        lesson["domain"] = domain
    if source_tool:
        lesson["source_tool"] = source_tool
    if source_url:
        lesson["source_url"] = source_url
    try:
        result = _engram.add_lesson(lesson)
        _track("add_lesson", success=True)
        _beta("knowledge_created", kind="lesson",
              domain=domain[:80] if domain else "",
              source_tool=source_tool[:40] if source_tool else "",
              tier=result.get("tier", "staging") if isinstance(result, dict) else "staging")
    except Exception as exc:
        _track("add_lesson", success=False)
        return f"添加教训失败: {_safe_err(exc)}"
    if result.get("status") == "duplicate":
        return _json(result)
    return f"教训已记录: {summary}"


@mcp.tool()
async def add_decision(
    question: str,
    choice: str,
    reasoning: str = "",
    source_tool: str = "",
    project: str = "",
    domain: str = "",
) -> str:
    """记录单条关键决策（用户明确选了某个方案）。 / Record one key decision when the user explicitly chose an option.

    **Lifecycle: writeback** — 对话中做出明确决策时调用。
    Lifecycle: writeback — call when an explicit decision is made during conversation.

    用途：用户说"我们决定用 X"或"以后都用 Y"时调用。
    Purpose: Call when the user says they decided to use X or will use Y going forward.

    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。
    Note: If the user gives a session summary for automatic extraction, use extract_session_insights instead.

    Args:
        question: 决策的问题，如"数据库选型"。 / Decision question, such as 'database choice'.
        choice: 做出的选择，如"PostgreSQL"。 / Chosen option, such as 'PostgreSQL'.
        reasoning: 选择的理由（可选）。 / Reasoning for the choice (optional).
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。 / Source tool, such as 'claude_code' or 'codex' (optional but recommended).
        project: 关联项目（可选）。 / Related project (optional).
        domain: 技术领域（可选），可填多个，逗号分隔，如 'architecture,database'。 / Technical domain (optional); may contain multiple comma-separated labels such as 'architecture,database'.
    """
    decision = {"question": question, "choice": choice}
    if reasoning:
        decision["reasoning"] = reasoning
    if source_tool:
        decision["source_tool"] = source_tool
    if project:
        decision["project"] = project
    if domain:
        decision["domain"] = domain
    try:
        result = _engram.add_decision(decision)
        _track("add_decision", success=True)
        _beta("knowledge_created", kind="decision",
              domain=domain[:80] if domain else "",
              source_tool=source_tool[:40] if source_tool else "",
              tier=result.get("tier", "staging") if isinstance(result, dict) else "staging")
    except Exception as exc:
        _track("add_decision", success=False)
        return f"添加决策失败: {_safe_err(exc)}"
    if result.get("status") == "duplicate":
        return _json(result)
    return f"决策已记录: {question} → {choice}"


@mcp.tool()
async def add_playbook(
    title: str,
    triggers: str,
    steps_json: str = "[]",
    description: str = "",
    domain: str = "",
    preconditions: str = "",
    pitfalls: str = "",
    outcome: str = "",
    source_tool: str = "",
) -> str:
    """记录操作手册（Playbook）— 结构化的多步骤流程。 / Record an operational playbook — a structured multi-step procedure.

    用途：完成一个多步骤操作流程后（如发布到 Registry、上架应用等），将步骤和经验记录为 Playbook，
    方便日后调取复用，避免重复摸索。
    Purpose: After completing a multi-step operational process (publishing to a registry, app deployment, etc.),
    record the steps as a Playbook for future retrieval.

    每条 Playbook 独立存储为单个文件，通过 triggers（记忆点关键词）快速调取。
    Each Playbook is stored as an individual file, quickly retrievable via trigger keywords.

    Args:
        title: 流程名称，如 'MCP Registry 发布流程'。 / Playbook name, e.g., 'MCP Registry publish workflow'.
        triggers: 记忆点关键词，逗号分隔，如 '发布,registry,上架'。 / Trigger keywords (comma-separated) for quick retrieval.
        steps_json: 步骤 JSON 数组，每个元素含 order/action/detail。 / Steps as a JSON array, each with order/action/detail.
        description: 流程概述（可选）。 / Brief description (optional).
        domain: 技术领域，逗号分隔（可选）。 / Domain labels, comma-separated (optional).
        preconditions: 前提条件，逗号分隔（可选）。 / Preconditions, comma-separated (optional).
        pitfalls: 常见陷阱，逗号分隔（可选）。 / Common pitfalls, comma-separated (optional).
        outcome: 预期结果（可选）。 / Expected outcome (optional).
        source_tool: 来源工具（可选）。 / Source tool (optional).
    """
    playbook: dict = {"title": title}
    playbook["triggers"] = [t.strip() for t in triggers.split(",") if t.strip()]
    try:
        steps = json.loads(steps_json)
        if isinstance(steps, list):
            playbook["steps"] = steps
    except json.JSONDecodeError:
        return "steps_json 格式错误，需要有效的 JSON 数组"
    if description:
        playbook["description"] = description
    if domain:
        playbook["domain"] = domain
    if preconditions:
        playbook["preconditions"] = [p.strip() for p in preconditions.split(",") if p.strip()]
    if pitfalls:
        playbook["pitfalls"] = [p.strip() for p in pitfalls.split(",") if p.strip()]
    if outcome:
        playbook["outcome"] = outcome
    if source_tool:
        playbook["source_tool"] = source_tool
    try:
        result = _engram.add_playbook(playbook)
        _track("add_playbook", success=True)
    except Exception as exc:
        _track("add_playbook", success=False)
        return f"添加 Playbook 失败: {_safe_err(exc)}"
    if result.get("status") == "duplicate":
        return _json(result)
    if result.get("error"):
        return _json(result)
    return f"Playbook 已记录: {title} (triggers: {triggers})"


@mcp.tool()
async def get_playbooks(domain: str = "", limit: int = 20) -> str:
    """列出已保存的操作手册（Playbooks）。 / List saved operational playbooks.

    用途：查看有哪些已记录的操作流程，可按领域筛选。
    Purpose: Browse recorded operational procedures, optionally filtered by domain.

    Args:
        domain: 按领域筛选（可选）。 / Filter by domain (optional).
        limit: 返回条数上限（默认 20）。 / Maximum items to return (default 20).
    """
    try:
        result = _engram.get_playbooks(domain=domain or None, limit=limit)
        _track("get_playbooks", success=True)
    except Exception as exc:
        _track("get_playbooks", success=False)
        return f"获取 Playbooks 失败: {_safe_err(exc)}"
    if not result:
        return "尚无已保存的 Playbook。"
    return _json(result)


@mcp.tool()
async def get_playbook(playbook_id: str) -> str:
    """获取单条 Playbook 的完整内容。 / Get the full content of a single Playbook by ID.

    用途：根据 ID 调取某条操作手册的详细步骤。
    Purpose: Retrieve detailed steps for a specific operational playbook by its ID.

    Args:
        playbook_id: Playbook ID。 / The Playbook ID.
    """
    try:
        result = _engram.get_playbook(playbook_id)
        _track("get_playbook", success=True)
    except Exception as exc:
        _track("get_playbook", success=False)
        return f"获取 Playbook 失败: {_safe_err(exc)}"
    if result.get("error"):
        return _json(result)
    return _json(result)


@mcp.tool()
async def get_recent_playbooks(limit: int = 5) -> str:
    """获取最近使用过的 Playbook（按 last_reviewed 倒序）。 / Get recently used Playbooks sorted by last_reviewed descending.

    用途：冷启动或会话开始时，主动浮现用户最近用过的操作流程，方便快速复用。
    Purpose: Surface recently used playbooks at session start for quick reuse.

    Args:
        limit: 返回条数上限（默认 5）。 / Maximum items to return (default 5).
    """
    try:
        result = _engram.get_recent_playbooks(limit=limit)
        _track("get_recent_playbooks", success=True)
    except Exception as exc:
        _track("get_recent_playbooks", success=False)
        return f"获取近期 Playbook 失败: {_safe_err(exc)}"
    if not result:
        return "尚无最近使用的 Playbook。 / No recently used Playbooks."
    return _json(result)


@mcp.tool()
async def update_playbook(
    playbook_id: str,
    title: str = "",
    triggers: str = "",
    steps_json: str = "",
    description: str = "",
    domain: str = "",
    preconditions: str = "",
    pitfalls: str = "",
    outcome: str = "",
    status: str = "",
) -> str:
    """更新已有 Playbook 的字段。 / Update fields on an existing Playbook.

    用途：修正或补充已记录的操作手册内容，如添加新步骤、更新触发词、修改描述等。
    Purpose: Correct or enrich an existing playbook — add steps, update triggers, fix descriptions, etc.

    只传入需要更新的字段，未传入的字段保持不变。版本号自动递增。
    Only pass the fields you want to change; omitted fields stay unchanged. Version auto-increments.

    Args:
        playbook_id: 要更新的 Playbook ID。 / ID of the Playbook to update.
        title: 新标题（可选）。 / New title (optional).
        triggers: 新触发词，逗号分隔（可选）。 / New trigger keywords, comma-separated (optional).
        steps_json: 新步骤 JSON 数组（可选）。 / New steps as a JSON array (optional).
        description: 新描述（可选）。 / New description (optional).
        domain: 新领域（可选）。 / New domain (optional).
        preconditions: 新前提条件，逗号分隔（可选）。 / New preconditions, comma-separated (optional).
        pitfalls: 新陷阱，逗号分隔（可选）。 / New pitfalls, comma-separated (optional).
        outcome: 新预期结果（可选）。 / New expected outcome (optional).
        status: 新状态，如 active/outdated/staging（可选）。 / New status, e.g., active/outdated/staging (optional).
    """
    updates: dict = {}
    if title:
        updates["title"] = title
    if triggers:
        updates["triggers"] = [t.strip() for t in triggers.split(",") if t.strip()]
    if steps_json:
        try:
            steps = json.loads(steps_json)
            if isinstance(steps, list):
                updates["steps"] = steps
        except json.JSONDecodeError:
            return "steps_json 格式错误，需要有效的 JSON 数组"
    if description:
        updates["description"] = description
    if domain:
        updates["domain"] = domain
    if preconditions:
        updates["preconditions"] = [p.strip() for p in preconditions.split(",") if p.strip()]
    if pitfalls:
        updates["pitfalls"] = [p.strip() for p in pitfalls.split(",") if p.strip()]
    if outcome:
        updates["outcome"] = outcome
    if status:
        updates["status"] = status
    if not updates:
        return "未提供任何更新字段。 / No update fields provided."
    try:
        result = _engram.update_playbook(playbook_id, updates)
        _track("update_playbook", success=True)
    except Exception as exc:
        _track("update_playbook", success=False)
        return f"更新 Playbook 失败: {_safe_err(exc)}"
    if result.get("error"):
        return _json(result)
    return f"Playbook 已更新: {result.get('title', playbook_id)} (v{result.get('version', '?')})"


@mcp.tool()
async def prepare_playbook_execution(
    playbook_id: str,
    params_json: str = "{}",
) -> str:
    """准备 Playbook 引导执行计划（参数替换 + 逐步状态跟踪）。 / Prepare a Playbook execution plan with parameter substitution and per-step tracking.

    用途："按上次流程来" — 调取已有 Playbook，替换参数后返回可执行计划。AI 逐步确认执行，不自动运行。
    Purpose: "Follow the previous procedure" — fetch a Playbook, substitute parameters, return an executable plan. AI confirms each step; no auto-execution.

    Args:
        playbook_id: Playbook ID。 / The Playbook ID.
        params_json: 参数 JSON 对象，键值对替换步骤中的 ${variable}（可选）。 / Parameters JSON object for ${variable} substitution (optional).
    """
    params = {}
    if params_json and params_json != "{}":
        try:
            parsed = json.loads(params_json)
            if isinstance(parsed, dict):
                params = parsed
        except json.JSONDecodeError:
            return "params_json 格式错误，需要有效的 JSON 对象"
    try:
        result = _engram.prepare_playbook_execution(playbook_id, params=params)
        _track("prepare_playbook_execution", success=True)
    except Exception as exc:
        _track("prepare_playbook_execution", success=False)
        return f"准备执行计划失败: {_safe_err(exc)}"
    if result.get("error"):
        return _json(result)
    return _json(result)


@mcp.tool()
async def update_execution_step(
    playbook_id: str,
    step_order: int,
    status: str,
    notes: str = "",
) -> str:
    """更新执行计划中某一步的状态。 / Update the status of a step in an execution plan.

    在 prepare_playbook_execution 之后逐步调用，标记每一步的完成情况。
    Call step-by-step after prepare_playbook_execution to track progress.

    Args:
        playbook_id: Playbook ID。
        step_order: 步骤序号（order 字段）。 / Step order number.
        status: "completed" | "skipped" | "failed"
        notes: 可选备注（如失败原因）。 / Optional note (e.g. failure reason).
    """
    try:
        result = _engram.update_execution_step(playbook_id, step_order, status, notes)
        _track("update_execution_step", success=True)
    except Exception as exc:
        _track("update_execution_step", success=False)
        return f"更新步骤状态失败: {_safe_err(exc)}"
    if result.get("error"):
        return _json(result)
    return _json(result)


@mcp.tool()
async def get_execution_status(playbook_id: str) -> str:
    """查看 Playbook 的当前执行进度。 / Get current execution progress of a Playbook.

    返回每一步的状态和整体完成度。
    Returns step-by-step status and overall completion.

    Args:
        playbook_id: Playbook ID。
    """
    try:
        result = _engram.get_execution_status(playbook_id)
        _track("get_execution_status", success=True)
    except Exception as exc:
        _track("get_execution_status", success=False)
        return f"查询执行状态失败: {_safe_err(exc)}"
    return _json(result)


@mcp.tool()
async def archive_playbook(playbook_id: str) -> str:
    """归档 Playbook（标记为过时但不删除）。 / Archive a Playbook (mark as outdated without deleting).

    用途：当某个操作流程已不再使用或有新版本替代时，将其归档。
    Purpose: When a procedure is no longer in use or has been superseded, archive it.

    Args:
        playbook_id: 要归档的 Playbook ID。 / ID of the Playbook to archive.
    """
    try:
        result = _engram.archive_playbook(playbook_id)
        _track("archive_playbook", success=True)
    except Exception as exc:
        _track("archive_playbook", success=False)
        return f"归档 Playbook 失败: {_safe_err(exc)}"
    if result.get("error"):
        return _json(result)
    return f"Playbook 已归档: {result.get('title', playbook_id)}"


@mcp.tool()
async def register_tool(
    name: str,
    path: str = "",
    category: str = "other",
    version: str = "",
    purpose: str = "",
    install_method: str = "",
    notes: str = "",
    source_tool: str = "",
) -> str:
    """注册本地工具/程序到环境图谱（已存在则更新）。 / Register a local tool or program in the environment registry; updates if it already exists.

    用途：安装、发现或确认某个工具/程序/运行时的位置和版本后调用，让所有 AI 工具都能快速查到。
    Purpose: Call after installing, discovering, or confirming a tool's location and version, so all AI tools can find it.

    写入时机 / When to call:
    - 安装新工具后（pip install, npm install -g, 手动下载等）
    - 发现系统上已有工具的准确路径后
    - 工具版本升级后
    - 发现某些路径不能用时（如 Windows Store stub）更新 notes 警告

    Args:
        name: 工具名称（如 'Python', 'gh', 'wrangler'）。 / Tool name, e.g., 'Python', 'gh', 'wrangler'.
        path: 可执行文件或配置文件的完整路径。 / Full path to executable or config file.
        category: 分类：runtime, cli, library, credential, config, service, other。 / Category.
        version: 版本号。 / Version string.
        purpose: 用途简述。 / Brief description of what this tool is for.
        install_method: 安装方式（pip, npm, manual, system 等）。 / How it was installed.
        notes: 备注（注意事项、陷阱、替代方案等）。 / Notes, caveats, alternatives.
        source_tool: 哪个 AI 工具登记的（如 'claude_code', 'codex'）。 / Which AI tool registered this.
    """
    tool_entry: dict = {"name": name}
    if path:
        tool_entry["path"] = path
    if category:
        tool_entry["category"] = category
    if version:
        tool_entry["version"] = version
    if purpose:
        tool_entry["purpose"] = purpose
    if install_method:
        tool_entry["install_method"] = install_method
    if notes:
        tool_entry["notes"] = notes
    try:
        result = _engram.register_tool(tool_entry, registered_by=source_tool)
        _track("register_tool", success=True)
    except Exception as exc:
        _track("register_tool", success=False)
        return f"注册工具失败: {_safe_err(exc)}"
    action = result.pop("_action", "registered")
    action_zh = "已更新" if action == "updated" else "已注册"
    return f"工具{action_zh}: {name}" + (f" ({path})" if path else "")


@mcp.tool()
async def find_tool(query: str) -> str:
    """搜索已注册的本地工具/程序。 / Search for registered local tools and programs.

    用途：需要查找某个工具的路径、版本或安装方式时调用。避免重复搜索或重新安装已有工具。
    Purpose: Call when you need a tool's path, version, or install method. Prevents re-searching or re-installing.

    Args:
        query: 搜索关键词（名称、分类、用途均可匹配）。 / Search keywords matching name, category, purpose, or path.
    """
    try:
        results = _engram.find_tool(query)
        _track("find_tool", success=True)
    except Exception as exc:
        _track("find_tool", success=False)
        return f"搜索工具失败: {_safe_err(exc)}"
    if not results:
        return f"未找到匹配 '{query}' 的工具。"
    return _json(results)


@mcp.tool()
async def list_tools(category: str = "") -> str:
    """列出所有已注册的本地工具/程序。 / List all registered local tools and programs.

    用途：查看当前环境中所有已知的工具、运行时和程序。
    Purpose: View all known tools, runtimes, and programs in the current environment.

    Args:
        category: 按分类筛选（runtime, cli, library, credential, config, service, other），留空列出全部。 / Filter by category; empty lists all.
    """
    try:
        results = _engram.list_tools(category=category or None)
        _track("list_tools", success=True)
    except Exception as exc:
        _track("list_tools", success=False)
        return f"列出工具失败: {_safe_err(exc)}"
    if not results:
        return "尚无已注册的工具。"
    return _json(results)


@mcp.tool()
async def bulk_add_knowledge(items_json: str, item_type: str = "lesson", source_tool: str = "") -> str:
    """批量记录多条 lessons 或 decisions。 / Batch-add multiple lessons or decisions in one call.

    用途：已有结构化条目列表，需要一次性导入多条经验或决策时调用。
    Purpose: Call when you already have a structured list of items and want to import many lessons or decisions at once.

    注意：如果输入是自由文本笔记而不是 JSON 数组，用 ingest_notes 或 extract_session_insights。
    Note: If the input is free-form notes rather than a JSON array, use ingest_notes or extract_session_insights.

    Args:
        items_json: 条目 JSON 数组。 / JSON array of items.
        item_type: 条目类型：'lesson' 或 'decision'。 / Item type: 'lesson' or 'decision'.
        source_tool: 记录来源工具。 / Recording source tool.
    """
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError:
        return _json({"error": "items_json must be a valid JSON array"})
    if not isinstance(items, list):
        return _json({"error": "items_json must be a JSON array"})
    return _json(_engram.bulk_add_knowledge(items, item_type=item_type, source_tool=source_tool))


@mcp.tool()
async def ingest_notes(text: str, source_tool: str = "", domain: str = "") -> str:
    """从自由文本笔记中提取经验教训和关键决策并写入知识库。 / Extract lessons and key decisions from free-form notes and save them to the knowledge base.

    用途：用户贴了一段笔记，希望 Engram 尝试解析其中的 lessons 和 decisions 时调用。
    Purpose: Call when the user pastes notes and wants Engram to parse possible lessons and decisions from them.

    注意：如果是会话结束摘要，extract_session_insights 更贴近场景；如果已经明确一条 lesson 或 decision，用 add_lesson 或 add_decision。
    Note: For an end-of-session summary, extract_session_insights fits better; for one explicit lesson or decision, use add_lesson or add_decision.

    Args:
        text: 多行自由文本笔记。 / Multi-line free-form notes.
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。 / Source tool, such as 'claude_code' or 'codex' (optional but recommended).
        domain: 默认领域（可填多个，逗号分隔），未命中关键词推断时使用。 / Default domain, optionally comma-separated; used when keyword inference does not find a domain.
    """
    return _json(_engram.ingest_notes(text, source_tool=source_tool, domain=domain))


@mcp.tool()
async def extract_session_insights(summary: str, source_tool: str = "") -> str:
    """从会话摘要中批量自动提取经验教训和决策（你不需要自己分类）。 / Automatically extract lessons and decisions from a session summary without manually classifying them.

    **Lifecycle: writeback (auto)** — 自动提取的知识默认进入 staging 层，需要 review 后才升级为 verified。
    Lifecycle: writeback (auto) — auto-extracted knowledge defaults to staging tier and requires review before promotion to verified.

    用途：会话结束时，把一段自由文本摘要交给 Engram，它会自动解析出 lessons 和 decisions 并存入知识库。
    Purpose: Call at the end of a session with a free-text summary so Engram can parse and store lessons and decisions.

    注意：如果你已经明确知道要记一条 lesson 或 decision，直接用 add_lesson 或 add_decision 更精准；本工具适合不确定里面有什么值得记的场景。
    Note: If you already know one exact lesson or decision to save, add_lesson or add_decision is more precise; this tool fits summaries where the useful knowledge is not yet classified.

    Args:
        summary: 自由文本会话摘要，段落或要点列表均可。 / Free-text session summary; paragraphs or bullet lists both work.
        source_tool: 调用来源工具，如 'claude_code', 'codex'。 / Calling source tool, such as 'claude_code' or 'codex'.
    """
    return _json(_engram.extract_session_insights(summary, source_tool=source_tool))


@mcp.tool()
async def update_knowledge(item_id: str, updates_json: str) -> str:
    """按 ID 更新 lesson 或 decision（自动识别类型）。 / Update a lesson or decision by ID, automatically detecting the item type.

    用途：需要修改已有知识条目的内容、状态或元数据时调用。
    Purpose: Call when an existing knowledge item's content, status, or metadata needs to be changed.

    注意：如果只是确认某条知识仍有效，用 review_knowledge；如果要归档，用 archive_knowledge。
    Note: If you only need to confirm an item is still valid, use review_knowledge; to archive it, use archive_knowledge.

    Args:
        item_id: lesson 或 decision 的 ID。 / ID of the lesson or decision.
        updates_json: 要更新字段的 JSON 字符串。 / JSON string containing fields to update.
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return _json({"error": "updates_json must be valid JSON"})
    return _json(_engram.update_knowledge(item_id, updates))


@mcp.tool()
async def archive_knowledge(item_id: str) -> str:
    """按 ID 归档 lesson 或 decision（自动识别类型）。 / Archive a lesson or decision by ID, automatically detecting the item type.

    用途：某条知识已经过时但不应删除时调用。
    Purpose: Call when a knowledge item is outdated but should be preserved rather than deleted.

    注意：如果只是内容重复需要合并，用 merge_knowledge。
    Note: If the item is a duplicate that should be merged, use merge_knowledge.

    Args:
        item_id: 要归档的 lesson 或 decision ID。 / ID of the lesson or decision to archive.
    """
    result = _engram.archive_knowledge(item_id)
    _beta("knowledge_rejected", action="archive")
    return _json(result)


@mcp.tool()
async def review_knowledge(knowledge_id: str) -> str:
    """标记一条知识为"已复习"（刷新 last_reviewed 时间戳，不改内容）。 / Mark one knowledge item as reviewed, refreshing last_reviewed without changing content.

    用途：用户确认某条经验或决策仍然有效时调用，防止它被标记为过期。
    Purpose: Call when the user confirms a lesson or decision is still valid, preventing it from being treated as stale.

    注意：如果要修改内容，用 update_knowledge。
    Note: Use update_knowledge when the content itself needs to change.

    Args:
        knowledge_id: 要复习的知识条目 ID。 / ID of the knowledge item to review.
    """
    result = _engram.review_knowledge(knowledge_id)
    _beta("knowledge_reviewed")
    return _json(result)


@mcp.tool()
async def get_stale_knowledge(days: int = 30, limit: int = 20) -> str:
    """列出超过指定天数未复习的知识条目。 / List knowledge items not reviewed for more than the specified number of days.

    用途：定期检查哪些经验或决策需要复习或归档。
    Purpose: Call during periodic maintenance to find lessons or decisions that need review or archiving.

    注意：如果想直接归档某条，用 archive_knowledge；如果确认仍有效，用 review_knowledge 刷新。
    Note: Use archive_knowledge to archive an item directly; use review_knowledge to refresh an item that is still valid.

    Args:
        days: 超过多少天算过期（默认 30）。 / Number of days after which an item is stale (default 30).
        limit: 最多返回多少条（默认 20）。 / Maximum number of items to return (default 20).
    """
    return _json(_engram.get_stale_knowledge(days=days, limit=limit))


@mcp.tool()
async def request_outline_review(lang: str = "zh") -> str:
    """生成交互式知识审查 HTML 页面，用户可在浏览器中逐条保留或归档知识。 / Generate an interactive knowledge review HTML page where the user can retain or archive items.

    用途：用户说"帮我核对一下记忆"、"看看我的知识库"、"review my knowledge"时调用。
    Purpose: Call when the user wants to audit their knowledge base, e.g. "review my knowledge" or "check my memory".

    生成的 HTML 页面包含身份画像总览、经验教训（按领域分组）、关键决策，可逐条勾选保留/归档。
    用户审查完成后，将结果粘贴回对话或下载 JSON，再调用 apply_review 执行归档。

    Args:
        lang: 页面语言，"zh"（中文）或 "en"（英文），默认中文。 / Page language: "zh" (Chinese) or "en" (English), default "zh".
    """
    path = _engram.export_review_page(lang=lang)
    return _json({
        "status": "review_page_generated",
        "path": str(path),
        "message": f"知识审查页面已生成: {path}。请在浏览器中打开，审查完成后将结果粘贴回对话。"
        if lang == "zh"
        else f"Review page generated: {path}. Open in browser, then paste review results back.",
    })


@mcp.tool()
async def apply_review(review_text: str) -> str:
    """执行知识审查结果——归档用户标记为不需要的条目。 / Execute knowledge review results — archive items the user marked for removal.

    用途：用户从审查页面复制审查结果文本后，调用此工具执行归档操作。
    Purpose: After the user copies review results from the HTML review page, call this to execute archival.

    输入格式（每行一条 `archive lesson <id>` 或 `archive decision <id>`），或审查页面下载的 JSON 字符串。

    Args:
        review_text: 审查结果文本或 JSON 字符串。 / Review results text or JSON string from the review page.
    """
    import json as _json_mod

    # Try to parse as JSON first
    try:
        data = _json_mod.loads(review_text)
        if isinstance(data, dict) and "archive" in data:
            result = _engram.apply_review(data)
            return _json(result)
    except (ValueError, TypeError):
        pass

    # Treat as text format
    result = _engram.apply_review(review_text)
    return _json(result)


@mcp.tool()
async def merge_knowledge(primary_id: str, secondary_id: str) -> str:
    """将次要知识条目合并进主知识条目。 / Merge a secondary knowledge item into a primary knowledge item.

    用途：find_similar_knowledge 发现重复或高度相似条目后，用来保留主条目并归档次要条目。
    Purpose: Call after find_similar_knowledge identifies duplicate or highly similar items, keeping the primary item and archiving the secondary one.

    注意：主条目的内容会保留，次要条目的关联关系会转移后归档。
    Note: The primary item's content is preserved; related links from the secondary item are transferred before it is archived.

    Args:
        primary_id: 要保留的主条目 ID。 / ID of the primary item to keep.
        secondary_id: 要合并并归档的次要条目 ID。 / ID of the secondary item to merge and archive.
    """
    return _json(_engram.merge_knowledge(primary_id, secondary_id))


@mcp.tool()
async def link_knowledge(id_a: str, id_b: str) -> str:
    """在两个知识条目之间创建双向关联。 / Create a bidirectional link between two knowledge items.

    用途：当两条 lesson 或 decision 在原因、结果或主题上有关联时调用。
    Purpose: Call when two lessons or decisions are related by cause, outcome, topic, or supporting context.

    注意：如果只是想查已有关系，用 get_related_knowledge。
    Note: Use get_related_knowledge when you only want to inspect existing links.

    Args:
        id_a: 第一个 lesson 或 decision 的 ID。 / ID of the first lesson or decision.
        id_b: 第二个 lesson 或 decision 的 ID。 / ID of the second lesson or decision.
    """
    return _json(_engram.link_knowledge(id_a, id_b))


@mcp.tool()
async def unlink_knowledge(id_a: str, id_b: str) -> str:
    """移除两个知识条目之间的双向关联。 / Remove the bidirectional link between two knowledge items.

    用途：发现两条 lesson 或 decision 不再相关，或之前错误关联时调用。
    Purpose: Call when two lessons or decisions are no longer related or were linked by mistake.

    注意：这不会删除或归档任何知识，只移除关系。
    Note: This does not delete or archive any knowledge; it only removes the relationship.

    Args:
        id_a: 第一个 lesson 或 decision 的 ID。 / ID of the first lesson or decision.
        id_b: 第二个 lesson 或 decision 的 ID。 / ID of the second lesson or decision.
    """
    return _json(_engram.unlink_knowledge(id_a, id_b))


@mcp.tool()
async def update_identity(field: str, updates_json: str) -> str:
    """更新一个身份字段。 / Update one identity field.

    用途：需要修改 profile、preferences、trust_boundaries、work_style 或 quality_standards 时调用。
    Purpose: Call when changing profile, preferences, trust_boundaries, work_style, or quality_standards.

    注意：updates_json 必须只包含该字段允许的键；敏感字段边界应通过 trust_boundaries 管理。
    Note: updates_json should contain only keys valid for that field; manage sensitive-field boundaries through trust_boundaries.

    Args:
        field: 字段名：profile、preferences、trust_boundaries、work_style 或 quality_standards。 / Field name: profile, preferences, trust_boundaries, work_style, or quality_standards.
        updates_json: 包含要更新字段的 JSON 字符串。 / JSON string containing the fields to update.

    Field-specific keys / 字段专用键:
        profile: role, language, technical_level, description / role、language、technical_level、description。
        preferences: work_patterns (dict), communication (str), tool_preferences (dict), playbook_auto_extract (bool, default true) / work_patterns（字典）、communication（字符串）、tool_preferences（字典）、playbook_auto_extract（布尔，默认 true）。
        trust_boundaries: default_sharing, tool_access, private_fields, restricted_fields / default_sharing、tool_access、private_fields、restricted_fields。
        work_style: preferences (dict), communication (str) / preferences（字典）、communication（字符串）。
        quality_standards: acceptance_threshold (1-5), rules (list) / acceptance_threshold（1-5）、rules（列表）。
    """
    if field not in IDENTITY_FIELDS:
        return _json({"error": f"Unknown field: {field}. Valid: {sorted(IDENTITY_FIELDS)}"})
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return _json({"error": "updates_json must be valid JSON"})
    dispatch = {
        "profile": _engram.update_profile,
        "preferences": _engram.update_preferences,
        "trust_boundaries": _engram.update_trust_boundaries,
        "work_style": _engram.update_work_style,
        "quality_standards": _engram.update_quality_standards,
    }
    try:
        dispatch[field](updates)
        _track("update_identity", success=True)
    except Exception as exc:
        _track("update_identity", success=False)
        raise
    return _json({"success": True, "field": field, "updated_keys": list(updates.keys())})


@mcp.tool()
async def save_project_snapshot(project_folder: str, data_json: str) -> str:
    """写入或更新项目的知识快照（写操作，不是读取）。 / Write or update a project's knowledge snapshot; this is a write operation, not a read.

    用途：保存或更新当前项目的技术栈、已知问题、注释等信息。
    Purpose: Call to save or update a project's tech stack, known issues, notes, and related metadata.

    注意：读取项目快照用 get_project_context，不是本工具。
    Note: Use get_project_context to read a project snapshot; this tool writes one.

    Args:
        project_folder: 项目文件夹路径。 / Project folder path.
        data_json: JSON 字符串，支持字段 title、tech_stack、known_issues、notes。 / JSON string supporting fields: title, tech_stack, known_issues, and notes.
    """
    err = _validate_path(project_folder)
    if err:
        return f"错误: {err}"
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return "错误: data_json 必须是合法的 JSON。"
    if not isinstance(data, dict):
        return "错误: data_json 应为 JSON 对象（{}），不能是数组或标量。"
    _engram.save_project_snapshot(project_folder, data)
    _track("save_project_snapshot", success=True)
    return f"项目快照已保存: {project_folder}"


# ===========================================================================
# WEB CONTENT TOOL (1)
# ===========================================================================


@mcp.tool()
async def read_web_content(url: str) -> str:
    """读取网页、视频或文章的文本内容（通过 Engram Reader 本地服务）。 / Read text content from a web page, video, or article through the local Engram Reader service.

    用途：用户发链接并要求分析、看看或读一下时调用。
    Purpose: Call when the user sends a URL and asks to analyze, inspect, or read it.

    注意：需要 Engram Reader 本地服务运行在 localhost:7890；支持 YouTube 字幕、B 站、公众号文章、知乎和通用网页。
    Note: Requires the local Engram Reader service on localhost:7890; supports YouTube subtitles, Bilibili, WeChat articles, Zhihu, and general web pages.

    Args:
        url: 要提取内容的网页链接。 / URL to extract content from.
    """
    import urllib.request
    import urllib.error

    try:
        payload = json.dumps({"url": url}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:7890/extract",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("error"):
                return f"提取失败: {data['error']}"
            content = data.get("content", "")
            source = data.get("source", "unknown")
            if not content:
                return "未能提取到内容。请确认链接可访问，或在浏览器中打开后用插件提取。"
            return f"[来源: {source}]\n\n{content}"
    except urllib.error.URLError:
        return "Engram Reader 服务未运行。请先启动: python reader_server.py"
    except Exception as e:
        return f"读取失败: {_safe_err(e)}"


# ===========================================================================
# IMPORT / EXPORT TOOLS (4)
# ===========================================================================


@mcp.tool()
async def export_engram(output_path: Optional[str] = None) -> str:
    """导出整个 Engram 为单一备份文件。 / Export the entire Engram store as a single backup file.

    用途：用于备份、迁移到另一台机器或跨设备同步。
    Purpose: Call for backup, migration to another machine, or cross-device sync.

    注意：导出包含全部身份、知识和项目数据，请按隐私级别处理文件。
    Note: The export contains all identity, knowledge, and project data, so handle the file according to its privacy level.

    Args:
        output_path: 导出路径（可选，默认存到 ~/.engram/exports/engram_backup_<日期>.json）。 / Export path (optional; defaults to ~/.engram/exports/engram_backup_<date>.json).
    """
    err = _validate_path(output_path, allow_empty=True)
    if err:
        return f"错误: {err}"
    try:
        path = _engram.export_all(output_path)
        return f"导出成功: {path}"
    except Exception as e:
        return f"导出失败: {_safe_err(e)}"


@mcp.tool()
async def import_engram(input_path: str, merge: bool = True) -> str:
    """从备份文件导入 Engram 数据。 / Import Engram data from a backup file.

    用途：从备份恢复，或从另一台机器迁移数据。
    Purpose: Call to restore from backup or migrate data from another machine.

    注意：merge=True 会合并数据；merge=False 会覆盖现有数据，使用前要确认风险。
    Note: merge=True merges data; merge=False overwrites existing data, so confirm the risk before using it.

    Args:
        input_path: 备份文件路径（export_engram 生成的 JSON 文件）。 / Backup file path, usually a JSON file generated by export_engram.
        merge: True 表示合并模式（保留已有数据并追加新数据），False 表示覆盖模式。 / True means merge mode (keep existing data and append new data); False means overwrite mode.
    """
    err = _validate_path(input_path)
    if err:
        return _json({"error": err})
    result = _engram.import_all(input_path, merge=merge)
    return _json(result)


@mcp.tool()
async def export_engram_to_openclaw(output_dir: str = "") -> str:
    """导出 Engram 为 OpenClaw 兼容格式（SOUL.md + MEMORY.md + USER.md）。 / Export Engram to the OpenClaw-compatible format: SOUL.md, MEMORY.md, and USER.md.

    用途：需要把 Engram 数据交给 OpenClaw 或兼容工作流使用时调用。
    Purpose: Call when Engram data needs to be used by OpenClaw or compatible workflows.

    注意：如果 output_dir 为空，会导出到 Engram 的 compat/openclaw 目录。
    Note: If output_dir is empty, files are exported to Engram's compat/openclaw directory.

    Args:
        output_dir: 输出目录（可选）。 / Output directory (optional).
    """
    try:
        target_dir = output_dir or str(_engram.root / "compat" / "openclaw")
        result = export_to_openclaw(_engram, target_dir)
        files = result.get("files", [])
        if result.get("status") == "success":
            return _json(files)
        return _json(result)
    except Exception as e:
        return f"导出 OpenClaw 兼容格式失败: {_safe_err(e)}"


@mcp.tool()
async def import_engram_from_openclaw(
    soul_path: str = "",
    memory_path: str = "",
    user_path: str = "",
) -> str:
    """从 OpenClaw 格式导入数据到 Engram（SOUL.md、MEMORY.md、USER.md）。 / Import OpenClaw-format data into Engram from SOUL.md, MEMORY.md, and USER.md.

    用途：需要把 OpenClaw 或兼容记忆文件迁移进 Engram 时调用。
    Purpose: Call when migrating OpenClaw or compatible memory files into Engram.

    注意：只提供存在的文件路径即可；导入逻辑会按文件类型处理。
    Note: Provide only the file paths that exist; the import logic handles each file type.

    Args:
        soul_path: SOUL.md 文件路径（可选）。 / Path to SOUL.md (optional).
        memory_path: MEMORY.md 文件路径（可选）。 / Path to MEMORY.md (optional).
        user_path: USER.md 文件路径（可选）。 / Path to USER.md (optional).
    """
    try:
        result = import_from_openclaw(_engram, soul_path, memory_path, user_path)
        return _json(result)
    except Exception as e:
        return f"从 OpenClaw 兼容格式导入失败: {_safe_err(e)}"


@mcp.tool()
async def get_audit_log(limit: int = 50) -> str:
    """获取最近的审计日志条目。 / Get recent audit log entries.

    用途：需要查看 Engram 最近的读写操作、排查行为或核对记录时调用。
    Purpose: Call when inspecting recent Engram reads/writes, debugging behavior, or auditing activity.

    注意：只有启用 ENGRAM_AUDIT=1 后才会有审计日志。最多返回 200 条。
    Note: Audit entries exist only when ENGRAM_AUDIT=1 has been enabled. Max 200 entries.

    Args:
        limit: 最多返回多少条（默认 50，上限 200，按最近优先）。 / Maximum entries to return (default 50, max 200, most recent first).
    """
    _MAX_AUDIT_ENTRIES = 200
    limit = max(1, min(limit, _MAX_AUDIT_ENTRIES))
    log_path = _engram.root / "audit.log"
    if not log_path.is_file():
        return _json({"entries": [], "total": 0, "message": "Audit logging not enabled. Set ENGRAM_AUDIT=1."})

    # Tail-read: only load enough bytes from the end to cover *limit* entries,
    # avoiding reading potentially large log files entirely into memory.
    _APPROX_LINE_SIZE = 512  # generous estimate per JSONL entry
    file_size = log_path.stat().st_size
    read_size = min(file_size, limit * _APPROX_LINE_SIZE)

    with open(log_path, "rb") as f:
        if read_size < file_size:
            f.seek(-read_size, 2)  # seek from end
            partial = f.read().decode("utf-8", errors="replace")
            # first line may be partial — discard it
            tail_lines = partial.split("\n")[1:]
        else:
            tail_lines = f.read().decode("utf-8", errors="replace").split("\n")

    entries = []
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break

    _engram._audit.log("read", "audit_log", detail=f"returned {len(entries)}")
    return _json({"entries": entries, "total": len(entries)})


# ===========================================================================
# WORKFLOW SHORTCUTS (2)
# ===========================================================================


@mcp.tool()
async def wrap_up_session(
    summary: str,
    project_folder: str = "",
    source_tool: str = "",
    project_title: str = "",
    tech_stack: str = "",
    known_issues: str = "",
) -> str:
    """会话结束一键收尾：自动提取知识、操作流程并保存项目快照。 / Wrap up a session in one step: extract knowledge, detect playbooks, and save a project snapshot.

    **Lifecycle: session-end** — 对话结束时调用，完成知识提取和上下文保存。
    Lifecycle: session-end — call at conversation end to extract knowledge and persist session context.

    用途：一次对话结束时调用，把会话摘要交给 Engram 自动提取 lessons、decisions 和 Playbook 草稿，并可选更新项目快照。
    Purpose: Call at the end of a conversation to let Engram extract lessons, decisions, and playbook drafts from the summary and optionally update the project snapshot.

    Playbook 自动提取：如果摘要描述了一个多步骤操作流程（3+ 步骤，含顺序标记和操作动词），会自动生成 Playbook 草稿存入 staging。返回值中会包含 playbook_draft 字段（含 confidence: high/medium），AI 工具应根据 confidence 决定是否提示用户。可通过 update_preferences(playbook_auto_extract=false) 关闭此功能。
    Playbook auto-extraction: If the summary describes a multi-step operational workflow (3+ steps with sequential markers and action verbs), a Playbook draft is auto-generated into staging. The return value includes a playbook_draft field (with confidence: high/medium); AI tools should decide whether to notify the user based on confidence. Disable via update_preferences(playbook_auto_extract=false).

    注意：如果只想提取知识不用保存项目，用 extract_session_insights；如果只想保存项目快照，用 save_project_snapshot。
    Note: Use extract_session_insights when you only want extraction, and save_project_snapshot when you only want to save a project snapshot.

    Args:
        summary: 会话摘要（自由文本，段落或要点列表均可）。 / Session summary in free text; paragraphs or bullet lists both work.
        project_folder: 项目文件夹路径（可选，不填则只提取知识不保存快照）。 / Project folder path (optional; omit it to extract knowledge without saving a snapshot).
        source_tool: 调用来源工具，如 'claude_code', 'codex'。 / Calling source tool, such as 'claude_code' or 'codex'.
        project_title: 项目名称（可选，仅在首次保存快照时需要）。 / Project title (optional; mainly needed when first saving a snapshot).
        tech_stack: 技术栈（可选，逗号分隔）。 / Tech stack (optional, comma-separated).
        known_issues: 已知问题（可选，逗号分隔）。 / Known issues (optional, comma-separated).
    """
    _session.detect_tool(source_tool)
    if project_folder:
        _session.detect_project(project_folder)

    results = {}

    # Step 1: Extract insights
    try:
        insights = _engram.extract_session_insights(summary, source_tool=source_tool)
        results["insights"] = insights
    except Exception as exc:
        logger.warning("extract_session_insights failed: %s", exc)
        results["insights"] = {"error": str(exc)}

    # Step 1.5: Auto-extract Playbook if session looks like a procedure
    try:
        playbook = _engram.extract_playbook_from_session(
            summary, source_tool=source_tool,
        )
        if playbook:
            pb_confidence = playbook.get("confidence", "medium")
            _zh = _user_lang() == "zh"
            if pb_confidence == "high":
                _pb_msg = ("检测到可复用的操作流程，已生成 Playbook 草稿。" if _zh
                           else "Reusable workflow detected — Playbook draft generated.")
            else:
                _pb_msg = ("检测到可能的操作流程，已静默存入草稿。" if _zh
                           else "Possible workflow detected — silently saved as draft.")
            results["playbook_draft"] = {
                "title": playbook.get("title", ""),
                "playbook_id": playbook.get("id", ""),
                "steps_count": len(playbook.get("steps", [])),
                "pitfalls_count": len(playbook.get("pitfalls", [])),
                "tier": "staging",
                "confidence": pb_confidence,
                "message": _pb_msg,
            }
    except Exception as exc:
        logger.warning("playbook extraction failed: %s", exc)

    # Step 2: Save project snapshot (if project_folder provided)
    if project_folder:
        try:
            snapshot_data: dict = {}
            if project_title:
                snapshot_data["title"] = project_title
            if tech_stack:
                snapshot_data["tech_stack"] = [s.strip() for s in tech_stack.split(",") if s.strip()]
            if known_issues:
                snapshot_data["known_issues"] = [s.strip() for s in known_issues.split(",") if s.strip()]
            _engram.save_project_snapshot(project_folder, snapshot_data)
            results["project_snapshot"] = {"saved": True, "folder": project_folder}
        except Exception as exc:
            logger.warning("save_project_snapshot failed: %s", exc)
            results["project_snapshot"] = {"error": str(exc)}

    # Step 3: Auto-reconcile external AI memories and configs
    _reconcile_imported = 0
    try:
        reconcile = _engram.reconcile_memories()
        if reconcile["imported"] > 0:
            results["memory_sync"] = reconcile
            _reconcile_imported += reconcile["imported"]
    except Exception as exc:
        logger.warning("reconcile_memories failed: %s", exc)

    try:
        cfg_sync = _engram.reconcile_ai_configs()
        if cfg_sync["imported"] > 0:
            results["config_sync"] = cfg_sync
            _reconcile_imported += cfg_sync["imported"]
    except Exception as exc:
        logger.warning("reconcile_ai_configs failed: %s", exc)

    if _reconcile_imported > 0:
        _beta("reconcile", imported=_reconcile_imported)

    # Step 4: Evaluate tier promotions (staging->verified based on access evidence)
    try:
        tier_result = _engram.evaluate_tiers()
        if tier_result["promoted"] > 0:
            results["tier_promotions"] = tier_result
            _beta("knowledge_promoted", count=tier_result["promoted"], method="auto_access")
    except Exception as exc:
        logger.warning("evaluate_tiers failed: %s", exc)

    # Step 5: Report staging backlog
    try:
        staging = _engram.get_staging_summary()
        if staging["total_staging"] > 0:
            _zh = _user_lang() == "zh"
            if _zh:
                _stg_msg = (
                    f"有 {staging['total_staging']} 条待审知识"
                    f"（{staging['staging_lessons']} 条经验 + "
                    f"{staging['staging_decisions']} 条决策）。"
                    "建议使用 review_knowledge 审查。"
                )
            else:
                _stg_msg = (
                    f"{staging['total_staging']} knowledge items pending review "
                    f"({staging['staging_lessons']} lessons + "
                    f"{staging['staging_decisions']} decisions). "
                    "Consider using review_knowledge to review them."
                )
            results["staging_reminder"] = {
                "message": _stg_msg,
                **staging,
            }
    except Exception as exc:
        logger.warning("get_staging_summary failed: %s", exc)

    # Step 6: Beta event — session end
    _beta("session_end",
          source_tool=source_tool[:40] if source_tool else "",
          has_project=bool(project_folder),
          insights=bool(results.get("insights")))

    # Step 7: Record this tool call BEFORE flushing so it's included
    _track("wrap_up_session", success=True)

    # Step 7: Flush anonymous usage statistics (local + optional remote)
    # force=True: wrap_up_session is the last chance before process exit
    try:
        if _tracker is not None:
            from importlib.metadata import version as _pkg_version
            try:
                _ver = _pkg_version("piia-engram")
            except Exception:
                _ver = "dev"
            k_counts = {}
            try:
                k_counts["lessons"] = len(_engram.get_lessons(limit=None, _update_access=False))
                k_counts["decisions"] = len(_engram.get_decisions(limit=None, _update_access=False))
                k_counts["domains"] = len(_engram.get_domains())
            except Exception:
                pass
            _tier = os.environ.get("ENGRAM_TOOLS", "core")
            _tracker.flush(
                knowledge_counts=k_counts,
                engram_version=_ver,
                tools_tier=_tier,
                force=True,
            )
    except Exception as exc:
        logger.debug("telemetry flush skipped: %s", exc)

    # Step 8: Periodic anonymous feedback report (weekly, if opted in)
    try:
        from piia_engram.telemetry import is_feedback_enabled, _feedback_due, send_feedback
        if is_feedback_enabled() and _feedback_due():
            from piia_engram.setup_wizard import _build_feedback_report
            report = _build_feedback_report()
            send_feedback(report)
    except Exception as exc:
        logger.debug("feedback send skipped: %s", exc)

    return _json(results)


@mcp.tool()
async def export_feedback_report() -> str:
    """导出匿名内测反馈报告。 / Export anonymous beta feedback report.

    用途：用户想分享使用反馈时调用。报告只包含计数和分布，不含知识内容或个人信息。
    Purpose: Call when the user wants to share usage feedback. The report contains only counts and distributions — no knowledge content or personal information.
    """
    from piia_engram.setup_wizard import _build_feedback_report
    report = _build_feedback_report()
    return _json(report)


@mcp.tool()
async def start_project(
    description: str,
    project_folder: str,
    project_title: str = "",
    tech_stack: str = "",
    limit: int = 10,
) -> str:
    """新项目一键启动：继承跨项目经验并建立项目档案。 / Start a new project in one step: inherit cross-project knowledge and create a project record.

    用途：开始一个新项目时调用，一次拿到过往相关 lessons 和 decisions，并初始化项目快照。
    Purpose: Call when starting a new project to retrieve relevant prior lessons and decisions and initialize the project snapshot.

    注意：如果只想获取可继承经验、不需要创建项目档案，请直接用 get_knowledge_inheritance。
    Note: If you only want inheritable knowledge and do not need a project record, use get_knowledge_inheritance directly.

    Args:
        description: 新项目的自由文本描述（用于匹配已有知识）。 / Free-text description of the new project, used to match existing knowledge.
        project_folder: 项目文件夹路径。 / Project folder path.
        project_title: 项目名称（可选）。 / Project title (optional).
        tech_stack: 技术栈（可选，逗号分隔）。 / Tech stack (optional, comma-separated).
        limit: 最多继承多少条经验（默认 10，上限 20）。 / Maximum number of knowledge items to inherit (default 10, max 20).
    """
    results = {}

    # Step 1: Knowledge inheritance
    limit = min(int(limit), 20)
    inheritance = _engram.get_knowledge_inheritance(description, limit=limit)
    results["inherited_knowledge"] = inheritance

    # Step 2: Initialize project snapshot
    snapshot_data: dict = {}
    if project_title:
        snapshot_data["title"] = project_title
    elif description:
        snapshot_data["title"] = description[:80]
    if tech_stack:
        snapshot_data["tech_stack"] = [s.strip() for s in tech_stack.split(",") if s.strip()]
    _engram.save_project_snapshot(project_folder, snapshot_data)
    results["project_snapshot"] = {"created": True, "folder": project_folder}

    return _json(results)


# ===========================================================================
# RESOURCES (5)
# ===========================================================================


@mcp.resource("engram://identity/profile")
def resource_profile() -> str:
    """用户身份画像（已按信任边界过滤）。"""
    return _json(_engram.get_safe_profile())


@mcp.resource("engram://identity/preferences")
def resource_preferences() -> str:
    """用户工作偏好（v2.0）。"""
    return _json(_engram.get_preferences())


@mcp.resource("engram://identity/trust-boundaries")
def resource_trust_boundaries() -> str:
    """数据信任边界。"""
    return _json(_engram.get_trust_boundaries())


@mcp.resource("engram://identity/work-style")
def resource_work_style() -> str:
    """用户工作偏好（v1兼容）。"""
    return _json(_engram.get_work_style())


@mcp.resource("engram://identity/quality-standards")
def resource_quality_standards() -> str:
    """用户质量标准。"""
    return _json(_engram.get_quality_standards())


@mcp.resource("engram://knowledge/domains")
def resource_domains() -> str:
    """用户技术领域经验图谱。"""
    return _json(_engram.get_domains())


@mcp.resource("engram://stats")
def resource_stats() -> str:
    """知识资产统计。"""
    return _json(_engram.get_stats())


# ===========================================================================
# AGENT CONTEXT RECOVERY (3)
# ===========================================================================


@mcp.tool()
async def save_agent_context(
    tool: str,
    content: str,
    session_id: str = "",
    project_folder: str = "",
    actions_json: str = "",
) -> str:
    """自动保存 AI 对话上下文检查点。 / Auto-save an AI conversation context checkpoint.

    用途：在关键节点（任务启动、阶段完成、方向变更）自动调用，静默记录工作状态。
    Purpose: Call at key moments (task start, milestone, direction change) to silently record work state.

    设计理念：像 Office 自动保存 — 平时无感，崩溃/重启时可找回。
    Design: Like Office autosave — invisible during work, recoverable after crash/restart.

    Args:
        tool: 调用来源工具名，如 'claude_code', 'codex', 'cursor'。 / Source tool name.
        content: 上下文内容（当前任务、进度、下一步，自由文本）。 / Context content (current tasks, progress, next steps, free text).
        session_id: 会话 ID（可选）。留空则新建会话，填入已有 ID 则追加到同一会话文件。 / Session ID (optional). Empty creates new session; existing ID appends to same file.
        project_folder: 项目路径（可选，写入文件头）。 / Project folder path (optional, written to file header).
        actions_json: 结构化动作日志（可选），JSON 数组，每个元素含 tool_called, arguments_summary, result_summary。用于 Playbook 自动提取。 / Structured action log (optional), JSON array of {tool_called, arguments_summary, result_summary}. Used for higher-fidelity Playbook extraction.
    """
    _session.detect_tool(tool)
    if project_folder:
        _session.detect_project(project_folder)
    actions = None
    if actions_json:
        try:
            parsed = json.loads(actions_json)
            if isinstance(parsed, list):
                actions = parsed
        except json.JSONDecodeError:
            pass
    result = _engram.save_agent_context(
        tool=tool,
        content=content,
        session_id=session_id,
        project_folder=project_folder,
        actions=actions,
    )
    _track("save_agent_context", success=True)
    return _json(result)


@mcp.tool()
async def get_recent_context(
    tool: str = "",
    limit: int = 1,
) -> str:
    """找回最近的 AI 对话上下文。 / Retrieve the most recent AI conversation context.

    用途：上下文丢失时（工具重启、会话断开）调用，找回之前的工作状态。
    Purpose: Call after context loss (tool restart, session disconnect) to recover previous work state.

    不会自动加载到新会话 — 只在你需要时才读取。
    Does NOT auto-load into new sessions — only reads when you ask.

    Args:
        tool: 工具名（可选）。留空则搜索所有工具的上下文。 / Tool name (optional). Empty searches all tools.
        limit: 最多返回几个会话（默认 1 = 最近一次）。 / Max sessions to return (default 1 = most recent).
    """
    sessions = _engram.get_recent_context(tool=tool, limit=limit)
    _track("get_recent_context", success=True)
    if not sessions:
        return _json({"message": "没有找到保存的上下文记录。", "sessions": []})
    return _json({"sessions": sessions})


@mcp.tool()
async def list_agent_sessions(
    tool: str = "",
    limit: int = 20,
) -> str:
    """列出可用的 AI 对话上下文记录（仅元数据）。 / List available AI context sessions (metadata only).

    用途：查看有哪些历史会话记录可以找回。
    Purpose: See which historical session records are available for recovery.

    Args:
        tool: 工具名（可选）。留空则列出所有工具。 / Tool name (optional). Empty lists all tools.
        limit: 最多返回多少条（默认 20）。 / Max entries to return (default 20).
    """
    sessions = _engram.list_agent_sessions(tool=tool, limit=limit)
    _track("list_agent_sessions", success=True)
    return _json({"sessions": sessions, "total": len(sessions)})


# Apply tool tier filter AFTER all @mcp.tool() decorators have run
_apply_tool_tier()


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    args = _parse_args()

    # ── Startup self-check: detect stale invocation paths ──
    _invoked_via = sys.argv[0] if sys.argv else ""
    if "engram_core" in _invoked_via:
        print(
            "[engram] WARNING: Invoked via deprecated path 'engram_core'. "
            "Update your MCP config to use 'piia_engram.mcp_server'. "
            "Run 'engram doctor --fix' to auto-repair.",
            file=sys.stderr,
        )

    # Detect ephemeral/Docker environments where no local AI tools exist.
    # Skip auto_migrate and reconcile to speed up startup (critical for
    # mcp-proxy which has short connection timeouts).
    _is_ephemeral = (
        os.path.isfile("/.dockerenv")
        or os.environ.get("ENGRAM_EPHEMERAL", "").strip().lower() in ("1", "true", "yes")
    )

    # Auto-migrate legacy configs on first run after upgrade (stdio only;
    # must happen before mcp.run() to avoid polluting the MCP stdio channel).
    if args.transport == "stdio" and not _is_ephemeral:
        try:
            from piia_engram.setup_wizard import auto_migrate  # type: ignore[import]
        except ImportError:
            try:
                from setup_wizard import auto_migrate  # type: ignore[import]
            except ImportError:
                auto_migrate = None  # type: ignore[assignment]
        if auto_migrate is not None:
            auto_migrate()

    # Auto-reconcile on MCP server startup — runs once regardless of which
    # AI tool connects.  This ensures cross-tool memory sync happens even if
    # the AI tool never calls get_user_context.
    # Skip in ephemeral containers — no AI tool configs to scan.
    if not _is_ephemeral:
        try:
            _mem = _engram.reconcile_memories()
            _cfg = _engram.reconcile_ai_configs()
            if _mem["imported"] or _cfg["imported"]:
                _msgs = []
                if _mem["imported"]:
                    _msgs.append(f"memories={_mem['imported']}")
                if _cfg["imported"]:
                    _msgs.append(f"configs={_cfg['imported']}")
                print(
                    f"[engram] startup sync: {', '.join(_msgs)}",
                    file=sys.stderr,
                )
        except Exception as exc:
            logger.warning("startup sync failed: %s", exc)

    if args.transport == "sse":
        if not _HAS_STARLETTE:
            print("ERROR: SSE mode requires starlette. Install: pip install piia-engram[remote]")
            sys.exit(1)
        token = os.environ.get("ENGRAM_AUTH_TOKEN", "").strip()
        if not token:
            print("ERROR: ENGRAM_AUTH_TOKEN environment variable is required for SSE mode.")
            print(
                'Generate one with: python -c "import secrets; '
                'print(secrets.token_urlsafe(32))"'
            )
            sys.exit(1)

        mcp.settings.host = args.host
        mcp.settings.port = args.port

        if args.host == "0.0.0.0":
            print(
                "WARNING: Binding to 0.0.0.0 exposes Engram to the network. "
                "Use HTTPS (nginx/caddy) in production.",
                file=sys.stderr,
            )

        allowed_origins = os.environ.get("ENGRAM_CORS_ORIGINS", "").strip()

        print(f"Engram MCP server (SSE) on http://{args.host}:{args.port}/sse")

        starlette_app = mcp.sse_app()
        starlette_app.add_middleware(TokenAuthMiddleware, token=token)

        if allowed_origins:
            from starlette.middleware.cors import CORSMiddleware
            starlette_app.add_middleware(
                CORSMiddleware,
                allow_origins=[o.strip() for o in allowed_origins.split(",")],
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization"],
            )

        import uvicorn
        uvicorn.run(starlette_app, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")
