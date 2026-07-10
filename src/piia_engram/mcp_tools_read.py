"""Read-path MCP tools (context, recall, knowledge queries)."""
from __future__ import annotations

from typing import Optional
import json

try:
    from . import mcp_server as S
    from .knowledge_search_service import search_knowledge as _search_knowledge_service
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]
    from knowledge_search_service import (  # type: ignore[no-redef]
        search_knowledge as _search_knowledge_service,
    )

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
            triggers 关键词匹配，命中时浮现「相关 Playbook」小节（标题 + ID；用 get_playbooks(mode="get") 查看完整步骤）。
            Optional current user prompt. Appended to the context and matched against stored
            playbook trigger keywords; hits surface a "Matched Playbooks" section (title + id;
            call get_playbooks(mode="get") for the full steps).
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
    if not S._gov_rt.caller_is_owner(S._get_engram().root):
        return S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, "", tool="get_user_context"
        )
    # Auto-bootstrap on first call to an empty store: import discoverable rule
    # files (CLAUDE.md / AGENTS.md / .cursorrules) so cold-start delivers "it
    # already knows me" without a manual `engram setup`. Mirrors get_resume_brief.
    # Runs AFTER the owner gate above (bootstrap writes lessons, so a non-owner
    # caller is refused first) but BEFORE generate_context so the imported data is
    # reflected. It must NOT be gated on `if not context`: generate_context
    # returns a non-empty "identity not set" scaffold for an empty store, which
    # previously shadowed this trigger (bootstrap only fired via get_resume_brief
    # — a brand-new user calling get_user_context got the scaffold instead of
    # their auto-imported rules).
    from piia_engram.bootstrap import needs_bootstrap, run_bootstrap

    imported_rules = 0
    if needs_bootstrap(S._get_engram()):
        boot = run_bootstrap(S._get_engram())
        imported_rules = (
            boot.get("user_rules_imported", 0) + boot.get("project_rules_imported", 0)
        )
    try:
        context = S._get_engram().generate_context(
            project_folder, level=level, max_tokens=token_budget,
        )
        S._track("get_user_context", success=True)
        S._beta("cold_start", level=level)
    except Exception as exc:
        S._track("get_user_context", success=False)
        S.logger.warning("generate_context failed: %s", exc)
        return f"Engram 上下文加载失败: {S._safe_err(exc)}"
    if imported_rules and context:
        context = (
            f"[首次连接自动导入 {imported_rules} 条规则 from "
            f"CLAUDE.md/AGENTS.md]\n\n{context}"
        )
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

            candidates = S._get_engram().get_playbooks(
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
    perms = S._gov_rt.describe_caller_permissions(S._get_engram().root)
    context += S._format_permissions_section(perms)

    # Cold-start context is a rendered string bundling identity + top
    # lessons/decisions + snapshot — unfilterable by field. Gate owner-only.
    return S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, context, tool="get_user_context"
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
    refusal = S._gov_rt.maybe_refuse_export(S._get_engram().root, tool="refresh_quick_context")
    if refusal is not None:
        return refusal
    try:
        path = S._get_engram().refresh_quick_context(level=level)
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
    refusal = S._gov_rt.maybe_refuse_export(S._get_engram().root, tool="get_identity_card")
    if refusal is not None:
        return refusal
    try:
        card = S._get_engram().export_identity_card()
        S._track("get_identity_card", success=True)
    except Exception as exc:
        S._track("get_identity_card", success=False)
        S.logger.warning("export_identity_card failed: %s", exc)
        return f"身份卡生成失败: {S._safe_err(exc)}"
    if not card:
        return "身份卡为空——尚未积累足够的知识。"
    return card


@S.mcp.tool()
async def get_identity_facets(facet: str = "all", safe: bool = True) -> str:
    """按切面读取用户身份信息（画像/偏好/信任边界/工作风格/质量标准/领域图谱）。 / Read user identity facets: profile, preferences, trust boundaries, work style, quality standards, and the domain map.

    用途：需要单独读取某一类身份字段（如角色语言、协作偏好、验收标准、技术领域）时调用。
    Purpose: Call when you need a single identity facet such as role/language, collaboration preferences, acceptance standards, or technical domains.

    注意：完整冷启动上下文用 get_user_context；facet 名与 update_identity 的 field 一一对应（外加 domains）。
    Note: Use get_user_context for the full cold-start context; facet names mirror the update_identity fields (plus domains).

    Args:
        facet: all | profile | preferences | trust_boundaries | work_style | quality_standards | domains，默认 all 聚合全部。 / One facet name, or "all" (default) for the aggregate of every facet.
        safe: 仅作用于 profile 切面：默认 True，按 trust_boundaries 过滤敏感字段；设 False 仅在用户明确要求时使用。 / Applies to the profile facet only; default True filters sensitive fields per trust_boundaries. Set False only when the user explicitly requests full profile access.
    """
    # A non-owner caller must not be able to opt out of the profile redaction by
    # passing safe=False. Under governance, force safe=True for anyone below the
    # private-self owner; the owner (and the byte-identical flag-off path, where
    # caller_is_owner is always True) keeps the caller-supplied value. (Code
    # review 2026-06-23 S2-1/A1-1: caller-controlled safe= leaked decrypted PII.)
    effective_safe = safe or not S._gov_rt.caller_is_owner(S._get_engram().root)
    readers = {
        "profile": lambda: S._get_engram().get_profile(safe=effective_safe),
        "preferences": lambda: S._get_engram().get_preferences(),
        "trust_boundaries": lambda: S._get_engram().get_trust_boundaries(),
        "work_style": lambda: S._get_engram().get_work_style(),
        "quality_standards": lambda: S._get_engram().get_quality_standards(),
        "domains": lambda: S._get_engram().get_domains(),
    }
    if facet == "all":
        return S._json({name: read() for name, read in readers.items()})
    reader = readers.get(facet)
    if reader is None:
        return (
            f"unknown facet: {facet!r} "
            f"(expected all | {' | '.join(readers)})"
        )
    value = reader()
    if facet == "domains" and not value:
        return "尚无领域经验记录。"
    return S._json(value)


@S.mcp.tool()
async def get_lessons(
    domain: Optional[str] = None,
    source_tool: Optional[str] = None,
    project_folder: str = "",
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
    effective_project = project_folder or S._session.project_folder or None
    if effective_project:
        S._session.detect_project(effective_project)
    lessons = S._get_engram().get_lessons(
        domain=domain, source_tool=source_tool, limit=limit,
        project_folder=effective_project,
        _update_access=S._gov_rt.caller_is_owner(S._get_engram().root),
    )
    lessons = S._gov_rt.maybe_govern_list(S._get_engram().root, lessons, tool="get_lessons")
    if not lessons:
        return "尚无经验教训记录。"
    return S._json(lessons)


@S.mcp.tool()
async def get_decisions(
    source_tool: Optional[str] = None,
    project: Optional[str] = None,
    project_folder: str = "",
    domain: Optional[str] = None,
    limit: int = 30,
    thread_seed_id: str = "",
    history_question: str = "",
    history_threshold: float = 0.6,
) -> str:
    """按时间列出关键决策；也可还原决策链或某个问题的修订历史。 / List the user's key decisions by time; can also reconstruct a decision thread or a question's revision history.

    用途：浏览/筛选最近决策；给 thread_seed_id 时还原包含该条目的【决策链】（演进顺序、
    superseded 标记、当前 head）；给 history_question 时按问题文本模糊匹配，返回该决策
    的完整修订历史（revisions + current）。
    Purpose: Browse or filter recent decisions. With thread_seed_id, reconstruct the DECISION THREAD containing that item (evolution order, superseded flags, current heads). With history_question, fuzzy-match by question text and return the full revision history (revisions + current).

    注意：有明确关键词搜索决策内容用 search_knowledge(scope="decisions")；thread_seed_id
    与 history_question 互斥，同时给时 thread_seed_id 优先；演进关系由 manage_relation 建立。
    Note: For keyword search use search_knowledge(scope="decisions"). thread_seed_id and history_question are mutually exclusive (thread_seed_id wins). Evolution edges are built via manage_relation.

    Args:
        source_tool: 按来源工具过滤（如 'claude_code', 'codex'）。 / Filter by source tool, such as 'claude_code' or 'codex'.
        project: 按项目过滤（可选）。 / Filter by project (optional).
        domain: 按领域过滤（如 'architecture'），支持多标签决策的包含匹配。 / Filter by domain, such as 'architecture'; supports contains matching for multi-label decisions.
        limit: 最多返回多少条（默认 30）。 / Maximum number of items to return (default 30).
        thread_seed_id: 决策链中任意一条的 ID；给了就返回决策链视图。 / ID of any item in a thread; returns the thread view when provided.
        history_question: 决策问题的关键词或完整问题文本；给了就返回修订历史。 / Keywords or full question text; returns the revision history when provided.
        history_threshold: 修订历史的相似度阈值（0-1，默认 0.6）。 / Similarity threshold for history matching (0-1, default 0.6).
    """
    if thread_seed_id:
        thread = S._get_engram().get_decision_thread(thread_seed_id)
        # 'order' rows are derived previews ({id, status, summary}) that do not
        # carry the source item's sensitivity label, so content-only gating
        # would be partial. Gate the derived thread view owner-only.
        thread = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, thread, tool="get_decisions"
        )
        return S._json(thread)
    if history_question:
        result = S._get_engram().get_decision_history(
            history_question, threshold=history_threshold
        )
        result = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, result, tool="get_decisions"
        )
        return S._json(result)
    # Read-path side-effect gate (Codex round-6): owner-only access bookkeeping.
    effective_project = project_folder or S._session.project_folder or None
    if effective_project:
        S._session.detect_project(effective_project)
    decisions = S._get_engram().get_decisions(
        limit=limit,
        source_tool=source_tool,
        project=project,
        project_folder=effective_project,
        domain=domain,
        _update_access=S._gov_rt.caller_is_owner(S._get_engram().root),
    )
    decisions = S._gov_rt.maybe_govern_list(S._get_engram().root, decisions, tool="get_decisions")
    if not decisions:
        return "尚无决策记录。"
    return S._json(decisions)


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
        snapshot = S._get_engram().get_project_snapshot(project_folder)
        snapshot = S._gov_rt.maybe_govern_one(
            S._get_engram().root, snapshot, tool="get_project_context"
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
    projects = S._get_engram().list_projects()
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
        lessons = S._get_engram().get_relevant_lessons(
            project_folder=project_folder, limit=limit,
            _update_access=S._gov_rt.caller_is_owner(S._get_engram().root),
        )
        # governance gate (opt-in; OFF => byte-identical to the line above).
        lessons = S._gov_rt.maybe_govern_list(
            S._get_engram().root, lessons, tool="get_relevant_knowledge"
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
    perms = S._gov_rt.describe_caller_permissions(S._get_engram().root)
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
    pack = S._get_engram().get_knowledge_inheritance(description, limit=limit)
    pack = S._gov_rt.maybe_govern_result(
        S._get_engram().root, pack, tool="get_knowledge_inheritance", list_fields=("items",)
    )
    return S._json(pack)


_DEFAULT_MAX_FIELD_CHARS = 400


def _truncate_long_strings(obj, max_chars):
    """Recursively bound the size of string VALUES in a JSON-able structure.

    Any ``str`` longer than ``max_chars`` is clipped to its first ``max_chars``
    characters plus a ``" [+N chars truncated]"`` marker. Dict keys, numbers,
    booleans, and short strings are left untouched, so item shape, ids, and
    headlines survive. ``max_chars <= 0`` disables truncation (escape hatch).
    Never mutates ``obj``: the truncating path builds fresh dict/list containers;
    the ``max_chars <= 0`` escape hatch returns ``obj`` unchanged.
    """
    if max_chars <= 0:
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_long_strings(v, max_chars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_long_strings(v, max_chars) for v in obj]
    if isinstance(obj, str) and len(obj) > max_chars:
        return obj[:max_chars] + f" [+{len(obj) - max_chars} chars truncated]"
    return obj


@S.mcp.tool()
async def search_knowledge(query: str, scope: str = "all", limit: int = 10,
                           filters_json: str = "", project_folder: str = "",
                           include_freshness: bool = False,
                           max_field_chars: int = _DEFAULT_MAX_FIELD_CHARS) -> str:
    r"""搜索知识库（lessons/decisions/playbooks）。 / Search lessons, decisions, and playbooks by keyword.

    **Lifecycle: retrieval** — 在对话中需要检索历史知识时调用。
    Lifecycle: retrieval — call during conversation when past knowledge is needed.

    Call when the user asks to find knowledge about a specific topic,
    or recalls a procedure ('X how to' / 'X steps').

    If you only have a project path and no query, use get_relevant_knowledge;
    if you have an existing knowledge ID, use explore_knowledge(mode="similar").

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
        max_field_chars: Per-field size cap. Every string field in each returned
            item (detail/reasoning/description/steps/...) longer than this is
            clipped with a "[+N chars truncated]" marker so a few large bodies
            cannot blow up the client. Item shape, ids, and headlines are kept.
            Set 0 for full untruncated bodies (default 400).
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
        eng = S._get_engram()
        allow_index = S._gov_rt.caller_is_owner(eng.root)
        result = _search_knowledge_service(
            eng,
            query=query, scope=scope, limit=limit, filters=filters,
            allow_hybrid_index=allow_index,
            project_folder=effective_project,
        )
        # governance gate (opt-in; OFF => byte-identical to the line above).
        result = S._gov_rt.maybe_govern_buckets(S._get_engram().root, result, tool="search_knowledge")
        # Opt-in freshness annotation, applied AFTER governance filtering so it
        # only ever annotates items the caller may already see (Provenance &
        # Freshness Contract v1, follow-up B). Pure/non-destructive; default OFF
        # keeps the response byte-identical.
        if include_freshness and isinstance(result, dict):
            for _bucket in ("lessons", "decisions", "playbooks"):
                items = result.get(_bucket)
                if isinstance(items, list):
                    result[_bucket] = S._provenance.annotate_freshness(items)
        # Result-size discipline: a few large knowledge bodies must not blow up
        # the MCP client. Bound each item's string fields HERE, at the MCP
        # boundary, BEFORE usage_policy / _caller_permissions are injected so
        # that policy and permission metadata are never clipped regardless of
        # the cap. Engram.search_knowledge (reused by the CLI and recall_service)
        # is untouched, so internal consumers keep full fidelity.
        if isinstance(result, dict) and max_field_chars > 0:
            for _bucket in ("lessons", "decisions", "playbooks"):
                items = result.get(_bucket)
                if isinstance(items, list):
                    result[_bucket] = [
                        _truncate_long_strings(item, max_field_chars)
                        for item in items
                    ]
        if isinstance(result, dict):
            playbooks = result.get("playbooks")
            if isinstance(playbooks, list):
                for item in playbooks:
                    S._inject_usage_policy(item)
        S._track("search_knowledge", success=True)
    except Exception as exc:
        S._track("search_knowledge", success=False)
        return f"搜索失败: {S._safe_err(exc)}"
    perms = S._gov_rt.describe_caller_permissions(S._get_engram().root)
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
    overview = S._get_engram().get_knowledge_overview(section, stale_days=stale_days)
    # digest embeds FULL top_lessons/top_decisions plus label-stripped preview
    # rows nested several levels down (recent_items, stale.{lessons,decisions});
    # field-by-field gating across those derived rows is error-prone and loses
    # the original sensitivity label. Gate the aggregate view owner-only.
    overview = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, overview, tool="get_knowledge_overview"
    )
    return S._json(overview)


@S.mcp.tool()
async def explore_knowledge(
    mode: str = "related",
    item_id: str = "",
    limit: int = 0,
    threshold: float = 0.45,
) -> str:
    """探索知识关联：相连条目、相似条目或全库合并建议。 / Explore knowledge links: related items, similar items, or merge candidates across the library.

    用途：已知条目 ID 时沿关系图看相关知识（related）或查近似重复（similar）；定期维护时
    全库扫描可合并条目（merge_candidates，附可直接执行的 merge 命令）。
    Purpose: Follow the knowledge graph from a known item (related), find near-duplicates of an item (similar), or scan the whole library for merge candidates during maintenance (merge_candidates, with actionable merge commands).

    注意：只有关键词没有 ID 时用 search_knowledge；执行合并用 merge_knowledge。
    Note: Use search_knowledge when you only have keywords; use merge_knowledge to actually merge.

    Args:
        mode: related | similar | merge_candidates（默认 related）。 / Exploration mode (default related).
        item_id: related/similar 模式必填：lesson 或 decision 的 ID。 / Required for related/similar: ID of a lesson or decision.
        limit: 最多返回多少条；0 = 按模式默认（similar:5，merge_candidates:10）。 / Maximum items; 0 = per-mode default (similar:5, merge_candidates:10).
        threshold: merge_candidates 模式的相似度阈值（0.2–1.0，默认 0.45）。 / Similarity threshold for merge_candidates (0.2–1.0, default 0.45).
    """
    if mode == "merge_candidates":
        merges = S._get_engram().suggest_merges(threshold=threshold, limit=limit or 10)
        # Each suggestion embeds item summaries from a full-library scan; gate
        # the aggregate maintenance view owner-only.
        merges = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, merges, tool="explore_knowledge"
        )
        return S._json(merges)
    if mode not in ("related", "similar"):
        return (
            f"unknown mode: {mode!r} "
            "(expected related | similar | merge_candidates)"
        )
    if not item_id:
        return "item_id is required for mode='related' / mode='similar'"
    if mode == "related":
        related = S._get_engram().get_related_knowledge(item_id)
        related = S._gov_rt.maybe_govern_result(
            S._get_engram().root, related, tool="explore_knowledge",
            list_fields=("related",), item_fields=("source",),
        )
        return S._json(related)
    similar = S._get_engram().find_similar_knowledge(item_id, limit=limit or 5)
    similar = S._gov_rt.maybe_govern_result(
        S._get_engram().root, similar, tool="explore_knowledge",
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
    refusal = S._gov_rt.maybe_refuse_export(S._get_engram().root, tool="export_knowledge_report")
    if refusal is not None:
        return refusal
    return S._get_engram().export_knowledge_report()


# ===========================================================================
# WRITE TOOLS (18)
# ===========================================================================
