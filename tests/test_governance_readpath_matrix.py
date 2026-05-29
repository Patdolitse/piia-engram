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
    # start_project embeds the get_knowledge_inheritance bundle (round-16 P1-1).
    ("start_project", "get_knowledge_inheritance",
     {"query": "new project", "items": [_pub(), _sec()]},
     {"description": "new", "project_folder": "/x"}, "filter"),
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
    ("archive_playbook", "archive_playbook", {"title": SECRET}, {"playbook_id": "sec-1"}, "withhold"),
    # ---- owner-only aggregates / dumps / derived views (maybe_govern_owner_only) ----
    ("prepare_playbook_execution", "prepare_playbook_execution",
     {"playbook_id": "sec-1", "steps": [{"order": 1, "text": SECRET}]},
     {"playbook_id": "sec-1"}, "withhold"),
    ("get_knowledge_overview", "get_knowledge_overview",
     {"digest": {"top_lessons": [_sec()]}, "health": {}, "stale": {}},
     {}, "withhold"),
    ("suggest_merges", "suggest_merges", [{"pair": [_sec(), _sec()]}], {}, "withhold"),
    ("get_decision_thread", "get_decision_thread",
     {"order": [{"id": "sec-1", "summary": SECRET}], "active_ids": []},
     {"seed_id": "sec-1"}, "withhold"),
    ("get_execution_status", "get_execution_status",
     {"title": SECRET, "steps": [{"action": SECRET, "status": "pending"}]},
     {"playbook_id": "sec-1"}, "withhold"),
    ("get_identity_card", "export_identity_card", "# identity card\n- " + SECRET, {}, "withhold"),
    ("get_user_context", "generate_context", "identity card\n" + SECRET, {}, "withhold"),
    ("get_resume_brief", "get_resume_brief", "resume brief\n" + SECRET, {}, "withhold"),
    ("get_daily_log", "get_daily_log", "daily log\n" + SECRET,
     {"project_folder": "/x"}, "withhold"),
    ("export_knowledge_report", "export_knowledge_report", "# report\n" + SECRET,
     {}, "withhold"),
]

# The set the leak matrix actually exercises — used by the coverage backstop.
_GOVERNED = {row[0] for row in _MATRIX}

# Export tools: the disclosure surface is a FILE written to disk (full-store
# backup / review HTML), not the MCP return value. Governed by a PRE-write owner
# gate (maybe_refuse_export) and verified by test_export_tools_refuse_non_owner.
_EXPORT_OWNER_ONLY = {
    "export_engram", "export_engram_to_openclaw", "request_outline_review",
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
    "get_domains", "list_projects", "list_agent_sessions",
    "find_tool", "list_tools", "get_audit_log", "export_feedback_report", "doctor",
    "update_execution_step",  # counts/status only, no body
    # caller-supplied-content writes — echo only what the caller just passed in,
    # so there is nothing to read *back* above the ceiling.
    "memory_store", "add_lesson", "add_decision", "add_playbook",
    "bulk_add_knowledge", "ingest_notes", "extract_session_insights",
    "save_project_snapshot", "save_agent_context", "wrap_up_session",
    "register_tool", "update_identity", "refresh_quick_context",
    # relation/maintenance ops returning caller IDs / counts only.
    "add_relation", "apply_review",
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
