"""Write-path MCP tools (memory store, playbooks, tool registry)."""
from __future__ import annotations

import json
import re

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]

@S.mcp.tool()
async def memory_store(
    kind: str,
    content_json: str = "",
    source_tool: str = "",
    items_json: str = "",
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
        kind: 知识类型 — 'lesson' | 'decision' | 'playbook'。批量模式下作为各条目的类型（playbook 不支持批量）。 / Knowledge type; in batch mode, the item type for every item (playbook not supported in batch).
        content_json: 知识内容 JSON 字符串（单条模式必填）。格式因 kind 而异：
            - lesson: {"summary": "...", "detail": "...", "domain": "..."}
            - decision: {"question": "...", "choice": "...", "reasoning": "..."}
            - playbook: {"title": "...", "triggers": "...", "steps_json": "[...]"}
            Content JSON string (required in single mode). Schema varies by kind (see above).
        source_tool: 调用来源工具（可选），如 'claude_code', 'cursor'。 / Source tool (optional).
        items_json: 条目 JSON 数组；给了就走批量写入（一次导入多条 lesson/decision）。 / JSON array of items; when provided, batch-writes multiple lessons/decisions in one call.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="memory_store")
    if refusal is not None:
        S._track("memory_store", success=False)
        return refusal

    kind = kind.strip().lower()

    if items_json:
        # Batch path (absorbs the former bulk_add_knowledge tool).
        if kind == "playbook":
            return "批量模式仅支持 lesson / decision，playbook 请逐条写入"
        try:
            items = json.loads(items_json)
        except json.JSONDecodeError:
            return S._json({"error": "items_json must be a valid JSON array"})
        if not isinstance(items, list):
            return S._json({"error": "items_json must be a JSON array"})
        # An agent cannot self-certify trust: strip any tier/approval fields
        # each item smuggled through items_json so the risk-based write gate
        # stays the sole authority over tier (high-risk items -> staging).
        for _item in items:
            S.strip_untrusted_trust_fields(_item)
        return S._json(S._locked_engram_call(
            S._engram.bulk_add_knowledge,
            items,
            item_type=kind or "lesson",
            source_tool=source_tool,
        ))

    try:
        content = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return "content_json 格式错误，应为 JSON 字符串"

    if not isinstance(content, dict):
        return "content_json 应为 JSON 对象（{}），不能是数组或标量"

    # An agent cannot self-certify trust: strip any tier/approval fields it
    # smuggled through content_json so the risk-based write gate is the sole
    # authority over tier (high-risk content -> staging, not verified).
    S.strip_untrusted_trust_fields(content)

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
        S._track("memory_store", success=False)
        return f"不支持的 kind: {kind}。可用: lesson, decision, playbook"
    else:
        S._track("memory_store", success=False)
        return "kind 不能为空。可用: lesson, decision, playbook"

    try:
        if kind == "lesson":
            result = S._locked_engram_call(S._engram.add_lesson, content)
            label = content.get("summary", "")[:60]
            S._track("memory_store", success=True)
            if result.get("status") == "duplicate":
                # Dedup-reject echoes the matched stored item — gate it (see add_lesson).
                return S._json(S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="memory_store"))
            return f"教训已记录: {label}"
        elif kind == "decision":
            result = S._locked_engram_call(S._engram.add_decision, content)
            label = f"{content.get('question', '')} → {content.get('choice', '')}"[:60]
            S._track("memory_store", success=True)
            if result.get("status") == "duplicate":
                # Dedup-reject echoes the matched stored item — gate it (see add_lesson).
                return S._json(S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="memory_store"))
            return f"决策已记录: {label}"
        else:  # playbook
            result = S._locked_engram_call(S._engram.add_playbook, content)
            label = content.get("title", "")[:60]
            S._track("memory_store", success=True)
            return f"Playbook 已记录: {label}"
    except Exception as exc:
        S._track("memory_store", success=False)
        return f"memory_store 失败: {S._safe_err(exc)}"


@S.mcp.tool()
async def add_lesson(
    summary: str,
    detail: str = "",
    domain: str = "",
    source_tool: str = "",
    source_url: str = "",
    source_agent: str = "",
    run_id: str = "",
    last_validated_at: str = "",
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
        source_agent: 产生/校验此条目的 agent 身份（可选，如 'claude_code'，比 source_tool 更细）。 / Agent identity that produced or validated this entry (optional; finer-grained than source_tool).
        run_id: 产生此条目的工作流/会话运行 ID（可选）。 / Workflow/session run id that produced this entry (optional).
        last_validated_at: 人/agent 最近确认此条目仍然成立的 ISO-8601 时间（可选）。 / ISO-8601 time this entry was last confirmed to still hold (optional).
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="add_lesson")
    if refusal is not None:
        S._track("add_lesson", success=False)
        return refusal

    lesson = {"summary": summary}
    if detail:
        lesson["detail"] = detail
    if domain:
        lesson["domain"] = domain
    if source_tool:
        lesson["source_tool"] = source_tool
    if source_url:
        lesson["source_url"] = source_url
    S._attach_provenance(
        lesson, source_agent=source_agent, run_id=run_id,
        last_validated_at=last_validated_at,
    )
    try:
        result = S._locked_engram_call(S._engram.add_lesson, lesson)
        S._track("add_lesson", success=True)
        S._beta("knowledge_created", kind="lesson",
              domain=domain[:80] if domain else "",
              source_tool=source_tool[:40] if source_tool else "",
              tier=result.get("tier", "staging") if isinstance(result, dict) else "staging")
    except Exception as exc:
        S._track("add_lesson", success=False)
        return f"添加教训失败: {S._safe_err(exc)}"
    if result.get("status") == "duplicate":
        # The dedup-reject payload echoes the MATCHED stored item's
        # ``existing_summary`` (content the caller never supplied) — a low-trust
        # agent could submit a near-duplicate to read back a work/secret lesson.
        # Gate it like any write-echo: owner sees it, lower tiers get a
        # title/body-free confirmation.
        return S._json(S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="add_lesson"))
    return f"教训已记录: {summary}"


@S.mcp.tool()
async def add_decision(
    question: str,
    choice: str,
    reasoning: str = "",
    source_tool: str = "",
    project: str = "",
    domain: str = "",
    supersedes: str = "",
    source_agent: str = "",
    run_id: str = "",
    last_validated_at: str = "",
) -> str:
    """记录单条关键决策（用户明确选了某个方案）。 / Record one key decision when the user explicitly chose an option.

    **Lifecycle: writeback** — 对话中做出明确决策时调用。
    Lifecycle: writeback — call when an explicit decision is made during conversation.

    用途：用户说"我们决定用 X"或"以后都用 Y"时调用。
    Purpose: Call when the user says they decided to use X or will use Y going forward.

    注意：如果用户给了一段会话摘要让你自动提取，请用 extract_session_insights 而不是本工具。
    Note: If the user gives a session summary for automatic extraction, use extract_session_insights instead.

    决策链（Decision Thread）：同一问题改选方案时，会自动在决策链中标记旧决策为 superseded。
    也可显式传 supersedes 参数指定被取代的旧决策 ID。
    Decision thread: when the same question gets a different choice, the old decision is
    automatically marked superseded. You may also explicitly pass supersedes with the old ID.

    Args:
        question: 决策的问题，如"数据库选型"。 / Decision question, such as 'database choice'.
        choice: 做出的选择，如"PostgreSQL"。 / Chosen option, such as 'PostgreSQL'.
        reasoning: 选择的理由（可选）。 / Reasoning for the choice (optional).
        source_tool: 记录来源工具，如 'claude_code', 'codex'（可选，建议填写）。 / Source tool, such as 'claude_code' or 'codex' (optional but recommended).
        project: 关联项目（可选）。 / Related project (optional).
        domain: 技术领域（可选），可填多个，逗号分隔，如 'architecture,database'。 / Technical domain (optional); may contain multiple comma-separated labels such as 'architecture,database'.
        supersedes: 被本决策取代的旧决策 ID（可选）。填写后自动在决策链中建立 supersedes 关系。 / ID of the old decision this one replaces (optional). Creates a supersedes edge in the decision thread.
        source_agent: 产生/校验此决策的 agent 身份（可选）。 / Agent identity that produced or validated this decision (optional).
        run_id: 产生此决策的工作流/会话运行 ID（可选）。 / Workflow/session run id that produced this decision (optional).
        last_validated_at: 最近确认此决策仍然成立的 ISO-8601 时间（可选）。 / ISO-8601 time this decision was last confirmed to still hold (optional).
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="add_decision")
    if refusal is not None:
        S._track("add_decision", success=False)
        return refusal

    decision = {"question": question, "choice": choice}
    if reasoning:
        decision["reasoning"] = reasoning
    if source_tool:
        decision["source_tool"] = source_tool
    if project:
        decision["project"] = project
    if domain:
        decision["domain"] = domain
    if supersedes:
        decision["supersedes"] = supersedes
    S._attach_provenance(
        decision, source_agent=source_agent, run_id=run_id,
        last_validated_at=last_validated_at,
    )
    try:
        result = S._locked_engram_call(S._engram.add_decision, decision)
        S._track("add_decision", success=True)
        S._beta("knowledge_created", kind="decision",
              domain=domain[:80] if domain else "",
              source_tool=source_tool[:40] if source_tool else "",
              tier=result.get("tier", "staging") if isinstance(result, dict) else "staging")
    except Exception as exc:
        S._track("add_decision", success=False)
        return f"添加决策失败: {S._safe_err(exc)}"
    if result.get("status") == "duplicate":
        # Dedup-reject echoes the matched stored decision's ``existing_title`` —
        # gate it like any write-echo (see add_lesson).
        return S._json(S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="add_decision"))
    return f"决策已记录: {question} → {choice}"


@S.mcp.tool()
async def add_playbook(
    title: str,
    triggers: str,
    steps_json: str = "[]",
    required_tools_json: str = "[]",
    tool_refs: str = "",
    description: str = "",
    domain: str = "",
    preconditions: str = "",
    pitfalls: str = "",
    outcome: str = "",
    source_tool: str = "",
    scope_type: str = "global",
    project_folder: str = "",
    source_agent: str = "",
    run_id: str = "",
    last_validated_at: str = "",
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
        required_tools_json: 工具依赖 JSON 数组（可选），只声明工具名/用途，不写本机路径。 / Tool dependencies JSON array (optional); declares names/purposes, not local paths.
        tool_refs: 简写工具名，逗号分隔（可选）。 / Shorthand tool names, comma-separated (optional).
        description: 流程概述（可选）。 / Brief description (optional).
        domain: 技术领域，逗号分隔（可选）。 / Domain labels, comma-separated (optional).
        preconditions: 前提条件，逗号分隔（可选）。 / Preconditions, comma-separated (optional).
        pitfalls: 常见陷阱，逗号分隔（可选）。 / Common pitfalls, comma-separated (optional).
        outcome: 预期结果（可选）。 / Expected outcome (optional).
        source_tool: 来源工具（可选）。 / Source tool (optional).
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="add_playbook")
    if refusal is not None:
        S._track("add_playbook", success=False)
        return refusal

    playbook: dict = {"title": title}
    playbook["triggers"] = [t.strip() for t in triggers.split(",") if t.strip()]
    try:
        steps = json.loads(steps_json)
        if isinstance(steps, list):
            playbook["steps"] = steps
    except json.JSONDecodeError:
        return "steps_json 格式错误，需要有效的 JSON 数组"
    if required_tools_json and required_tools_json != "[]":
        try:
            required_tools = json.loads(required_tools_json)
            if not isinstance(required_tools, list):
                return "required_tools_json 格式错误，需要有效的 JSON 数组"
            playbook["required_tools"] = required_tools
        except json.JSONDecodeError:
            return "required_tools_json 格式错误，需要有效的 JSON 数组"
    if tool_refs:
        playbook["tool_refs"] = [t.strip() for t in re.split(r"[\n,;，、；]+", tool_refs) if t.strip()]
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
    if project_folder:
        S._session.detect_project(project_folder)
    effective_project = project_folder or S._session.project_folder
    scope_type = (scope_type or "global").strip().lower()
    if scope_type == "project" or project_folder:
        if not effective_project:
            return "project scope requires project_folder"
        playbook["scope_type"] = "project"
        playbook["project_folder"] = effective_project
    else:
        playbook["scope_type"] = "global"
    S._attach_provenance(
        playbook, source_agent=source_agent, run_id=run_id,
        last_validated_at=last_validated_at,
    )
    try:
        result = S._locked_engram_call(S._engram.add_playbook, playbook)
        S._track("add_playbook", success=True)
    except Exception as exc:
        S._track("add_playbook", success=False)
        return f"添加 Playbook 失败: {S._safe_err(exc)}"
    if result.get("status") == "duplicate":
        # Dedup-reject echoes the matched stored playbook's ``existing_title`` —
        # gate it like any write-echo (see add_lesson).
        return S._json(S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="add_playbook"))
    if result.get("error"):
        return S._json(result)
    return f"Playbook 已记录: {title} (triggers: {triggers})"


# ---------------------------------------------------------------------------
# Playbook usage-policy header (task #16: passive-reference semantic)
#
# Every playbook returned by an MCP tool carries a ``usage_policy`` field
# instructing any consuming AI to treat it as a passive reference, NOT an
# execution command.  This embeds the user's "decision ∈ user / execution ∈
# AI" operating model into the data format so it travels with the playbook
# across tools.  The field is injected in the MCP layer (not the core) so
# the stored data stays clean.
# ---------------------------------------------------------------------------

_PLAYBOOK_USAGE_POLICY = (
    "本 playbook 是被动参考资料——取用后须先与用户确认方案再逐步执行，"
    "不得自动驱动决策或一键跑完全部步骤。\n"
    "This playbook is a passive reference — after retrieval, confirm the "
    "plan with the user before proceeding step by step. Do not auto-drive "
    "decisions or execute all steps at once."
)

_EXECUTION_USAGE_POLICY = (
    "本执行计划需要逐步确认——每完成一步，须与用户核实结果再执行下一步，"
    "不得跳步或一键全部执行。\n"
    "This execution plan requires step-by-step confirmation. After each "
    "step, verify the result with the user before proceeding. Do not skip "
    "steps or execute all at once."
)


def _inject_usage_policy(item, policy=_PLAYBOOK_USAGE_POLICY):
    """Add ``usage_policy`` to a playbook / execution-plan dict.

    Skips governance-withheld stubs (``governance_withheld: True``) and
    non-dict values so callers can apply it unconditionally.
    """
    if isinstance(item, dict) and not item.get("governance_withheld"):
        item["usage_policy"] = policy
    return item


@S.mcp.tool()
async def get_playbooks(
    domain: str = "",
    limit: int = 20,
    project_folder: str = "",
    mode: str = "list",
    playbook_id: str = "",
    confirm_cross_project: bool = False,
    status: str = "all",
    scope_type: str = "all",
    include_content: bool = False,
) -> str:
    """Playbook 统一读取入口：列表 / 单条 / 最近使用 / 管理视图。 / Unified Playbook reader: list, single item, recently used, or management view.

    用途：浏览已记录的操作流程；按 ID 调取单条手册详细步骤；冷启动时浮现最近用过的流程；
    或查看含归档/删除元数据的管理视图。
    Purpose: Browse recorded procedures; fetch one playbook's full steps by ID; surface
    recently used playbooks at session start; or inspect the management view with
    archived/deleted metadata.

    Args:
        domain: 按领域筛选（mode=list，可选）。 / Filter by domain (mode=list, optional).
        limit: 返回条数上限（默认 20；mode=recent 建议传 5，mode=management 建议传 100）。 / Max items (default 20; suggest 5 for recent, 100 for management).
        project_folder: 项目目录（可选）。 / Project folder (optional).
        mode: list（列表，默认）| get（单条）| recent（最近使用）| management（管理视图）。 / list (default) | get | recent | management.
        playbook_id: Playbook ID（mode=get 必填；默认 mode 下传入则自动按 get 处理）。 / Playbook ID (required for mode=get; implies get when passed with default mode).
        confirm_cross_project: 跨项目读取确认（mode=get）。 / Cross-project read confirmation (mode=get).
        status: 状态筛选 all/active/archived/deleted（mode=management）。 / Status filter (mode=management).
        scope_type: 范围筛选 all/global/project（mode=management）。 / Scope filter (mode=management).
        include_content: 管理视图是否含正文（mode=management）。 / Include full content in management view.
    """
    # 隐式升级：默认 mode 下给了 playbook_id 就按单条读取处理。
    if playbook_id and mode == "list":
        mode = "get"
    if mode not in ("list", "get", "recent", "management"):
        return (
            f"未知 mode: {mode}。可用: list / get / recent / management。 "
            f"/ Unknown mode: {mode}. Available: list / get / recent / management."
        )
    if mode == "get":
        if not playbook_id:
            return "mode=get 需要提供 playbook_id。 / mode=get requires playbook_id."
        try:
            if project_folder:
                S._session.detect_project(project_folder)
            effective_project = project_folder or S._session.project_folder or None
            result = S._engram.get_playbook(
                playbook_id,
                _update_access=S._gov_rt.caller_is_owner(S._engram.root),
                project_folder=effective_project,
                confirm_cross_project=confirm_cross_project,
            )
            result = S._gov_rt.maybe_govern_one(S._engram.root, result, tool="get_playbooks")
            S._track("get_playbooks", success=True)
        except Exception as exc:
            S._track("get_playbooks", success=False)
            return f"获取 Playbook 失败: {S._safe_err(exc)}"
        if result.get("error"):
            return S._json(result)
        _inject_usage_policy(result)
        return S._json(result)
    if mode == "recent":
        try:
            if project_folder:
                S._session.detect_project(project_folder)
            effective_project = project_folder or S._session.project_folder or None
            result = S._engram.get_recent_playbooks(limit=limit, project_folder=effective_project)
            result = S._gov_rt.maybe_govern_list(
                S._engram.root, result, tool="get_playbooks"
            )
            S._track("get_playbooks", success=True)
        except Exception as exc:
            S._track("get_playbooks", success=False)
            return f"获取近期 Playbook 失败: {S._safe_err(exc)}"
        if not result:
            return "尚无最近使用的 Playbook。 / No recently used Playbooks."
        for item in result:
            _inject_usage_policy(item)
        return S._json(result)
    if mode == "management":
        try:
            result = S._engram.list_playbooks_for_management(
                status=status,
                project_folder=project_folder or None,
                scope_type=scope_type,
                include_content=include_content,
                limit=limit,
            )
            S._track("get_playbooks", success=True)
        except Exception as exc:
            S._track("get_playbooks", success=False)
            return f"List Playbooks for management failed: {S._safe_err(exc)}"
        result = S._gov_rt.maybe_govern_owner_only(
            S._engram.root, result, tool="get_playbooks"
        )
        return S._json(result)
    try:
        if project_folder:
            S._session.detect_project(project_folder)
        effective_project = project_folder or S._session.project_folder or None
        result = S._engram.get_playbooks(
            domain=domain or None, limit=limit,
            project_folder=effective_project,
            _update_access=S._gov_rt.caller_is_owner(S._engram.root),
        )
        result = S._gov_rt.maybe_govern_list(S._engram.root, result, tool="get_playbooks")
        S._track("get_playbooks", success=True)
    except Exception as exc:
        S._track("get_playbooks", success=False)
        return f"获取 Playbooks 失败: {S._safe_err(exc)}"
    if not result:
        return "尚无已保存的 Playbook。"
    for item in result:
        _inject_usage_policy(item)
    return S._json(result)


@S.mcp.tool()
async def manage_playbook(
    action: str,
    playbook_id: str,
    title: str = "",
    triggers: str = "",
    steps_json: str = "",
    required_tools_json: str = "",
    tool_refs: str = "",
    description: str = "",
    domain: str = "",
    preconditions: str = "",
    pitfalls: str = "",
    outcome: str = "",
    status: str = "",
    reason: str = "",
    dry_run: bool = True,
    confirm: bool = False,
) -> str:
    """Playbook 统一管理入口：更新 / 归档 / 删除 / 恢复。 / Unified Playbook management: update, archive, delete, restore.

    用途：修正补充手册内容（update）、标记过时（archive）、确认后软删除（delete）、
    恢复归档或已删手册（restore）。
    Purpose: Correct or enrich a playbook (update), mark outdated (archive),
    soft-delete after confirmation (delete), or restore (restore).

    update 只传需要更新的字段，未传字段保持不变，版本号自动递增。
    delete/restore 默认 dry_run=True 预览，需 confirm=True 才真正执行。
    For update, pass only the fields to change; version auto-increments.
    delete/restore default to dry_run=True preview and require confirm=True.

    Args:
        action: update | archive | delete | restore。
        playbook_id: 目标 Playbook ID。 / Target Playbook ID.
        title: 新标题（update，可选）。 / New title (update, optional).
        triggers: 新触发词，逗号分隔（update，可选）。 / New trigger keywords, comma-separated (update, optional).
        steps_json: 新步骤 JSON 数组（update，可选）。 / New steps as a JSON array (update, optional).
        required_tools_json: 新工具依赖 JSON 数组（update，可选），只声明工具名/用途。 / New tool dependencies JSON array (update, optional).
        tool_refs: 新简写工具名，逗号分隔（update，可选）。 / New shorthand tool names, comma-separated (update, optional).
        description: 新描述（update，可选）。 / New description (update, optional).
        domain: 新领域（update，可选）。 / New domain (update, optional).
        preconditions: 新前提条件，逗号分隔（update，可选）。 / New preconditions, comma-separated (update, optional).
        pitfalls: 新陷阱，逗号分隔（update，可选）。 / New pitfalls, comma-separated (update, optional).
        outcome: 新预期结果（update，可选）。 / New expected outcome (update, optional).
        status: 新状态，如 active/outdated/staging（update，可选）。 / New status (update, optional).
        reason: 删除原因（delete，可选）。 / Deletion reason (delete, optional).
        dry_run: 预览不落盘（delete/restore，默认 True）。 / Preview without writing (delete/restore, default True).
        confirm: 确认执行（delete/restore，必须显式 True）。 / Explicit confirmation (delete/restore).
    """
    # a4: write-path governance gate — must run unconditionally BEFORE action
    # validation so a low-trust caller gets a governance refusal, never an
    # "unknown action" hint (writer-spy matrix).
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="manage_playbook")
    if refusal is not None:
        S._track("manage_playbook", success=False)
        return refusal

    action = action.strip().lower()
    if action == "update":
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
        if required_tools_json:
            try:
                required_tools = json.loads(required_tools_json)
                if not isinstance(required_tools, list):
                    return "required_tools_json 格式错误，需要有效的 JSON 数组"
                updates["required_tools"] = required_tools
            except json.JSONDecodeError:
                return "required_tools_json 格式错误，需要有效的 JSON 数组"
        if tool_refs:
            updates["tool_refs"] = [t.strip() for t in re.split(r"[\n,;，、；]+", tool_refs) if t.strip()]
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
            result = S._locked_engram_call(S._engram.update_playbook, playbook_id, updates)
            S._track("manage_playbook", success=True)
        except Exception as exc:
            S._track("manage_playbook", success=False)
            return f"更新 Playbook 失败: {S._safe_err(exc)}"
        if result.get("error"):
            return S._json(result)
        # The ack echoes the stored title when the caller omitted the title arg —
        # gate so a low-trust caller can't read a secret title back (round-16).
        ack = f"Playbook 已更新: {result.get('title', playbook_id)} (v{result.get('version', '?')})"
        return S._gov_rt.maybe_govern_write_ack(S._engram.root, ack, tool="manage_playbook")
    if action == "archive":
        try:
            result = S._locked_engram_call(S._engram.archive_playbook, playbook_id)
            S._track("manage_playbook", success=True)
        except Exception as exc:
            S._track("manage_playbook", success=False)
            return f"归档 Playbook 失败: {S._safe_err(exc)}"
        if result.get("error"):
            return S._json(result)
        ack = f"Playbook archived: {playbook_id}"
        return S._gov_rt.maybe_govern_write_ack(S._engram.root, ack, tool="manage_playbook")
    if action == "delete":
        try:
            result = S._locked_engram_call(
                S._engram.delete_playbook,
                playbook_id=playbook_id,
                reason=reason,
                dry_run=dry_run,
                confirm=confirm,
            )
            S._track("manage_playbook", success=True)
        except Exception as exc:
            S._track("manage_playbook", success=False)
            return f"Delete Playbook failed: {S._safe_err(exc)}"
        if result.get("error"):
            return S._json(result)
        result = S._gov_rt.maybe_govern_write_ack(
            S._engram.root, result, tool="manage_playbook"
        )
        return S._json(result)
    if action == "restore":
        try:
            result = S._locked_engram_call(
                S._engram.restore_playbook,
                playbook_id=playbook_id,
                dry_run=dry_run,
                confirm=confirm,
            )
            S._track("manage_playbook", success=True)
        except Exception as exc:
            S._track("manage_playbook", success=False)
            return f"Restore Playbook failed: {S._safe_err(exc)}"
        if result.get("error"):
            return S._json(result)
        result = S._gov_rt.maybe_govern_write_ack(
            S._engram.root, result, tool="manage_playbook"
        )
        return S._json(result)
    return (
        f"未知 action: {action}。可用: update / archive / delete / restore。 "
        f"/ Unknown action: {action}. Available: update / archive / delete / restore."
    )


@S.mcp.tool()
async def playbook_execution(
    action: str,
    playbook_id: str,
    params_json: str = "{}",
    project_folder: str = "",
    confirm_cross_project: bool = False,
    step_order: int = 0,
    step_status: str = "",
    notes: str = "",
) -> str:
    """Playbook 执行统一入口：准备计划 / 更新步骤 / 查看状态。 / Unified Playbook execution: prepare a plan, update a step, or check status.

    用途："按上次流程来" — prepare 调取 Playbook 替换参数生成被动参考计划；
    update_step 逐步标记完成情况；status 查看步骤状态与结果汇总。
    AI 逐步确认执行，不自动运行。
    Purpose: "Use the previous procedure" — prepare fetches a Playbook, substitutes
    parameters, and returns a passive step plan; update_step tracks per-step progress;
    status returns the step rollup. AI confirms each step; no auto-execution.

    Owner/export surface: prepare writes an execution-plan file and is refused for
    non-owner callers when governance is enabled.

    Args:
        action: prepare | update_step | status。
        playbook_id: Playbook ID。 / The Playbook ID.
        params_json: 参数 JSON 对象，替换步骤中的 ${variable}（prepare，可选）。 / Parameters JSON object for ${variable} substitution (prepare, optional).
        project_folder: 项目目录（prepare，可选）。 / Project folder (prepare, optional).
        confirm_cross_project: 跨项目确认（prepare）。 / Cross-project confirmation (prepare).
        step_order: 步骤序号（update_step）。 / Step order number (update_step).
        step_status: "completed" | "skipped" | "failed"（update_step）。
        notes: 可选备注，如失败原因（update_step）。 / Optional note, e.g. failure reason (update_step).
    """
    # a4: write-path governance gate — must run unconditionally BEFORE action
    # validation so a low-trust caller gets a governance refusal, never an
    # "unknown action" hint (writer-spy matrix).
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="playbook_execution")
    if refusal is not None:
        S._track("playbook_execution", success=False)
        return refusal

    action = action.strip().lower()
    if action == "prepare":
        params = {}
        if params_json and params_json != "{}":
            try:
                parsed = json.loads(params_json)
                if isinstance(parsed, dict):
                    params = parsed
            except json.JSONDecodeError:
                return "params_json 格式错误，需要有效的 JSON 对象"
        # prepare is NOT a pure read: core.save_execution_plan PERSISTS the
        # (parameter-substituted) step bodies to
        # <root>/playbooks/executions/<id>.json BEFORE we could govern the
        # return (Codex round-18 P1). Governing only the return left a
        # secret-bearing file on disk for a non-owner — same two-step exfil as
        # the export tools. Gate BEFORE the writer runs: a non-owner gets a
        # refusal and no execution-plan file is created. Owner proceeds and
        # gets the full plan.
        refusal = S._gov_rt.maybe_refuse_export(S._engram.root, tool="playbook_execution")
        if refusal is not None:
            S._track("playbook_execution", success=False)
            return refusal
        try:
            if project_folder:
                S._session.detect_project(project_folder)
            effective_project = project_folder or S._session.project_folder or None
            result = S._locked_engram_call(
                S._engram.prepare_playbook_execution,
                playbook_id,
                params=params,
                project_folder=effective_project,
                confirm_cross_project=confirm_cross_project,
            )
            S._track("playbook_execution", success=True)
        except Exception as exc:
            S._track("playbook_execution", success=False)
            return f"准备执行计划失败: {S._safe_err(exc)}"
        _inject_usage_policy(result, _EXECUTION_USAGE_POLICY)
        return S._json(result)
    if action == "update_step":
        if step_status not in ("completed", "skipped", "failed"):
            return (
                "step_status 需为 completed / skipped / failed。 "
                "/ step_status must be completed / skipped / failed."
            )
        try:
            result = S._locked_engram_call(
                S._engram.update_execution_step,
                playbook_id,
                step_order,
                step_status,
                notes,
            )
            S._track("playbook_execution", success=True)
        except Exception as exc:
            S._track("playbook_execution", success=False)
            return f"更新步骤状态失败: {S._safe_err(exc)}"
        return S._json(result)
    if action == "status":
        try:
            result = S._engram.get_execution_status(playbook_id)
            S._track_read_safe("playbook_execution", success=True)
        except Exception as exc:
            S._track_read_safe("playbook_execution", success=False)
            return f"查询执行状态失败: {S._safe_err(exc)}"
        # Read sibling of prepare: returns the stored playbook title +
        # (substituted) step bodies. Gate owner-only to match, or it re-opens
        # the same bypass (Codex round-16).
        result = S._gov_rt.maybe_govern_owner_only(
            S._engram.root, result, tool="playbook_execution"
        )
        _inject_usage_policy(result, _EXECUTION_USAGE_POLICY)
        return S._json(result)
    return (
        f"未知 action: {action}。可用: prepare / update_step / status。 "
        f"/ Unknown action: {action}. Available: prepare / update_step / status."
    )


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="register_tool")
    if refusal is not None:
        return refusal

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
        result = S._locked_engram_call(
            S._engram.register_tool,
            tool_entry,
            registered_by=source_tool,
        )
        S._track("register_tool", success=True)
    except Exception as exc:
        S._track("register_tool", success=False)
        return f"注册工具失败: {S._safe_err(exc)}"
    action = result.pop("_action", "registered")
    action_zh = "已更新" if action == "updated" else "已注册"
    return f"工具{action_zh}: {name}" + (f" ({path})" if path else "")


@S.mcp.tool()
async def find_tool(query: str) -> str:
    """搜索已注册的本地工具/程序。 / Search for registered local tools and programs.

    用途：需要查找某个工具的路径、版本或安装方式时调用。避免重复搜索或重新安装已有工具。
    Purpose: Call when you need a tool's path, version, or install method. Prevents re-searching or re-installing.

    Args:
        query: 搜索关键词（名称、分类、用途均可匹配）。 / Search keywords matching name, category, purpose, or path.
    """
    try:
        results = S._engram.find_tool(query)
        S._track("find_tool", success=True)
    except Exception as exc:
        S._track("find_tool", success=False)
        return f"搜索工具失败: {S._safe_err(exc)}"
    if not results:
        return f"未找到匹配 '{query}' 的工具。"
    return S._json(results)


@S.mcp.tool()
async def list_tools(category: str = "") -> str:
    """列出所有已注册的本地工具/程序。 / List all registered local tools and programs.

    用途：查看当前环境中所有已知的工具、运行时和程序。
    Purpose: View all known tools, runtimes, and programs in the current environment.

    Args:
        category: 按分类筛选（runtime, cli, library, credential, config, service, other），留空列出全部。 / Filter by category; empty lists all.
    """
    try:
        results = S._engram.list_tools(category=category or None)
        S._track("list_tools", success=True)
    except Exception as exc:
        S._track("list_tools", success=False)
        return f"列出工具失败: {S._safe_err(exc)}"
    if not results:
        return "尚无已注册的工具。"
    return S._json(results)

