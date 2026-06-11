"""Knowledge-ops MCP tools (bulk add, merge, lifecycle, decisions)."""
from __future__ import annotations

import json

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]

@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="ingest_notes")
    if refusal is not None:
        return refusal
    return S._json(S._locked_engram_call(
        S._engram.ingest_notes,
        text,
        source_tool=source_tool,
        domain=domain,
    ))


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="extract_session_insights")
    if refusal is not None:
        return refusal
    return S._json(S._locked_engram_call(
        S._engram.extract_session_insights,
        summary,
        source_tool=source_tool,
    ))


@S.mcp.tool()
async def update_knowledge(item_id: str, updates_json: str) -> str:
    """按 ID 更新 lesson 或 decision（自动识别类型）。 / Update a lesson or decision by ID, automatically detecting the item type.

    用途：需要修改已有知识条目的内容、状态或元数据时调用。
    Purpose: Call when an existing knowledge item's content, status, or metadata needs to be changed.

    注意：如果只是确认某条知识仍有效，用 review_staging(action="review_item")；如果要归档，用 archive_knowledge。
    Note: If you only need to confirm an item is still valid, use review_staging(action="review_item"); to archive it, use archive_knowledge.

    Args:
        item_id: lesson 或 decision 的 ID。 / ID of the lesson or decision.
        updates_json: 要更新字段的 JSON 字符串。 / JSON string containing fields to update.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="update_knowledge")
    if refusal is not None:
        return refusal

    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return S._json({"error": "updates_json must be valid JSON"})
    # Returns the FULL stored item; an attacker who guesses an id can no-op
    # update and read a secret item back through this "write" tool (Codex
    # round-16 P1-3). Gate the returned item — over-ceiling → withheld stub.
    result = S._locked_engram_call(S._engram.update_knowledge, item_id, updates)
    result = S._gov_rt.maybe_govern_one(S._engram.root, result, tool="update_knowledge")
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
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="archive_knowledge")
    if refusal is not None:
        return refusal

    result = S._locked_engram_call(S._engram.archive_knowledge, item_id)
    S._beta("knowledge_rejected", action="archive")
    # Returns the full stored item (delegates to update_*) — same read-back
    # bypass as update_knowledge; gate the returned item (Codex round-16 P1-3).
    result = S._gov_rt.maybe_govern_one(S._engram.root, result, tool="archive_knowledge")
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
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="review_staging")
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
            S._engram,
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
            S._engram,
            actions,
            confirm=confirm,
            dry_run=dry_run,
            operation="review",
            filters=filters,
            limit=limit,
            offset=offset,
        )
        return S._json(S._gov_rt.maybe_govern_write_ack(
            S._engram.root, result, tool="review_staging",
        ))
    if action == "review_item":
        if not knowledge_id:
            return (
                "action=review_item 需要提供 knowledge_id。 "
                "/ action=review_item requires knowledge_id."
            )
        result = S._locked_engram_call(S._engram.review_knowledge, knowledge_id)
        S._beta("knowledge_reviewed")
        # Pure read-disguised-as-write: only bumps last_reviewed yet returns the
        # full stored item. Gate the returned item (Codex round-16 P1-3).
        result = S._gov_rt.maybe_govern_one(S._engram.root, result, tool="review_staging")
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
                result = S._locked_engram_call(S._engram.apply_review, data)
                return S._json(result)
        except (ValueError, TypeError):
            pass
        # Treat as text format
        result = S._locked_engram_call(S._engram.apply_review, review_text)
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
    stale = S._engram.get_stale_knowledge(days=days, limit=limit)
    # dict of {days, limit, lessons:[...], decisions:[...]} — buckets filters the
    # two item lists (titles can themselves carry sensitive text), scalars pass.
    stale = S._gov_rt.maybe_govern_buckets(S._engram.root, stale, tool="get_stale_knowledge")
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
    refusal = S._gov_rt.maybe_refuse_export(S._engram.root, tool="request_outline_review")
    if refusal is not None:
        return refusal
    path = S._engram.export_review_page(lang=lang)
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
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="merge_knowledge")
    if refusal is not None:
        return refusal

    # Returns {primary_title, secondary_title} — stored titles the caller only
    # referenced by id. Gate the ack so lower tiers don't read titles back
    # (Codex round-16 write-echo class).
    result = S._locked_engram_call(S._engram.merge_knowledge, primary_id, secondary_id)
    result = S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="merge_knowledge")
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
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="manage_relation")
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
            return S._json(S._locked_engram_call(S._engram.add_relation, src_id, rel, dst_id))
        return S._json(S._locked_engram_call(S._engram.remove_relation, src_id, rel, dst_id))
    # Untyped bidirectional link/unlink. Ack message embeds both item titles
    # ("Linked: <title> ↔ <title>") — gate so a low-trust caller can't read a
    # secret title back (round-16 write-echo).
    if action == "link":
        result = S._locked_engram_call(S._engram.link_knowledge, src_id, dst_id)
    else:
        result = S._locked_engram_call(S._engram.unlink_knowledge, src_id, dst_id)
    result = S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="manage_relation")
    return S._json(result)



