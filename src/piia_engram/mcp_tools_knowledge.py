"""Knowledge-ops MCP tools (bulk add, merge, lifecycle, decisions)."""
from __future__ import annotations

import json

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]


def _confirmation_detail(content) -> str:
    return json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)


def _confirmation_required(kind: str, title: str, content) -> str:
    return S._json({
        "status": "confirmation_required",
        "requires_confirmation": True,
        "changed": False,
        "dry_run": True,
        "kind": kind,
        "content_title": title,
        "content_detail": _confirmation_detail(content),
        "instruction": (
            "Show content_title and content_detail to the user. Only call the "
            "write tool again with user_confirmed=true after the user confirms."
        ),
    })


def _is_user_confirmed(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


@S.mcp.tool()
async def ingest_notes(
    text: str,
    source_tool: str = "",
    domain: str = "",
    user_confirmed: bool = False,
) -> str:
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="ingest_notes")
    if refusal is not None:
        return refusal
    preview = {
        "text": text,
        "source_tool": source_tool,
        "domain": domain,
    }
    if not _is_user_confirmed(user_confirmed):
        return _confirmation_required(
            "ingest_notes",
            "Ingest notes memory extraction",
            preview,
        )
    result = S._locked_engram_call(
        S._get_engram().ingest_notes,
        text,
        source_tool=source_tool,
        domain=domain,
    )
    return S._json(S._gov_rt.maybe_govern_write_ack(
        S._get_engram().root, result, tool="ingest_notes",
    ))


@S.mcp.tool()
async def extract_session_insights(
    summary: str,
    source_tool: str = "",
    project_folder: str = "",
    user_confirmed: bool = False,
) -> str:
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="extract_session_insights")
    if refusal is not None:
        return refusal
    preview = {
        "summary": summary,
        "source_tool": source_tool,
        "project_folder": project_folder,
    }
    if not _is_user_confirmed(user_confirmed):
        return _confirmation_required(
            "extract_session_insights",
            "Extract session insights memory write",
            preview,
        )
    if project_folder:
        S._session.detect_project(project_folder)
    result = S._locked_engram_call(
        S._get_engram().extract_session_insights,
        summary,
        source_tool=source_tool,
        project_folder=project_folder,
    )
    return S._json(S._gov_rt.maybe_govern_write_ack(
        S._get_engram().root, result, tool="extract_session_insights",
    ))


@S.mcp.tool()
async def update_knowledge(
    item_id: str, updates_json: str, expected_version: int | None = None
) -> str:
    """按 ID 更新 lesson、decision 或 playbook（自动识别类型）。 / Update a lesson, decision, or playbook by ID, automatically detecting the item type.

    用途：需要修改已有知识条目的内容、状态或元数据时调用。内容变更会自动保留旧版本为不可变快照并递增版本号（用 get_knowledge_history 查看）。
    Purpose: Call when an existing knowledge item's content, status, or metadata needs to be changed. Content changes automatically retain the prior body as an immutable snapshot and bump the version (see get_knowledge_history).

    注意：如果只是确认某条知识仍有效，用 review_staging(action="review_item")；如果要归档，用 archive_knowledge。
    Note: If you only need to confirm an item is still valid, use review_staging(action="review_item"); to archive, use archive_knowledge.

    Args:
        item_id: lesson、decision 或 playbook 的 ID。 / ID of the lesson, decision, or playbook.
        updates_json: 要更新字段的 JSON 字符串。 / JSON string containing fields to update.
        expected_version: 乐观并发保护（可选）：传入当前版本号，不匹配则拒绝写入且零改动（version_conflict）。修订指引的 guidance.revision.expected_version 会给出当前值。 / Optimistic-concurrency guard (optional): the current version; a mismatch refuses the write with zero changes (version_conflict). The dedup guidance's revision.expected_version carries the current value.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="update_knowledge")
    if refusal is not None:
        return refusal

    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return S._json({"error": "updates_json must be valid JSON"})
    # Returns the FULL stored item; an attacker who guesses an id can no-op
    # update and read a secret item back through this "write" tool (Codex
    # round-16 P1-3). Gate the returned item — over-ceiling → withheld stub.
    result = S._locked_engram_call(
        S._get_engram().update_knowledge, item_id, updates, expected_version=expected_version
    )
    result = S._gov_rt.maybe_govern_one(S._get_engram().root, result, tool="update_knowledge")
    return S._json(result)


@S.mcp.tool()
async def get_knowledge_history(
    item_id: str, include_bodies: bool = False, version: int | None = None
) -> str:
    """查看一个知识条目（lesson/decision/playbook）的修订历史。 / Return the revision history (superseded snapshots) of one knowledge item.

    **Lifecycle: retrieval** — 需要追溯某条知识改过什么、何时改的、改前长什么样时调用。
    Lifecycle: retrieval — call when you need what changed on an item, when, and what the prior body looked like.

    用途：修订后核对旧行为、审计版本链、或找回被改掉的内容。
    Purpose: verify prior behavior after a revision, audit a version chain, or recover replaced content.

    Args:
        item_id: 条目 ID（稳定 HEAD id，不是快照 id）。 / Item id (the stable HEAD id, not a snapshot id).
        include_bodies: 是否在结果里带快照正文（默认 false 只给元数据）。 / Include snapshot bodies in the result (default false returns metadata only).
        version: 精确按版本号取一个快照（可选）；不存在时返回 version_not_found 而不是近似值。 / Exact by-version snapshot lookup (optional); a miss returns version_not_found, never a nearest match.
    """
    result = S._locked_engram_call(
        S._get_engram().get_knowledge_history,
        item_id,
        include_bodies=include_bodies,
        version=version,
    )
    # History payloads embed knowledge bodies in the "snapshots" list (and the
    # by-version "snapshot" item): govern them like any mixed-list read.
    result = S._gov_rt.maybe_govern_result(
        S._get_engram().root, result, tool="get_knowledge_history",
        list_fields=("snapshots",), item_fields=("snapshot",),
    )
    return S._json(result)


@S.mcp.tool()
async def archive_knowledge(item_id: str) -> str:
    """按 ID 归档 lesson 或 decision（自动识别类型）。 / Archive a lesson or decision by ID, automatically detecting the item type.

    用途：某条知识已经过时但不应删除时调用。
    Purpose: Call when a knowledge item is outdated but should be preserved rather than deleted.

    注意：如果只是内容重复需要合并，用 merge_knowledge。
    Note: If the item is a duplicate that should be merged, use merge_knowledge.

    Args:
        item_id: 要归档的 lesson 或 decision ID。 / ID of the lesson or decision to archive.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="archive_knowledge")
    if refusal is not None:
        return refusal

    result = S._locked_engram_call(S._get_engram().archive_knowledge, item_id)
    S._beta("knowledge_rejected", action="archive")
    # Returns the full stored item (delegates to update_*) — same read-back
    # bypass as update_knowledge; gate the returned item (Codex round-16 P1-3).
    result = S._gov_rt.maybe_govern_one(S._get_engram().root, result, tool="archive_knowledge")
    return S._json(result)


@S.mcp.tool()
async def confirm_knowledge(
    item_id: str,
    by: str = "human",
    anchor_ref: str = "",
    project_root: str = "",
) -> str:
    """Owner-only: explicitly stamp a knowledge item with human/test/anchor freshness provenance.

    Owner/admin surface: writes owner-confirmed provenance stamps and is refused for non-owner callers when governance is enabled.

    用途：用户/owner 已经确认某条知识仍成立，或明确背书它由测试信号/锚点支撑时调用。
    Purpose: Call only after explicit owner confirmation that a knowledge item is
    still valid, or is backed by a test signal / owner-approved anchor.

    Args:
        item_id: lesson、decision 或 playbook 的 ID。 / ID of the lesson, decision, or playbook.
        by: human（人确认）| test（测试信号）| anchor（显式锚点）。 / human | test | anchor.
        anchor_ref: by=anchor 时必填的锚点字符串，如 dep:jest 或 file:package.json。 / Required when by=anchor.
        project_root: by=anchor 时可选的当前仓库根目录，用于捕获 anchor_project_id。 / Optional current repository root for anchor project binding.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="confirm_knowledge")
    if refusal is not None:
        return refusal

    anchor_project_id = None
    if str(by or "").strip().lower() == "anchor" and project_root.strip():
        from piia_engram import freshness_anchors

        anchor_project_id = freshness_anchors.read_project_id(project_root)

    result = S._locked_engram_call(
        S._get_engram().confirm_knowledge,
        item_id,
        by=by,
        anchor_ref=anchor_ref or None,
        anchor_project_id=anchor_project_id,
    )
    result = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, result, tool="confirm_knowledge"
    )
    return S._json(result)


@S.mcp.tool()
async def onboard_repo(project_root: str = "") -> str:
    """Owner-only: scan a repo and create staging repo-fact candidates.

    Owner/admin surface: writes staging candidate repo-facts and is refused for
    non-owner callers when governance is enabled.

    用途：owner 扫描仓库中的 npm/Python/file 锚点，生成 staging 候选事实供后续确认；
    不会自动验证或提升信任。
    Purpose: Scan the repo's npm/Python/file anchors and create staging
    repo-fact candidates for the owner to accept later. Nothing is auto-verified.

    Args:
        project_root: 仓库根目录；留空时使用当前工作目录。 / Repository root; defaults to cwd.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="onboard_repo")
    if refusal is not None:
        return refusal

    import os as _os

    root = project_root.strip() or _os.getcwd()
    result = S._locked_engram_call(S._get_engram().onboard_repo, root)
    result = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, result, tool="onboard_repo"
    )
    return S._json(result)


@S.mcp.tool()
async def onboard_accept(item_id: str, project_root: str = "") -> str:
    """Owner-only: accept an onboard candidate and stamp anchor provenance.

    Owner/admin surface: promotes a staging candidate to a verified owner fact
    and is refused for non-owner callers when governance is enabled.

    用途：owner 确认一条 onboard 候选，先按仓库校验其锚点，再提升为 verified 并盖
    anchor 确认戳；锚点无效或绑定到不同仓库时拒绝。
    Purpose: Owner-accept an onboard candidate by checking its anchor against
    the repo, then promoting it to a verified fact with anchor provenance.

    Args:
        item_id: onboard 候选的 ID。 / The onboard candidate id.
        project_root: 仓库根目录；留空时使用当前工作目录。 / Repository root; defaults to cwd.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="onboard_accept")
    if refusal is not None:
        return refusal

    import os as _os

    root = project_root.strip() or _os.getcwd()
    result = S._locked_engram_call(
        S._get_engram().accept_onboard_candidate, item_id, project_root=root
    )
    result = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, result, tool="onboard_accept"
    )
    return S._json(result)


@S.mcp.tool()
async def check_anchors(
    project_root: str,
    adopt_legacy: bool = False,
) -> str:
    """Owner-only: revalidate anchor-backed freshness provenance for one repository.

    Owner/admin surface: checks project-local anchors and writes anchor_status / anchor_checked_at; refused for non-owner callers when governance is enabled.

    用途：owner 在当前仓库内确认依赖或文件锚点是否仍成立；失效锚点会回落时间衰减。
    Purpose: Let the owner re-check dependency/file anchors for a repository;
    invalid anchors fall back to time decay through the pure freshness policy.

    Args:
        project_root: 当前仓库根目录。 / Current repository root.
        adopt_legacy: 是否为旧 anchor 绑定当前 project id，但不重新判定状态。 / Whether to bind legacy anchors to the current project id without changing status.
    """
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="check_anchors")
    if refusal is not None:
        return refusal

    result = S._locked_engram_call(
        S._get_engram().revalidate_anchors,
        project_root,
        adopt_legacy=adopt_legacy,
    )
    result = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, result, tool="check_anchors"
    )
    return S._json(result)


@S.mcp.tool()
async def review_staging(
    action: str = "list",
    actions_json: str = "[]",
    confirm: bool = False,
    dry_run: bool = True,
    filters_json: str = "{}",
    limit: int = 50,
    offset: int = 0,
    knowledge_id: str = "",
    review_text: str = "",
) -> str:
    """知识评审统一入口：列队列 / 批量审批 / 单条复习 / 执行审查结果。 / Unified knowledge review: list the staging queue, batch-approve, refresh one item, or apply review-page results.

    用途：action=list 查看待审核 staging 候选（metadata-only，只返回 id/类型/领域/
    计数，不回显正文）；batch 批量 approve/reject staging 候选（默认 dry_run 预览，
    confirm=True 才落盘）；review_item 标记单条知识"已复习"（只刷新 last_reviewed，
    不改内容）；apply_text 执行审查页面粘贴回来的归档结果。
    Purpose: action=list inspects pending staging candidates (metadata-only);
    batch approves/rejects candidates (dry-run preview by default); review_item
    marks one knowledge item as reviewed; apply_text executes archive results
    pasted back from the review page.

    Cross-queue visibility: list 响应附带 ``other_queues`` —— 其他待审积压的计数
    （如 playbook scope review），空 staging 队列不会掩盖其他待办。
    The list response includes ``other_queues`` so an empty staging queue never
    hides pending work elsewhere.

    Args:
        action: list（默认）| batch | review_item | apply_text。
        actions_json: JSON array of {"id": "...", "action": "approve|reject"}（batch）。
        confirm: 与 dry_run=False 同时为 True 才真正变更（batch）。 / Must be true together with dry_run=false to mutate (batch).
        dry_run: 默认 True 只预览不变更（batch）。 / Defaults to true; no knowledge is changed when true (batch).
        filters_json: 过滤 JSON 对象，如 {"type":"decision","domain":"release"}（list/batch）。 / Filters JSON object (list/batch).
        limit: 列表条数上限（list）。 / Max pending items to list.
        offset: 列表偏移（list）。 / Pending-list offset.
        knowledge_id: 要复习的知识条目 ID（review_item）。 / ID of the knowledge item to refresh (review_item).
        review_text: 审查结果文本或 JSON 字符串（apply_text）。 / Review results text or JSON string (apply_text).
    """
    # a4: write-path governance gate — must run unconditionally BEFORE action
    # validation so a low-trust caller gets a governance refusal, never an
    # "unknown action" hint (writer-spy matrix). Note: this is tighter than the
    # old read-only list_pending_staging — web callers are now refused even for
    # action="list" (documented in the v4 migration guide).
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="review_staging")
    if refusal is not None:
        return refusal

    action = action.strip().lower()
    if action == "list":
        try:
            filters = json.loads(filters_json or "{}")
        except json.JSONDecodeError:
            return S._json({"error": "filters_json must be a valid JSON object"})
        if not isinstance(filters, dict):
            return S._json({"error": "filters_json must be a JSON object"})
        from piia_engram.staging_review import list_pending_staging as _list_pending

        result = S._locked_engram_call(
            _list_pending,
            S._get_engram(),
            filters=filters,
            limit=limit,
            offset=offset,
        )
        S._track_read_safe("review_staging", success=True)
        return S._json(result)
    if action == "batch":
        try:
            actions = json.loads(actions_json)
        except json.JSONDecodeError:
            return S._json({"error": "actions_json must be a valid JSON array"})
        if not isinstance(actions, list):
            return S._json({"error": "actions_json must be a JSON array"})
        try:
            filters = json.loads(filters_json or "{}")
        except json.JSONDecodeError:
            return S._json({"error": "filters_json must be a valid JSON object"})
        if not isinstance(filters, dict):
            return S._json({"error": "filters_json must be a JSON object"})
        from piia_engram.staging_review import batch_review_staging as _batch_review

        result = S._locked_engram_call(
            _batch_review,
            S._get_engram(),
            actions,
            confirm=confirm,
            dry_run=dry_run,
            operation="review",
            filters=filters,
            limit=limit,
            offset=offset,
        )
        return S._json(S._gov_rt.maybe_govern_write_ack(
            S._get_engram().root, result, tool="review_staging",
        ))
    if action == "review_item":
        if not knowledge_id:
            return (
                "action=review_item 需要提供 knowledge_id。 "
                "/ action=review_item requires knowledge_id."
            )
        result = S._locked_engram_call(S._get_engram().review_knowledge, knowledge_id)
        S._beta("knowledge_reviewed")
        # Pure read-disguised-as-write: only bumps last_reviewed yet returns the
        # full stored item. Gate the returned item (Codex round-16 P1-3).
        result = S._gov_rt.maybe_govern_one(S._get_engram().root, result, tool="review_staging")
        return S._json(result)
    if action == "apply_text":
        if not review_text:
            return (
                "action=apply_text 需要提供 review_text。 "
                "/ action=apply_text requires review_text."
            )
        # Try to parse as JSON first
        try:
            data = json.loads(review_text)
            if isinstance(data, dict) and "archive" in data:
                result = S._locked_engram_call(S._get_engram().apply_review, data)
                return S._json(result)
        except (ValueError, TypeError):
            pass
        # Treat as text format
        result = S._locked_engram_call(S._get_engram().apply_review, review_text)
        return S._json(result)
    return (
        f"未知 action: {action}。可用: list / batch / review_item / apply_text。 "
        f"/ Unknown action: {action}. Available: list / batch / review_item / apply_text."
    )


@S.mcp.tool()
async def get_stale_knowledge(days: int = 30, limit: int = 20) -> str:
    """列出超过指定天数未复习的知识条目。 / List knowledge items not reviewed for more than the specified number of days.

    用途：定期检查哪些经验或决策需要复习或归档。
    Purpose: Call during periodic maintenance to find lessons or decisions that need review or archiving.

    注意：如果想直接归档某条，用 archive_knowledge；如果确认仍有效，用 review_staging(action="review_item") 刷新。
    Note: Use archive_knowledge to archive an item directly; use review_staging(action="review_item") to refresh an item that is still valid.

    Args:
        days: 超过多少天算过期（默认 30）。 / Number of days after which an item is stale (default 30).
        limit: 最多返回多少条（默认 20）。 / Maximum number of items to return (default 20).
    """
    stale = S._get_engram().get_stale_knowledge(days=days, limit=limit)
    # dict of {days, limit, lessons:[...], decisions:[...]} — buckets filters the
    # two item lists (titles can themselves carry sensitive text), scalars pass.
    stale = S._gov_rt.maybe_govern_buckets(S._get_engram().root, stale, tool="get_stale_knowledge")
    return S._json(stale)


@S.mcp.tool()
async def request_outline_review(lang: str = "zh") -> str:
    """生成交互式知识审查 HTML 页面，用户可在浏览器中逐条保留或归档知识。 / Generate an interactive knowledge review HTML page where the user can retain or archive items.

    Owner/export surface: writes an exports/review_*.html file and is refused for non-owner callers when governance is enabled.

    用途：用户说"帮我核对一下记忆"、"看看我的知识库"、"review my knowledge"时调用。
    Purpose: Call when the user wants to audit their knowledge base, e.g. "review my knowledge" or "check my memory".

    生成的 HTML 页面包含身份画像总览、经验教训（按领域分组）、关键决策，可逐条勾选保留/归档。
    用户审查完成后，将结果粘贴回对话或下载 JSON，再调用 review_staging(action="apply_text") 执行归档。

    Args:
        lang: 页面语言，"zh"（中文）或 "en"（英文），默认中文。 / Page language: "zh" (Chinese) or "en" (English), default "zh".
    """
    # The review HTML embeds profile + all lessons + key decisions; path-only
    # return still writes the full bodies to disk (Codex round-16 P2-1). Gate
    # before generating — a non-owner gets a refusal and no page is written.
    refusal = S._gov_rt.maybe_refuse_export(S._get_engram().root, tool="request_outline_review")
    if refusal is not None:
        return refusal
    path = S._get_engram().export_review_page(lang=lang)
    return S._json({
        "status": "review_page_generated",
        "path": str(path),
        "message": f"知识审查页面已生成: {path}。请在浏览器中打开，审查完成后将结果粘贴回对话。"
        if lang == "zh"
        else f"Review page generated: {path}. Open in browser, then paste review results back.",
    })


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="merge_knowledge")
    if refusal is not None:
        return refusal

    # Returns {primary_title, secondary_title} — stored titles the caller only
    # referenced by id. Gate the ack so lower tiers don't read titles back
    # (Codex round-16 write-echo class).
    result = S._locked_engram_call(S._get_engram().merge_knowledge, primary_id, secondary_id)
    result = S._gov_rt.maybe_govern_write_ack(S._get_engram().root, result, tool="merge_knowledge")
    return S._json(result)


@S.mcp.tool()
async def manage_relation(
    action: str,
    src_id: str,
    dst_id: str,
    rel: str = "",
) -> str:
    """知识关系统一入口：建立或移除条目间关联（无类型双向 / 有类型有向）。 / Unified knowledge relations: create or remove links between items (untyped bidirectional, or typed directed).

    用途：rel 留空时管理无类型、双向的"see also"关联；rel 取 led_to / supersedes /
    implemented_by 时管理有类型、有方向的演进边，用于重建"想法 → 决策 → 实现"
    决策链（喂给 get_decisions 的 thread_seed_id 分支）。unlink 幂等——关系不存在
    也不报错。
    Purpose: with rel empty this manages the untyped bidirectional "see also"
    link; with rel set (led_to / supersedes / implemented_by) it manages the
    typed, directed evolution edge consumed by decision threads
    (get_decisions thread_seed_id). unlink is idempotent.

    rel 取值 / values:
      - led_to：src 引出 / 导致 dst（src led to dst）
      - supersedes：src 取代 / 推翻 dst（src replaces dst; dst becomes obsolete）
      - implemented_by：决策 src 由 dst 实现（decision src realized by dst）

    Args:
        action: link（建立）| unlink（移除，幂等）。 / link (create) | unlink (remove, idempotent).
        src_id: 源条目 ID（无类型关联时即第一个条目）。 / Source item ID (first item for untyped links).
        dst_id: 目标条目 ID（无类型关联时即第二个条目）。 / Target item ID (second item for untyped links).
        rel: 留空 = 无类型双向；led_to / supersedes / implemented_by = 有类型有向。 / Empty = untyped bidirectional; led_to / supersedes / implemented_by = typed directed.
    """
    # a4: write-path governance gate — must run unconditionally BEFORE action
    # validation so a low-trust caller gets a governance refusal, never an
    # "unknown action" hint (writer-spy matrix).
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="manage_relation")
    if refusal is not None:
        return refusal

    action = action.strip().lower()
    if action not in ("link", "unlink"):
        return (
            f"未知 action: {action}。可用: link / unlink。 "
            f"/ Unknown action: {action}. Available: link / unlink."
        )
    if rel:
        if rel not in ("led_to", "supersedes", "implemented_by"):
            return (
                "rel 需为 led_to / supersedes / implemented_by，或留空表示无类型双向关联。 "
                "/ rel must be led_to / supersedes / implemented_by, "
                "or empty for an untyped bidirectional link."
            )
        if action == "link":
            return S._json(S._locked_engram_call(S._get_engram().add_relation, src_id, rel, dst_id))
        return S._json(S._locked_engram_call(S._get_engram().remove_relation, src_id, rel, dst_id))
    # Untyped bidirectional link/unlink. Ack message embeds both item titles
    # ("Linked: <title> ↔ <title>") — gate so a low-trust caller can't read a
    # secret title back (round-16 write-echo).
    if action == "link":
        result = S._locked_engram_call(S._get_engram().link_knowledge, src_id, dst_id)
    else:
        result = S._locked_engram_call(S._get_engram().unlink_knowledge, src_id, dst_id)
    result = S._gov_rt.maybe_govern_write_ack(S._get_engram().root, result, tool="manage_relation")
    return S._json(result)

