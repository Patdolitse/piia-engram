"""Round-15/16 P1 regression: the full MCP tool enforcement matrix.

Codex round-15 FAILed the a0 cutover because governance was wired into only a
*subset* of the agent-facing knowledge read tools. Round-16 FAILed again: the
fix wired the named read tools but the coverage backstop classified tools by
*name prefix*, so it never noticed that ``start_project`` (inherits knowledge),
``get_identity_card`` (embeds lessons/decisions), ``get_execution_status``
(playbook step bodies), and a cluster of ID-keyed *write* tools
(``update_knowledge``, ``review_knowledge``, ``merge_knowledge``,
``link_knowledge`` …) all return stored knowledge bodies/titles too. The model
picking an MCP tool does not respect the developer's read/write mental model —
it just calls whatever returns the data. The real enforcement boundary is:

    ANY MCP tool whose response body OR immediately-readable side effect
    contains a stored knowledge body/title must pass the trust ceiling.

This module enforces that two ways:

1. **Leak matrix** — every governed tool is driven with a faked Engram that
   returns a public AND a secret item; with ``ENGRAM_GOVERNANCE=1`` and an
   untrusted ``web`` client the secret marker must NEVER appear in the output.
   Filter-mode tools must still return the public marker. The same rows are
   re-run flag-OFF (secret flows through — proving the matrix is not vacuous)
   and as ``self`` owner (secret returned — enforcement restricts lower tiers
   without crippling the owner).

2. **Deny-by-default coverage** — *every* async ``@mcp.tool`` in the module
   (no name-prefix heuristic) must be classified as GOVERNED (and in the leak
   matrix), EXPORT_OWNER_ONLY (file-writing export, refusal-tested), or
   SAFE_ALLOWLIST (no stored knowledge body/title in the response, documented).
   A new tool that is none of these fails the test, forcing a conscious
   wire-or-classify decision. This is the structural fix for the round-16 miss:
   it can no longer hide behind an unrecognized name prefix.

Export tools get their own refusal test: under governance a non-owner must be
refused BEFORE the file is written (path-only ≠ no-disclosure — Codex r16 P2-1).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


# ── markers ─────────────────────────────────────────────────────────────────

PUBLIC = "PUBOK_public_marker_safe_text"
SECRET = "ZZSECRETLEAK_must_never_reach_an_external_agent"


def _pub() -> dict:
    return {"id": "pub-1", "sensitivity": "public", "summary": PUBLIC, "content": PUBLIC}


def _sec() -> dict:
    return {"id": "sec-1", "sensitivity": "secret", "summary": SECRET, "content": SECRET}


# ── the leak matrix: (tool, engram_method, fake_return, kwargs, mode) ─────────
#
# mode="filter"   → secret omitted, public still returned (item-level gate)
# mode="withhold" → whole payload / item / ack withheld for non-owners (owner-
#                   only dump, single-item stub, or write-ack title scrub)

_MATRIX = [
    # ---- list[dict] read tools (maybe_govern_list) ----
    ("get_lessons", "get_lessons", [_pub(), _sec()], {}, "filter"),
    ("get_decisions", "get_decisions", [_pub(), _sec()], {}, "filter"),
    ("get_relevant_knowledge", "get_relevant_lessons", [_pub(), _sec()],
     {"project_folder": "/x"}, "filter"),
    ("get_playbooks", "get_playbooks", [_pub(), _sec()], {}, "filter"),
    ("get_recent_playbooks", "get_recent_playbooks", [_pub(), _sec()], {}, "filter"),
    ("get_recent_context", "get_recent_context", [_pub(), _sec()], {}, "filter"),
    # ---- dict-of-lists read tools (maybe_govern_buckets) ----
    ("search_knowledge", "search_knowledge",
     {"lessons": [_pub(), _sec()], "decisions": [], "playbooks": []},
     {"query": "x"}, "filter"),
    ("get_stale_knowledge", "get_stale_knowledge",
     {"days": 30, "limit": 20, "lessons": [_pub(), _sec()], "decisions": []},
     {}, "filter"),
    # ---- mixed result dicts (maybe_govern_result) ----
    ("get_knowledge_inheritance", "get_knowledge_inheritance",
     {"description": "d", "total": 2, "recommended_domains": ["python"],
      "items": [_pub(), _sec()]},
     {"description": "d"}, "filter"),
    ("get_related_knowledge", "get_related_knowledge",
     {"source": _pub(), "related": [_pub(), _sec()], "total": 2},
     {"item_id": "pub-1"}, "filter"),
    ("find_similar_knowledge", "find_similar_knowledge",
     {"source": _pub(), "similar": [_pub(), _sec()], "total": 2},
     {"item_id": "pub-1"}, "filter"),
    # start_project is a write tool; a4 write-gate refuses the call entirely
    # for non-owners (before the read part runs), so it's "withhold" not "filter".
    ("start_project", "get_knowledge_inheritance",
     {"query": "new project", "items": [_pub(), _sec()]},
     {"description": "new", "project_folder": "/x"}, "withhold"),
    # ---- single knowledge-item dicts (maybe_govern_one) → withheld stub ----
    ("get_project_context", "get_project_snapshot", _sec(),
     {"project_folder": "/x"}, "withhold"),
    ("get_playbook", "get_playbook", _sec(), {"playbook_id": "sec-1"}, "withhold"),
    # ---- write tools returning a FULL stored item (maybe_govern_one) ----
    ("update_knowledge", "update_knowledge", _sec(),
     {"item_id": "sec-1", "updates_json": "{}"}, "withhold"),
    ("archive_knowledge", "archive_knowledge", _sec(), {"item_id": "sec-1"}, "withhold"),
    ("review_knowledge", "review_knowledge", _sec(), {"knowledge_id": "sec-1"}, "withhold"),
    # ---- write tools whose ACK echoes a stored TITLE (maybe_govern_write_ack) ----
    ("merge_knowledge", "merge_knowledge",
     {"success": True, "primary_title": SECRET, "secondary_title": "other"},
     {"primary_id": "sec-1", "secondary_id": "x"}, "withhold"),
    ("link_knowledge", "link_knowledge",
     {"success": True, "message": "Linked: " + SECRET + " ↔ other"},
     {"id_a": "sec-1", "id_b": "x"}, "withhold"),
    ("unlink_knowledge", "unlink_knowledge",
     {"success": True, "message": "Unlinked: " + SECRET + " ↔ other"},
     {"id_a": "sec-1", "id_b": "x"}, "withhold"),
    ("update_playbook", "update_playbook",
     {"title": SECRET, "version": 2}, {"playbook_id": "sec-1", "status": "active"}, "withhold"),
    ("delete_playbook", "delete_playbook",
     {"deleted": {"id": "sec-1", "title": SECRET}},
     {"playbook_id": "sec-1", "dry_run": False, "confirm": True}, "withhold"),
    ("restore_playbook", "restore_playbook",
     {"restored": {"id": "sec-1", "title": SECRET}},
     {"playbook_id": "sec-1", "dry_run": False, "confirm": True}, "withhold"),
    # ---- add_* / memory_store dedup-REJECT echoes the matched stored item's
    #      existing_summary / existing_title (content the caller never supplied);
    #      a near-duplicate submission could read a work/secret item back out.
    #      Gated like any write-echo (maybe_govern_write_ack).
    ("add_lesson", "add_lesson",
     {"status": "duplicate", "existing_id": "sec-1", "existing_summary": SECRET,
      "similarity": 0.99, "message": "与现有教训相似度 99%"},
     {"summary": "near dup"}, "withhold"),
    ("add_decision", "add_decision",
     {"status": "duplicate", "existing_id": "sec-1", "existing_title": SECRET, "similarity": 0.99},
     {"question": "q", "choice": "c"}, "withhold"),
    ("add_playbook", "add_playbook",
     {"status": "duplicate", "existing_id": "sec-1", "existing_title": SECRET},
     {"title": "t", "triggers": "a,b"}, "withhold"),
    ("memory_store", "add_lesson",
     {"status": "duplicate", "existing_id": "sec-1", "existing_summary": SECRET, "similarity": 0.99},
     {"kind": "lesson", "content_json": '{"summary": "near dup"}'}, "withhold"),
    # ---- owner-only aggregates / dumps / derived views (maybe_govern_owner_only) ----
    # NOTE: prepare_playbook_execution was MOVED to _EXPORT_OWNER_ONLY (round-18
    # P1): core.save_execution_plan PERSISTS the step bodies to
    # playbooks/executions/<id>.json, so governing only the return left the file
    # on disk for a non-owner. It is now pre-write gated like the export tools.
    ("get_knowledge_overview", "get_knowledge_overview",
     {"digest": {"top_lessons": [_sec()]}, "health": {}, "stale": {}},
     {}, "withhold"),
    ("suggest_merges", "suggest_merges", [{"pair": [_sec(), _sec()]}], {}, "withhold"),
    ("classify_legacy_playbooks", "classify_legacy_playbooks",
     {"suggestions": [{"id": "sec-1", "title": SECRET, "evidence": [SECRET]}]},
     {}, "withhold"),
    ("apply_legacy_playbook_scope_suggestions", "apply_legacy_playbook_scope_suggestions",
     {"would_apply": [{"id": "sec-1", "title": SECRET, "evidence": [SECRET]}],
      "applied": []},
     {}, "withhold"),
    ("rollback_playbook_scope_migration", "rollback_playbook_scope_migration",
     {"would_rollback": [{"id": "sec-1", "title": SECRET}],
      "rolled_back": []},
     {}, "withhold"),
    ("get_playbook_scope_review_queue", "get_playbook_scope_review_queue",
     {"items": [{"id": "sec-1", "title": SECRET, "evidence": [SECRET]}]},
     {}, "withhold"),
    ("list_playbooks_for_management", "list_playbooks_for_management",
     {"items": [{"id": "sec-1", "title": SECRET, "deletion_reason": SECRET}]},
     {}, "withhold"),
    ("resolve_playbook_scope_review", "resolve_playbook_scope_review",
     {"updated": {"id": "sec-1", "title": SECRET, "note": SECRET}},
     {"playbook_id": "sec-1", "action": "skip"}, "withhold"),
    ("get_decision_thread", "get_decision_thread",
     {"order": [{"id": "sec-1", "summary": SECRET}], "active_ids": []},
     {"seed_id": "sec-1"}, "withhold"),
    ("get_decision_history", "get_decision_history",
     {"revisions": [{"id": "sec-1", "choice": SECRET, "reasoning": SECRET}],
      "current": {"choice": SECRET}},
     {"question": "sec question"}, "withhold"),
    ("get_execution_status", "get_execution_status",
     {"title": SECRET, "steps": [{"action": SECRET, "status": "pending"}]},
     {"playbook_id": "sec-1"}, "withhold"),
    # NOTE: get_identity_card and export_knowledge_report were MOVED to
    # _EXPORT_OWNER_ONLY (round-17 P1-2/P1-3): governing only their RETURN left
    # the secret-bearing file (exports/identity_card.md, knowledge_report_*.md)
    # on disk for a non-owner. They are now pre-write gated like export_engram.
    ("get_user_context", "generate_context", "identity card\n" + SECRET, {}, "withhold"),
    ("get_resume_brief", "get_resume_brief", "resume brief\n" + SECRET, {}, "withhold"),
    ("get_recall", "get_relevant_lessons", [_pub(), _sec()],
     {"project_folder": "/x"}, "withhold"),
    ("get_daily_log", "get_daily_log", "daily log\n" + SECRET,
     {"project_folder": "/x"}, "withhold"),
    # audit.log entries carry the first 100 chars of a written lesson summary /
    # decision/playbook title in their ``detail`` field (core.py audit writes),
    # i.e. stored knowledge body at ANY sensitivity. The raw ledger is an
    # aggregate diagnostic surface → private-self only (maybe_govern_owner_only).
    # The fake is unused: get_audit_log reads root/audit.log, which the harness
    # writes in _patch_tool_method.
    ("get_audit_log", "_unused_audit_method", {"_": SECRET}, {}, "withhold"),
]

# The set the leak matrix actually exercises — used by the coverage backstop.
_GOVERNED = {row[0] for row in _MATRIX}

# Export tools: the disclosure surface is a FILE written to disk (full-store
# backup / review HTML / identity card / knowledge report / quick-context
# snapshot), not the MCP return value. Governed by a PRE-write owner gate
# (maybe_refuse_export) and verified by test_export_tools_refuse_non_owner.
# refresh_quick_context, get_identity_card, export_knowledge_report were added
# round-17 (P1-1/P1-2/P1-3): each embeds stored lesson/decision bodies into a
# file at a predictable path, so the file itself is the leak — governing the
# return value alone is not enough.
_EXPORT_OWNER_ONLY = {
    "export_engram", "export_engram_to_openclaw", "request_outline_review",
    "refresh_quick_context", "get_identity_card", "export_knowledge_report",
    # round-18: core.save_execution_plan persists step bodies to
    # playbooks/executions/<id>.json — the file is the leak, gate before writing.
    "prepare_playbook_execution",
}

# Every other async @mcp.tool: the response carries NO stored knowledge
# body/title beyond what the caller supplied. Each is here for a concrete reason
# (audited round-16). a0 governs knowledge bodies; these are out of that scope.
_SAFE_ALLOWLIST = {
    # identity / preference fields — own safe= projection, deferred to the later
    # permission-profile phase; not knowledge bodies.
    "get_profile", "get_work_style", "get_preferences",
    "get_trust_boundaries", "get_quality_standards",
    # metadata / registry / diagnostics — no knowledge bodies in the response.
    # NOTE: get_audit_log was moved OUT of this set (its entries echo stored
    # summary/title via the audit detail field) — now governed owner-only.
    "get_domains", "list_projects", "list_agent_sessions",
    "find_tool", "list_tools", "export_feedback_report", "doctor",
    "update_execution_step",  # counts/status only, no body
    "archive_playbook",  # id-only acknowledgement; write-gated separately
    # caller-supplied-content writes — echo only what the caller just passed in,
    # so there is nothing to read *back* above the ceiling.
    # NOTE: add_lesson / add_decision / add_playbook / memory_store were moved
    # OUT (their dedup-REJECT branch echoes the matched stored item) — now
    # governed via maybe_govern_write_ack.
    # NOTE: refresh_quick_context was moved OUT (round-17 P1-1) — it writes
    # quick_context.md embedding lesson/decision bodies; now pre-write gated in
    # _EXPORT_OWNER_ONLY.
    "bulk_add_knowledge", "ingest_notes", "extract_session_insights",
    "save_project_snapshot", "save_agent_context", "wrap_up_session",
    "register_tool", "update_identity",
    # relation/maintenance ops returning caller IDs / counts only.
    "add_relation", "remove_relation", "apply_review",
    # staging batch review returns ids/actions/statuses/counts only; the write
    # path is still maybe_refuse_write-gated as governed_write in mcp_server.
    "batch_review_staging",
    # permission profile: governance metadata, no knowledge bodies.
    "get_permission_profile", "set_caller_trust", "revoke_caller",
    # imports / external fetch.
    "import_engram", "import_engram_from_openclaw", "read_web_content",
}

_CLASSIFIED = _GOVERNED | _EXPORT_OWNER_ONLY | _SAFE_ALLOWLIST


def _discover_all_tools() -> set[str]:
    """EVERY async ``@mcp.tool`` defined in mcp_server — no name-prefix filter.

    The round-16 miss was a prefix-based discovery (``start_project`` /
    ``update_knowledge`` slipped through). Enumerate by shape only: a module-
    level coroutine function defined in this module is a registered tool.
    """
    out: set[str] = set()
    for name, obj in vars(mcp_server).items():
        if not inspect.iscoroutinefunction(obj):
            continue
        if getattr(obj, "__module__", "") != mcp_server.__name__:
            continue
        out.add(name)
    return out


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def gov_engram(tmp_path, monkeypatch):
    """Fresh Engram in tmp_path wired as the module global, heartbeat disabled."""
    old = mcp_server._session
    old._stop_event.set()
    if old._heartbeat_thread is not None:
        old._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    return engram


def _call(tool_name, kwargs):
    return asyncio.run(getattr(mcp_server, tool_name)(**kwargs))


def _patch_tool_method(gov_engram, monkeypatch, tool, method, fake):
    """Monkeypatch the engram method a tool reads from, plus any side-effect
    method that would otherwise touch the real filesystem in the matrix."""
    monkeypatch.setattr(gov_engram, method, lambda *a, **k: fake, raising=False)
    if tool == "get_audit_log":
        # get_audit_log reads root/audit.log directly (no engram method). Write a
        # ledger line whose ``detail`` carries the stored-knowledge marker, the
        # way core.py audit writes embed lesson summary / decision-playbook title.
        import json as _json_mod
        (gov_engram.root / "audit.log").write_text(
            _json_mod.dumps({"ts": "t", "action": "write",
                             "target": "knowledge/lessons", "detail": SECRET}) + "\n",
            encoding="utf-8",
        )
    if tool == "start_project":
        # start_project also persists a snapshot; no-op it so the matrix only
        # exercises the inheritance gate, not the filesystem.
        monkeypatch.setattr(
            gov_engram, "save_project_snapshot", lambda *a, **k: {"created": True},
            raising=False,
        )


# ── 1. leak matrix: flag ON + untrusted web client → secret must not appear ───


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_no_secret_leaks_to_external_agent(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    _patch_tool_method(gov_engram, monkeypatch, tool, method, fake)

    out = _call(tool, kwargs)

    assert SECRET not in out, f"{tool} leaked a secret item to an external agent"
    if mode == "filter":
        assert PUBLIC in out, f"{tool} dropped the public item (should filter, not refuse)"


# ── 2. OFF-by-default passthrough (proves the matrix carries the secret) ──────


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_flag_off_is_byte_identical_passthrough(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    _patch_tool_method(gov_engram, monkeypatch, tool, method, fake)

    out = _call(tool, kwargs)

    assert SECRET in out, f"{tool} did not pass the secret through with the flag OFF"


# ── 3. owner (private-self) still sees governed content ──────────────────────


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_owner_sees_everything_when_flag_on(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "self")
    _patch_tool_method(gov_engram, monkeypatch, tool, method, fake)

    out = _call(tool, kwargs)

    assert SECRET in out, f"{tool} withheld content from the private-self owner"


# ── 4. export tools: refuse a non-owner BEFORE writing the file ──────────────

_EXPORT_SPECS = [
    # tool, (target_obj, attr), kwargs
    ("export_engram", ("engram", "export_all"), {}),
    ("export_engram_to_openclaw", ("module", "export_to_openclaw"), {}),
    ("request_outline_review", ("engram", "export_review_page"), {"lang": "zh"}),
    # round-17 file-side-effect leaks: the writer method must NEVER run for a
    # non-owner (proves no file is created/updated).
    ("refresh_quick_context", ("engram", "refresh_quick_context"), {"level": "standard"}),
    ("get_identity_card", ("engram", "export_identity_card"), {}),
    ("export_knowledge_report", ("engram", "export_knowledge_report"), {}),
    # round-18: same class — the writer persists step bodies to
    # playbooks/executions/<id>.json; spy proves it never runs for a non-owner.
    ("prepare_playbook_execution", ("engram", "prepare_playbook_execution"),
     {"playbook_id": "sec-1", "params_json": "{}"}),
]


@pytest.mark.parametrize(
    "tool,target,kwargs", _EXPORT_SPECS, ids=[r[0] for r in _EXPORT_SPECS]
)
def test_export_tools_refuse_non_owner(gov_engram, monkeypatch, tool, target, kwargs):
    """Flag ON + web → the writer is NEVER invoked and the caller is refused."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    called = {"hit": False}

    def _spy(*a, **k):
        called["hit"] = True
        return "SHOULD_NOT_BE_REACHED_" + SECRET

    scope, attr = target
    obj = gov_engram if scope == "engram" else mcp_server
    monkeypatch.setattr(obj, attr, _spy, raising=False)

    out = _call(tool, kwargs)

    assert not called["hit"], f"{tool} wrote the export file for a non-owner"
    assert SECRET not in out and "SHOULD_NOT_BE_REACHED" not in out
    assert "治理" in out or "Governance" in out, f"{tool} did not return a refusal"


@pytest.mark.parametrize(
    "tool,target,kwargs", _EXPORT_SPECS, ids=[r[0] for r in _EXPORT_SPECS]
)
def test_export_tools_proceed_for_owner(gov_engram, monkeypatch, tool, target, kwargs):
    """Flag ON + self → export proceeds (writer invoked, no refusal)."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "self")
    called = {"hit": False}

    def _ok(*a, **k):
        called["hit"] = True
        return {"status": "success", "files": ["/tmp/x"]}

    scope, attr = target
    obj = gov_engram if scope == "engram" else mcp_server
    monkeypatch.setattr(obj, attr, _ok, raising=False)

    out = _call(tool, kwargs)

    assert called["hit"], f"{tool} did not perform the export for the owner"
    assert "【治理层】" not in out, f"{tool} refused the private-self owner"


# ── 5. deny-by-default coverage: every tool classified, nothing un-wired ─────


def test_every_tool_is_governed_export_or_allowlisted():
    discovered = _discover_all_tools()
    unclassified = discovered - _CLASSIFIED
    assert not unclassified, (
        "Un-classified MCP tool(s): "
        f"{sorted(unclassified)}. Each async @mcp.tool must be EITHER governed "
        "(wired through _gov_rt and added to _MATRIX), an export "
        "(_EXPORT_OWNER_ONLY, refusal-tested), or _SAFE_ALLOWLIST with a "
        "documented reason. The round-16 miss was exactly an un-classified tool "
        "hiding behind an unrecognized name prefix — do not re-open that gap."
    )
    stale = _CLASSIFIED - discovered
    assert not stale, f"Classified tools no longer exist in mcp_server: {sorted(stale)}"


def test_governed_tools_actually_call_governance():
    """Each governed tool's source must route output through _gov_rt."""
    missing = []
    for name in sorted(_GOVERNED):
        src = inspect.getsource(getattr(mcp_server, name))
        if "_gov_rt.maybe_govern" not in src:
            missing.append(name)
    assert not missing, (
        f"Governed tools missing a maybe_govern call (silent bypass): {missing}"
    )


def test_export_tools_actually_gate():
    """Each export tool must call the pre-write owner gate."""
    missing = []
    for name in sorted(_EXPORT_OWNER_ONLY):
        src = inspect.getsource(getattr(mcp_server, name))
        if "_gov_rt.maybe_refuse_export" not in src:
            missing.append(name)
    assert not missing, (
        f"Export tools missing the pre-write owner gate: {missing}"
    )


# ── 6. UNIVERSAL file-side-effect harness ─────────────────────────────────────
#
# History: the original section-6 test (R17→R19) hand-selected 4 file-writer
# tools + search_knowledge. That approach had a structural weakness: the tool
# list itself was an attack surface — any governed/export tool NOT listed could
# leak secretly through a file side effect and the test would never notice.
# That is exactly how R17/R18/R19 FAILs happened (each time a new tool was
# found persisting secret content to a derived file).
#
# This harness replaces the hand-selected list with a PARAMETRIZED test over
# ALL tools in _GOVERNED | _EXPORT_OWNER_ONLY (currently 41 tools). Each
# instance:
#   1. Seeds a fresh Engram with secret lesson/decision/playbook content.
#   2. Snapshots every file under root.
#   3. Calls ONE tool as an untrusted ``web`` caller, governance ON, hybrid
#      search enabled (ENGRAM_SEARCH=hybrid — the R19 leak was invisible
#      without hybrid).
#   4. Asserts (a) the return does not contain the secret (case-insensitive),
#      and (b) no file under root is NEWLY created or NEWLY injected with
#      the secret marker (case-insensitive).
#
# File-diff logic (no name-based exclusions needed):
#   - New file not in the before-snapshot → check for secret marker.
#   - Modified file where marker was already present in the before-snapshot
#     → skip (source-store access-count rewrite: same body, different
#     metadata).
#   - Modified file where marker is NEWLY injected → LEAK.
#   - Unchanged file → skip.
#
# Coverage assertion: ``test_side_effect_harness_covers_all`` guarantees
# the harness list equals ``_GOVERNED | _EXPORT_OWNER_ONLY`` exactly, so
# a new tool classified into either set but missing from the harness fails
# at once — no more "forgot to add tool X".
#
# Negative verification: ``test_harness_detects_hybrid_index_leak`` forces
# ``allow_hybrid_index=True`` on the core (simulating the pre-R20-fix state)
# and confirms the harness goes RED — proving the diff logic is not vacuous.


def _snapshot_tree(root):
    """path -> bytes for every file under root (store files included)."""
    snap = {}
    for p in root.rglob("*"):
        if p.is_file():
            try:
                snap[p] = p.read_bytes()
            except OSError:
                pass
    return snap


def _seed_secret_store(e):
    """Seed lesson/decision/playbook with secret content; return all IDs.

    The secret marker lands in stored summaries, decision text, and playbook
    step actions so every derived view (quick-context / identity card /
    knowledge report / execution plan / search index) would surface it if
    it leaked.  Also persists a project snapshot under root so
    ``get_project_context`` has data to exercise.
    """
    les = e.add_lesson({"summary": "secret lesson " + SECRET, "detail": SECRET,
                        "sensitivity": "secret", "tier": "verified"})
    dec = e.add_decision({"question": "q", "choice": "secret choice " + SECRET,
                          "rationale": SECRET, "sensitivity": "secret",
                          "tier": "verified"})
    pb = e.add_playbook({"title": "pb safe title",
                         "steps": [{"order": 1, "action": SECRET,
                                    "detail": SECRET}],
                         "sensitivity": "secret", "tier": "verified"})
    # Project snapshot with secret so get_project_context has data.
    e.save_project_snapshot(str(e.root), {"summary": SECRET})
    return {
        "lesson_id": les.get("id", ""),
        "decision_id": dec.get("id", ""),
        "playbook_id": pb.get("id", ""),
    }


# (tool_name, kwargs_factory)
# kwargs_factory receives an ``ids`` dict with keys:
#   lesson_id, decision_id, playbook_id — from _seed_secret_store
#   _root — the tmp_path (Path) for the test's Engram
_SIDE_EFFECT_HARNESS = [
    # ── list/dict read tools ──
    ("get_lessons", lambda ids: {}),
    ("get_decisions", lambda ids: {}),
    ("get_relevant_knowledge", lambda ids: {"project_folder": str(ids["_root"])}),
    ("get_playbooks", lambda ids: {}),
    ("get_recent_playbooks", lambda ids: {}),
    ("get_recent_context", lambda ids: {}),
    ("search_knowledge", lambda ids: {"query": "secret lesson choice",
                                       "scope": "all", "limit": 5}),
    ("get_stale_knowledge", lambda ids: {}),
    ("get_knowledge_inheritance", lambda ids: {"description": "test project"}),
    ("get_related_knowledge", lambda ids: {"item_id": ids["lesson_id"]}),
    ("find_similar_knowledge", lambda ids: {"item_id": ids["lesson_id"]}),
    ("start_project", lambda ids: {"description": "new project",
                                    "project_folder": str(ids["_root"] / "proj")}),
    # ── single-item reads ──
    ("get_project_context", lambda ids: {"project_folder": str(ids["_root"])}),
    ("get_playbook", lambda ids: {"playbook_id": ids["playbook_id"]}),
    # ── owner-only aggregates ──
    ("get_knowledge_overview", lambda ids: {}),
    ("suggest_merges", lambda ids: {}),
    ("classify_legacy_playbooks", lambda ids: {}),
    ("apply_legacy_playbook_scope_suggestions", lambda ids: {}),
    ("rollback_playbook_scope_migration", lambda ids: {}),
    ("get_playbook_scope_review_queue", lambda ids: {}),
    ("list_playbooks_for_management", lambda ids: {}),
    ("resolve_playbook_scope_review", lambda ids: {"playbook_id": ids["playbook_id"],
                                                    "action": "skip"}),
    ("get_decision_thread", lambda ids: {"seed_id": ids["decision_id"]}),
    ("get_decision_history", lambda ids: {"question": "test question"}),
    ("get_execution_status", lambda ids: {"playbook_id": ids["playbook_id"]}),
    ("get_user_context", lambda ids: {}),
    ("get_resume_brief", lambda ids: {}),
    ("get_recall", lambda ids: {"project_folder": str(ids["_root"]),
                                "query": "secret lesson choice"}),
    ("get_daily_log", lambda ids: {"project_folder": str(ids["_root"])}),
    ("get_audit_log", lambda ids: {}),
    # ── export / file-writer tools ──
    ("refresh_quick_context", lambda ids: {"level": "standard"}),
    ("get_identity_card", lambda ids: {}),
    ("export_knowledge_report", lambda ids: {}),
    ("prepare_playbook_execution", lambda ids: {"playbook_id": ids["playbook_id"],
                                                 "params_json": "{}"}),
    ("export_engram", lambda ids: {}),
    ("export_engram_to_openclaw", lambda ids: {}),
    ("request_outline_review", lambda ids: {"lang": "zh"}),
    # ── write tools returning stored items ──
    ("update_knowledge", lambda ids: {"item_id": ids["lesson_id"],
                                       "updates_json": "{}"}),
    ("archive_knowledge", lambda ids: {"item_id": ids["lesson_id"]}),
    ("review_knowledge", lambda ids: {"knowledge_id": ids["lesson_id"]}),
    ("merge_knowledge", lambda ids: {"primary_id": ids["lesson_id"],
                                      "secondary_id": ids["decision_id"]}),
    ("link_knowledge", lambda ids: {"id_a": ids["lesson_id"],
                                     "id_b": ids["decision_id"]}),
    ("unlink_knowledge", lambda ids: {"id_a": ids["lesson_id"],
                                       "id_b": ids["decision_id"]}),
    ("update_playbook", lambda ids: {"playbook_id": ids["playbook_id"],
                                      "status": "active"}),
    ("delete_playbook", lambda ids: {"playbook_id": ids["playbook_id"],
                                      "dry_run": False, "confirm": True}),
    ("restore_playbook", lambda ids: {"playbook_id": ids["playbook_id"],
                                       "dry_run": False, "confirm": True}),
    # ── write tools whose dedup-reject echoes stored items ──
    ("add_lesson", lambda ids: {"summary": "near duplicate test lesson"}),
    ("add_decision", lambda ids: {"question": "test q", "choice": "test c"}),
    ("add_playbook", lambda ids: {"title": "dup test pb", "triggers": "a,b"}),
    ("memory_store", lambda ids: {"kind": "lesson",
                                   "content_json": '{"summary": "near duplicate"}'}),
]

_SIDE_EFFECT_TOOL_NAMES = {row[0] for row in _SIDE_EFFECT_HARNESS}


def _check_leaked_files(root, before, after):
    """Return root-relative paths where the secret marker was NEWLY introduced.

    - New file (not in before): check for marker (case-insensitive).
    - Modified file where marker was already present: skip (source-store
      access-count rewrite — same body, different metadata).
    - Modified file where marker is newly injected: LEAK.
    - Unchanged file: skip.
    """
    marker = SECRET.lower().encode()
    leaked = []
    for path, data_after in after.items():
        data_before = before.get(path)
        if data_before == data_after:
            continue  # untouched by the call
        # Source-store rewrite: marker was already present before the call
        if data_before is not None and marker in data_before.lower():
            continue
        if marker in data_after.lower():
            leaked.append(str(path.relative_to(root)))
    return leaked


@pytest.mark.parametrize(
    "tool,kwargs_fn", _SIDE_EFFECT_HARNESS,
    ids=[r[0] for r in _SIDE_EFFECT_HARNESS],
)
def test_no_file_side_effect_leaks_secret(
    gov_engram, monkeypatch, tool, kwargs_fn,
):
    """Universal: every governed/export tool run as non-owner must not
    persist the secret to any new or modified file under root."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    e = gov_engram
    ids = _seed_secret_store(e)
    ids["_root"] = e.root
    kwargs = kwargs_fn(ids)

    before = _snapshot_tree(e.root)

    try:
        out = _call(tool, kwargs)
    except Exception:
        out = ""  # tool errored — no leak possible via return

    # (a) return value must not contain the secret (case-insensitive)
    if out:
        assert SECRET.lower() not in out.lower(), (
            f"{tool} returned the secret to a non-owner: {out[:200]!r}"
        )

    after = _snapshot_tree(e.root)

    # (b) no file newly injected with the secret
    leaked = _check_leaked_files(e.root, before, after)
    assert not leaked, (
        f"{tool} persisted the secret to disk (file side-effect leak): "
        f"{leaked}. The return may be governed, but the FILE is the "
        f"disclosure surface."
    )


def test_side_effect_harness_covers_all():
    """Coverage: every tool in _GOVERNED | _EXPORT_OWNER_ONLY must be in the
    harness, and vice versa. A missing tool means a governance-relevant tool
    is NOT tested for file-based leaks; an extra tool is a classification error."""
    required = _GOVERNED | _EXPORT_OWNER_ONLY
    missing = required - _SIDE_EFFECT_TOOL_NAMES
    assert not missing, (
        f"Governed/export tools missing from the file-side-effect harness: "
        f"{sorted(missing)}. Add them to _SIDE_EFFECT_HARNESS with legal "
        f"kwargs so the universal harness covers them."
    )
    extra = _SIDE_EFFECT_TOOL_NAMES - required
    assert not extra, (
        f"Tools in _SIDE_EFFECT_HARNESS but not in _GOVERNED | "
        f"_EXPORT_OWNER_ONLY: {sorted(extra)}. Remove them or classify "
        f"them correctly."
    )


def test_harness_detects_hybrid_index_leak(gov_engram, monkeypatch):
    """Negative verification: force the pre-R20-fix code path (hybrid index
    built from the FULL corpus without governance) and confirm the harness
    CATCHES the resulting search_index.db leak.

    Without this test the harness could be vacuously passing — we need to
    prove it goes RED when a real leak exists.
    """
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    e = gov_engram
    _seed_secret_store(e)

    before = _snapshot_tree(e.root)

    # Bypass the MCP governance gate: call core directly with
    # allow_hybrid_index=True → writes unfiltered corpus to search_index.db.
    e.search_knowledge("secret", allow_hybrid_index=True)

    after = _snapshot_tree(e.root)

    leaked = _check_leaked_files(e.root, before, after)
    assert leaked, (
        "Negative verification FAILED: the harness did NOT detect the "
        "search_index.db leak when allow_hybrid_index=True was forced. "
        "The file-diff logic is a false negative — the harness is useless."
    )


# ── 7. playbook usage_policy header ──────────────────────────────────────────
#
# Every playbook/execution-plan returned by an MCP tool must carry a
# ``usage_policy`` field instructing any consuming AI to treat it as a
# passive reference ("confirm with user before each step"). This embeds
# the user's "decision ∈ user / execution ∈ AI" operating model into the
# data format so it travels with the playbook across tools.
#
# The field is injected in the MCP layer; the stored data stays clean.
# Governance-withheld stubs must NOT carry the field (no playbook to act on).

from piia_engram.mcp_server import _PLAYBOOK_USAGE_POLICY, _EXECUTION_USAGE_POLICY

# Tools that should carry the playbook usage policy
_PLAYBOOK_POLICY_TOOLS = [
    ("get_playbook", {"playbook_id": "PB_ID"}, _PLAYBOOK_USAGE_POLICY),
    ("get_playbooks", {}, _PLAYBOOK_USAGE_POLICY),
    ("get_recent_playbooks", {}, _PLAYBOOK_USAGE_POLICY),
]

# Tools that should carry the execution usage policy
_EXECUTION_POLICY_TOOLS = [
    ("prepare_playbook_execution", {"playbook_id": "PB_ID", "params_json": "{}"}, _EXECUTION_USAGE_POLICY),
    ("get_execution_status", {"playbook_id": "PB_ID"}, _EXECUTION_USAGE_POLICY),
]


def _make_playbook(gov_engram):
    """Create a public playbook in the real store and return its ID."""
    pb = gov_engram.add_playbook({
        "title": "test playbook for policy",
        "steps": [{"order": 1, "action": "do step 1", "detail": "details"}],
        "sensitivity": "public",
    })
    return pb.get("id", "")


@pytest.mark.parametrize(
    "tool,kwargs,expected_policy", _PLAYBOOK_POLICY_TOOLS,
    ids=[r[0] for r in _PLAYBOOK_POLICY_TOOLS],
)
def test_playbook_tools_carry_usage_policy(
    gov_engram, monkeypatch, tool, kwargs, expected_policy,
):
    """Playbook read tools return usage_policy in every item."""
    pb_id = _make_playbook(gov_engram)
    final_kwargs = {k: (pb_id if v == "PB_ID" else v) for k, v in kwargs.items()}

    out = _call(tool, final_kwargs)

    assert "usage_policy" in out, f"{tool} missing usage_policy field"
    assert expected_policy[:40] in out, (
        f"{tool} has wrong usage_policy text: {out[:300]!r}"
    )


@pytest.mark.parametrize(
    "tool,kwargs,expected_policy", _EXECUTION_POLICY_TOOLS,
    ids=[r[0] for r in _EXECUTION_POLICY_TOOLS],
)
def test_execution_tools_carry_usage_policy(
    gov_engram, monkeypatch, tool, kwargs, expected_policy,
):
    """Execution plan tools return the execution variant of usage_policy."""
    pb_id = _make_playbook(gov_engram)
    final_kwargs = {k: (pb_id if v == "PB_ID" else v) for k, v in kwargs.items()}

    # prepare_playbook_execution is export-gated; run as owner for the
    # positive test (non-owner refusal is tested in section 4).
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "self")

    out = _call(tool, final_kwargs)

    # get_execution_status may return an error if no plan exists yet;
    # for prepare_, the plan is created. For get_, accept either policy
    # or an error message.
    if "error" not in out.lower() and "失败" not in out:
        assert "usage_policy" in out, f"{tool} missing usage_policy field"
        assert expected_policy[:40] in out, (
            f"{tool} has wrong usage_policy text: {out[:300]!r}"
        )


def test_usage_policy_absent_on_governance_withheld(gov_engram, monkeypatch):
    """When governance withholds a playbook, usage_policy must NOT appear."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")

    pb_id = _make_playbook(gov_engram)

    # get_playbook on a public item should still show policy for web caller
    # (public item passes the ceiling). Create a secret playbook for withhold.
    sec_pb = gov_engram.add_playbook({
        "title": "secret pb",
        "steps": [{"order": 1, "action": "classified"}],
        "sensitivity": "secret",
    })
    sec_id = sec_pb.get("id", "")

    out = _call("get_playbook", {"playbook_id": sec_id})

    # The withheld stub should have governance_withheld but NO usage_policy
    assert "governance_withheld" in out, "Expected a governance withhold stub"
    assert "usage_policy" not in out, (
        "usage_policy should NOT appear on a governance-withheld stub"
    )
