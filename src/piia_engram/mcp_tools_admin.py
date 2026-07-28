"""Admin MCP tools (permissions, governance, import/export, projects)."""
from __future__ import annotations

from typing import Optional
import json
import os
from time import perf_counter

try:
    from . import mcp_server as S
    from .session_closeout import (
        begin_wrap_up_operation as _begin_wrap_up_operation,
        budget_exhausted as _budget_exhausted,
        budget_metadata as _closeout_budget_metadata,
        configured_closeout_mode as _configured_closeout_mode,
        configured_wrap_up_budget_ms as _configured_wrap_up_budget_ms,
        finish_wrap_up_operation as _finish_wrap_up_operation,
        mark_wrap_up_stage as _mark_wrap_up_stage,
        public_wrap_up_operation_status as _public_wrap_up_operation_status,
        read_wrap_up_operation as _read_wrap_up_operation,
        resolve_wrap_up_operation_id as _resolve_wrap_up_operation_id,
        skipped_stage as _skipped_stage,
    )
except ImportError:  # plain-script mode (no package context)
    import mcp_server as S  # type: ignore[no-redef]
    from session_closeout import (  # type: ignore[no-redef]
        begin_wrap_up_operation as _begin_wrap_up_operation,
        budget_exhausted as _budget_exhausted,
        budget_metadata as _closeout_budget_metadata,
        configured_closeout_mode as _configured_closeout_mode,
        configured_wrap_up_budget_ms as _configured_wrap_up_budget_ms,
        finish_wrap_up_operation as _finish_wrap_up_operation,
        mark_wrap_up_stage as _mark_wrap_up_stage,
        public_wrap_up_operation_status as _public_wrap_up_operation_status,
        read_wrap_up_operation as _read_wrap_up_operation,
        resolve_wrap_up_operation_id as _resolve_wrap_up_operation_id,
        skipped_stage as _skipped_stage,
    )


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


def _elapsed_ms(start: float) -> int:
    return max(0, int((perf_counter() - start) * 1000))


def _count_value(value) -> int:
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@S.mcp.tool()
async def get_permission_profile() -> str:
    """查看当前所有调用者的权限全景：谁有什么信任级别、能看到什么。 / View the permission landscape: who has what trust level and what they can access.

    用途：想了解"哪些 AI 工具可以读我的 Engram"、"Cursor 能看到私密数据吗"时调用。
    显示显式授权、自动分类规则、信任级别定义、已撤销的调用者。
    Purpose: call when you want to know which AI tools can read your Engram data,
    what each one can access, and which ones have been revoked. Shows explicit
    grants, auto-classification rules, trust level definitions, and revoked callers.

    治理层开启时（ENGRAM_GOVERNANCE=1），仅 private-self 可调用。
    When governance is enabled, only the owner (private-self) can call this.
    """
    try:
        is_owner = S._gov_rt.caller_is_owner(S._get_engram().root)
    except Exception:
        is_owner = False
    if not is_owner:
        return S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, "", tool="get_permission_profile"
        )
    result = S._get_engram().get_permission_profile()
    result = S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, result, tool="get_permission_profile"
    )
    return S._json(result)


@S.mcp.tool()
async def manage_caller_trust(
    action: str,
    agent_id: str,
    trust_level: str = "",
) -> str:
    """调用者信任统一入口：授予 / 修改 / 撤销某个 AI 工具的信任级别。 / Unified caller-trust management: grant, change, or revoke an AI tool's trust level.

    Owner/admin surface: changes caller trust grants and is refused for non-owner callers when governance is enabled.

    用途：action=grant 设置或修改信任级别，例如让 Cursor 访问工作级数据：
    manage_caller_trust("grant", "cursor", "trusted-local")；action=revoke 撤销
    未来访问权（前向撤销——已返回的上下文无法召回；重新授权再调 grant）。
    Purpose: action=grant sets or changes a caller's trust level; action=revoke
    denies all future reads by that caller (forward-only — context already
    returned cannot be recalled; re-authorize via grant).

    可用信任级别 / Available trust levels:
    - private-self: 全部可见（自用/CLI/doctor） / Full access (self/CLI/doctor)
    - trusted-local: 公开+工作级可见（Claude Code/Codex/Cursor 等） / Public + work level (primary AI tools)
    - read-only-external: 仅公开可见（未知/外部工具） / Public only (unknown/external)

    Args:
        action: grant（授予/修改）| revoke（撤销）。 / grant | revoke.
        agent_id: 调用者标识（如 'cursor', 'codex', 'web-client'）。 / Caller identifier.
        trust_level: 要设置的信任级别（grant 必填）。 / Trust level to assign (required for grant).
    """
    # Owner-only gate — must run unconditionally BEFORE action validation so a
    # non-owner gets a governance refusal, never an "unknown action" hint
    # (writer-spy matrix). Keeps the grant store owner-controlled: no
    # self-escalation path re-opens here.
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="manage_caller_trust")
    if refusal is not None:
        return refusal

    action = action.strip().lower()
    if action == "grant":
        if not trust_level:
            return (
                "action=grant 需要提供 trust_level"
                "（private-self / trusted-local / read-only-external）。 "
                "/ action=grant requires trust_level."
            )
        result = S._locked_engram_call(S._get_engram().set_caller_trust, agent_id, trust_level)
        result = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, result, tool="manage_caller_trust"
        )
        return S._json(result)
    if action == "revoke":
        result = S._locked_engram_call(S._get_engram().revoke_caller, agent_id)
        result = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, result, tool="manage_caller_trust"
        )
        return S._json(result)
    return (
        f"未知 action: {action}。可用: grant / revoke。 "
        f"/ Unknown action: {action}. Available: grant / revoke."
    )


@S.mcp.tool()
async def update_identity(field: str, updates_json: str, source_tool: str = "") -> str:
    """更新一个身份字段。 / Update one identity field.

    用途：需要修改 profile、preferences、trust_boundaries、work_style 或 quality_standards 时调用。
    Purpose: Call when changing profile, preferences, trust_boundaries, work_style, or quality_standards.

    注意：updates_json 必须只包含该字段允许的键；敏感字段边界应通过 trust_boundaries 管理。
    Note: updates_json should contain only keys valid for that field; manage sensitive-field boundaries through trust_boundaries.

    Args:
        field: 字段名：profile、preferences、trust_boundaries、work_style 或 quality_standards。 / Field name: profile, preferences, trust_boundaries, work_style, or quality_standards.
        updates_json: 包含要更新字段的 JSON 字符串。 / JSON string containing the fields to update.
        source_tool: 调用来源工具（如 'claude_code', 'codex', 'cursor'），用于字段级溯源。 / Source tool for field-level provenance tracking.

    Field-specific keys / 字段专用键:
        profile: role, language, technical_level, description / role、language、technical_level、description。
        preferences: work_patterns (dict), communication (str), tool_preferences (dict), playbook_auto_extract (bool, default true) / work_patterns（字典）、communication（字符串）、tool_preferences（字典）、playbook_auto_extract（布尔，默认 true）。
        trust_boundaries: default_sharing, tool_access, private_fields, restricted_fields / default_sharing、tool_access、private_fields、restricted_fields。
        work_style: preferences (dict), communication (str) / preferences（字典）、communication（字符串）。
        quality_standards: acceptance_threshold (1-5), rules (list) / acceptance_threshold（1-5）、rules（列表）。
    """
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="update_identity")
    if refusal is not None:
        return refusal

    if field not in S.IDENTITY_FIELDS:
        return S._json({"error": f"Unknown field: {field}. Valid: {sorted(S.IDENTITY_FIELDS)}"})
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError:
        return S._json({"error": "updates_json must be valid JSON"})
    dispatch = {
        "profile": S._get_engram().update_profile,
        "preferences": S._get_engram().update_preferences,
        "trust_boundaries": S._get_engram().update_trust_boundaries,
        "work_style": S._get_engram().update_work_style,
        "quality_standards": S._get_engram().update_quality_standards,
    }
    try:
        fn = dispatch[field]
        # Pass source_tool for provenance tracking (profile supports it)
        if field == "profile" and source_tool:
            S._locked_engram_call(fn, updates, source_tool=source_tool)
        else:
            S._locked_engram_call(fn, updates)
        S._track("update_identity", success=True)
    except Exception as exc:
        S._track("update_identity", success=False)
        return S._json({
            "success": False,
            "field": field,
            "error": f"update_identity failed: {S._safe_err(exc)}",
        })
    return S._json({"success": True, "field": field, "updated_keys": list(updates.keys())})


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="save_project_snapshot")
    if refusal is not None:
        return refusal

    err = S._validate_path(project_folder)
    if err:
        return f"错误: {err}"
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return "错误: data_json 必须是合法的 JSON。"
    if not isinstance(data, dict):
        return "错误: data_json 应为 JSON 对象（{}），不能是数组或标量。"
    S._locked_engram_call(S._get_engram().save_project_snapshot, project_folder, data)
    S._track("save_project_snapshot", success=True)
    return f"项目快照已保存: {project_folder}"


# ===========================================================================
# USER PORTRAIT TOOL (1)
# ===========================================================================


@S.mcp.tool()
async def user_portrait(action: str = "get") -> str:
    """用户写照统一入口：查看 / 对比 / 保存快照。 / Unified user portrait: view, compare, or snapshot.

    用途：action=get 生成精简写照——身份（角色/语言/技术水平）+ 经验/决策/领域/
    项目/工具聚合计数与主要领域，不含任何经验/决策原文，只读不写盘；compare 对比
    最近两份快照给出成长增量（计数增减、新增领域/工具、身份字段变化，只读，不足
    两份时返回当前写照并提示尚无基线）；save 保存带时间戳的版本化快照
    （<engram>/portraits/<时间戳>.json，写操作，只保留最近若干份）。
    Purpose: action=get builds the lean portrait (identity + aggregate counts,
    NO raw lesson/decision text, read-only); compare reports the growth delta
    between the two most recent snapshots (read-only); save persists a
    timestamped versioned snapshot (write; older snapshots are pruned).

    Args:
        action: get（默认）| compare | save。
    """
    # a4: write-path governance gate — must run unconditionally BEFORE action
    # validation (writer-spy matrix). Web/low-trust callers are refused here;
    # trusted-local callers passing this gate still hit the owner-only result
    # gates in the get/compare branches, matching the old per-tool behavior.
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="user_portrait")
    if refusal is not None:
        return refusal

    action = action.strip().lower()
    if action == "get":
        portrait = S._get_engram().build_user_portrait()
        portrait = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, portrait, tool="user_portrait"
        )
        S._track_read_safe("user_portrait", success=True)
        return S._json(portrait)
    if action == "compare":
        previous = S._get_engram().get_previous_portrait()
        latest = S._get_engram().get_latest_portrait()
        if latest is None:
            # No stored snapshots at all — build a fresh (unsaved) one to show.
            latest = S._get_engram().build_user_portrait()
        if previous is None:
            payload = {
                "growth": None,
                "note_zh": "尚无可对比的历史快照，先运行 user_portrait(action=\"save\") 建立基线。",
                "note_en": "No prior snapshot to compare; run user_portrait(action=\"save\") first to establish a baseline.",
                "latest": latest,
            }
        else:
            payload = {"growth": S._get_engram().compare_user_portraits(previous, latest)}
        payload = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, payload, tool="user_portrait"
        )
        S._track_read_safe("user_portrait", success=True)
        return S._json(payload)
    if action == "save":
        saved = S._locked_engram_call(S._get_engram().save_user_portrait, None)
        saved = S._gov_rt.maybe_govern_owner_only(
            S._get_engram().root, saved, tool="user_portrait"
        )
        S._track("user_portrait", success=True)
        return S._json(saved)
    return (
        f"未知 action: {action}。可用: get / compare / save。 "
        f"/ Unknown action: {action}. Available: get / compare / save."
    )


# ===========================================================================
# WEB CONTENT TOOL (1)
# ===========================================================================

# Output length cap for the formatted MCP string. Web articles legitimately
# need more room than the per-field search cap, but unbounded bodies blow up
# the client context — so we clip with a visible marker.
_READER_MAX_OUTPUT_CHARS = 8000


def _clip_reader_output(text: str) -> str:
    """Bound any reader output string (success body, error, or exception)."""
    if len(text) <= _READER_MAX_OUTPUT_CHARS:
        return text
    removed = len(text) - _READER_MAX_OUTPUT_CHARS
    return (
        f"{text[:_READER_MAX_OUTPUT_CHARS]}\n\n"
        f"[+{removed} chars truncated / 已截断 {removed} 字]"
    )


def _format_web_content(wc) -> str:
    """Render a reader.WebContent into a bounded, user-facing string."""
    if wc.error:
        return _clip_reader_output(wc.error)
    parts = [f"[来源 / source: {wc.source}]"]
    if wc.title:
        parts.append(f"# {wc.title}")
    parts.append(wc.content)
    return _clip_reader_output("\n\n".join(parts))


@S.mcp.tool()
async def read_web_content(url: str) -> str:
    """读取网页、视频或文章的文本内容。 / Read text content from a web page, video, or article.

    用途：用户发链接并要求分析、看看或读一下时调用。
    Purpose: Call when the user sends a URL and asks to analyze, inspect, or read it.

    工作方式：优先使用本地边车（localhost:7890，覆盖 YouTube 字幕、B 站、公众号等），
    边车不可用时自动降级到包内内置 reader（需 `pip install "piia-engram[reader]"`）。
    How it works: prefers the local sidecar (localhost:7890; YouTube subtitles,
    Bilibili, WeChat, ...), and falls back to the self-contained built-in reader
    (install with `pip install "piia-engram[reader]"`) when no sidecar is running.

    Args:
        url: 要提取内容的网页链接。 / URL to extract content from.
    """
    try:
        from . import reader
    except ImportError:  # plain-script mode (no package context)
        import reader  # type: ignore[no-redef]

    try:
        wc = await reader.extract_web_content(url)
    except Exception as e:
        return _clip_reader_output(f"读取失败 / read failed: {S._safe_err(e)}")
    return _format_web_content(wc)


# ===========================================================================
# IMPORT / EXPORT TOOLS (2)
# ===========================================================================


@S.mcp.tool()
async def export_engram(
    output_path: Optional[str] = None,
    format: str = "native",
    output_dir: str = "",
) -> str:
    """导出 Engram 数据：单文件备份或 OpenClaw 兼容格式。 / Export Engram data: a single-file backup, or the OpenClaw-compatible format.

    Owner/export surface: writes backup/export files and is refused for non-owner callers when governance is enabled.

    用途：format="native"（默认）把整库导出为单一 JSON 备份文件，用于备份、迁移
    到另一台机器或跨设备同步；format="openclaw" 导出为 SOUL.md + MEMORY.md +
    USER.md，交给 OpenClaw 或兼容工作流使用。
    Purpose: format="native" (default) exports the entire store as one JSON
    backup for backup/migration/sync; format="openclaw" exports SOUL.md,
    MEMORY.md, and USER.md for OpenClaw-compatible workflows.

    注意：导出包含全部身份、知识和项目数据，请按隐私级别处理文件。
    Note: Exports contain all identity, knowledge, and project data, so handle the files according to their privacy level.

    Args:
        output_path: 导出路径（format=native，可选，默认 ~/.engram/exports/engram_backup_<日期>.json）。 / Export path (native; optional).
        format: native（默认）| openclaw。
        output_dir: 输出目录（format=openclaw，可选，默认 Engram 的 compat/openclaw 目录）。 / Output directory (openclaw; optional).
    """
    # The export writes the ENTIRE store (identity + all knowledge) to files.
    # path-only ≠ no-disclosure: an agent with filesystem read then opens it
    # (Codex round-16 P2-1, two-step exfil). Gate BEFORE writing — a non-owner
    # gets a refusal and no file is produced. Also runs BEFORE format
    # validation so a non-owner never sees an "unknown format" hint
    # (writer-spy matrix).
    refusal = S._gov_rt.maybe_refuse_export(S._get_engram().root, tool="export_engram")
    if refusal is not None:
        return refusal
    format = format.strip().lower()
    if format == "openclaw":
        try:
            target_dir = output_dir or str(S._get_engram().root / "compat" / "openclaw")
            result = S.export_to_openclaw(S._get_engram(), target_dir)
            files = result.get("files", [])
            if result.get("status") == "success":
                return S._json(files)
            return S._json(result)
        except Exception as e:
            return f"导出 OpenClaw 兼容格式失败: {S._safe_err(e)}"
    if format != "native":
        return (
            f"未知 format: {format}。可用: native / openclaw。 "
            f"/ Unknown format: {format}. Available: native / openclaw."
        )
    err = S._validate_path(output_path, allow_empty=True)
    if err:
        return f"错误: {err}"
    try:
        path = S._get_engram().export_all(output_path)
        return f"导出成功: {path}"
    except Exception as e:
        return f"导出失败: {S._safe_err(e)}"


@S.mcp.tool()
async def import_engram(
    input_path: str = "",
    merge: bool = True,
    dry_run: bool = False,
    format: str = "native",
    soul_path: str = "",
    memory_path: str = "",
    user_path: str = "",
) -> str:
    """导入 Engram 数据：从备份文件或 OpenClaw 兼容文件。 / Import Engram data: from a backup file, or from OpenClaw-compatible files.

    Owner/admin surface: imports or overwrites local store data and is refused for non-owner callers when governance is enabled.

    用途：format="native"（默认）从 export_engram 生成的备份恢复或跨机迁移；
    format="openclaw" 从 SOUL.md / MEMORY.md / USER.md 迁移进 Engram（只提供
    存在的文件路径即可，导入逻辑按文件类型处理）。
    Purpose: format="native" (default) restores or migrates from an
    export_engram backup; format="openclaw" imports SOUL.md / MEMORY.md /
    USER.md files (provide only the paths that exist).

    注意：dry_run=True 只返回元数据预览，不写入数据；merge=False 会覆盖现有数据，使用前要确认风险。
    Note: dry_run=True returns a metadata-only preview without writing; merge=False overwrites existing data, so confirm the risk first.

    Args:
        input_path: 备份文件路径（format=native 必填）。 / Backup file path (required for format=native).
        merge: True 合并模式（保留已有数据并追加），False 覆盖模式（native）。 / Merge vs overwrite mode (native).
        dry_run: True 仅预览导入计划，不修改本地数据（native）。 / Preview the import plan without mutating (native).
        format: native（默认）| openclaw。
        soul_path: SOUL.md 文件路径（format=openclaw，可选）。 / Path to SOUL.md (openclaw, optional).
        memory_path: MEMORY.md 文件路径（format=openclaw，可选）。 / Path to MEMORY.md (openclaw, optional).
        user_path: USER.md 文件路径（format=openclaw，可选）。 / Path to USER.md (openclaw, optional).
    """
    # Whole-store import/overwrite — owner-only, gated before any side effect
    # and BEFORE format/path validation (writer-spy matrix).
    refusal = S._gov_rt.maybe_refuse_owner_write(S._get_engram().root, tool="import_engram")
    if refusal is not None:
        return refusal
    format = format.strip().lower()
    if format == "openclaw":
        try:
            result = S.import_from_openclaw(S._get_engram(), soul_path, memory_path, user_path)
            return S._json(result)
        except Exception as e:
            return f"从 OpenClaw 兼容格式导入失败: {S._safe_err(e)}"
    if format != "native":
        return (
            f"未知 format: {format}。可用: native / openclaw。 "
            f"/ Unknown format: {format}. Available: native / openclaw."
        )
    if not input_path:
        return S._json({
            "error": "format=native 需要提供 input_path。 / format=native requires input_path."
        })
    err = S._validate_path(input_path)
    if err:
        return S._json({"error": err})
    result = S._get_engram().import_all(input_path, merge=merge, dry_run=dry_run)
    return S._json(result)


@S.mcp.tool()
async def get_audit_log(limit: int = 50) -> str:
    """获取最近的审计日志条目。 / Get recent audit log entries.

    用途：需要查看 Engram 最近的读写操作、排查行为或核对记录时调用。
    Purpose: Call when inspecting recent Engram reads/writes, debugging behavior, or auditing activity.

    注意：审计日志默认开启；用 ENGRAM_AUDIT=0 可关闭。最多返回 200 条。
    Note: Audit logging is on by default (opt out with ENGRAM_AUDIT=0). Max 200 entries.

    Args:
        limit: 最多返回多少条（默认 50，上限 200，按最近优先）。 / Maximum entries to return (default 50, max 200, most recent first).
    """
    _MAX_AUDIT_ENTRIES = 200
    limit = max(1, min(limit, _MAX_AUDIT_ENTRIES))
    log_path = S._get_engram().root / "audit.log"
    if not log_path.is_file():
        return S._json({"entries": [], "total": 0, "message": "No audit entries yet. Audit logging is on by default; opt out with ENGRAM_AUDIT=0."})

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

    S._get_engram()._audit.log("read", "audit_log", detail=f"returned {len(entries)}")
    # The raw ledger entries carry a ``detail`` field that stores the first 100
    # chars of a written lesson summary / decision/playbook title (core.py audit
    # writes), i.e. stored knowledge body at ANY sensitivity level. The audit log
    # is an aggregate diagnostic surface that cannot be cleanly per-item filtered,
    # so it is private-self only — a low-trust agent must not read it back.
    return S._json(S._gov_rt.maybe_govern_owner_only(
        S._get_engram().root, {"entries": entries, "total": len(entries)}, tool="get_audit_log"))


# ===========================================================================
# WORKFLOW SHORTCUTS (2)
# ===========================================================================


@S.mcp.tool()
async def wrap_up_session(
    summary: str,
    project_folder: str = "",
    source_tool: str = "",
    project_title: str = "",
    tech_stack: str = "",
    known_issues: str = "",
    user_confirmed: bool = False,
    run_reconcile: bool = False,
    idempotency_key: str = "",
    reconcile_scope: str = "project",
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
        idempotency_key: 可选幂等键；重试同一键只返回既有 operation 状态，不重复写入。 / Optional idempotency key; retrying the same key returns the existing operation status without duplicate writes.
        reconcile_scope: "project"（有项目路径时默认精确项目隔离）或显式 "global"。 / "project" for exact project isolation when a project path is present, or explicit "global".
    """
    # a4: write-path governance gate — wrap_up_session fans out into many writes
    # (extract insights/playbook, save snapshot, daily log, evaluate_tiers), so
    # gate the whole entry. read-only-external is refused before any side effect.
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="wrap_up_session")
    if refusal is not None:
        return refusal

    preview = {
        "summary": summary,
        "project_folder": project_folder,
        "source_tool": source_tool,
        "project_title": project_title,
        "tech_stack": tech_stack,
        "known_issues": known_issues,
        "run_reconcile": run_reconcile,
        "reconcile_scope": reconcile_scope,
    }
    if not _is_user_confirmed(user_confirmed):
        return _confirmation_required(
            "wrap_up_session",
            "Wrap up session memory write",
            preview,
        )

    requested_reconcile_scope = str(reconcile_scope or "project").strip().lower()
    if requested_reconcile_scope not in {"project", "global"}:
        return S._json({
            "status": "error",
            "error": "reconcile_scope must be 'project' or 'global'",
        })
    effective_reconcile_scope = (
        "project"
        if project_folder and requested_reconcile_scope == "project"
        else "global"
    )

    S._session.detect_tool(source_tool)
    if project_folder:
        S._session.detect_project(project_folder)

    results = {}
    timing: dict[str, int] = {}
    maintenance: dict[str, object] = {}
    total_start = perf_counter()
    closeout_budget_ms = _configured_wrap_up_budget_ms()
    closeout_mode = _configured_closeout_mode()
    maintenance["closeout_mode"] = closeout_mode
    maintenance["reconcile_scope"] = {
        "requested": requested_reconcile_scope,
        "effective": effective_reconcile_scope,
        "project_scoped": effective_reconcile_scope == "project",
    }
    operation_state, idempotent_replay = _begin_wrap_up_operation(
        S._get_engram().root,
        idempotency_key=idempotency_key,
        project_folder=project_folder,
        source_tool=source_tool,
        budget_ms=closeout_budget_ms,
        closeout_mode=closeout_mode,
    )
    operation_id = str(operation_state.get("operation_id") or "")
    if idempotent_replay:
        public_operation = _public_wrap_up_operation_status(operation_state)
        replay_payload = {
            "status": public_operation.get("status", "unknown"),
            "idempotent_replay": True,
            "operation": public_operation,
            "maintenance": {
                "idempotency": {
                    "status": "replayed",
                    "reason": "same_idempotency_key",
                },
            },
        }
        return S._json(S._gov_rt.maybe_govern_write_ack(
            S._get_engram().root, replay_payload, tool="wrap_up_session",
        ))

    stage_errors: list[str] = []
    stage_partials: list[str] = []

    def _stage_start(stage: str) -> float:
        _mark_wrap_up_stage(S._get_engram().root, operation_id, stage, "running")
        return perf_counter()

    def _stage_finish(
        stage: str,
        status: str,
        started_at: float,
        *,
        reason: str = "",
        error: str = "",
        counts: dict[str, int] | None = None,
        committed: dict[str, object] | None = None,
    ) -> None:
        if status == "error":
            stage_errors.append(stage)
        elif status == "partial":
            stage_partials.append(stage)
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            stage,
            status,
            timing_ms=_elapsed_ms(started_at),
            reason=reason,
            error=error,
            counts=counts,
            committed=committed,
        )

    # Step 1: Extract insights
    if closeout_mode == "fast":
        results["insights"] = {"skipped": True}
        maintenance["extract_session_insights"] = _skipped_stage("fast_closeout")
        timing["extract_session_insights_ms"] = 0
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "extract_session_insights",
            "skipped",
            timing_ms=0,
            reason="fast_closeout",
        )
    else:
        stage_start = _stage_start("extract_session_insights")
        stage_status = "ok"
        stage_error = ""
        insight_counts: dict[str, int] = {}
        try:
            insights = S._locked_engram_call(
                S._get_engram().extract_session_insights,
                summary,
                source_tool=source_tool,
                project_folder=project_folder,
            )
            results["insights"] = insights
            if isinstance(insights, dict):
                insight_counts = {
                    "saved_lessons": _count_value(insights.get("saved_lessons")),
                    "saved_decisions": _count_value(insights.get("saved_decisions")),
                }
            maintenance["extract_session_insights"] = {"status": "ok", **insight_counts}
        except Exception as exc:
            S.logger.warning("extract_session_insights failed: %s", exc)
            stage_status = "error"
            stage_error = S._safe_err(exc)
            results["insights"] = {"error": stage_error}
            maintenance["extract_session_insights"] = {"status": "error", "error": stage_error}
        finally:
            timing["extract_session_insights_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "extract_session_insights",
                stage_status,
                stage_start,
                error=stage_error,
                counts=insight_counts,
                committed=insight_counts,
            )

    # Step 1.5: Auto-extract Playbook if session looks like a procedure
    stage_start = _stage_start("extract_playbook_from_session")
    playbook_stage_status = "ok"
    playbook_stage_reason = ""
    playbook_stage_error = ""
    try:
        if closeout_mode == "fast":
            maintenance["extract_playbook_from_session"] = _skipped_stage("fast_closeout")
            playbook_stage_status = "skipped"
            playbook_stage_reason = "fast_closeout"
            playbook = None
        else:
            playbook = S._locked_engram_call(
                S._get_engram().extract_playbook_from_session,
                summary,
                source_tool=source_tool,
                project_folder=project_folder,
            )
        if playbook:
            pb_confidence = playbook.get("confidence", "medium")
            _zh = S._user_lang() == "zh"
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
            maintenance["extract_playbook_from_session"] = {
                "status": "ok",
                "draft": True,
            }
        elif closeout_mode != "fast":
            maintenance["extract_playbook_from_session"] = {
                "status": "ok",
                "draft": False,
            }
    except Exception as exc:
        playbook_stage_status = "error"
        playbook_stage_error = S._safe_err(exc)
        S.logger.warning("playbook extraction failed: %s", exc)
        maintenance["extract_playbook_from_session"] = {
            "status": "error",
            "error": playbook_stage_error,
        }
    finally:
        timing["extract_playbook_from_session_ms"] = (
            0 if closeout_mode == "fast" else _elapsed_ms(stage_start)
        )
        _stage_finish(
            "extract_playbook_from_session",
            playbook_stage_status,
            stage_start,
            reason=playbook_stage_reason,
            error=playbook_stage_error,
            committed={"playbook_draft": bool(results.get("playbook_draft"))},
        )

    # Step 2: Save project snapshot (if project_folder provided)
    if project_folder:
        stage_start = _stage_start("save_project_snapshot")
        snapshot_stage_status = "ok"
        snapshot_stage_error = ""
        try:
            snapshot_data: dict = {}
            if project_title:
                snapshot_data["title"] = project_title
            if tech_stack:
                snapshot_data["tech_stack"] = [s.strip() for s in tech_stack.split(",") if s.strip()]
            if known_issues:
                snapshot_data["known_issues"] = [s.strip() for s in known_issues.split(",") if s.strip()]
            project_info = S._collect_project_info(project_folder)
            verified_at = S._dt.now().isoformat()
            if project_info:
                project_info["last_auto_snapshot"] = verified_at
            snapshot_data.update(S._attach_current_state(
                project_info,
                verified_at=verified_at,
                handoff=S._session_handoff_state(
                    summary,
                    source_tool=source_tool,
                    project_folder=project_folder,
                    session_ref=operation_id,
                ),
            ))
            S._locked_engram_call(
                S._get_engram().save_project_snapshot,
                project_folder,
                snapshot_data,
                source_tool=source_tool,
                source_session=operation_id,
            )
            results["project_snapshot"] = {"saved": True, "folder": project_folder}
            maintenance["save_project_snapshot"] = {"status": "ok", "saved": True}
        except Exception as exc:
            snapshot_stage_status = "error"
            snapshot_stage_error = S._safe_err(exc)
            S.logger.warning("save_project_snapshot failed: %s", exc)
            results["project_snapshot"] = {"error": snapshot_stage_error}
            maintenance["save_project_snapshot"] = {
                "status": "error",
                "error": snapshot_stage_error,
            }
        finally:
            timing["save_project_snapshot_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "save_project_snapshot",
                snapshot_stage_status,
                stage_start,
                error=snapshot_stage_error,
                committed={"project_snapshot": snapshot_stage_status == "ok"},
            )
    else:
        timing["save_project_snapshot_ms"] = 0
        maintenance["save_project_snapshot"] = _skipped_stage("no_project_folder")
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "save_project_snapshot",
            "skipped",
            timing_ms=0,
            reason="no_project_folder",
        )

    # Step 2.5: Append human-readable entry to today's daily log
    # (v3.30 mechanism 5). Always-on, lossy-safe single-file append per
    # (project, day). Falls back to "(no-project)" bucket if no folder.
    stage_start = _stage_start("append_daily_log")
    daily_stage_status = "ok"
    daily_stage_error = ""
    try:
        daily_target = project_folder or "(no-project)"
        # Keep the daily entry compact — first ~600 chars of the summary
        # plus a one-line tally is enough for "what happened today" recall.
        ins = results.get("insights") or {}
        tally_parts = []
        if isinstance(ins, dict):
            saved_lessons = _count_value(ins.get("saved_lessons"))
            saved_decisions = _count_value(ins.get("saved_decisions"))
            if saved_lessons:
                tally_parts.append(f"lessons={saved_lessons}")
            if saved_decisions:
                tally_parts.append(f"decisions={saved_decisions}")
        if "playbook_draft" in results:
            tally_parts.append("playbook=draft")
        tally = " · ".join(tally_parts) if tally_parts else "no-new-knowledge"
        body = summary.strip()
        if len(body) > 600:
            body = body[:600].rstrip() + "…"
        daily_content = f"_{tally}_\n\n{body}"
        daily_result = S._locked_engram_call(
            S._get_engram().append_daily_log,
            project_folder=daily_target,
            content=daily_content,
            event_type="session",
            source_tool=source_tool,
        )
        results["daily_log"] = {
            "file": daily_result["file"],
            "created": daily_result["created"],
        }
        maintenance["append_daily_log"] = {
            "status": "ok",
            "written": True,
            "created": bool(daily_result["created"]),
        }
    except Exception as exc:
        daily_stage_status = "error"
        daily_stage_error = S._safe_err(exc)
        S.logger.warning("append_daily_log failed: %s", exc)
        maintenance["append_daily_log"] = {
            "status": "error",
            "error": daily_stage_error,
        }
    finally:
        timing["append_daily_log_ms"] = _elapsed_ms(stage_start)
        _stage_finish(
            "append_daily_log",
            daily_stage_status,
            stage_start,
            error=daily_stage_error,
            committed={"daily_log": daily_stage_status == "ok"},
        )

    # Step 3: Auto-reconcile external AI memories and configs
    _reconcile_imported = 0
    if run_reconcile:
        stage_start = _stage_start("reconcile_memories")
        reconcile_stage_status = "ok"
        reconcile_stage_error = ""
        try:
            reconcile_kwargs = (
                {"project_folder": project_folder}
                if effective_reconcile_scope == "project"
                else {}
            )
            reconcile = S._locked_engram_call(
                S._get_engram().reconcile_memories,
                **reconcile_kwargs,
            )
            imported = int(reconcile.get("imported", 0) or 0)
            maintenance["reconcile_memories"] = {
                "status": "ok",
                "imported": imported,
                "scope": reconcile.get("scope", {}),
            }
            if imported > 0:
                results["memory_sync"] = reconcile
                _reconcile_imported += imported
        except Exception as exc:
            reconcile_stage_status = "error"
            reconcile_stage_error = S._safe_err(exc)
            S.logger.warning("reconcile_memories failed: %s", exc)
            maintenance["reconcile_memories"] = {
                "status": "error",
                "error": reconcile_stage_error,
            }
        finally:
            timing["reconcile_memories_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "reconcile_memories",
                reconcile_stage_status,
                stage_start,
                error=reconcile_stage_error,
                counts={"imported": _reconcile_imported},
            )

        stage_start = _stage_start("reconcile_ai_configs")
        cfg_stage_status = "ok"
        cfg_stage_error = ""
        try:
            config_kwargs = (
                {
                    "search_roots": [project_folder],
                    "project_folder": project_folder,
                }
                if effective_reconcile_scope == "project"
                else {}
            )
            cfg_sync = S._locked_engram_call(
                S._get_engram().reconcile_ai_configs,
                **config_kwargs,
            )
            imported = int(cfg_sync.get("imported", 0) or 0)
            config_budget_exhausted = bool(cfg_sync.get("budget_exhausted"))
            if config_budget_exhausted:
                cfg_stage_status = "partial"
                cfg_stage_reason = "import_budget_exhausted"
            else:
                cfg_stage_reason = ""
            maintenance["reconcile_ai_configs"] = {
                "status": "partial" if config_budget_exhausted else "ok",
                "imported": imported,
                "scanned_files": int(cfg_sync.get("scanned_files", 0) or 0),
                "budget_exhausted": config_budget_exhausted,
                "scope": cfg_sync.get("scope", {}),
            }
            if imported > 0:
                results["config_sync"] = cfg_sync
                _reconcile_imported += imported
        except Exception as exc:
            cfg_stage_status = "error"
            cfg_stage_reason = ""
            cfg_stage_error = S._safe_err(exc)
            S.logger.warning("reconcile_ai_configs failed: %s", exc)
            maintenance["reconcile_ai_configs"] = {
                "status": "error",
                "error": cfg_stage_error,
            }
        finally:
            timing["reconcile_ai_configs_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "reconcile_ai_configs",
                cfg_stage_status,
                stage_start,
                reason=cfg_stage_reason,
                error=cfg_stage_error,
            )
    else:
        maintenance["reconcile_memories"] = {
            "status": "skipped",
            "reason": "default_session_end_budget",
        }
        maintenance["reconcile_ai_configs"] = {
            "status": "skipped",
            "reason": "default_session_end_budget",
        }
        timing["reconcile_memories_ms"] = 0
        timing["reconcile_ai_configs_ms"] = 0
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "reconcile_memories",
            "skipped",
            timing_ms=0,
            reason="default_session_end_budget",
        )
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "reconcile_ai_configs",
            "skipped",
            timing_ms=0,
            reason="default_session_end_budget",
        )

    if _reconcile_imported > 0:
        S._beta("reconcile", imported=_reconcile_imported)

    # Step 4: Evaluate staging items and surface promotion suggestions.
    if closeout_mode == "fast" or _budget_exhausted(
        total_start=total_start,
        budget_ms=closeout_budget_ms,
    ):
        maintenance["evaluate_tiers"] = _skipped_stage("closeout_budget_exhausted")
        timing["evaluate_tiers_ms"] = 0
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "evaluate_tiers",
            "skipped",
            timing_ms=0,
            reason="closeout_budget_exhausted",
        )
    else:
        stage_start = _stage_start("evaluate_tiers")
        tier_stage_status = "ok"
        tier_stage_error = ""
        tier_counts: dict[str, int] = {}
        try:
            tier_result = S._locked_engram_call(S._get_engram().evaluate_tiers)
            tier_counts = {"suggested": _count_value(tier_result.get("suggested"))}
            maintenance["evaluate_tiers"] = {"status": "ok", **tier_counts}
            if tier_result.get("suggested", 0) > 0:
                results["promotion_suggestions"] = tier_result
        except Exception as exc:
            tier_stage_status = "error"
            tier_stage_error = S._safe_err(exc)
            S.logger.warning("evaluate_tiers failed: %s", exc)
            maintenance["evaluate_tiers"] = {
                "status": "error",
                "error": tier_stage_error,
            }
        finally:
            timing["evaluate_tiers_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "evaluate_tiers",
                tier_stage_status,
                stage_start,
                error=tier_stage_error,
                counts=tier_counts,
            )

    # Step 5: Report staging backlog
    skip_staging_summary = closeout_mode == "fast" or _budget_exhausted(
        total_start=total_start,
        budget_ms=closeout_budget_ms,
    )
    stage_start = perf_counter()
    staging_stage_status = "ok"
    staging_stage_reason = ""
    staging_stage_error = ""
    staging = {"total_staging": 0}
    if not skip_staging_summary:
        stage_start = _stage_start("staging_summary")
    try:
        if skip_staging_summary:
            maintenance["staging_summary"] = _skipped_stage("closeout_budget_exhausted")
            staging_stage_status = "skipped"
            staging_stage_reason = "closeout_budget_exhausted"
            staging = {"total_staging": 0}
        else:
            staging = S._get_engram().get_staging_summary()
            maintenance["staging_summary"] = {
                "status": "ok",
                "total_staging": _count_value(staging.get("total_staging")),
            }
        if staging["total_staging"] > 0:
            _zh = S._user_lang() == "zh"
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
        staging_stage_status = "error"
        staging_stage_error = S._safe_err(exc)
        S.logger.warning("get_staging_summary failed: %s", exc)
        maintenance["staging_summary"] = {
            "status": "error",
            "error": staging_stage_error,
        }
    finally:
        timing["staging_summary_ms"] = (
            0 if skip_staging_summary else _elapsed_ms(stage_start)
        )
        if skip_staging_summary:
            _mark_wrap_up_stage(
                S._get_engram().root,
                operation_id,
                "staging_summary",
                "skipped",
                timing_ms=0,
                reason=staging_stage_reason,
            )
        else:
            _stage_finish(
                "staging_summary",
                staging_stage_status,
                stage_start,
                reason=staging_stage_reason,
                error=staging_stage_error,
                counts={"total_staging": _count_value(staging.get("total_staging", 0))},
            )

    # Step 6: Beta event — session end
    S._beta("session_end",
          source_tool=source_tool[:40] if source_tool else "",
          has_project=bool(project_folder),
          insights=bool(results.get("insights")))

    # Step 7: Record this tool call BEFORE flushing so it's included
    S._track("wrap_up_session", success=True)

    # Step 7: Flush anonymous usage statistics (local + optional remote)
    # force=True: wrap_up_session is the last chance before process exit
    if closeout_mode == "fast" or _budget_exhausted(
        total_start=total_start,
        budget_ms=closeout_budget_ms,
    ):
        maintenance["telemetry_flush"] = _skipped_stage("closeout_budget_exhausted")
        timing["telemetry_flush_ms"] = 0
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "telemetry_flush",
            "skipped",
            timing_ms=0,
            reason="closeout_budget_exhausted",
        )
    else:
        stage_start = _stage_start("telemetry_flush")
        telemetry_stage_status = "ok"
        telemetry_stage_error = ""
        try:
            if S._tracker is not None:
                from importlib.metadata import version as _pkg_version
                try:
                    _ver = _pkg_version("piia-engram")
                except Exception:
                    _ver = "dev"
                k_counts = {}
                try:
                    k_counts["lessons"] = len(S._get_engram().get_lessons(limit=None, _update_access=False))
                    k_counts["decisions"] = len(S._get_engram().get_decisions(limit=None, _update_access=False))
                    k_counts["domains"] = len(S._get_engram().get_domains())
                except Exception:
                    pass
                _tier = os.environ.get("ENGRAM_TOOLS", "core")
                S._tracker.flush(
                    knowledge_counts=k_counts,
                    engram_version=_ver,
                    tools_tier=_tier,
                    force=True,
                )
            maintenance["telemetry_flush"] = {"status": "ok", "flushed": S._tracker is not None}
        except Exception as exc:
            telemetry_stage_status = "error"
            telemetry_stage_error = S._safe_err(exc)
            S.logger.debug("telemetry flush skipped: %s", exc)
            maintenance["telemetry_flush"] = {
                "status": "error",
                "error": telemetry_stage_error,
            }
        finally:
            timing["telemetry_flush_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "telemetry_flush",
                telemetry_stage_status,
                stage_start,
                error=telemetry_stage_error,
            )

    # Step 8: Periodic anonymous feedback report (weekly, if opted in)
    if closeout_mode == "fast" or _budget_exhausted(
        total_start=total_start,
        budget_ms=closeout_budget_ms,
    ):
        maintenance["feedback_send"] = _skipped_stage("closeout_budget_exhausted")
        timing["feedback_send_ms"] = 0
        _mark_wrap_up_stage(
            S._get_engram().root,
            operation_id,
            "feedback_send",
            "skipped",
            timing_ms=0,
            reason="closeout_budget_exhausted",
        )
    else:
        stage_start = _stage_start("feedback_send")
        feedback_stage_status = "ok"
        feedback_stage_error = ""
        feedback_sent = False
        try:
            from piia_engram.telemetry import is_feedback_enabled, _feedback_due, send_feedback
            if is_feedback_enabled() and _feedback_due():
                from piia_engram.setup_wizard import _build_feedback_report
                report = _build_feedback_report()
                send_feedback(report)
                feedback_sent = True
            maintenance["feedback_send"] = {"status": "ok", "sent": feedback_sent}
        except Exception as exc:
            feedback_stage_status = "error"
            feedback_stage_error = S._safe_err(exc)
            S.logger.debug("feedback send skipped: %s", exc)
            maintenance["feedback_send"] = {
                "status": "error",
                "error": feedback_stage_error,
            }
        finally:
            timing["feedback_send_ms"] = _elapsed_ms(stage_start)
            _stage_finish(
                "feedback_send",
                feedback_stage_status,
                stage_start,
                error=feedback_stage_error,
            )

    timing["total_ms"] = _elapsed_ms(total_start)
    maintenance["budget"] = _closeout_budget_metadata(
        total_start=total_start,
        budget_ms=closeout_budget_ms,
    )
    terminal_status = (
        "partial_complete"
        if stage_errors or stage_partials
        else "completed"
    )
    operation_state = _finish_wrap_up_operation(
        S._get_engram().root,
        operation_id,
        status=terminal_status,
        timing_ms=timing["total_ms"],
        outcome={
            "status": terminal_status,
            "stage_errors": list(stage_errors),
            "stage_partials": list(stage_partials),
            "saved_lessons": _count_value((results.get("insights") or {}).get("saved_lessons"))
            if isinstance(results.get("insights"), dict) else 0,
            "saved_decisions": _count_value((results.get("insights") or {}).get("saved_decisions"))
            if isinstance(results.get("insights"), dict) else 0,
            "daily_log_written": bool(results.get("daily_log")),
            "project_snapshot_saved": bool(
                isinstance(results.get("project_snapshot"), dict)
                and results["project_snapshot"].get("saved")
            ),
        },
    )
    results["maintenance"] = maintenance
    results["timing"] = timing
    results["operation"] = _public_wrap_up_operation_status(operation_state)

    return S._json(S._gov_rt.maybe_govern_write_ack(
        S._get_engram().root, results, tool="wrap_up_session",
    ))


@S.mcp.tool()
async def get_wrap_up_session_status(
    operation_id: str = "",
    idempotency_key: str = "",
) -> str:
    """读取 wrap_up_session 的阶段状态。 / Read metadata-only stage status for a wrap_up_session operation.

    用途：当 MCP 客户端在会话收尾时遇到 transport timeout，可用调用前已知的
    idempotency_key，或成功返回过的 operation_id，查询最后阶段和终态。长期未更新的
    running 会只读标记为 stale_running。该工具不返回会话摘要正文或项目本地路径。
    Purpose: after a session-closeout transport timeout, query metadata-only
    state by the caller-known idempotency_key or a returned operation_id.
    Long-unupdated running records are projected as stale_running without
    mutating the record. No summary text or local project path is returned.
    """
    try:
        resolved_operation_id = _resolve_wrap_up_operation_id(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
        )
        state = _read_wrap_up_operation(S._get_engram().root, resolved_operation_id)
    except ValueError as exc:
        return S._json({"status": "error", "error": str(exc)})
    if not state:
        return S._json({
            "status": "not_found",
            "operation_id": resolved_operation_id,
            "lookup": "idempotency_key" if idempotency_key else "operation_id",
        })
    S._track_read_safe("get_wrap_up_session_status", success=True)
    return S._json(_public_wrap_up_operation_status(state))


@S.mcp.tool()
async def export_feedback_report() -> str:
    """导出匿名内测反馈报告。 / Export anonymous beta feedback report.

    用途：用户想分享使用反馈时调用。报告只包含计数和分布，不含知识内容或个人信息。
    Purpose: Call when the user wants to share usage feedback. The report contains only counts and distributions — no knowledge content or personal information.
    """
    from piia_engram.setup_wizard import _build_feedback_report
    report = _build_feedback_report()
    return S._json(report)


@S.mcp.tool()
async def doctor(output_format: str = "markdown") -> str:
    """记忆系统自诊断。 / Memory system self-diagnosis.

    用途：检查 Engram 记忆系统健康状态，发现潜在问题（数据碎片、过期知识、冲突决策、
    身份层异常等）。这是 MCP doctor；CLI 冲突视图请用 ``engram conflicts list``。
    Purpose: Run a comprehensive health check on the Engram memory system —
    detects data fragmentation, stale knowledge, conflicting decisions, identity
    issues, and more.

    Args:
        output_format: "markdown" 或 "json"。 / "markdown" or "json".
    """
    from datetime import datetime

    checks: list[dict] = []

    # 1. Identity completeness
    profile = S._get_engram().get_profile()
    missing_identity = [f for f in ("role", "language", "description") if not profile.get(f)]
    checks.append({
        "name": "identity_completeness",
        "status": "WARN" if missing_identity else "PASS",
        "detail": f"缺少字段: {missing_identity}" if missing_identity else "profile 完整",
    })

    # 2. Provenance tracking
    has_prov = bool(profile.get("_provenance"))
    checks.append({
        "name": "identity_provenance",
        "status": "PASS" if has_prov else "INFO",
        "detail": "字段级溯源已启用" if has_prov else "无溯源数据（首次 update_profile 后自动生成）",
    })

    # 2.5 R5: Detect if ENGRAM_DIR is inside a cloud-synced or
    # network-mounted directory. Cloud sync services (iCloud, Dropbox,
    # OneDrive, Google Drive) can cause corruption when two machines
    # sync the same JSON files concurrently. NFS/CIFS mounts lack
    # the atomic rename guarantees that _atomic_write_json relies on.
    _cloud_markers = {
        # (path substring, human label)
        "icloud": "iCloud Drive",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive",
        "google drive": "Google Drive",
        "googledrive": "Google Drive",
        # NFS / network markers (Unix mount paths)
        "/mnt/": "network mount",
        "/nfs/": "NFS mount",
        "/smb/": "SMB/CIFS mount",
    }
    engram_dir_str = str(S._get_engram().root).replace("\\", "/").lower()
    cloud_hit = None
    for marker, label in _cloud_markers.items():
        if marker in engram_dir_str:
            cloud_hit = label
            break
    # Windows: also check if the drive letter maps to a network path
    if not cloud_hit and hasattr(os, "name") and os.name == "nt":
        try:
            import subprocess
            drive = str(S._get_engram().root)[:2]  # e.g. "Z:"
            result = subprocess.run(
                ["net", "use", drive],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and "Remote name" in result.stdout:
                cloud_hit = f"mapped network drive ({drive})"
        except Exception:
            pass
    checks.append({
        "name": "storage_location",
        "status": "WARN" if cloud_hit else "PASS",
        "detail": (
            f"ENGRAM_DIR 位于 {cloud_hit} 目录内。"
            "云同步/NFS 可能导致并发写入冲突和数据损坏。"
            "建议将 ENGRAM_DIR 设为本地非同步目录。"
            f" / ENGRAM_DIR is inside a {cloud_hit} directory. "
            "Concurrent sync may corrupt JSON files."
        ) if cloud_hit else (
            f"ENGRAM_DIR={S._get_engram().root} — 本地目录，无云同步风险"
        ),
    })

    # 3. Knowledge counts
    lessons = S._get_engram().get_lessons(limit=None, _update_access=False)
    decisions = S._get_engram().get_decisions(limit=None, _update_access=False)
    checks.append({
        "name": "knowledge_volume",
        "status": "PASS",
        "detail": f"lessons={len(lessons)}, decisions={len(decisions)}",
    })

    # 4. Stale knowledge
    # NOTE: get_knowledge_overview() returns {"digest", "health", "stale"} — the
    # health-report payload (incl. items_needing_review / items_to_archive /
    # health_score) is nested under "health", not at top-level. Earlier versions
    # of doctor read overview["lifecycle"] / overview["health_score"] directly,
    # which silently returned defaults and produced health_score=0 with empty
    # stale/archive lists (regression flagged in v3.29.4, lesson 81d05b09c8ee).
    overview = S._get_engram().get_knowledge_overview()
    health_report = overview.get("health", {}) if isinstance(overview, dict) else {}
    stale = health_report.get("items_needing_review", [])
    archive = health_report.get("items_to_archive", [])
    checks.append({
        "name": "stale_knowledge",
        "status": "WARN" if len(stale) > 10 else "PASS",
        "detail": f"需复审: {len(stale)}, 可归档: {len(archive)}",
    })

    # 5. Duplicate detection
    from piia_engram.storage import SIMILARITY_THRESHOLD
    dup_count = 0
    for i, a in enumerate(lessons):
        for b in lessons[i + 1:]:
            sim = S._get_engram()._bigram_similarity(a.get("summary", ""), b.get("summary", ""))
            if sim >= SIMILARITY_THRESHOLD:
                dup_count += 1
        if dup_count > 20:
            break
    checks.append({
        "name": "near_duplicates",
        "status": "WARN" if dup_count > 5 else "PASS",
        "detail": f"近似重复对数: {dup_count}",
    })

    # 6. Conflicting decisions
    try:
        from piia_engram.conflict_governance import sample_conflicts, split_conflicts

        all_conflicts = S._get_engram().detect_active_decision_conflicts(
            decisions,
            include_suppressed=True,
        )
        conflicts, suppressed_conflicts = split_conflicts(all_conflicts)
        conflict_samples = sample_conflicts(conflicts, limit=10)
    except Exception:
        conflicts = []
        suppressed_conflicts = []
        conflict_samples = []
    checks.append({
        "name": "decision_conflicts",
        "status": "WARN" if conflicts else "PASS",
        "detail": (
            "冲突决策 / decision conflicts: "
            f"{len(conflicts)} 未抑制 / unsuppressed, "
            f"{len(suppressed_conflicts)} 已抑制 / suppressed"
        ),
        "count_unsuppressed": len(conflicts),
        "count_suppressed": len(suppressed_conflicts),
        "samples": conflict_samples,
    })

    # 7. Health score (also nested under overview["health"])
    health = health_report.get("health_score", 0)
    dimensions = health_report.get("dimensions", {})
    dim_breakdown = (
        " · ".join(f"{k}={v}" for k, v in dimensions.items()) if dimensions else ""
    )
    checks.append({
        "name": "health_score",
        "status": "PASS" if health >= 70 else "WARN",
        "detail": f"{health}/100" + (f" ({dim_breakdown})" if dim_breakdown else ""),
    })

    # 8.5 v3.30 mechanism (1): unclean-exit detection
    try:
        unclean = getattr(S._get_engram(), "_prev_unclean", None) or S._get_engram().get_unclean_exit_marker()
    except Exception:
        unclean = None
    if unclean:
        last_seen = unclean.get("last_seen_at", "")
        pid = unclean.get("pid", "?")
        checks.append({
            "name": "unclean_exit",
            "status": "WARN",
            "detail": (
                f"上次会话异常退出 (pid={pid}, last_seen={last_seen}). "
                "进度可能丢失了最近一次 heartbeat checkpoint 之后的内容"
                "（最多丢失 heartbeat 间隔时间，默认 5 分钟）。"
                " / Previous session exited uncleanly. Up to one "
                "heartbeat interval (default 5 min) of progress may "
                "be lost."
            ),
        })
    else:
        checks.append({
            "name": "unclean_exit",
            "status": "PASS",
            "detail": "上次会话正常退出",
        })

    # 8. Quick context freshness
    qc_path = S._get_engram().root / "quick_context.md"
    if qc_path.exists():
        age_hours = (datetime.now().timestamp() - qc_path.stat().st_mtime) / 3600
        checks.append({
            "name": "quick_context_freshness",
            "status": "PASS" if age_hours < 24 else "WARN",
            "detail": f"最后更新: {age_hours:.1f} 小时前",
        })
    else:
        checks.append({
            "name": "quick_context_freshness",
            "status": "FAIL",
            "detail": "quick_context.md 不存在",
        })

    passed = sum(1 for c in checks if c["status"] == "PASS")
    warned = sum(1 for c in checks if c["status"] == "WARN")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    from piia_engram.runtime_capabilities import get_runtime_capabilities

    runtime_capabilities = get_runtime_capabilities()

    result = {
        "summary": f"{passed} PASS, {warned} WARN, {failed} FAIL (共 {len(checks)} 项)",
        "checks": checks,
        "runtime_capabilities": runtime_capabilities,
    }

    if output_format == "json":
        S._track("doctor", success=True)
        return S._json(result)

    # Markdown output
    lines = ["# Engram Doctor Report", ""]
    lines.append(f"**总结**: {result['summary']}")
    lines.append("")
    lines.append(
        "**Runtime capability fingerprint**: "
        f"`{runtime_capabilities['fingerprint']}` "
        f"({runtime_capabilities['runtime_version']})"
    )
    lines.append("")
    lines.append("| 检查项 | 状态 | 详情 |")
    lines.append("|--------|------|------|")
    for c in checks:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}.get(c["status"], "?")
        lines.append(f"| {c['name']} | {icon} {c['status']} | {c['detail']} |")

    decision_conflict_check = next(
        (c for c in checks if c.get("name") == "decision_conflicts"),
        {},
    )
    samples = decision_conflict_check.get("samples") or []
    if samples:
        lines.append("")
        lines.append("## Decision conflict samples / 决策冲突样本")
        lines.append("")
        for sample in samples:
            lines.append(
                "- "
                f"{sample.get('id1')} ↔ {sample.get('id2')} "
                f"(q={sample.get('q_sim')}, c={sample.get('c_sim')}): "
                f"{sample.get('q1')} / {sample.get('q2')}"
            )
        lines.append("")
        lines.append(
            "Use `engram conflicts list` and `engram conflicts resolve` to review "
            "or close these pairs. / 使用 `engram conflicts list` 和 "
            "`engram conflicts resolve` 查看或关闭这些冲突对。"
        )

    S._track("doctor", success=True)
    return "\n".join(lines)


@S.mcp.tool()
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
    # a4: write-path governance gate
    refusal = S._gov_rt.maybe_refuse_write(S._get_engram().root, tool="start_project")
    if refusal is not None:
        return refusal

    results = {}

    # Step 1: Knowledge inheritance
    limit = min(int(limit), 20)
    inheritance = S._get_engram().get_knowledge_inheritance(description, limit=limit)
    # start_project embeds the SAME inheritance bundle get_knowledge_inheritance
    # returns; gate its items identically or this becomes an ungoverned sibling
    # read tool (Codex round-16 P1-1). The ``start_`` prefix kept it out of the
    # earlier prefix-based coverage check — now caught by all-tool classification.
    inheritance = S._gov_rt.maybe_govern_result(
        S._get_engram().root, inheritance, tool="start_project", list_fields=("items",)
    )
    results["inherited_knowledge"] = inheritance

    # Step 2: Initialize project snapshot
    snapshot_data: dict = {}
    if project_title:
        snapshot_data["title"] = project_title
    elif description:
        snapshot_data["title"] = description[:80]
    if tech_stack:
        snapshot_data["tech_stack"] = [s.strip() for s in tech_stack.split(",") if s.strip()]
    S._locked_engram_call(S._get_engram().save_project_snapshot, project_folder, snapshot_data)
    results["project_snapshot"] = {"created": True, "folder": project_folder}

    return S._json(results)


# ===========================================================================
# RESOURCES (5)
# ===========================================================================
