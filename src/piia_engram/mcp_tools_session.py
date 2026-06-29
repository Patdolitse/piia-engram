"""Session MCP tools (agent context, wrap-up, daily log)."""
from __future__ import annotations

import json

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]

@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="save_agent_context")
    if refusal is not None:
        return refusal

    S._session.detect_tool(tool)
    if project_folder:
        S._session.detect_project(project_folder)
    actions = None
    if actions_json:
        try:
            parsed = json.loads(actions_json)
            if isinstance(parsed, list):
                actions = parsed
        except json.JSONDecodeError:
            pass
    result = S._locked_engram_call(
        S._get_engram().save_agent_context,
        tool=tool,
        content=content,
        session_id=session_id,
        project_folder=project_folder,
        actions=actions,
    )
    S._track("save_agent_context", success=True)
    return S._json(result)


@S.mcp.tool()
async def get_recent_context(
    tool: str = "",
    project_folder: str = "",
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
    effective_project = project_folder or S._session.project_folder
    if effective_project:
        S._session.detect_project(effective_project)
    sessions = S._get_engram().get_recent_context(
        tool=tool,
        project_folder=effective_project,
        limit=limit,
    )
    sessions = S._gov_rt.maybe_govern_list(
        S._get_engram().root, sessions, tool="get_recent_context"
    )
    S._track("get_recent_context", success=True)
    if not sessions:
        return S._json({"message": "没有找到保存的上下文记录。", "sessions": []})
    return S._json({"sessions": sessions})


@S.mcp.tool()
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
    sessions = S._get_engram().list_agent_sessions(tool=tool, limit=limit)
    S._track("list_agent_sessions", success=True)
    return S._json({"sessions": sessions, "total": len(sessions)})


@S.mcp.tool()
async def get_resume_brief(
    project_folder: str = "",
    token_budget: int = 2000,
    include_resume_pack: bool = False,
) -> str:
    """跨会话/跨工具接续简报（v3.30 新增）。 / Cross-session, cross-tool resume brief.

    **用途：v3.30 行业首家"一次调用拿到完整接续简报"的高层 API。**
    用户切换工具（Claude Code → Codex/Cursor）或开新对话时，AI 调用本工具
    一次即可拿到：用户身份 + 当前项目状态 + 今日日志 + 最近会话上下文 +
    最近经验/决策 + 建议阅读的项目文档清单。结果用
    ``<engram-resume priority="high">`` XML 标签包裹，提示客户端 AI 优先遵守。

    Purpose (v3.30): the "what does the next AI need to know in 30 seconds"
    high-level endpoint. When users switch tools or open a new chat,
    calling this once returns identity + project state + today's daily log
    + recent context + top lessons/decisions + suggested project docs to
    read. Result is wrapped in ``<engram-resume priority="high">`` so client
    AIs (Claude Code additionalContext, Codex system prompt, etc.) treat
    it as high-priority reference context.

    Lifecycle: **session start** — call before the first user message in a
    new session when continuing prior work, or whenever the user says
    things like "接着上次", "继续之前", "what were we doing".

    Args:
        project_folder: 项目文件夹路径（可选）。留空只返回身份卡。 /
            Project folder (optional). Empty returns identity-only.
        token_budget: 输出 token 软上限（默认 2000，约 8000 字符）。 /
            Soft cap for output tokens (default 2000 ≈ 8000 chars).
        include_resume_pack: Include structured ``project_resume_pack.v1`` in
            the JSON response. Defaults to false to preserve existing output.
    """
    # Auto-bootstrap on first ever call when store is empty.
    from piia_engram.bootstrap import needs_bootstrap, run_bootstrap

    if needs_bootstrap(S._get_engram()):
        run_bootstrap(S._get_engram())

    brief = S._get_engram().get_resume_brief(
        project_folder=project_folder,
        token_budget=token_budget,
        include_resume_pack=include_resume_pack,
    )
    # a2: embed caller permissions into the brief's markdown body
    # (same pattern as a1 in get_user_context, but targeting the dict)
    perms = S._gov_rt.describe_caller_permissions(S._get_engram().root)
    perm_section = S._format_permissions_section(perms)
    if isinstance(brief, dict) and "markdown" in brief:
        md = brief["markdown"]
        close_tag = "</engram-resume>"
        if close_tag in md:
            brief["markdown"] = md.replace(
                close_tag, perm_section + "\n" + close_tag, 1
            )
        else:
            brief["markdown"] = md + perm_section
    # Resume brief bundles top lessons/decisions + recent context; owner-only.
    brief = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, brief, tool="get_resume_brief"
    )
    S._track("get_resume_brief", success=True)
    return S._json(brief)


@S.mcp.tool()
async def get_recall(
    project_folder: str = "",
    query: str = "",
    limit: int = 8,
    token_budget: int = 2000,
    include_freshness: bool = True,
    collapse_versions: bool = True,
) -> str:
    """获取结构化 Recall Surface v1 载荷。 / Get a structured Recall Surface v1 payload.

    用途：在新任务、跨工具接续或需要"一次拿到可执行记忆包"时调用。
    返回身份摘要、最近活动、项目/查询相关知识和治理元数据。
    Purpose: Call when a new task or cross-tool handoff needs one structured,
    actionable memory bundle: identity slice, recent activity, relevant
    knowledge, and governance metadata.

    注意：该聚合视图可能组合多类知识和最近上下文，因此治理开启时仅 owner
    (private-self) 可读；非 owner 会在读取前被拒绝，不触发搜索或遥测写入。
    Note: Because this aggregate view can combine multiple knowledge classes and
    recent context, it is owner-only when governance is enabled. Non-owners are
    refused before any search or telemetry side effect runs.

    Args:
        project_folder: 项目文件夹路径（可选）。 / Project folder path (optional).
        query: 可选搜索焦点。 / Optional search focus.
        limit: 最多返回多少条知识（默认 8，上限 20）。 / Max knowledge items (default 8, max 20).
        token_budget: 知识片段的粗略 token 预算（默认 2000）。 / Rough token budget for knowledge items.
        include_freshness: 是否附加 freshness 提示。 / Attach freshness hints.
        collapse_versions: 是否折叠版本链到当前 HEAD。 / Collapse version chains to current heads.
    """
    try:
        is_owner = S._gov_rt.caller_is_owner(S._get_engram().root)
    except Exception:
        is_owner = False
    if not is_owner:
        return S._gov_rt.maybe_govern_owner_only(S._get_engram().root, "", tool="get_recall")

    try:
        if project_folder:
            S._session.detect_project(project_folder)
        safe_limit = max(1, min(int(limit), 20))
        safe_budget = max(0, int(token_budget))
        payload = S._recall_service.gather_recall(
            S._get_engram(),
            project_folder=project_folder,
            query=query,
            limit=safe_limit,
            token_budget=safe_budget,
            include_freshness=include_freshness,
            collapse_versions=collapse_versions,
            include_trust=True,
            # Owner gate decides whether this aggregate view may be read at all.
            # When governance is on, role-scoped memory then narrows which
            # sensitivity tiers the owner-context call may project.
            role_scoped_memory=S._gov_rt.governance_enabled(),
        )
        # First-value funnel (opt-in, content-blind). After the owner gate, so a
        # refused non-owner read never reaches here. current_tool drives the
        # cross-tool relation (computed locally; the tool pair is never recorded).
        try:
            from . import telemetry as _tel

            S._recall_service.record_recall_funnel(
                payload, current_tool=_tel.current_tool_label(), surface="mcp"
            )
        except Exception:
            pass
        perms = S._gov_rt.describe_caller_permissions(S._get_engram().root)
        meta = payload.setdefault("meta", {})
        governance = meta.setdefault("governance", {})
        if isinstance(governance, dict):
            governance["trust_level"] = perms.get("trust_level", governance.get("trust_level"))
        meta["_caller_permissions"] = perms
        S._track("get_recall", success=True)
    except Exception as exc:
        S._track("get_recall", success=False)
        return S._json({"error": f"recall failed: {S._safe_err(exc)}"})
    return S._json(payload)


@S.mcp.tool()
async def preview_context_governance(
    mode: str,
    payload_json: str = "{}",
    options_json: str = "{}",
) -> str:
    """Build a local context-governance preview; proposal-only, no apply.

    Owner-gated aggregate read surface: when governance is enabled, non-owner
    callers are refused before any store-backed mode can gather knowledge.

    Modes:
    - safe_context: redact/trim a supplied payload, or gather recall if payload is empty.
    - freshness_conflicts: scan local lessons/decisions and return metadata-only review proposals.
    - replay_packet: build a redacted context-compression replay packet from supplied summary.
    - external_evidence: render a local Markdown evidence draft from supplied evidence rows.

    Args:
        mode: One of safe_context, freshness_conflicts, replay_packet, external_evidence.
        payload_json: Optional JSON object. For external_evidence, use {"evidence": [...]}.
        options_json: Optional JSON object for mode-specific options such as max_chars, lockdown, compact_summary, title, project_folder, query, limit, or token_budget.
    """
    try:
        is_owner = S._gov_rt.caller_is_owner(S._get_engram().root)
    except Exception:
        is_owner = False
    if not is_owner:
        return S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root,
            "",
            tool="preview_context_governance",
        )

    def _parse_object(raw: str, field: str) -> dict:
        if not raw or raw == "{}":
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a valid JSON object: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} must be a JSON object")
        return parsed

    try:
        payload = _parse_object(payload_json, "payload_json")
        options = _parse_object(options_json, "options_json")
        result = S._context_governance.build_context_governance_preview(
            mode,
            engram=S._get_engram(),
            payload=payload,
            options=options,
        )
        S._track("preview_context_governance", success=True)
    except Exception as exc:
        S._track("preview_context_governance", success=False)
        return S._json({"error": f"context governance preview failed: {S._safe_err(exc)}"})
    return S._json(result)


@S.mcp.tool()
async def get_daily_log(
    project_folder: str,
    date: str = "",
) -> str:
    """读取项目的每日日志（人类可读的会话时间线）。 / Read a project's daily log (human-readable session timeline).

    用途：v3.30 新增——每次 wrap_up_session 会在 ``~/.engram/daily/<pid>/<YYYY-MM-DD>.md``
    追加一条带时间戳的条目（含 lesson/decision/playbook 计数 + 摘要前 600 字符）。
    新会话需要"上次到底干了什么"的快速概览时调本工具，比 search_knowledge 更直观。
    Purpose (v3.30): every wrap_up_session appends a timestamped entry to
    ``~/.engram/daily/<pid>/<YYYY-MM-DD>.md`` with a lesson/decision tally
    and the first ~600 chars of the summary. Call this when a new session
    needs a glance-able "what happened today" timeline — faster than
    search_knowledge for recall.

    Args:
        project_folder: 项目文件夹路径。 / Project folder path.
        date: ISO 日期 ``YYYY-MM-DD``（可选，默认今天）。 / ISO date (optional, defaults to today).
    """
    log = S._get_engram().get_daily_log(
        project_folder=project_folder,
        date=date or None,
    )
    # Daily log embeds per-session summaries (first ~600 chars); owner-only.
    log = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, log, tool="get_daily_log"
    )
    S._track("get_daily_log", success=True)
    return S._json(log)


# Apply tool tier filter AFTER all @mcp.tool() decorators have run
S._apply_tool_tier()


# ===========================================================================
# Entry point
# ===========================================================================
