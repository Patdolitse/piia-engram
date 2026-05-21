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
import os
import sys
from pathlib import Path
from typing import Optional

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

IDENTITY_FIELDS = frozenset({
    "profile",
    "preferences",
    "trust_boundaries",
    "work_style",
    "quality_standards",
})

TOOL_TIER = os.environ.get("ENGRAM_TOOLS", "all").strip().lower() or "all"
TIER1_TOOLS = frozenset({
    "get_user_context",
    "get_identity_card",
    "search_knowledge",
    "add_lesson",
    "add_decision",
    "get_relevant_knowledge",
    "save_project_snapshot",
    "get_project_context",
    "extract_session_insights",
    "export_engram",
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
    """Keep all tools by default; allow ENGRAM_TOOLS=core to expose Tier-1 only."""
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
        if not auth_header.startswith("Bearer ") or auth_header[7:] != self.token:
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
    """获取用户的个性化上下文（冷启动）。

    在每次新对话开始时调用此工具，了解用户是谁、如何工作、学到了什么、质量标准是什么。
    这是最重要的工具——它提供完整的冷启动上下文。

    Args:
        project_folder: 当前项目文件夹路径（可选，用于获取项目特定上下文）。
    """
    context = _engram.generate_context(project_folder)
    if not context:
        return "Engram 为空——这可能是新用户。尚无用户上下文可用。"
    return context


@mcp.tool()
async def get_identity_card() -> str:
    """导出用户的可携带 AI 身份卡（Markdown 格式）。

    一个自包含的摘要，可以分享给任何 AI 工具。
    包含：用户身份、工作方式、质量标准、经验和教训。
    """
    card = _engram.export_identity_card()
    if not card:
        return "身份卡为空——尚未积累足够的知识。"
    return card


@mcp.tool()
async def get_profile(safe: bool = False) -> str:
    """获取用户身份画像。

    Args:
        safe: 设为 True 时按 trust_boundaries.restricted_fields 过滤敏感字段。
    """
    return _json(_engram.get_profile(safe=safe))


@mcp.tool()
async def get_work_style() -> str:
    """获取用户的工作偏好（工作模式、节奏、沟通风格）。"""
    return _json(_engram.get_work_style())


@mcp.tool()
async def get_preferences() -> str:
    """获取用户的工作偏好（v2.0，含工具偏好、工作模式、沟通风格）。"""
    return _json(_engram.get_preferences())


@mcp.tool()
async def get_trust_boundaries() -> str:
    """获取数据信任边界（哪些工具可以访问哪些Engram数据）。"""
    return _json(_engram.get_trust_boundaries())


@mcp.tool()
async def get_quality_standards() -> str:
    """获取用户的质量标准和验收条件。"""
    return _json(_engram.get_quality_standards())


@mcp.tool()
async def get_lessons(
    domain: Optional[str] = None,
    source_tool: Optional[str] = None,
    limit: int = 50,
) -> str:
    """获取用户从过去项目中学到的经验教训。

    用这些来避免重复过去的错误。

    Args:
        domain: 按领域过滤（如 'python'）。支持多标签教训的包含匹配。
        source_tool: 按来源工具过滤（如 'claude_code', 'codex'）。
        limit: 最多返回多少条（默认 50）。
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
    """按时间列出用户做过的关键决策（不需要搜索词）。

    用途：想浏览最近的决策记录，或按领域/项目/来源筛选时调用。
    注意：如果你有明确的关键词想搜索决策内容，用 search_knowledge(scope="decisions") 更精准。

    Args:
        source_tool: 按来源工具过滤（如 'claude_code', 'codex'）。
        project: 按项目过滤（可选）。
        domain: 按领域过滤（如 'architecture'）。支持多标签决策的包含匹配。
        limit: 最多返回多少条（默认 30）。
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
    """获取用户的技术领域经验图谱（涉及哪些技术/领域）。"""
    domains = _engram.get_domains()
    if not domains:
        return "尚无领域经验记录。"
    return _json(domains)


@mcp.tool()
async def get_project_context(project_folder: str) -> str:
    """读取特定项目的知识快照（项目级，只含该项目的历史）。

    用途：想了解某个项目之前的技术栈、已知问题、协作次数时调用。
    注意：如果想获取用户级的完整身份上下文（跨项目），用 get_user_context。
    如果想写入项目快照，用 save_project_snapshot。

    Args:
        project_folder: 项目文件夹路径。
    """
    snapshot = _engram.get_project_snapshot(project_folder)
    if not snapshot:
        return f"未找到项目知识记录: {project_folder}"
    return _json(snapshot)


@mcp.tool()
async def list_projects() -> str:
    """列出用户参与过的所有项目及基本信息。"""
    projects = _engram.list_projects()
    if not projects:
        return "尚无项目记录。"
    return _json(projects)


@mcp.tool()
async def get_relevant_knowledge(project_folder: str, limit: int = 8) -> str:
    """按项目路径自动推荐最相关的经验教训（无需搜索词）。

    用途：你知道当前项目路径但不知道该搜什么词时调用——Engram 根据项目技术栈自动筛选。
    注意：如果用户给了明确的搜索词，用 search_knowledge 更直接。

    Args:
        project_folder: 当前项目文件夹路径。
        limit: 最多返回多少条（默认 8）。
    """
    lessons = _engram.get_relevant_lessons(
        project_folder=project_folder, limit=limit
    )
    if not lessons:
        return "尚无相关经验教训。"
    return _json(lessons)


@mcp.tool()
async def get_knowledge_inheritance(description: str, limit: int = 10) -> str:
    """Get a knowledge inheritance pack for a new project or task.

    Scores all active lessons and decisions against a free-text description
    and returns the most relevant items ranked by relevance, without requiring
    a saved project snapshot.

    Args:
        description: Free-text description of the new project or task.
        limit: Max items to return in total (default 10, max 20).
    """
    limit = min(int(limit), 20)
    return _json(_engram.get_knowledge_inheritance(description, limit=limit))


@mcp.tool()
async def search_knowledge(query: str, scope: str = "all", limit: int = 10) -> str:
    """按关键词搜索经验教训和决策（你知道要找什么）。

    用途：用户说"帮我找一下关于 X 的经验"时调用。
    注意：如果你只有项目路径、没有搜索词，用 get_relevant_knowledge 按项目自动推荐。
    如果你有一条已有知识的 ID 想找相似的，用 find_similar_knowledge。"""
    return _json(_engram.search_knowledge(query, scope=scope, limit=limit))


@mcp.tool()
async def get_knowledge_overview(section: str = "all", stale_days: int = 30) -> str:
    """Get a unified knowledge overview: digest + health report + stale items.

    Args:
        section: "all" | "digest" | "health" | "stale".
        stale_days: Days threshold for stale check.
    """
    return _json(_engram.get_knowledge_overview(section, stale_days=stale_days))


@mcp.tool()
async def get_related_knowledge(item_id: str) -> str:
    """Get all knowledge items linked to a given lesson or decision ID."""
    return _json(_engram.get_related_knowledge(item_id))


@mcp.tool()
async def find_similar_knowledge(item_id: str, limit: int = 5) -> str:
    """根据已有知识条目 ID 查找内容相似的条目。

    用途：你已经有一条 lesson 或 decision 的 ID，想看还有没有类似的。
    注意：如果你没有 ID、只有关键词，用 search_knowledge。"""
    return _json(_engram.find_similar_knowledge(item_id, limit=limit))


@mcp.tool()
async def export_knowledge_report() -> str:
    """Export a full Markdown knowledge report to ~/.engram/exports/ and return the content."""
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
    """记录单条经验教训（你已经知道要记什么）。

    用途：用户明确说出一条踩坑经验或技术发现时调用。
    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。

    Args:
        summary: 教训的一行摘要。
        detail: 详细说明（可选）。
        domain: 技术领域（可选）。可填多个，逗号分隔，如 'python,testing'。
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。
        source_url: 如果教训来自外部内容，填写来源URL（可选）。
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
    result = _engram.add_lesson(lesson)
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
    """记录单条关键决策（用户明确选了某个方案）。

    用途：用户说"我们决定用 X"或"以后都用 Y"时调用。
    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。

    Args:
        question: 决策的问题（如"数据库选型"）。
        choice: 做出的选择（如"PostgreSQL"）。
        reasoning: 选择的理由（可选）。
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。
        domain: 技术领域（可选）。可填多个，逗号分隔，如 'architecture,database'。
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
    result = _engram.add_decision(decision)
    if result.get("status") == "duplicate":
        return _json(result)
    return f"决策已记录: {question} → {choice}"


@mcp.tool()
async def bulk_add_knowledge(items_json: str, item_type: str = "lesson", source_tool: str = "") -> str:
    """Batch-add multiple lessons or decisions in one call.

    Args:
        items_json: JSON array of items.
        item_type: "lesson" or "decision".
        source_tool: Recording source tool.
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
    """从自由文本笔记中提取经验教训和关键决策并写入知识库。

    Args:
        text: 多行自由文本笔记。
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。
        domain: 默认领域（可填多个，逗号分隔）；未命中关键词推断时使用。
    """
    return _json(_engram.ingest_notes(text, source_tool=source_tool, domain=domain))


@mcp.tool()
async def extract_session_insights(summary: str, source_tool: str = "") -> str:
    """从会话摘要中批量自动提取经验教训和决策（你不需要自己分类）。

    用途：会话结束时，把一段自由文本摘要交给 Engram，它会自动解析出 lessons 和 decisions 并存入知识库。
    注意：如果你已经明确知道要记一条 lesson 或 decision，直接用 add_lesson / add_decision 更精准。
    本工具适合"我不确定里面有什么值得记的，让 Engram 自己判断"的场景。

    Args:
        summary: 自由文本会话摘要，段落或要点列表均可。
        source_tool: 调用来源工具，如 'claude_code', 'codex'。
    """
    return _json(_engram.extract_session_insights(summary, source_tool=source_tool))


@mcp.tool()
async def update_knowledge(item_id: str, updates_json: str) -> str:
    """Update a lesson or decision by ID (auto-detects type).

    Args:
        item_id: The ID of the lesson or decision.
        updates_json: JSON string of fields to update.
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return _json({"error": "updates_json must be valid JSON"})
    return _json(_engram.update_knowledge(item_id, updates))


@mcp.tool()
async def archive_knowledge(item_id: str) -> str:
    """Archive a lesson or decision by ID. Automatically detects the item type."""
    return _json(_engram.archive_knowledge(item_id))


@mcp.tool()
async def review_knowledge(knowledge_id: str) -> str:
    """标记一条知识为"已复习"（刷新 last_reviewed 时间戳，不改内容）。

    用途：用户确认某条经验/决策仍然有效时调用，防止它被标记为过期。
    注意：如果要修改内容，用 update_knowledge。

    Args:
        knowledge_id: 要复习的知识条目 ID。
    """
    return _json(_engram.review_knowledge(knowledge_id))


@mcp.tool()
async def get_stale_knowledge(days: int = 30, limit: int = 20) -> str:
    """列出超过指定天数未复习的知识条目。

    用途：定期检查哪些经验/决策需要复习或归档。
    注意：如果想直接归档某条，用 archive_knowledge。如果确认仍有效，用 review_knowledge 刷新。

    Args:
        days: 超过多少天算过期（默认 30）。
        limit: 最多返回多少条（默认 20）。
    """
    return _json(_engram.get_stale_knowledge(days=days, limit=limit))


@mcp.tool()
async def merge_knowledge(primary_id: str, secondary_id: str) -> str:
    """Merge secondary knowledge item into primary.

    Transfers related_ids from secondary to primary, then archives secondary.
    Use after find_similar_knowledge identifies a duplicate.

    Args:
        primary_id: ID of the item to keep (its content is preserved).
        secondary_id: ID of the item to merge in (will be archived).
    """
    return _json(_engram.merge_knowledge(primary_id, secondary_id))


@mcp.tool()
async def link_knowledge(id_a: str, id_b: str) -> str:
    """Create a bidirectional link between two knowledge items (lessons or decisions)."""
    return _json(_engram.link_knowledge(id_a, id_b))


@mcp.tool()
async def unlink_knowledge(id_a: str, id_b: str) -> str:
    """Remove the bidirectional link between two knowledge items."""
    return _json(_engram.unlink_knowledge(id_a, id_b))


@mcp.tool()
async def update_identity(field: str, updates_json: str) -> str:
    """Update an identity field.

    Args:
        field: One of: profile | preferences | trust_boundaries | work_style | quality_standards
        updates_json: JSON string with the fields to update.

    Field-specific keys:
        profile: role, language, technical_level, description
        preferences: work_patterns (dict), communication (str), tool_preferences (dict)
        trust_boundaries: default_sharing, tool_access, private_fields, restricted_fields
        work_style: preferences (dict), communication (str)
        quality_standards: acceptance_threshold (1-5), rules (list)
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
    dispatch[field](updates)
    return _json({"success": True, "field": field, "updated_keys": list(updates.keys())})


@mcp.tool()
async def save_project_snapshot(project_folder: str, data_json: str) -> str:
    """写入/更新项目的知识快照（写操作，不是读取）。

    用途：保存或更新当前项目的技术栈、已知问题、注释等信息。
    注意：读取项目快照用 get_project_context，不是本工具。

    Args:
        project_folder: 项目文件夹路径。
        data_json: JSON 字符串，支持字段: title, tech_stack, known_issues, notes。
    """
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return "错误: data_json 必须是合法的 JSON。"
    _engram.save_project_snapshot(project_folder, data)
    return f"项目快照已保存: {project_folder}"


# ===========================================================================
# WEB CONTENT TOOL (1)
# ===========================================================================


@mcp.tool()
async def read_web_content(url: str) -> str:
    """读取网页/视频/文章的文本内容（通过 Engram Reader 本地服务）。

    支持：YouTube字幕、B站、公众号文章、知乎、通用网页。
    需要 Engram Reader 本地服务运行中 (localhost:7890)。

    Args:
        url: 要提取内容的网页链接。
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
    """导出整个 Engram 为单一备份文件。

    用于：备份、迁移到另一台机器、跨设备同步。
    导出包含全部身份、知识、项目数据。

    Args:
        output_path: 导出路径（可选，默认存到 ~/.engram/exports/engram_backup_<日期>.json）。
    """
    try:
        path = _engram.export_all(output_path)
        return f"导出成功: {path}"
    except Exception as e:
        return f"导出失败: {e}"


@mcp.tool()
async def import_engram(input_path: str, merge: bool = True) -> str:
    """从备份文件导入 Engram 数据。

    用于：从备份恢复、从另一台机器迁移数据。

    Args:
        input_path: 备份文件路径（export_engram 生成的 JSON 文件）。
        merge: True=合并模式（保留已有数据，追加新数据），False=覆盖模式。
    """
    result = _engram.import_all(input_path, merge=merge)
    return _json(result)


@mcp.tool()
async def export_engram_to_openclaw(output_dir: str = "") -> str:
    """导出 Engram 为 OpenClaw 兼容格式（SOUL.md + MEMORY.md + USER.md）。"""
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
    """从 OpenClaw 格式导入数据到 Engram（SOUL.md/MEMORY.md/USER.md）。"""
    try:
        result = import_from_openclaw(_engram, soul_path, memory_path, user_path)
        return _json(result)
    except Exception as e:
        return f"从 OpenClaw 兼容格式导入失败: {e}"


@mcp.tool()
async def get_audit_log(limit: int = 50) -> str:
    """Get recent audit log entries.

    Args:
        limit: Max entries to return (default 50, most recent first).
    """
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
    """会话结束一键收尾：自动提取知识 + 保存项目快照。

    用途：一次对话结束时调用，把会话摘要交给 Engram。它会：
    1. 从摘要中自动提取 lessons 和 decisions（等同于 extract_session_insights）
    2. 如果提供了 project_folder，同时更新项目快照

    Args:
        summary: 会话摘要（自由文本，段落或要点列表均可）。
        project_folder: 项目文件夹路径（可选，不填则只提取知识不保存快照）。
        source_tool: 调用来源工具，如 'claude_code', 'codex'。
        project_title: 项目名称（可选，仅在首次保存快照时需要）。
        tech_stack: 技术栈（可选，逗号分隔）。
        known_issues: 已知问题（可选，逗号分隔）。
    """
    results = {}

    # Step 1: Extract insights
    insights = _engram.extract_session_insights(summary, source_tool=source_tool)
    results["insights"] = insights

    # Step 2: Save project snapshot (if project_folder provided)
    if project_folder:
        snapshot_data: dict = {}
        if project_title:
            snapshot_data["title"] = project_title
        if tech_stack:
            snapshot_data["tech_stack"] = [s.strip() for s in tech_stack.split(",") if s.strip()]
        if known_issues:
            snapshot_data["known_issues"] = [s.strip() for s in known_issues.split(",") if s.strip()]
        _engram.save_project_snapshot(project_folder, snapshot_data)
        results["project_snapshot"] = {"saved": True, "folder": project_folder}

    return _json(results)


@mcp.tool()
async def start_project(
    description: str,
    project_folder: str,
    project_title: str = "",
    tech_stack: str = "",
    limit: int = 10,
) -> str:
    """新项目一键启动：继承跨项目经验 + 建立项目档案。

    用途：开始一个新项目时调用，一次拿到过往相关经验并初始化项目记录。它会：
    1. 根据项目描述从已有知识中找最相关的 lessons 和 decisions（等同于 get_knowledge_inheritance）
    2. 自动创建项目快照（等同于 save_project_snapshot）

    注意：如果只想获取可继承的经验、不需要创建项目档案，请直接用 get_knowledge_inheritance。

    Args:
        description: 新项目的自由文本描述（用于匹配已有知识）。
        project_folder: 项目文件夹路径。
        project_title: 项目名称（可选）。
        tech_stack: 技术栈（可选，逗号分隔）。
        limit: 最多继承多少条经验（默认 10，上限 20）。
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
    """用户身份画像。"""
    return _json(_engram.get_profile())


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

        print(f"Engram MCP server (SSE) on http://{args.host}:{args.port}/sse")

        starlette_app = mcp.sse_app()
        starlette_app.add_middleware(TokenAuthMiddleware, token=token)

        import uvicorn
        uvicorn.run(starlette_app, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")
