"""Strict-mode closure (4.21.1, plan V2): MCP default-refuse, reconcile precedence, tombstones.

Strict-gated (ENGRAM_APPROVAL=strict):
  * an agent can only *propose* over MCP; every other mutating tool is refused
    (default-refuse with an explicit allowlist), whatever caller trust it claims;
  * agent-supplied trust fields (user_confirmed, tier, status, approval_*, promotion_*)
    never make a row authoritative;
  * the served MCP instructions drop the auto-write lifecycle.
Mode-independent (disclosed):
  * B.1 reconcile_authorized=false beats ENGRAM_RECONCILE=1;
  * B.2 text rejected by an explicit Owner mark is refused on every insert route;
  * B.3 retired rows count as duplicates (revocable).
Owner verbs live in the local ``engram review`` CLI; its applies are attributed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from knowledge_seed import raw_write_json
from piia_engram.core import Engram
from piia_engram.reconcile import ReconcileMixin
from piia_engram.staging_review import batch_review_staging

# Mutating MCP tools an agent may still call under strict. Every other tool in a
# mutating governance class is refused (default-refuse).
STRICT_ALLOWLIST: dict[str, str] = {
    # proposals: knowledge rows they create land in staging
    "add_lesson": "proposal",
    "add_decision": "proposal",
    "memory_store": "proposal",  # kind=playbook refused
    "ingest_notes": "proposal",
    "extract_session_insights": "proposal",
    "wrap_up_session": "proposal",
    "onboard_repo": "proposal",
    # no knowledge change
    "review_staging": "list / dry-run only",
    "playbook_execution": "run-log files + access_count only",
    "save_agent_context": "checkpoint files only",
    "start_project": "project registry",
    "register_tool": "tool registry",
    "check_anchors": "anchor check",
    "user_portrait": "derived portrait snapshot, never identity/profile.json",
    # exports: files on disk, never knowledge rows
    "export_engram": "export",
    "export_knowledge_report": "export",
    "get_identity_card": "export",
    "refresh_quick_context": "export",
    "request_outline_review": "export",
}

# Self-reported client types; strict refusal must not depend on them.
CLIENT_TYPES = ["claude_code", "self", ""]

# Root-level files a tool may touch without changing knowledge or identity.
DERIVED_FILES = {
    "audit.log",
    "metrics_log.jsonl",
    "telemetry.log",
    "telemetry_config.json",
    "session_state.json",
    "search_index.db",
    "quick_context.md",
    "beta_events.jsonl",
    "first_value_events.jsonl",
    "governance_ledger.jsonl",
    ".engram-governance-ledger.lock",
}
DERIVED_DIRS = {"logs", "contexts", "exports", "daily", "metrics", "operations", "backups"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _audit_on(monkeypatch):
    """Refusals and receipts are asserted through audit.log (off by default in tests)."""
    monkeypatch.setenv("ENGRAM_AUDIT", "1")


def _run(coro):
    return asyncio.run(coro)


def _mcp_server():
    import piia_engram.mcp_server as mcp_server

    return mcp_server


def _mutating_tools() -> set[str]:
    m = _mcp_server()
    return {n for n, c in m.TOOL_GOVERNANCE_CLASS.items() if c in m.WRITE_GATE_CLASSES_MUTATING}


def _read_tools() -> list[str]:
    m = _mcp_server()
    return sorted(n for n, c in m.TOOL_GOVERNANCE_CLASS.items() if c == "read")


def _refused_tools() -> list[str]:
    return sorted(_mutating_tools() - set(STRICT_ALLOWLIST))


def _seed_root(root: Path) -> Path:
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}), encoding="utf-8"
    )
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "lessons.json").write_text("[]", encoding="utf-8")
    (root / "knowledge" / "decisions.json").write_text("[]", encoding="utf-8")
    return root


def _is_derived(rel: Path) -> bool:
    if rel.parts and rel.parts[0] in DERIVED_DIRS:
        return True
    return len(rel.parts) == 1 and rel.name in DERIVED_FILES


def _snapshot(root: Path) -> dict[str, str]:
    """Hash of every file that is not a derived/log file."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not _is_derived(p.relative_to(root)):
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _dummy_kwargs(func) -> dict:
    import inspect

    kwargs: dict = {}
    for name, param in inspect.signature(func).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        ann = param.annotation
        kwargs[name] = 1 if ann is int else False if ann is bool else 1.0 if ann is float else "x"
    return kwargs


def _is_strict_refusal(result) -> bool:
    return (
        _mcp_server()._gov_rt.is_governance_refusal(result)
        and "ENGRAM_APPROVAL=strict" in result
        and "engram review" in result  # names the Owner's CLI path
    )


def _audit(root: Path) -> list[dict]:
    path = root / "audit.log"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _rows(root: Path, kind: str = "lesson") -> list[dict]:
    name = "lessons.json" if kind == "lesson" else "decisions.json"
    path = root / "knowledge" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _tombstones(root: Path) -> list[dict]:
    path = root / "knowledge" / "tombstones.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _reject(eng: Engram, item_id: str) -> None:
    result = batch_review_staging(eng, [{"id": item_id, "action": "reject"}], dry_run=False, confirm=True)
    assert result["counts"]["applied"] == 1


@pytest.fixture
def mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _seed_root(tmp_path / "engram")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    m = _mcp_server()
    m._engram = Engram(root)
    return m, root


@pytest.fixture
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    return Engram(root=tmp_path)


# ---------------------------------------------------------------------------
# 1. completeness
# ---------------------------------------------------------------------------


def test_strict_allowlist_names_only_real_mutating_tools():
    assert set(STRICT_ALLOWLIST) - _mutating_tools() == set()


def test_default_refuse_covers_the_owner_verbs():
    refused = set(_refused_tools())
    owner_verbs = {
        "add_playbook", "manage_playbook", "update_identity", "update_knowledge",
        "confirm_knowledge", "merge_knowledge", "archive_knowledge", "manage_relation",
        "import_engram", "onboard_accept", "manage_caller_trust", "save_project_snapshot",
    }
    assert owner_verbs <= refused


# ---------------------------------------------------------------------------
# 2. writer-spy: default-refuse under strict, whatever the caller claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("client_type", CLIENT_TYPES)
@pytest.mark.parametrize("tool_name", _refused_tools())
def test_strict_refuses_and_leaves_store_unchanged(mcp, monkeypatch, tool_name, client_type):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
    func = getattr(m, tool_name)

    before = _snapshot(root)
    result = _run(func(**_dummy_kwargs(func)))

    assert _is_strict_refusal(result), f"{tool_name} not refused under strict: {result!r:.200}"
    assert _snapshot(root) == before, f"{tool_name} changed the store while refusing"
    refusals = [a for a in _audit(root) if a.get("action") == "refused"]
    assert any(tool_name in json.dumps(a) and "strict_owner_only" in json.dumps(a) for a in refusals)


@pytest.mark.parametrize("tool_name", _refused_tools())
def test_unset_mode_never_returns_the_strict_refusal(mcp, tool_name):
    m, _root = mcp
    func = getattr(m, tool_name)

    assert not _is_strict_refusal(_run(func(**_dummy_kwargs(func))))


def test_strict_memory_store_playbook_refused_lesson_staged(mcp, monkeypatch):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    before = _snapshot(root)
    refused = _run(m.memory_store(
        kind="playbook", content_json=json.dumps({"title": "t", "steps": ["a", "b", "c"]}),
        user_confirmed=True,
    ))
    assert _is_strict_refusal(refused)
    assert _snapshot(root) == before

    _run(m.memory_store(
        kind="lesson", content_json=json.dumps({"summary": "strict proposal lands in staging"}),
        user_confirmed=True,
    ))
    assert [r["tier"] for r in _rows(root)] == ["staging"]


@pytest.mark.parametrize(
    "call",
    [
        {"action": "batch", "dry_run": False, "confirm": True},
        {"action": "review_item"},
        {"action": "apply_text", "dry_run": False, "confirm": True},
    ],
    ids=["batch-apply", "review_item", "apply_text"],
)
def test_strict_review_staging_refuses_everything_but_list_and_dry_run(mcp, monkeypatch, call):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    row = m._engram.add_lesson("a proposal waiting for the owner", domain="t")
    actions = json.dumps([{"id": row["id"], "action": "approve"}])

    assert json.loads(_run(m.review_staging(action="list")))["status"] == "listed"
    dry = json.loads(_run(m.review_staging(action="batch", actions_json=actions, dry_run=True)))
    assert dry["status"] == "dry_run"

    before = _snapshot(root)
    kwargs = dict(call)
    if kwargs["action"] == "batch":
        kwargs["actions_json"] = actions
    elif kwargs["action"] == "review_item":
        kwargs["knowledge_id"] = row["id"]
    else:
        kwargs["review_text"] = json.dumps({"confirmed": [row["id"]]})
    result = _run(m.review_staging(**kwargs))
    assert _is_strict_refusal(result)
    assert _snapshot(root) == before


def test_strict_user_portrait_save_never_touches_identity(mcp, monkeypatch):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    profile = (root / "identity" / "profile.json").read_bytes()

    result = _run(m.user_portrait(action="save"))

    assert not _is_strict_refusal(result)
    assert (root / "identity" / "profile.json").read_bytes() == profile
    assert _rows(root) == []


def test_strict_playbook_execution_writes_run_logs_and_usage_counter_only(mcp, monkeypatch):
    m, root = mcp
    playbook = m._engram.add_playbook({"title": "t", "steps": ["a", "b", "c"], "triggers": ["x"]})
    row_path = root / "playbooks" / f"{playbook['id']}.json"
    before = json.loads(row_path.read_text(encoding="utf-8"))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    _run(m.playbook_execution(action="prepare", playbook_id=playbook["id"]))

    after = json.loads(row_path.read_text(encoding="utf-8"))
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed <= {"access_count"}
    assert (root / "playbooks" / "executions" / f"{playbook['id']}.json").exists()
    assert _rows(root) == []


def _knowledge_and_identity(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for sub in ("knowledge", "identity"):
        for p in sorted((root / sub).rglob("*")):
            if p.is_file():
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


_NON_PROPOSAL_ALLOWED = sorted(
    name for name, role in STRICT_ALLOWLIST.items() if role != "proposal" and name != "review_staging"
)


@pytest.mark.parametrize("tool_name", _NON_PROPOSAL_ALLOWED)
def test_strict_allowlisted_tools_never_change_knowledge_or_identity(mcp, monkeypatch, tool_name):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    func = getattr(m, tool_name)

    before = _knowledge_and_identity(root)
    try:
        _run(func(**_dummy_kwargs(func)))
    except Exception:
        pass

    assert _knowledge_and_identity(root) == before, f"{tool_name} changed knowledge/ or identity/"


# ---------------------------------------------------------------------------
# 3. read sweep: no read tool writes knowledge or identity under strict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", _read_tools())
def test_strict_read_tools_write_only_derived_files(mcp, monkeypatch, tool_name):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    func = getattr(m, tool_name)

    def call():
        try:
            _run(func(**_dummy_kwargs(func)))
        except Exception:
            pass  # a dummy argument may be rejected; only the store delta matters

    call()  # first use of a fresh root may bootstrap it; the live store is past that
    before = _snapshot(root)
    call()

    assert _snapshot(root) == before, f"{tool_name} wrote outside the derived-file list"


# ---------------------------------------------------------------------------
# 4. agent-supplied trust fields never make a row authoritative
# ---------------------------------------------------------------------------

_TRUST_FIELDS = {
    "tier": "verified",
    "status": "active",
    "user_confirmed": True,
    "approval_status": "approved",
    "approval_required": False,
    "promotion_reason": "user_confirmed",
    "promoted_at": "2026-01-01T00:00:00Z",
}


def test_strict_memory_store_strips_trust_fields(mcp, monkeypatch):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    _run(m.memory_store(
        kind="lesson",
        content_json=json.dumps({"summary": "smuggled trust fields", **_TRUST_FIELDS}),
        user_confirmed=True,
    ))

    (row,) = _rows(root)
    assert row["tier"] == "staging"
    assert row["approval_status"] == "pending"
    assert "promotion_reason" not in row and "promoted_at" not in row
    assert row.get("user_confirmed") is not True


def test_strict_core_add_lesson_and_decision_strip_trust_fields(eng, tmp_path):
    eng.add_lesson({"summary": "core smuggle", **_TRUST_FIELDS})
    eng.add_decision({"question": "q?", "choice": "c", "reasoning": "r", **_TRUST_FIELDS})

    for kind in ("lesson", "decision"):
        (row,) = _rows(tmp_path, kind)
        assert row["tier"] == "staging"
        assert "promotion_reason" not in row and "promoted_at" not in row


_TRUST_KWARGS = {
    "tier": "verified",
    "status": "active",
    "user_confirmed": True,
    "approval_status": "approved",
    "approval_required": False,
    "promotion_reason": "user_confirmed",
    "promoted_at": "2026-01-01T00:00:00Z",
}


@pytest.mark.parametrize("route", sorted(["add_lesson", "add_decision", "memory_store",
                                           "memory_store_bulk", "ingest_notes",
                                           "extract_session_insights", "wrap_up_session",
                                           "onboard_repo", "reconcile"]))
def test_strict_insert_point_ignores_trust_fields_on_every_route(mcp, monkeypatch, route):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    real_lesson, real_decision = Engram.add_lesson, Engram.add_decision

    def lesson_with_trust(self, *args, **kwargs):
        return real_lesson(self, *args, **{**kwargs, **_TRUST_KWARGS})

    def decision_with_trust(self, *args, **kwargs):
        return real_decision(self, *args, **{**kwargs, **_TRUST_KWARGS})

    monkeypatch.setattr(Engram, "add_lesson", lesson_with_trust)
    monkeypatch.setattr(Engram, "add_decision", decision_with_trust)

    STAGED_ROUTES[route](m, root)

    created = _rows(root, "lesson") + _rows(root, "decision")
    assert created, f"route {route} created no rows"
    for row in created:
        assert row["tier"] == "staging", route
        assert row.get("approval_status") == "pending", route
        assert "promotion_reason" not in row and "promoted_at" not in row, route
        assert row.get("user_confirmed") is not True, route


def test_unset_memory_store_trust_fields_behaviour_is_pinned(mcp):
    m, root = mcp

    _run(m.memory_store(
        kind="lesson",
        content_json=json.dumps({"summary": "unset smuggle", **_TRUST_FIELDS}),
        user_confirmed=True,
    ))

    (row,) = _rows(root)
    # 4.21.0: tier is stripped at the MCP layer and the risk gate decides (low risk -> verified).
    assert row["tier"] == "verified"


# ---------------------------------------------------------------------------
# 5. reconcile precedence (B.1): the config key beats the env var
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "config", "expected"),
    [
        (None, None, True),
        (None, True, True),
        (None, False, False),
        ("0", None, False),
        ("0", True, False),
        ("0", False, False),
        ("1", None, True),
        ("1", True, True),
        ("1", False, False),  # the only cell that changed from 4.21.0
    ],
)
def test_reconcile_authorization_table(tmp_path, monkeypatch, env, config, expected):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    if env is None:
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    else:
        monkeypatch.setenv("ENGRAM_RECONCILE", env)
    if config is not None:
        (tmp_path / "telemetry_config.json").write_text(
            json.dumps({"reconcile_authorized": config}), encoding="utf-8"
        )

    assert ReconcileMixin._reconcile_authorized() is expected


def test_overridden_env_is_reported_not_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    (tmp_path / "telemetry_config.json").write_text(
        json.dumps({"reconcile_authorized": False}), encoding="utf-8"
    )
    eng = Engram(root=tmp_path)

    result = eng.reconcile_memories()

    assert result["imported"] == 0
    assert any(
        "reconcile_env_overridden_by_config" in json.dumps(a) for a in _audit(tmp_path)
    )
    from piia_engram import reconcile

    assert "ENGRAM_RECONCILE=1" in reconcile.reconcile_env_conflict_note()


# ---------------------------------------------------------------------------
# 6. tombstones (B.2): explicit reject marks only, permanent, every route
# ---------------------------------------------------------------------------


def test_reject_mark_writes_a_text_free_tombstone(eng, tmp_path):
    summary = "a proposal the owner turns down"
    row = eng.add_lesson(summary, domain="t")

    _reject(eng, row["id"])

    (stone,) = _tombstones(tmp_path)
    assert stone["id"] == row["id"]
    assert {"id", "kind", "scope", "h1", "h2", "rejected_at", "via"} <= set(stone)
    assert summary not in json.dumps(stone)


def test_approve_writes_no_tombstone(eng, tmp_path):
    row = eng.add_lesson("a proposal the owner accepts", domain="t")

    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)

    assert _tombstones(tmp_path) == []


def test_unset_archive_knowledge_over_mcp_and_core_never_tombstones(mcp):
    import inspect

    m, root = mcp
    via_mcp = m._engram.add_lesson("archived over mcp", domain="t")
    via_core = m._engram.add_lesson("archived over core", domain="t")
    params = set(inspect.signature(m.archive_knowledge).parameters)
    assert params == {"item_id"}, "the MCP tool must not accept a reason or internal flag"

    _run(m.archive_knowledge(item_id=via_mcp["id"]))
    m._engram.archive_knowledge(via_core["id"])

    statuses = {r["id"]: r["status"] for r in _rows(root)}
    assert statuses[via_mcp["id"]] != "active" and statuses[via_core["id"]] != "active"
    assert _tombstones(root) == []


def test_unset_mcp_batch_reject_tombstone_names_the_caller(mcp):
    m, root = mcp
    row = m._engram.add_lesson("x", domain="t", tier="staging")
    actions = json.dumps([{"id": row["id"], "action": "reject"}])

    _run(m.review_staging(action="batch", actions_json=actions, dry_run=False, confirm=True))

    (stone,) = _tombstones(root)
    assert stone["via"].startswith("mcp:")


def test_plain_archive_of_a_staging_row_writes_no_tombstone(eng, tmp_path):
    row = eng.add_lesson("archived without a reject mark", domain="t")

    eng.archive_knowledge(row["id"])

    assert _tombstones(tmp_path) == []


def test_capacity_moves_write_no_tombstone(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "1")
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_CEILING", "3")
    monkeypatch.setenv("ENGRAM_REVIEW_MIN_STAY_DAYS", "0")
    eng = Engram(root=tmp_path)

    for i in range(3):
        eng.add_lesson(f"queued proposal number {i} about topic {i * 7}", domain="t")

    assert _tombstones(tmp_path) == []


def test_identical_text_is_refused_even_when_citing_the_rejection(eng, tmp_path):
    row = eng.add_lesson("Always run the full suite before any commit", domain="t")
    _reject(eng, row["id"])

    plain = eng.add_lesson("always run the FULL suite, before any commit!", domain="other")
    cited = eng.add_lesson("Always run the full suite before any commit", domain="t", supersedes=row["id"])

    for result in (plain, cited):
        assert result["status"] == "rejected_before"
        assert result["rejection_id"] == row["id"]
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_reworded_reproposal_is_staged_and_flagged_then_re_reject_links(eng, tmp_path):
    first = eng.add_lesson("prefer tabs over spaces", domain="t")
    _reject(eng, first["id"])

    second = eng.add_lesson("use tab characters for indentation in every repo", domain="t", supersedes=first["id"])
    assert second["tier"] == "staging"
    assert second["reproposal_of_rejected"] == first["id"]

    _reject(eng, second["id"])
    stones = {s["id"]: s for s in _tombstones(tmp_path)}
    assert stones[second["id"]]["prior_rejection_id"] == first["id"]


def test_rejected_decision_cannot_be_proposed_again(eng):
    row = eng.add_decision("use tool A?", "yes", "because")
    _reject(eng, row["id"])

    again = eng.add_decision("use tool A?", "yes", "a different reason")

    assert again["status"] == "rejected_before"
    assert again["rejection_id"] == row["id"]


# Every staged route must reach the single insert check. A spy on the lookup
# records each checked claim; every row the route creates must have been checked.
_ROUTE_TEXT = "Verify each backup with sha256 before deleting the original file"


def _route_add_lesson(m, root):
    m._engram.add_lesson(_ROUTE_TEXT, domain="t")


def _route_add_decision(m, root):
    m._engram.add_decision("verify backups before deleting?", "yes, sha256 first", "safety")


def _route_memory_store(m, root):
    _run(m.memory_store(kind="lesson", content_json=json.dumps({"summary": _ROUTE_TEXT}), user_confirmed=True))


def _route_memory_store_bulk(m, root):
    _run(m.memory_store(
        kind="lesson", items_json=json.dumps([{"summary": _ROUTE_TEXT}, {"summary": _ROUTE_TEXT + " twice"}]),
        user_confirmed=True,
    ))


def _route_ingest_notes(m, root):
    _run(m.ingest_notes(text=f"Lesson: {_ROUTE_TEXT}.\nDecision: keep backups for 30 days because audits.", user_confirmed=True))


def _route_extract(m, root):
    _run(m.extract_session_insights(
        summary=f"We learned: {_ROUTE_TEXT}. We decided to keep backups for 30 days because audits need them.",
        user_confirmed=True,
    ))


def _route_wrap_up(m, root):
    _run(m.wrap_up_session(
        summary=(
            "We learned an important lesson: always verify each backup with sha256 before "
            "deleting the original file, because a corrupted copy lost data once. We decided "
            "to keep backups for 30 days because audits need them."
        ),
        user_confirmed=True,
    ))


def _route_onboard_repo(m, root):
    repo = root.parent / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "README.md").write_text("# demo\n\nRun `pytest` before committing.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _run(m.onboard_repo(project_root=str(repo)))


def _route_reconcile(m, root):
    mem = root.parent / "fake_claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "backup.md").write_text(
        f"---\nname: backup\ndescription: d\ntype: feedback\n---\n\n{_ROUTE_TEXT}.\n", encoding="utf-8"
    )
    m._engram._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]
    m._engram.reconcile_memories()


STAGED_ROUTES = {
    "add_lesson": _route_add_lesson,
    "add_decision": _route_add_decision,
    "memory_store": _route_memory_store,
    "memory_store_bulk": _route_memory_store_bulk,
    "ingest_notes": _route_ingest_notes,
    "extract_session_insights": _route_extract,
    "wrap_up_session": _route_wrap_up,
    "onboard_repo": _route_onboard_repo,
    "reconcile": _route_reconcile,
}


@pytest.mark.parametrize("route", sorted(STAGED_ROUTES))
def test_every_staged_route_passes_the_tombstone_check(mcp, monkeypatch, route):
    m, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    from piia_engram import tombstones

    checked: list[str] = []
    real_lookup = tombstones.lookup

    def spy(root_arg, kind, row):
        checked.append(tombstones.claim_hashes(kind, row)[0])
        return real_lookup(root_arg, kind, row)

    monkeypatch.setattr(tombstones, "lookup", spy)

    STAGED_ROUTES[route](m, root)

    created = [("lesson", r) for r in _rows(root, "lesson")] + [("decision", r) for r in _rows(root, "decision")]
    assert created, f"route {route} created no rows; the test input needs adjusting"
    for kind, row in created:
        assert tombstones.claim_hashes(kind, row)[0] in checked, f"{route} inserted an unchecked {kind}"


def test_unset_mode_honours_a_tombstone_left_by_a_strict_reject(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    eng = Engram(root=tmp_path)
    row = eng.add_lesson("seeded rejected claim", domain="t")
    _reject(eng, row["id"])
    monkeypatch.delenv("ENGRAM_APPROVAL")

    result = eng.add_lesson("Seeded rejected claim.", domain="t")

    assert result.get("status") == "rejected_before"
    assert result.get("rejection_id") == row["id"]
    assert any(
        a.get("action") == "refused" and "rejected_before" in json.dumps(a) for a in _audit(tmp_path)
    )


# ---------------------------------------------------------------------------
# 7. retired rows count as duplicates (B.3), in both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["strict", None])
def test_retired_row_blocks_the_same_text_revocably(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    if mode:
        monkeypatch.setenv("ENGRAM_APPROVAL", mode)
    else:
        monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root=tmp_path)
    row = eng.add_lesson("retired without an owner reject", domain="t")
    rows = _rows(tmp_path)
    rows[0]["status"] = "outdated"
    raw_write_json(tmp_path / "knowledge" / "lessons.json", rows)

    again = eng.add_lesson("retired without an owner reject", domain="t")

    assert again["status"] == "duplicate_retired"
    assert again["existing_id"] == row["id"]
    assert _tombstones(tmp_path) == []


def test_archived_row_blocks_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root=tmp_path)
    archive = tmp_path / "knowledge" / "overflow_archive"
    archive.mkdir(parents=True)
    (archive / "lessons.jsonl").write_text(json.dumps({
        "id": "0123456789ab", "summary": "expired unreviewed proposal", "status": "active",
        "tier": "staging", "overflow_archive_reason": "review_queue_quota",
        "overflow_archived_at": "2026-09-25T06:15:00Z",
    }) + "\n", encoding="utf-8")

    again = eng.add_lesson("expired unreviewed proposal", domain="t")

    assert again.get("status") == "duplicate_retired"
    assert again.get("where") == "archive"
    assert again.get("reason") == "review_queue_quota"
    assert any(
        a.get("action") == "refused" and "duplicate_retired" in json.dumps(a) for a in _audit(tmp_path)
    )


_MEMORY_BODY = """\
---
name: Deploy check
description: d
type: feedback
---

Always verify before deploying new features to production.
This prevents regression bugs from reaching users.
"""


def _memory_dir(tmp_path: Path) -> Path:
    mem = tmp_path / "fake_claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True)
    return mem


def test_reconcile_does_not_reimport_a_rejected_row(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem = _memory_dir(tmp_path)
    (mem / "deploy.md").write_text(_MEMORY_BODY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]
    assert eng.reconcile_memories()["imported"] == 1
    (queued,) = eng.get_lessons(limit=None, _update_access=False)
    _reject(eng, queued["id"])

    assert eng.reconcile_memories()["imported"] == 0
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_reconcile_skips_retired_rows_without_a_tombstone(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem = _memory_dir(tmp_path)
    (mem / "deploy.md").write_text(_MEMORY_BODY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]
    eng.reconcile_memories()
    rows = _rows(tmp_path)
    for row in rows:
        row["status"] = "outdated"
    raw_write_json(tmp_path / "knowledge" / "lessons.json", rows)

    assert eng.reconcile_memories()["imported"] == 0


# ---------------------------------------------------------------------------
# 8. local CLI: engram review export / apply / tombstone, attributed
# ---------------------------------------------------------------------------


def _cli(monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    from piia_engram import setup_wizard

    monkeypatch.setattr(sys, "argv", ["engram", "review", *argv])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    return int(exc.value.code or 0), capsys.readouterr().out


def _marks(tmp_path: Path, marks: list[dict]) -> Path:
    path = tmp_path / "marks.json"
    path.write_text(json.dumps(marks), encoding="utf-8")
    return path


def test_cli_apply_dry_run_by_default_then_applies_with_operator(eng, tmp_path, monkeypatch, capsys):
    keep = eng.add_lesson("keep this one", domain="t")
    drop = eng.add_lesson("drop this one", domain="t")
    marks = _marks(tmp_path, [{"id": keep["id"], "mark": "approve"}, {"id": drop["id"], "mark": "reject"}])
    before = _snapshot(tmp_path)

    code, out = _cli(monkeypatch, capsys, "apply", str(marks))
    assert code == 0 and "dry_run" in out
    assert _snapshot(tmp_path) == before

    code, out = _cli(monkeypatch, capsys, "apply", str(marks), "--operator", "owner", "--yes")
    assert code == 0 and "applied" in out
    tiers = {r["id"]: r["tier"] for r in eng.get_lessons(limit=None, _update_access=False)}
    assert tiers == {keep["id"]: "verified"}
    (stone,) = _tombstones(tmp_path)
    assert stone["id"] == drop["id"] and stone["via"] == "cli:owner"
    assert "keep this one" not in out and "drop this one" not in out
    receipts = [a for a in _audit(tmp_path) if a.get("action") == "owner_cli"]
    assert receipts
    receipt = json.dumps(receipts[-1])
    for field in ("operator", "isatty", "ppid", "parent_name", "host"):
        assert field in receipt
    assert receipts[-1].get("isatty") is False  # pytest stdin is not a TTY; accepted with --operator


def test_cli_yes_without_operator_is_refused(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("x", domain="t")
    marks = _marks(tmp_path, [{"id": row["id"], "mark": "approve"}])
    before = _snapshot(tmp_path)

    code, _out = _cli(monkeypatch, capsys, "apply", str(marks), "--yes")

    assert code != 0
    assert _snapshot(tmp_path) == before


def test_cli_apply_edit_type_sets_the_domain_label(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("never install on C:", domain="feedback")
    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)
    marks = _marks(tmp_path, [{"id": row["id"], "mark": "edit-type:rule"}])

    code, _out = _cli(monkeypatch, capsys, "apply", str(marks), "--operator", "owner", "--yes")

    assert code == 0
    (updated,) = eng.get_lessons(limit=None, _update_access=False)
    assert {"type:rule", "feedback"} <= set(updated["domain"].split(","))


def test_cli_apply_rejects_an_unknown_type(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("x", domain="t")
    marks = _marks(tmp_path, [{"id": row["id"], "mark": "edit-type:reference"}])
    before = _snapshot(tmp_path)

    code, _out = _cli(monkeypatch, capsys, "apply", str(marks), "--operator", "owner", "--yes")

    assert code != 0
    assert _snapshot(tmp_path) == before


def test_cli_export_sorts_reproposals_first_and_leaves_store_unchanged(eng, tmp_path, monkeypatch, capsys):
    first = eng.add_lesson("prefer tabs over spaces", domain="t")
    _reject(eng, first["id"])
    eng.add_lesson("an unrelated fresh proposal", domain="t")
    repro = eng.add_lesson("use tab characters for indentation", domain="t", supersedes=first["id"])
    out_dir = tmp_path / "review-out"
    before = _snapshot(tmp_path)

    code, _out = _cli(monkeypatch, capsys, "export", "--out", str(out_dir))

    assert code == 0
    after = {k: v for k, v in _snapshot(tmp_path).items() if not k.startswith("review-out")}
    assert after == before
    ids = json.loads((out_dir / "ids.json").read_text(encoding="utf-8"))
    assert ids[0] == repro["id"]
    text = (out_dir / "review.md").read_text(encoding="utf-8")
    assert f"re-proposal of rejected {first['id']}" in text


def test_cli_export_flags_a_cjk_near_rejected_variant(eng, tmp_path, monkeypatch, capsys):
    first = eng.add_lesson("先备份再删除原文件", domain="t")
    _reject(eng, first["id"])
    variant = eng.add_lesson("先备份 再删除 原文件", domain="t")
    assert variant.get("status") != "rejected_before"
    out_dir = tmp_path / "review-out"

    code, _out = _cli(monkeypatch, capsys, "export", "--out", str(out_dir))

    assert code == 0
    assert f"near-rejected {first['id']}" in (out_dir / "review.md").read_text(encoding="utf-8")


def test_cli_tombstone_backfill(eng, tmp_path, monkeypatch, capsys):
    retired = eng.add_lesson("rejected before tombstones existed", domain="t")
    pending = eng.add_lesson("still waiting for review", domain="t")
    rows = _rows(tmp_path)
    for row in rows:
        if row["id"] == retired["id"]:
            row["status"] = "outdated"
    raw_write_json(tmp_path / "knowledge" / "lessons.json", rows)
    ids_file = tmp_path / "ids.json"
    ids_file.write_text(json.dumps([retired["id"], pending["id"], "000000000000"]), encoding="utf-8")

    code, out = _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file))
    assert code == 0 and "dry_run" in out and _tombstones(tmp_path) == []

    _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file), "--operator", "owner", "--yes")
    code, out = _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file), "--operator", "owner", "--yes")

    assert [s["id"] for s in _tombstones(tmp_path)] == [retired["id"]]
    assert _tombstones(tmp_path)[0]["via"].startswith("backfill:")
    assert "000000000000" in out  # not found, reported
    assert pending["id"] in out  # refused: still active
    assert eng.add_lesson("rejected before tombstones existed", domain="t")["status"] == "rejected_before"


# ---------------------------------------------------------------------------
# 9. served MCP instructions
# ---------------------------------------------------------------------------


def test_strict_server_instructions_drop_the_auto_write_lifecycle(monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    text = _mcp_server().server_instructions()

    assert "without waiting for the user to ask" not in text
    assert "wrap_up_session" not in text
    assert "propose" in text.lower()


def test_default_server_instructions_are_unchanged(monkeypatch):
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    m = _mcp_server()

    text = m.server_instructions()
    assert text == m._DEFAULT_SERVER_INSTRUCTIONS
    assert "act on each phase without waiting for the user to ask" in text  # 4.21.0 text
    assert "wrap_up_session" in text
