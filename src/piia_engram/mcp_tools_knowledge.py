"""Knowledge-ops MCP tools (bulk add, merge, lifecycle, decisions)."""
from __future__ import annotations

import json

try:
    from . import mcp_server as S
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]

@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="bulk_add_knowledge")
    if refusal is not None:
        return refusal
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError:
        return S._json({"error": "items_json must be a valid JSON array"})
    if not isinstance(items, list):
        return S._json({"error": "items_json must be a JSON array"})
    # An agent cannot self-certify trust: strip any tier/approval fields each
    # item smuggled through items_json so the risk-based write gate stays the
    # sole authority over tier (high-risk items -> staging, not verified).
    for _item in items:
        S.strip_untrusted_trust_fields(_item)
    return S._json(S._locked_engram_call(
        S._engram.bulk_add_knowledge,
        items,
        item_type=item_type,
        source_tool=source_tool,
    ))


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

    注意：如果只是确认某条知识仍有效，用 review_knowledge；如果要归档，用 archive_knowledge。
    Note: If you only need to confirm an item is still valid, use review_knowledge; to archive it, use archive_knowledge.

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
async def review_knowledge(knowledge_id: str) -> str:
    """标记一条知识为"已复习"（刷新 last_reviewed 时间戳，不改内容）。 / Mark one knowledge item as reviewed, refreshing last_reviewed without changing content.

    用途：用户确认某条经验或决策仍然有效时调用，防止它被标记为过期。
    Purpose: Call when the user confirms a lesson or decision is still valid, preventing it from being treated as stale.

    注意：如果要修改内容，用 update_knowledge。
    Note: Use update_knowledge when the content itself needs to change.

    Args:
        knowledge_id: 要复习的知识条目 ID。 / ID of the knowledge item to review.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="review_knowledge")
    if refusal is not None:
        return refusal

    result = S._locked_engram_call(S._engram.review_knowledge, knowledge_id)
    S._beta("knowledge_reviewed")
    # Pure read-disguised-as-write: only bumps last_reviewed yet returns the full
    # stored item. Gate the returned item (Codex round-16 P1-3).
    result = S._gov_rt.maybe_govern_one(S._engram.root, result, tool="review_knowledge")
    return S._json(result)


@S.mcp.tool()
async def batch_review_staging(
    actions_json: str,
    confirm: bool = False,
    dry_run: bool = True,
    operation: str = "review",
    filters_json: str = "{}",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """批量审核 staging 知识候选（默认只预览）。 / Batch-review staging knowledge candidates (preview by default).

    Purpose: Use when multiple staging lessons or decisions should be approved
    or rejected in one owner-reviewed operation. The payload is metadata-only:
    ids, actions, statuses, and counts; it never echoes stored bodies.

    Args:
        actions_json: JSON array of {"id": "...", "action": "approve|reject"}.
        confirm: Must be true together with dry_run=false to mutate.
        dry_run: Defaults to true. When true, no knowledge is changed.
        operation: "review" (default) or "list_pending" for metadata-only queue.
        filters_json: JSON object for list_pending filters, e.g.
            {"type":"decision","domain":"release"}.
        limit: Max pending items to list.
        offset: Pending-list offset.
    """
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="batch_review_staging")
    if refusal is not None:
        return refusal
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
        operation=operation,
        filters=filters,
        limit=limit,
        offset=offset,
    )
    return S._json(S._gov_rt.maybe_govern_write_ack(
        S._engram.root, result, tool="batch_review_staging",
    ))


@S.mcp.tool()
async def list_pending_staging(
    filters_json: str = "{}",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """列出待审核 staging 候选（只读、metadata-only）。 / List pending staging candidates (read-only, metadata-only).

    Purpose: use before review to inspect the staging queue without approving,
    rejecting, or exposing draft bodies. Returns ids, types, domains, priority
    scores and counts only; never stored summaries/details/reasoning.

    Cross-queue visibility: the response also includes ``other_queues`` —
    counts for other pending review backlogs (e.g. playbook scope review),
    so an empty staging queue never hides pending work elsewhere.

    Args:
        filters_json: Optional JSON object, e.g. {"type":"decision","domain":"release"}.
        limit: Max pending items to list.
        offset: Pending-list offset.
    """
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
    return S._json(result)


@S.mcp.tool()
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
    用户审查完成后，将结果粘贴回对话或下载 JSON，再调用 apply_review 执行归档。

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
async def apply_review(review_text: str) -> str:
    """执行知识审查结果——归档用户标记为不需要的条目。 / Execute knowledge review results — archive items the user marked for removal.

    用途：用户从审查页面复制审查结果文本后，调用此工具执行归档操作。
    Purpose: After the user copies review results from the HTML review page, call this to execute archival.

    输入格式（每行一条 `archive lesson <id>` 或 `archive decision <id>`），或审查页面下载的 JSON 字符串。

    Args:
        review_text: 审查结果文本或 JSON 字符串。 / Review results text or JSON string from the review page.
    """
    # a4: write-path governance gate — apply_review archives stored items.
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="apply_review")
    if refusal is not None:
        return refusal

    import json as _json_mod

    # Try to parse as JSON first
    try:
        data = _json_mod.loads(review_text)
        if isinstance(data, dict) and "archive" in data:
            result = S._locked_engram_call(S._engram.apply_review, data)
            return S._json(result)
    except (ValueError, TypeError):
        pass

    # Treat as text format
    result = S._locked_engram_call(S._engram.apply_review, review_text)
    return S._json(result)


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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="link_knowledge")
    if refusal is not None:
        return refusal

    # Ack message embeds both item titles ("Linked: <title> ↔ <title>") — gate
    # so a low-trust caller can't read a secret title back (round-16 write-echo).
    result = S._locked_engram_call(S._engram.link_knowledge, id_a, id_b)
    result = S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="link_knowledge")
    return S._json(result)


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="unlink_knowledge")
    if refusal is not None:
        return refusal

    # Ack message embeds both item titles — same write-echo gate as link_knowledge.
    result = S._locked_engram_call(S._engram.unlink_knowledge, id_a, id_b)
    result = S._gov_rt.maybe_govern_write_ack(S._engram.root, result, tool="unlink_knowledge")
    return S._json(result)


@S.mcp.tool()
async def add_relation(src_id: str, rel: str, dst_id: str) -> str:
    """在两条知识之间建立【有类型、有方向】的关系，用于重建决策链。 / Create a TYPED, DIRECTED relation between two knowledge items, for reconstructing decision threads.

    用途：记录"想法 → 决策 → 实现"的演进。Purpose: record how a decision evolved.
    rel 取值 / values:
      - led_to：src 引出 / 导致 dst（src led to dst）
      - supersedes：src 取代 / 推翻 dst（src replaces dst; dst becomes obsolete）
      - implemented_by：决策 src 由 dst 实现（decision src realized by dst）

    与 link_knowledge 的区别：link_knowledge 是无类型、双向的"see also"；
    本工具是有类型、有方向的演进边，专门喂给 get_decision_thread。
    Difference from link_knowledge: that is an untyped bidirectional "see also";
    this is a typed, directed evolution edge consumed by get_decision_thread.

    Args:
        src_id: 源条目 ID。 / Source item ID.
        rel: led_to / supersedes / implemented_by。
        dst_id: 目标条目 ID。 / Target item ID.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="add_relation")
    if refusal is not None:
        return refusal
    return S._json(S._locked_engram_call(S._engram.add_relation, src_id, rel, dst_id))


@S.mcp.tool()
async def remove_relation(src_id: str, rel: str, dst_id: str) -> str:
    """移除两条知识之间的有类型关系（add_relation 的撤销）。 / Remove a typed, directed relation between two knowledge items (undo of add_relation).

    用途：关系建错了或不再成立时调用。幂等——关系不存在也不报错。
    Purpose: Call when a relation was created by mistake or is no longer valid. Idempotent.

    rel 取值 / values: led_to / supersedes / implemented_by（同 add_relation）。

    Args:
        src_id: 源条目 ID。 / Source item ID.
        rel: led_to / supersedes / implemented_by。
        dst_id: 目标条目 ID。 / Target item ID.
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._engram.root, tool="remove_relation")
    if refusal is not None:
        return refusal
    return S._json(S._locked_engram_call(S._engram.remove_relation, src_id, rel, dst_id))


@S.mcp.tool()
async def get_decision_thread(seed_id: str) -> str:
    """还原包含某条知识的【决策链】：这件事如何一步步演进到现在。 / Reconstruct the DECISION THREAD containing an item: how it evolved step by step.

    用途：换工具 / 跨会话时，快速看清"这个决策是怎么定下来的"。返回：按演进顺序
    排列的条目（order）、被取代项标记为 superseded、当前活跃节点（active_ids）与
    当前 head（heads）。只读，不修改任何知识。
    Purpose: quickly see how a decision was reached across tools/sessions. Returns
    items in evolution order, superseded ones flagged, plus active_ids and the
    current head(s). Read-only.

    关系由 add_relation 建立（led_to / supersedes / implemented_by）。
    Relations are built via add_relation.

    Args:
        seed_id: 决策链中任意一条的 ID。 / ID of any item in the thread.
    """
    thread = S._engram.get_decision_thread(seed_id)
    # 'order' rows are derived previews ({id, status, summary}) that do not
    # carry the source item's sensitivity label, so content-only gating would
    # be partial. Gate the derived thread view owner-only.
    thread = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, thread, tool="get_decision_thread"
    )
    return S._json(thread)


@S.mcp.tool()
async def get_decision_history(question: str, threshold: float = 0.6) -> str:
    """查询某个决策问题的完整修订历史：按时间顺序展示答案如何演变。 / Retrieve the full revision history of a decision question: show how the answer evolved in chronological order.

    用途：当你想知道"关于 X 我们之前怎么决定的""这个决策改过几次"时调用。与
    get_decision_thread 不同的是，本工具从【问题文本】出发而非 ID，自动模糊匹配
    所有相关决策，适合"我只记得大概问了什么"的场景。
    Purpose: call when you want to know "what did we decide about X" or "how many
    times did this decision change". Unlike get_decision_thread (which starts from
    an ID), this tool starts from **question text** and fuzzy-matches all related
    decisions — useful when you only remember the topic, not the ID.

    返回：按时间排列的 revisions 列表（每条含 id/question/choice/reasoning/timestamp/
    status/superseded_by）+ current 指向当前生效的决策。
    Returns: chronologically ordered revisions list + current points to the active one.

    Args:
        question: 决策问题的关键词或完整问题文本。 / Keywords or full question text to search for.
        threshold: 相似度阈值（0-1），默认 0.6。 / Similarity threshold (0-1), default 0.6.
    """
    result = S._engram.get_decision_history(question, threshold=threshold)
    result = S._gov_rt.maybe_govern_owner_only(
        S._engram.root, result, tool="get_decision_history"
    )
    return S._json(result)

