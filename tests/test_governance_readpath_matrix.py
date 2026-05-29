"""Round-15 P1 regression: the read-path enforcement matrix.

Codex round-15 FAILed the a0 cutover because governance was wired into only a
*subset* of the agent-facing knowledge read tools. Enforcement flags are not
like functional flags — they cannot be partially deployed. MCP tool selection
is model-side, so a single ungoverned sibling read tool (e.g. ``get_decisions``
while only ``search_knowledge`` is gated) is a *complete* bypass: a low-trust
agent just calls the ungoverned tool. The whole layer's guarantee collapses to
its weakest tool.

This module turns "missed a tool" into a test failure two ways:

1. **Leak matrix** — every governed read tool is driven with a faked Engram
   that returns a public item AND a secret item; with ``ENGRAM_GOVERNANCE=1``
   and ``ENGRAM_CLIENT_TYPE=web`` (an untrusted external agent) the secret
   marker must NEVER appear in the tool's output. Filtering tools must still
   return the public marker (proving they *filter*, not blanket-refuse).

2. **Coverage backstop** — every read-shaped ``@mcp.tool`` in the module must be
   classified as either GOVERNED (and present in the leak matrix) or explicitly
   ALLOWLISTED as non-knowledge (identity field, registry, audit, diagnostics,
   path-only export, external fetch). A newly-added read tool that is neither
   fails the coverage test, forcing a conscious wire-or-allowlist decision.

A third test pins the OFF-by-default contract: with the flag unset the secret
marker passes through every tool unchanged (byte-identical read path).
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


# ── the matrix: (tool, engram_method, fake_return, kwargs, mode) ─────────────
#
# mode="filter"  → secret omitted, public still returned (item-level gate)
# mode="withhold" → whole payload withheld for non-owners (owner-only gate, or a
#                   single secret item replaced by a stub)

_MATRIX = [
    # ---- list[dict] tools (maybe_govern_list) ----
    ("get_lessons", "get_lessons", [_pub(), _sec()], {}, "filter"),
    ("get_decisions", "get_decisions", [_pub(), _sec()], {}, "filter"),
    ("get_relevant_knowledge", "get_relevant_lessons", [_pub(), _sec()],
     {"project_folder": "/x"}, "filter"),
    ("get_playbooks", "get_playbooks", [_pub(), _sec()], {}, "filter"),
    ("get_recent_playbooks", "get_recent_playbooks", [_pub(), _sec()], {}, "filter"),
    ("get_recent_context", "get_recent_context", [_pub(), _sec()], {}, "filter"),
    # ---- dict-of-lists tools (maybe_govern_buckets) ----
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
    # ---- single knowledge-item dicts (maybe_govern_one) → withheld stub ----
    ("get_project_context", "get_project_snapshot", _sec(),
     {"project_folder": "/x"}, "withhold"),
    ("get_playbook", "get_playbook", _sec(), {"playbook_id": "sec-1"}, "withhold"),
    # ---- owner-only aggregates / dumps (maybe_govern_owner_only / _dump) ----
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
    ("get_user_context", "generate_context", "identity card\n" + SECRET, {}, "withhold"),
    ("get_resume_brief", "get_resume_brief", "resume brief\n" + SECRET, {}, "withhold"),
    ("get_daily_log", "get_daily_log", "daily log\n" + SECRET,
     {"project_folder": "/x"}, "withhold"),
    ("export_knowledge_report", "export_knowledge_report", "# report\n" + SECRET,
     {}, "withhold"),
]

# The set the leak matrix actually exercises — used by the coverage backstop.
_GOVERNED = {row[0] for row in _MATRIX}

# Read-shaped @mcp.tool functions that are intentionally NOT governed by a0,
# each for a concrete reason. a0 governs *knowledge bodies* (lessons, decisions,
# playbooks, project/aggregate views). These are out of that scope:
_ALLOWLIST_NON_KNOWLEDGE = {
    # identity / preference fields — covered by their own safe= projection and
    # deferred to the later permission-profile phase, not knowledge bodies.
    "get_identity_card", "get_profile", "get_work_style", "get_preferences",
    "get_trust_boundaries", "get_quality_standards",
    # metadata / registry / diagnostics — no knowledge bodies in the response.
    "get_domains", "list_projects", "list_agent_sessions",
    "get_execution_status", "find_tool", "list_tools", "get_audit_log",
    "export_feedback_report", "doctor",
    # writes-with-a-read-shaped-name / external fetch.
    "extract_session_insights", "read_web_content",
    # whole-store exports that return a FILE PATH (not the body) over MCP — the
    # disclosure risk is filesystem access, not an MCP read-path leak.
    "export_engram", "export_engram_to_openclaw",
}

_READ_PREFIXES = (
    "get_", "search_", "find_", "list_", "export_", "suggest_",
    "prepare_", "extract_", "read_",
)


def _discover_read_tools() -> set[str]:
    """All read-shaped async ``@mcp.tool`` functions defined in mcp_server."""
    out: set[str] = set()
    for name, obj in vars(mcp_server).items():
        if not inspect.iscoroutinefunction(obj):
            continue
        if getattr(obj, "__module__", "") != mcp_server.__name__:
            continue
        if name == "doctor" or name.startswith(_READ_PREFIXES):
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


# ── 1. leak matrix ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_no_secret_leaks_to_external_agent(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    """Flag ON + untrusted 'web' client → the secret marker must never appear."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    monkeypatch.setattr(gov_engram, method, lambda *a, **k: fake)

    out = _call(tool, kwargs)

    assert SECRET not in out, f"{tool} leaked a secret item to an external agent"
    if mode == "filter":
        assert PUBLIC in out, f"{tool} dropped the public item (should filter, not refuse)"


# ── 2. OFF-by-default passthrough ────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_flag_off_is_byte_identical_passthrough(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    """Flag unset → governance is a no-op; the raw payload (incl. secret) flows.

    This proves the matrix genuinely carries the secret through each tool, so
    the flag-ON absence above is meaningful and not a vacuous pass.
    """
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    monkeypatch.setattr(gov_engram, method, lambda *a, **k: fake)

    out = _call(tool, kwargs)

    assert SECRET in out, f"{tool} did not pass the secret through with the flag OFF"


# ── 3. owner (private-self) still sees governed content ──────────────────────


@pytest.mark.parametrize(
    "tool,method,fake,kwargs,mode", _MATRIX, ids=[r[0] for r in _MATRIX]
)
def test_owner_sees_everything_when_flag_on(
    gov_engram, monkeypatch, tool, method, fake, kwargs, mode
):
    """Flag ON + 'self' client → private-self ceiling returns the secret too.

    Enforcement must restrict *lower* tiers without crippling the owner's own
    read path.
    """
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "self")
    monkeypatch.setattr(gov_engram, method, lambda *a, **k: fake)

    out = _call(tool, kwargs)

    assert SECRET in out, f"{tool} withheld content from the private-self owner"


# ── 4. coverage backstop: no un-wired read tool may slip in ──────────────────


def test_every_read_tool_is_governed_or_allowlisted():
    discovered = _discover_read_tools()
    classified = _GOVERNED | _ALLOWLIST_NON_KNOWLEDGE
    unclassified = discovered - classified
    assert not unclassified, (
        "New read-shaped MCP tool(s) are neither governed nor allowlisted: "
        f"{sorted(unclassified)}. Wire each through _gov_rt.maybe_govern_* and "
        "add it to the leak matrix, or allowlist it as non-knowledge with a "
        "documented reason."
    )
    # Also catch stale classification entries (renamed/removed tools).
    stale = classified - discovered
    assert not stale, f"Classified tools no longer exist in mcp_server: {sorted(stale)}"


def test_governed_tools_actually_call_governance():
    """Each governed tool's source must route output through _gov_rt.maybe_govern."""
    missing = []
    for name in sorted(_GOVERNED):
        src = inspect.getsource(getattr(mcp_server, name))
        if "_gov_rt.maybe_govern" not in src:
            missing.append(name)
    assert not missing, (
        f"Governed tools missing a maybe_govern call (silent bypass): {missing}"
    )
