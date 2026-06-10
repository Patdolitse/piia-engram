"""Read-path MCP tools (context, recall, knowledge queries)."""
from __future__ import annotations

from typing import Optional
import json

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]

@S.mcp.tool()
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
        user_prompt: 用户当前提问（可选）。传入后会追加到上下文末尾，并与已存 Playbook 的
            triggers 关键词匹配，命中时浮现「相关 Playbook」小节（标题 + ID；用 get_playbook 查看完整步骤）。
            Optional current user prompt. Appended to the context and matched against stored
            playbook trigger keywords; hits surface a "Matched Playbooks" section (title + id;
            call get_playbook for the full steps).
    """
    if project_folder:
        S._session.detect_project(project_folder)
    # Owner-only gate evaluated BEFORE any side effect. Cold-start context is a
    # rendered string bundling identity + top lessons/decisions + snapshot —
    # unfilterable by field, hence owner-only. A non-owner (low-trust) caller
    # must receive the refusal WITHOUT us generating the context, recording
    # telemetry, or emitting a cold_start beta event: otherwise a low-trust
    # read would still land a write on disk (beta_events.jsonl), the recurring
    # "side-effect-before-govern" bug class. Owner-OFF governance → True here,
    # so the normal path is unchanged when governance is disabled.
    if not S._gov_rt.caller_is_owner(S._engram.root):
        return S._gov_rt.maybe_govern_owner_only(
            S._engram.root, "", tool="get_user_context"
        )
    try:
        context = S._engram.generate_context(
            project_folder, level=level, max_tokens=token_budget,
        )
        S._track("get_user_context", success=True)
        S._beta("cold_start", level=level)
    except Exception as exc:
        S._track("get_user_context", success=False)
        S.logger.warning("generate_context failed: %s", exc)
        return f"Engram 上下文加载失败: {S._safe_err(exc)}"
    if not context:
        # Auto-bootstrap: if discoverable rule files exist (CLAUDE.md, AGENTS.md,
        # .cursorrules), import them now so the user gets "it already knows me"
        # without needing to run `engram setup` first.
        from piia_engram.bootstrap import needs_bootstrap, run_bootstrap

        if needs_bootstrap(S._engram):
            boot = run_bootstrap(S._engram)
            if boot.get("user_rules_imported") or boot.get("project_rules_imported"):
                # Re-generate context with the freshly imported data.
                try:
                    context = S._engram.generate_context(
                        project_folder, level=level, max_tokens=token_budget,
                    )
                except Exception:
                    pass
                if context:
                    n = boot["user_rules_imported"] + boot["project_rules_imported"]
                    return (
                        f"[首次连接自动导入 {n} 条规则 from "
                        f"CLAUDE.md/AGENTS.md]\n\n{context}"
                    )

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

        # "Playbook finds you": match the prompt against stored playbook
        # triggers so relevant playbooks surface on the first message instead
        # of waiting for the AI to remember get_playbooks. Best-effort — a
        # matching failure must never break cold start. Reads use
        # _update_access=False: surfacing is not usage, so access stats and
        # last_reviewed stay untouched.
        try:
            from piia_engram.playbook_match import (
                match_playbooks,
                render_matched_section,
            )

            candidates = S._engram.get_playbooks(
                limit=S._PLAYBOOK_MATCH_SCAN_LIMIT,
                project_folder=project_folder or S._session.project_folder or None,
                _update_access=False,
            )
            matches = match_playbooks(user_prompt, candidates, limit=2)
            section = render_matched_section(matches, lang=S._user_lang())
            # Lowest-priority section: drop it entirely rather than crowd out
            # identity/prompt content when a token budget is set.
            if section and (
                token_budget is None
                or (len(context) + len(section)) // 3 <= token_budget
            ):
                context += section
        except Exception as exc:
            S.logger.warning("playbook trigger matching failed: %s", exc)

    # a1: embed caller permissions so the AI tool knows its trust boundary
    # from the first message. The section is appended BEFORE the governance
    # gate so owner callers see it in the full context; non-owner callers
    # get the gate's refusal string (they can use get_permission_profile).
    perms = S._gov_rt.describe_caller_permissions(S._engram.root)
    context += S._format_permissions_section(perms)

    # Cold-start context is a rendered string bundling identity + top
    # lessons/decisions + snapshot — unfilterable by field. Gate owner-only.
    return S._gov_rt.maybe_govern_owner_only(
        S._engram.root, context, tool="get_user_context"
    )


@S.mcp.tool()
async def refresh_quick_context(level: str = "standard") -> str:
    """刷新本地 `quick_context.md` 快照（跨工具 / 离线场景的快速通路）。 / Refresh the local quick_context.md snapshot (cross-tool / offline fast path).

    Owner/export surface: writes ~/.engram/quick_context.md and is refused for non-owner callers when governance is enabled.

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
    # quick_context.md embeds lesson summaries + decision text from
    # generate_context (Codex round-17 P1-1). The path-only return is governed,
    # but the FILE lands on disk for any caller — same two-step exfil as
    # export_engram. Gate BEFORE writing: a non-owner gets a refusal and no
    # snapshot file is produced.
    refusal = S._gov_rt.maybe_refuse_export(S._engram.root, tool="refresh_quick_context")
    if refusal is not None:
        return refusal
    try:
        path = S._engram.refresh_quick_context(level=level)
        S._track("refresh_quick_context", success=True)
        return f"已写入快照: {path} (level={level})"
    except Exception as exc:
        S._track("refresh_quick_context", success=False)
        S.logger.warning("refresh_quick_context failed: %s", exc)
        return f"快照写入失败: {S._safe_err(exc)}"


@S.mcp.tool()
async def get_identity_card() -> str:
    """导出用户的可携带 AI 身份卡（Markdown 格式）。 / Export the user's portable AI identity card as Markdown.

    Owner/export surface: writes exports/identity_card.md and is refused for non-owner callers when governance is enabled.

    用途：需要把用户身份、工作方式、质量标准、经验教训分享给其它 AI 工具时调用。
    Purpose: Call when another AI tool needs a self-contained summary of the user's identity, work style, quality standards, and lessons.

    注意：如果本会话只需要运行时上下文，用 get_user_context 更合适。
    Note: If the current session only needs runtime context, get_user_context is usually the better choice.
    """
    # export_identity_card embeds lesson summaries + decision text verbatim AND
    # writes exports/identity_card.md to disk (Codex round-16 P1-2 disproved the
    # allowlist exemption; round-17 P1-2 showed gating only the RETURN still
    # leaks the file). Gate BEFORE the writer runs: a non-owner gets a refusal
    # and no identity_card.md is produced. Owner gets the full card.
    refusal = S._gov_rt.maybe_refuse_export(S._engram.root, tool="get_identity_card")
    if refusal is not None:
        return refusal
    try:
        card = S._engram.export_identity_card()
        S._track("get_identity_card", success=True)
    except Exception as exc:
        S._track("get_identity_card", success=False)
        S.logger.warning("export_identity_card failed: %s", exc)
        return f"身份卡生成失败: {S._safe_err(exc)}"
    if not card:
        return "身份卡为空——尚未积累足够的知识。"
    return card


@S.mcp.tool()
async def get_profile(safe: bool = True) -> str:
    """获取用户身份画像。 / Get the user's identity profile.

    用途：需要读取角色、语言、技术水平、简介等用户画像字段时调用。
    Purpose: Call when you need profile fields such as role, language, technical level, or description.

    注意：默认遵守 trust_boundaries.restricted_fields 过滤敏感字段。设 safe=False 仅在用户明确要求时使用。
    Note: Respects trust_boundaries.restricted_fields by default. Set safe=False only when the user explicitly requests full profile access.

    Args:
        safe: 默认 True，按 trust_boundaries 过滤敏感字段。 / Default True; filters sensitive fields per trust_boundaries.
    """
    return S._json(S._engram.get_profile(safe=safe))


@S.mcp.tool()
async def get_work_style() -> str:
    """获取用户的工作偏好（工作模式、节奏、沟通风格）。 / Get the user's work style preferences: patterns, pace, and communication style.

    Deprecated compatibility read: prefer get_preferences for new callers.

    用途：需要单独读取旧版 work_style 偏好时调用。
    Purpose: Call when you specifically need the legacy work_style preference object.

    注意：新版偏好优先使用 get_preferences。
    Note: Prefer get_preferences for the newer preferences model.
    """
    return S._json(S._engram.get_work_style())


@S.mcp.tool()
async def get_preferences() -> str:
    """获取用户的工作偏好（v2.0，含工具偏好、工作模式、沟通风格）。 / Get the user's v2.0 preferences, including tool preferences, work patterns, and communication style.

    用途：需要读取用户如何协作、喜欢哪些工具、偏好什么工作方式时调用。
    Purpose: Call when you need to understand how the user collaborates, which tools they prefer, and how they like work to be done.

    注意：如果只需要完整冷启动上下文，用 get_user_context。
    Note: Use get_user_context when you need the full cold-start context rather than preferences alone.
    """
    return S._json(S._engram.get_preferences())


@S.mcp.tool()
async def get_trust_boundaries() -> str:
    """获取数据信任边界（哪些工具可以访问哪些 Engram 数据）。 / Get data trust boundaries that define which tools may access which Engram data.

    用途：需要判断敏感字段、共享边界或工具访问权限时调用。
    Purpose: Call when you need to inspect sensitive fields, sharing boundaries, or tool access permissions.

    注意：普通上下文读取会自动遵守安全画像逻辑；不要用本工具绕过隐私边界。
    Note: Normal context reads already respect safe-profile behavior; do not use this tool to bypass privacy boundaries.
    """
    return S._json(S._engram.get_trust_boundaries())


@S.mcp.tool()
async def get_quality_standards() -> str:
    """获取用户的质量标准和验收条件。 / Get the user's quality standards and acceptance criteria.

    用途：需要知道用户如何判断工作是否完成、测试和证据要求有多严格时调用。
    Purpose: Call when you need to know how the user judges completion, tests, evidence, and acceptance quality.

    注意：冷启动时 get_user_context 通常已经包含关键质量标准。
    Note: get_user_context usually includes the key quality standards during cold start.
    """
    return S._json(S._engram.get_quality_standards())


@S.mcp.tool()
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
    # Read-path side-effect gate (Codex round-6): only the owner's reads record
    # access bookkeeping. A non-owner read must NOT bump access_count /
    # last_reviewed — that is a low-trust write to data files, and (since the
    # bump happens before governance filtering) it would also touch entries
    # above the caller's sensitivity ceiling.
    lessons = S._engram.get_lessons(
        domain=domain, source_tool=source_tool, limit=limit,
        _update_access=S._gov_rt.caller_is_owner(S._engram.root),
    )
    lessons = S._gov_rt.maybe_govern_list(S._engram.root, lessons, tool="get_lessons")
    if not lessons:
        return "尚无经验教训记录。"
    return S._json(lessons)


@S.mcp.tool()
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
    # Read-path side-effect gate (Codex round-6): owner-only access bookkeeping.
    decisions = S._engram.get_decisions(
        limit=limit,
        source_tool=source_tool,
        project=project,
        domain=domain,
        _update_access=S._gov_rt.caller_is_owner(S._engram.root),
    )
    decisions = S._gov_rt.maybe_govern_list(S._engram.root, decisions, tool="get_decisions")
    if not decisions:
        return "尚无决策记录。"
    return S._json(decisions)


@S.mcp.tool()
async def get_domains() -> str:
    """获取用户的技术领域经验图谱。 / Get the user's technical domain experience map.

    用途：查看用户在哪些技术、领域或主题上积累了经验。
    Purpose: Call to see which technologies, domains, or topics the user has experience in.

    注意：如果要读取某个领域里的具体经验，用 get_lessons(domain=...) 或 search_knowledge。
    Note: To read concrete knowledge within a domain, use get_lessons(domain=...) or search_knowledge.
    """
    domains = S._engram.get_domains()
    if not domains:
        return "尚无领域经验记录。"
    return S._json(domains)


@S.mcp.tool()
async def get_project_context(project_folder: str) -> str:
    """读取特定项目的知识快照（项目级，只含该项目的历史）。 / Read the knowledge snapshot for a specific project, containing only that project's history.

    用途：想了解某个项目之前的技术栈、已知问题、协作次数时调用。
    Purpose: Call when you need a project's previous tech stack, known issues, notes, or collaboration history.

    注意：如果想获取用户级完整身份上下文，用 get_user_context；如果想写入项目快照，用 save_project_snapshot。
    Note: Use get_user_context for full user-level context; use save_project_snapshot to write a project snapshot.

    Args:
        project_folder: 项目文件夹路径。 / Project folder path.
    """
    S._session.detect_project(project_folder)
    try:
        snapshot = S._engram.get_project_snapshot(project_folder)
        snapshot = S._gov_rt.maybe_govern_one(
            S._engram.root, snapshot, tool="get_project_context"
        )
        S._track("get_project_context", success=True)
    except Exception as exc:
        S._track("get_project_context", success=False)
        raise
    if not snapshot:
        return f"未找到项目知识记录: {project_folder}"
    return S._json(snapshot)


@S.mcp.tool()
async def list_projects() -> str:
    """列出用户参与过的所有项目及基本信息。 / List all projects the user has worked on with basic metadata.

    用途：需要发现已有项目记录、确认项目路径或查看项目清单时调用。
    Purpose: Call when discovering saved project records, confirming project paths, or reviewing the project list.

    注意：读取单个项目详情用 get_project_context。
    Note: Use get_project_context to read details for one project.
    """
    projects = S._engram.list_projects()
    if not projects:
        return "尚无项目记录。"
    return S._json(projects)


@S.mcp.tool()
async def get_relevant_knowledge(
    project_folder: str, limit: int = 8, include_freshness: bool = False
) -> str:
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
        include_freshness: 为每条结果附加 freshness/新鲜度提示（默认 False，保持旧输出不变）。 / Attach a per-item freshness hint (default False; output is unchanged when omitted).
    """
    try:
        lessons = S._engram.get_relevant_lessons(
            project_folder=project_folder, limit=limit,
            _update_access=S._gov_rt.caller_is_owner(S._engram.root),
        )
        # governance gate (opt-in; OFF => byte-identical to the line above).
        lessons = S._gov_rt.maybe_govern_list(
            S._engram.root, lessons, tool="get_relevant_knowledge"
        )
        # Freshness annotation is opt-in and applied AFTER governance filtering so
        # it can only ever annotate items the caller is already allowed to see
        # (Provenance & Freshness Contract v1, follow-up B). Pure/non-destructive.
        if include_freshness:
            lessons = S._provenance.annotate_freshness(lessons)
        S._track("get_relevant_knowledge", success=True)
    except Exception as exc:
        S._track("get_relevant_knowledge", success=False)
        raise
    perms = S._gov_rt.describe_caller_permissions(S._engram.root)
    if not lessons:
        return S._json({"items": [], "_caller_permissions": perms,
                       "note": "尚无相关经验教训。"})
    return S._json({"items": lessons, "_caller_permissions": perms})


@S.mcp.tool()
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
    pack = S._engram.get_knowledge_inheritance(description, limit=limit)
    pack = S._gov_rt.maybe_govern_result(
        S._engram.root, pack, tool="get_knowledge_inheritance", list_fields=("items",)
    )
    return S._json(pack)


@S.mcp.tool()
async def search_knowledge(query: str, scope: str = "all", limit: int = 10,
                           filters_json: str = "", project_folder: str = "",
                           include_freshness: bool = False) -> str:
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
        include_freshness: Attach a per-item freshness hint (fresh/aging/stale)
            to each returned item. Default False keeps the response unchanged.
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
        if project_folder:
            S._session.detect_project(project_folder)
        effective_project = project_folder or S._session.project_folder or None
        # The hybrid (ENGRAM_SEARCH=hybrid) path rebuilds the FULL active corpus
        # into <root>/search_index.db BEFORE we govern the return — a non-owner
        # would get an empty/filtered response yet leave the secret bodies
        # readable in the FTS index file (Codex round-19 file-side-effect leak).
        # Suppress that persisted index for non-owners; caller_is_owner is True
        # when governance is OFF, so the disabled/owner path is unchanged.
        allow_index = S._gov_rt.caller_is_owner(S._engram.root)
        result = S._engram.search_knowledge(
            query, scope=scope, limit=limit, filters=filters,
            allow_hybrid_index=allow_index,
            project_folder=effective_project,
        )
        # governance gate (opt-in; OFF => byte-identical to the line above).
        result = S._gov_rt.maybe_govern_buckets(S._engram.root, result, tool="search_knowledge")
        # Opt-in freshness annotation, applied AFTER governance filtering so it
        # only ever annotates items the caller may already see (Provenance &
        # Freshness Contract v1, follow-up B). Pure/non-destructive; default OFF
        # keeps the response byte-identical.
        if include_freshness and isinstance(result, dict):
            for _bucket in ("lessons", "decisions", "playbooks"):
                items = result.get(_bucket)
                if isinstance(items, list):
                    result[_bucket] = S._provenance.annotate_freshness(items)
        if isinstance(result, dict):
            playbooks = result.get("playbooks")
            if isinstance(playbooks, list):
                for item in playbooks:
                    S._inject_usage_policy(item)
        S._track("search_knowledge", success=True)
    except Exception as exc:
        S._track("search_knowledge", success=False)
        return f"搜索失败: {S._safe_err(exc)}"
    perms = S._gov_rt.describe_caller_permissions(S._engram.root)
    result["_caller_permissions"] = perms
    return S._json(result)


@S.mcp.tool()
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
    overview = S._engram.get_knowledge_overview(section, stale_days=stale_days)
    # digest embeds FULL top_lessons/top_decisions plus label-stripped preview
    # rows nested several levels down (recent_items, stale.{lessons,decisions});
    # field-by-field gating across those derived rows is error-prone and loses
    # the original sensitivity label. Gate the aggregate view owner-only.
    overview = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, overview, tool="get_knowledge_overview"
    )
    return S._json(overview)


@S.mcp.tool()
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
    merges = S._engram.suggest_merges(threshold=threshold, limit=limit)
    # Each suggestion embeds item summaries from a full-library scan; gate the
    # aggregate maintenance view owner-only.
    merges = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, merges, tool="suggest_merges"
    )
    return S._json(merges)


@S.mcp.tool()
async def classify_legacy_playbooks(project_folders_json: str = "[]") -> str:
    """Dry-run classification suggestions for legacy Playbook scopes.

    This scans existing Playbooks and known projects, then returns a reviewable
    migration plan. It does not mutate stored Playbooks.
    """
    project_folders = None
    if project_folders_json and project_folders_json != "[]":
        try:
            parsed = json.loads(project_folders_json)
        except json.JSONDecodeError:
            return "project_folders_json must be a valid JSON array"
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            return "project_folders_json must be a JSON array of strings"
        project_folders = parsed
    result = S._engram.classify_legacy_playbooks(project_folders=project_folders)
    # Full-library maintenance view: suggestions include playbook titles and
    # project evidence, so only the owner should see the aggregate report.
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="classify_legacy_playbooks"
    )
    return S._json(result)


@S.mcp.tool()
async def apply_legacy_playbook_scope_suggestions(
    project_folders_json: str = "[]",
    playbook_ids_json: str = "[]",
    min_confidence: float = 0.7,
    dry_run: bool = True,
    confirm: bool = False,
) -> str:
    """Apply high-confidence legacy Playbook project/global scope suggestions.

    Owner/admin surface: reorganizes stored Playbook metadata and is refused for non-owner callers when governance is enabled.

    Default mode is preview-only. Actual writes require ``dry_run=False`` and
    ``confirm=True`` and are owner-only because this reorganizes stored
    Playbook metadata across the whole corpus.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(
        S._engram.root, tool="apply_legacy_playbook_scope_suggestions"
    )
    if refusal is not None:
        return refusal

    def _parse_optional_string_list(raw: str, field: str) -> list[str] | None | str:
        if not raw or raw == "[]":
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return f"{field} must be a valid JSON array"
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            return f"{field} must be a JSON array of strings"
        return parsed

    project_folders = _parse_optional_string_list(
        project_folders_json, "project_folders_json"
    )
    if isinstance(project_folders, str):
        return project_folders
    playbook_ids = _parse_optional_string_list(playbook_ids_json, "playbook_ids_json")
    if isinstance(playbook_ids, str):
        return playbook_ids

    try:
        result = S._engram.apply_legacy_playbook_scope_suggestions(
            project_folders=project_folders,
            playbook_ids=playbook_ids,
            min_confidence=min_confidence,
            dry_run=dry_run,
            confirm=confirm,
        )
        S._track("apply_legacy_playbook_scope_suggestions", success=True)
    except Exception as exc:
        S._track("apply_legacy_playbook_scope_suggestions", success=False)
        return f"Apply legacy Playbook scope suggestions failed: {S._safe_err(exc)}"
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="apply_legacy_playbook_scope_suggestions"
    )
    return S._json(result)


@S.mcp.tool()
async def rollback_playbook_scope_migration(
    playbook_ids_json: str = "[]",
    dry_run: bool = True,
    confirm: bool = False,
) -> str:
    """Rollback the latest Playbook scope migration for selected Playbooks.

    Owner/admin surface: rewrites Playbook scope metadata and is refused for non-owner callers when governance is enabled.

    Default mode is preview-only. Actual rollback requires ``dry_run=False``
    and ``confirm=True`` and is owner-only.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(
        S._engram.root, tool="rollback_playbook_scope_migration"
    )
    if refusal is not None:
        return refusal

    playbook_ids = None
    if playbook_ids_json and playbook_ids_json != "[]":
        try:
            parsed = json.loads(playbook_ids_json)
        except json.JSONDecodeError:
            return "playbook_ids_json must be a valid JSON array"
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            return "playbook_ids_json must be a JSON array of strings"
        playbook_ids = parsed

    try:
        result = S._engram.rollback_playbook_scope_migration(
            playbook_ids=playbook_ids,
            dry_run=dry_run,
            confirm=confirm,
        )
        S._track("rollback_playbook_scope_migration", success=True)
    except Exception as exc:
        S._track("rollback_playbook_scope_migration", success=False)
        return f"Rollback Playbook scope migration failed: {S._safe_err(exc)}"
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="rollback_playbook_scope_migration"
    )
    return S._json(result)


@S.mcp.tool()
async def get_playbook_scope_review_queue(
    project_folders_json: str = "[]",
    include_resolved: bool = False,
    limit: int = 50,
) -> str:
    """List unresolved legacy Playbooks that need manual scope review."""
    project_folders = None
    if project_folders_json and project_folders_json != "[]":
        try:
            parsed = json.loads(project_folders_json)
        except json.JSONDecodeError:
            return "project_folders_json must be a valid JSON array"
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            return "project_folders_json must be a JSON array of strings"
        project_folders = parsed
    try:
        result = S._engram.get_playbook_scope_review_queue(
            project_folders=project_folders,
            include_resolved=include_resolved,
            limit=limit,
        )
        S._track("get_playbook_scope_review_queue", success=True)
    except Exception as exc:
        S._track("get_playbook_scope_review_queue", success=False)
        return f"Get Playbook scope review queue failed: {S._safe_err(exc)}"
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="get_playbook_scope_review_queue"
    )
    return S._json(result)


@S.mcp.tool()
async def resolve_playbook_scope_review(
    playbook_id: str,
    action: str,
    project_folder: str = "",
    project_folders_json: str = "[]",
    note: str = "",
    dry_run: bool = True,
    confirm: bool = False,
) -> str:
    """Resolve one Playbook scope review item.

    Actions (exact values): 'accept_global', 'accept_project', 'accept_shared', 'skip'.
    - accept_project requires project_folder (single folder path; a single-item
      project_folders_json is also accepted).
    - accept_shared requires project_folders_json (JSON array of folder paths;
      project_folder alone is also accepted as one entry).
    Mutations require dry_run=False AND confirm=True; default is a dry-run preview.

    Owner/admin surface: mutates legacy Playbook review state and is refused for non-owner callers when governance is enabled.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(
        S._engram.root, tool="resolve_playbook_scope_review"
    )
    if refusal is not None:
        return refusal
    project_folders = None
    if project_folders_json and project_folders_json != "[]":
        try:
            parsed = json.loads(project_folders_json)
        except json.JSONDecodeError:
            return "project_folders_json must be a valid JSON array"
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            return "project_folders_json must be a JSON array of strings"
        project_folders = parsed
    try:
        result = S._engram.resolve_playbook_scope_review(
            playbook_id=playbook_id,
            action=action,
            project_folder=project_folder or None,
            project_folders=project_folders,
            note=note,
            dry_run=dry_run,
            confirm=confirm,
        )
        S._track("resolve_playbook_scope_review", success=True)
    except Exception as exc:
        S._track("resolve_playbook_scope_review", success=False)
        return f"Resolve Playbook scope review failed: {S._safe_err(exc)}"
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="resolve_playbook_scope_review"
    )
    return S._json(result)


@S.mcp.tool()
async def get_related_knowledge(item_id: str) -> str:
    """获取与某条 lesson 或 decision 相连的所有知识。 / Get all knowledge items linked to a given lesson or decision.

    用途：已知一个知识 ID，想沿着知识关系图查看相关经验和决策时调用。
    Purpose: Call when you have a knowledge ID and want to follow the knowledge graph to related lessons and decisions.

    注意：如果想找内容相似但尚未显式关联的条目，用 find_similar_knowledge。
    Note: Use find_similar_knowledge to find similar items that are not explicitly linked.

    Args:
        item_id: lesson 或 decision 的 ID。 / ID of a lesson or decision.
    """
    related = S._engram.get_related_knowledge(item_id)
    related = S._gov_rt.maybe_govern_result(
        S._engram.root, related, tool="get_related_knowledge",
        list_fields=("related",), item_fields=("source",),
    )
    return S._json(related)


@S.mcp.tool()
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
    similar = S._engram.find_similar_knowledge(item_id, limit=limit)
    similar = S._gov_rt.maybe_govern_result(
        S._engram.root, similar, tool="find_similar_knowledge",
        list_fields=("similar",), item_fields=("source",),
    )
    return S._json(similar)


@S.mcp.tool()
async def export_knowledge_report() -> str:
    """导出完整 Markdown 知识报告并返回内容。 / Export a full Markdown knowledge report and return its content.

    Owner/export surface: writes an exports/knowledge_report_*.md file and is refused for non-owner callers when governance is enabled.

    用途：需要把当前知识库整理成人可读报告，用于审阅、归档或分享时调用。
    Purpose: Call when the knowledge base should be rendered into a readable report for review, archiving, or sharing.

    注意：报告会保存到 ~/.engram/exports/，同时返回正文内容。
    Note: The report is saved under ~/.engram/exports/ and the content is returned as well.
    """
    # export_knowledge_report writes exports/knowledge_report_*.md to disk AND
    # returns the body. Gating only the RETURN (the old maybe_govern_dump) still
    # left the full Markdown report — with secret summaries/details — on disk for
    # a non-owner (Codex round-17 P1-3). Gate BEFORE the writer: a non-owner gets
    # a refusal and no report file is produced. Owner gets the full report.
    refusal = S._gov_rt.maybe_refuse_export(S._engram.root, tool="export_knowledge_report")
    if refusal is not None:
        return refusal
    return S._engram.export_knowledge_report()


# ===========================================================================
# WRITE TOOLS (18)
# ===========================================================================

