"""Engram MCP Server.

Exposes Engram as an MCP server over stdio or SSE transport.
Any MCP-compatible AI tool can access the user's identity, preferences,
lessons, decisions, and skills.

Usage:
    python mcp_server.py
    python -m engram_core.mcp_server --transport sse

Designed for local stdio transport and self-hosted remote SSE transport.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

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


def _track(tool_name: str, success: bool = True) -> None:
    """Record a tool call for anonymous usage statistics (if tracker available)."""
    if _tracker is not None:
        _tracker.record(tool_name, success=success)

IDENTITY_FIELDS = frozenset({
    "profile",
    "preferences",
    "trust_boundaries",
    "work_style",
    "quality_standards",
})

TOOL_TIER = os.environ.get("ENGRAM_TOOLS", "core").strip().lower() or "core"
TIER1_TOOLS = frozenset({
    # Session lifecycle
    "get_user_context",          # cold-start: load identity + context
    "wrap_up_session",           # session end: save insights + sync
    # Knowledge read/write
    "add_lesson",                # store reusable experience
    "add_decision",              # record decision + reasoning
    "search_knowledge",          # search across all knowledge
    "get_relevant_knowledge",    # project-aware knowledge retrieval
    # Identity
    "get_identity_card",         # export identity for non-MCP tools
    "update_identity",           # update profile/preferences/standards
    # Project context
    "get_project_context",       # current project state
    "save_project_snapshot",     # persist project state
})

mcp = FastMCP(
    "engram",
    instructions=(
        "Engram — AI 记忆印记。\n"
        "This server gives you access to the user's personal knowledge: "
        "who they are, how they work, what they've learned, and their quality standards.\n\n"
        "START every new conversation by calling get_user_context to understand the user."
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
# READ TOOLS (18)
# ===========================================================================


@mcp.tool()
async def get_user_context(project_folder: Optional[str] = None) -> str:
    """获取用户的个性化上下文（冷启动）。 / Get the user's personalized cold-start context.

    用途：在每次新对话开始时调用，了解用户是谁、如何工作、学到了什么、质量标准是什么。
    Purpose: Call at the start of each new conversation to understand who the user is, how they work, what they have learned, and their quality bar.

    注意：这是最重要的冷启动工具；如果只需要某个项目历史，用 get_project_context。
    Note: This is the primary cold-start tool; use get_project_context when you only need one project's history.

    Args:
        project_folder: 当前项目文件夹路径（可选，用于获取项目特定上下文）。 / Current project folder path (optional, used to include project-specific context).
    """
    try:
        context = _engram.generate_context(project_folder)
        _track("get_user_context", success=True)
    except Exception as exc:
        _track("get_user_context", success=False)
        logger.warning("generate_context failed: %s", exc)
        return f"Engram 上下文加载失败: {exc}"
    if not context:
        return "Engram 为空——这可能是新用户。尚无用户上下文可用。"
    return context


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
        return f"身份卡生成失败: {exc}"
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
async def search_knowledge(query: str, scope: str = "all", limit: int = 10) -> str:
    """按关键词搜索经验教训和决策（你知道要找什么）。 / Search lessons and decisions by keyword when you know what to look for.

    用途：用户说“帮我找一下关于 X 的经验”时调用。
    Purpose: Call when the user asks to find knowledge about a specific topic X.

    注意：如果你只有项目路径、没有搜索词，用 get_relevant_knowledge；如果有已有知识 ID 想找相似项，用 find_similar_knowledge。
    Note: If you only have a project path and no query, use get_relevant_knowledge; if you have an existing knowledge ID, use find_similar_knowledge.

    Args:
        query: 搜索关键词。 / Search query keywords.
        scope: 搜索范围：'all'、'lessons' 或 'decisions'。 / Search scope: 'all', 'lessons', or 'decisions'.
        limit: 最多返回多少条（默认 10）。 / Maximum number of items to return (default 10).
    """
    try:
        result = _engram.search_knowledge(query, scope=scope, limit=limit)
        _track("search_knowledge", success=True)
    except Exception as exc:
        _track("search_knowledge", success=False)
        return f"搜索失败: {exc}"
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
# WRITE TOOLS (17)
# ===========================================================================


@mcp.tool()
async def add_lesson(
    summary: str,
    detail: str = "",
    domain: str = "",
    source_tool: str = "",
    source_url: str = "",
) -> str:
    """记录单条经验教训（你已经知道要记什么）。 / Record one lesson learned when you already know what to save.

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
    except Exception as exc:
        _track("add_lesson", success=False)
        return f"添加教训失败: {exc}"
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

    用途：用户说“我们决定用 X”或“以后都用 Y”时调用。
    Purpose: Call when the user says they decided to use X or will use Y going forward.

    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。
    Note: If the user gives a session summary for automatic extraction, use extract_session_insights instead.

    Args:
        question: 决策的问题，如“数据库选型”。 / Decision question, such as 'database choice'.
        choice: 做出的选择，如“PostgreSQL”。 / Chosen option, such as 'PostgreSQL'.
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
    except Exception as exc:
        _track("add_decision", success=False)
        return f"添加决策失败: {exc}"
    if result.get("status") == "duplicate":
        return _json(result)
    return f"决策已记录: {question} → {choice}"


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
    return _json(_engram.archive_knowledge(item_id))


@mcp.tool()
async def review_knowledge(knowledge_id: str) -> str:
    """标记一条知识为“已复习”（刷新 last_reviewed 时间戳，不改内容）。 / Mark one knowledge item as reviewed, refreshing last_reviewed without changing content.

    用途：用户确认某条经验或决策仍然有效时调用，防止它被标记为过期。
    Purpose: Call when the user confirms a lesson or decision is still valid, preventing it from being treated as stale.

    注意：如果要修改内容，用 update_knowledge。
    Note: Use update_knowledge when the content itself needs to change.

    Args:
        knowledge_id: 要复习的知识条目 ID。 / ID of the knowledge item to review.
    """
    return _json(_engram.review_knowledge(knowledge_id))


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
        preferences: work_patterns (dict), communication (str), tool_preferences (dict) / work_patterns（字典）、communication（字符串）、tool_preferences（字典）。
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
        return f"读取失败: {e}"


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
        return f"导出失败: {e}"


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
        return f"导出 OpenClaw 兼容格式失败: {e}"


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
        return f"从 OpenClaw 兼容格式导入失败: {e}"


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
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    _engram._audit.log("read", "audit_log", detail=f"returned {len(entries)}/{len(lines)}")
    return _json({"entries": entries, "total": len(lines)})


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
    """会话结束一键收尾：自动提取知识并保存项目快照。 / Wrap up a session in one step: extract knowledge and save a project snapshot.

    用途：一次对话结束时调用，把会话摘要交给 Engram 自动提取 lessons 和 decisions，并可选更新项目快照。
    Purpose: Call at the end of a conversation to let Engram extract lessons and decisions from the summary and optionally update the project snapshot.

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
    results = {}

    # Step 1: Extract insights
    try:
        insights = _engram.extract_session_insights(summary, source_tool=source_tool)
        results["insights"] = insights
    except Exception as exc:
        logger.warning("extract_session_insights failed: %s", exc)
        results["insights"] = {"error": str(exc)}

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
    try:
        reconcile = _engram.reconcile_memories()
        if reconcile["imported"] > 0:
            results["memory_sync"] = reconcile
    except Exception as exc:
        logger.warning("reconcile_memories failed: %s", exc)

    try:
        cfg_sync = _engram.reconcile_ai_configs()
        if cfg_sync["imported"] > 0:
            results["config_sync"] = cfg_sync
    except Exception as exc:
        logger.warning("reconcile_ai_configs failed: %s", exc)

    # Step 4: Evaluate tier promotions (staging->verified based on access evidence)
    try:
        tier_result = _engram.evaluate_tiers()
        if tier_result["promoted"] > 0:
            results["tier_promotions"] = tier_result
    except Exception as exc:
        logger.warning("evaluate_tiers failed: %s", exc)

    # Step 5: Report staging backlog
    try:
        staging = _engram.get_staging_summary()
        if staging["total_staging"] > 0:
            results["staging_reminder"] = {
                "message": (
                    f"有 {staging['total_staging']} 条待审知识"
                    f"（{staging['staging_lessons']} 条经验 + "
                    f"{staging['staging_decisions']} 条决策）。"
                    "建议使用 review_knowledge 审查。"
                ),
                **staging,
            }
    except Exception as exc:
        logger.warning("get_staging_summary failed: %s", exc)

    # Step 6: Flush anonymous usage statistics (local log only)
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
            _tracker.flush(knowledge_counts=k_counts, engram_version=_ver)
    except Exception as exc:
        logger.debug("telemetry flush skipped: %s", exc)

    _track("wrap_up_session", success=True)
    return _json(results)


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


_apply_tool_tier()


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
# Entry point
# ===========================================================================

if __name__ == "__main__":
    args = _parse_args()

    # Auto-migrate legacy configs on first run after upgrade (stdio only;
    # must happen before mcp.run() to avoid polluting the MCP stdio channel).
    if args.transport == "stdio":
        try:
            from engram_core.setup_wizard import auto_migrate  # type: ignore[import]
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
