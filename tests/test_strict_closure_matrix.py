"""Strict-mode closure (4.21.1): owner-only MCP writes, reconcile precedence, tombstones.

Under ``ENGRAM_APPROVAL=strict`` an agent can only *propose* (lesson/decision rows
land in staging). Every path that would make knowledge authoritative without the
Owner -- identity edits, tier/content edits, approvals, merges, imports, relation
edits, playbooks -- is refused at the MCP layer and moves to the local
``engram review`` CLI. With the variable unset, the MCP surface is unchanged.

Rejections are permanent: a rejected staging row leaves a text-free tombstone
that ``add_lesson`` / ``add_decision`` and reconcile honour.
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

# Expected strict-mode outcome for every mutating MCP tool.
#   refused  -> strict refusal before any argument is consumed, store unchanged
#   proposal -> allowed; knowledge rows it creates land in staging
#   allowed  -> allowed; does not create authoritative knowledge
STRICT_OUTCOME: dict[str, str] = {
    "add_lesson": "proposal",
    "add_decision": "proposal",
    "memory_store": "proposal",  # kind=playbook is refused (see below)
    "ingest_notes": "proposal",
    "extract_session_insights": "proposal",
    "wrap_up_session": "proposal",
    "onboard_repo": "proposal",
    "add_playbook": "refused",
    "manage_playbook": "refused",
    "update_identity": "refused",
    "update_knowledge": "refused",
    "confirm_knowledge": "refused",
    "merge_knowledge": "refused",
    "archive_knowledge": "refused",
    "manage_relation": "refused",
    "import_engram": "refused",
    "onboard_accept": "refused",
    "manage_caller_trust": "refused",
    "save_project_snapshot": "refused",
    "user_portrait": "refused",
    "review_staging": "allowed",  # list / dry-run only; apply is refused (see below)
    "playbook_execution": "allowed",
    "save_agent_context": "allowed",
    "start_project": "allowed",
    "register_tool": "allowed",
    "check_anchors": "allowed",
    # export_owner_only: write report/export files, never knowledge rows
    "export_engram": "allowed",
    "export_knowledge_report": "allowed",
    "get_identity_card": "allowed",
    "refresh_quick_context": "allowed",
    "request_outline_review": "allowed",
}

REFUSED = sorted(name for name, outcome in STRICT_OUTCOME.items() if outcome == "refused")

# Self-reported client types an agent could send; strict refusal must not depend on them.
CLIENT_TYPES = ["claude_code", "self", ""]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _seed_root(root: Path) -> Path:
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}), encoding="utf-8"
    )
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "lessons.json").write_text("[]", encoding="utf-8")
    (root / "knowledge" / "decisions.json").write_text("[]", encoding="utf-8")
    return root


def _snapshot(root: Path) -> dict[str, str]:
    """Content hash of every data file; governance receipts are excluded."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.name.lower() in {"governance_ledger.jsonl", ".engram-governance-ledger.lock"}:
            continue
        if rel.parts and rel.parts[0] in {"logs", "contexts"}:
            continue
        out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
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
    import piia_engram.mcp_server as mcp_server

    return mcp_server._gov_rt.is_governance_refusal(result) and "ENGRAM_APPROVAL=strict" in result


@pytest.fixture
def mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _seed_root(tmp_path / "engram")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    import piia_engram.mcp_server as mcp_server

    mcp_server._engram = Engram(root)
    return mcp_server, root


# ---------------------------------------------------------------------------
# 1. completeness: every mutating MCP tool has a strict-mode outcome
# ---------------------------------------------------------------------------


def test_every_mutating_tool_has_a_strict_outcome():
    import piia_engram.mcp_server as mcp_server

    mutating = {
        name
        for name, cls in mcp_server.TOOL_GOVERNANCE_CLASS.items()
        if cls in mcp_server.WRITE_GATE_CLASSES_MUTATING
    }
    assert mutating - set(STRICT_OUTCOME) == set(), "classify new write tools for strict mode"
    assert set(STRICT_OUTCOME) - mutating == set(), "stale strict-mode entries"


# ---------------------------------------------------------------------------
# 2. writer-spy: refused under strict, whatever the caller claims to be
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("client_type", CLIENT_TYPES)
@pytest.mark.parametrize("tool_name", REFUSED)
def test_strict_refuses_owner_only_tool_and_leaves_store_unchanged(
    mcp, monkeypatch, tool_name, client_type
):
    mcp_server, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", client_type)
    func = getattr(mcp_server, tool_name)

    before = _snapshot(root)
    result = _run(func(**_dummy_kwargs(func)))

    assert _is_strict_refusal(result), f"{tool_name} not refused under strict: {result!r:.200}"
    assert _snapshot(root) == before, f"{tool_name} changed the store while refusing"


@pytest.mark.parametrize("tool_name", REFUSED)
def test_unset_mode_never_returns_the_strict_refusal(mcp, monkeypatch, tool_name):
    mcp_server, _root = mcp
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    func = getattr(mcp_server, tool_name)

    result = _run(func(**_dummy_kwargs(func)))

    assert not _is_strict_refusal(result)


def test_strict_memory_store_playbook_is_refused_but_lesson_is_staged(mcp, monkeypatch):
    mcp_server, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")

    before = _snapshot(root)
    refused = _run(mcp_server.memory_store(
        kind="playbook", content_json=json.dumps({"title": "t", "steps": ["a", "b", "c"]})
    ))
    assert _is_strict_refusal(refused)
    assert _snapshot(root) == before

    _run(mcp_server.memory_store(
        kind="lesson", content_json=json.dumps({"summary": "strict proposal lands in staging"})
    ))
    lessons = json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert [row["tier"] for row in lessons] == ["staging"]


def test_strict_review_staging_list_and_dry_run_allowed_apply_refused(mcp, monkeypatch):
    mcp_server, root = mcp
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    row = mcp_server._engram.add_lesson("a proposal waiting for the owner", domain="t")
    actions = json.dumps([{"id": row["id"], "action": "approve"}])

    listed = json.loads(_run(mcp_server.review_staging(action="list")))
    assert listed["status"] == "listed"
    dry = json.loads(_run(mcp_server.review_staging(action="batch", actions_json=actions)))
    assert dry["status"] == "dry_run"

    before = _snapshot(root)
    applied = _run(mcp_server.review_staging(
        action="batch", actions_json=actions, dry_run=False, confirm=True
    ))
    assert _is_strict_refusal(applied)
    assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# 3. reconcile precedence: the config key beats the env var
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
        ("1", False, False),  # 4.21.0 returned True here
    ],
)
def test_reconcile_authorization_config_false_wins(tmp_path, monkeypatch, env, config, expected):
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


# ---------------------------------------------------------------------------
# 4. tombstones
# ---------------------------------------------------------------------------


@pytest.fixture
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    return Engram(root=tmp_path)


def _tombstones(root: Path) -> list[dict]:
    path = root / "knowledge" / "tombstones.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _reject(eng: Engram, item_id: str) -> None:
    result = batch_review_staging(eng, [{"id": item_id, "action": "reject"}], dry_run=False, confirm=True)
    assert result["counts"]["applied"] == 1


def test_reject_writes_a_text_free_tombstone(eng, tmp_path):
    summary = "a proposal the owner turns down"
    row = eng.add_lesson(summary, domain="t")

    _reject(eng, row["id"])

    stones = _tombstones(tmp_path)
    assert [s["id"] for s in stones] == [row["id"]]
    assert {"id", "kind", "scope", "hash", "rejected_at", "via"} <= set(stones[0])
    assert summary not in json.dumps(stones)


def test_approve_writes_no_tombstone(eng, tmp_path):
    row = eng.add_lesson("a proposal the owner accepts", domain="t")

    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)

    assert _tombstones(tmp_path) == []


def test_archive_knowledge_of_a_staging_row_is_a_reject(eng, tmp_path):
    row = eng.add_lesson("archived straight from the queue", domain="t")

    eng.archive_knowledge(row["id"])

    assert [s["id"] for s in _tombstones(tmp_path)] == [row["id"]]


def test_rejected_lesson_cannot_be_proposed_again(eng, tmp_path):
    row = eng.add_lesson("Always run the full suite before any commit", domain="t")
    _reject(eng, row["id"])

    again = eng.add_lesson("always run the   FULL suite before any commit", domain="other")

    assert again["status"] == "rejected_before"
    assert again["rejection_id"] == row["id"]
    live = [r for r in eng.get_lessons(limit=None, _update_access=False)]
    assert live == []


def test_rejected_decision_cannot_be_proposed_again(eng):
    row = eng.add_decision("use tool A?", "yes", "because")
    _reject(eng, row["id"])

    again = eng.add_decision("use tool A?", "yes", "because")

    assert again["status"] == "rejected_before"
    assert again["rejection_id"] == row["id"]


def test_reproposal_citing_the_rejection_as_supersedes_is_allowed(eng):
    row = eng.add_lesson("prefer tabs over spaces", domain="t")
    _reject(eng, row["id"])

    again = eng.add_lesson("prefer tabs over spaces", domain="t", supersedes=row["id"])

    assert again.get("status") != "rejected_before"
    assert again["tier"] == "staging"


def _memory_dir(tmp_path: Path) -> Path:
    mem = tmp_path / "fake_claude" / "projects" / "p" / "memory"
    mem.mkdir(parents=True)
    return mem


_MEMORY_BODY = """\
---
name: Deploy check
description: d
type: feedback
---

Always verify before deploying new features to production.
This prevents regression bugs from reaching users.
"""


def test_reconcile_does_not_reimport_a_rejected_row(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem = _memory_dir(tmp_path)
    (mem / "deploy.md").write_text(_MEMORY_BODY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]
    assert eng.reconcile_memories()["imported"] == 1
    (queued,) = eng.get_lessons(limit=None, _update_access=False)
    _reject(eng, queued["id"])

    second = eng.reconcile_memories()

    assert second["imported"] == 0
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_reconcile_dedups_against_outdated_rows_without_a_tombstone(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem = _memory_dir(tmp_path)
    (mem / "deploy.md").write_text(_MEMORY_BODY, encoding="utf-8")
    eng._CLAUDE_MEMORY_GLOBS = [str(mem / "*.md")]
    eng.reconcile_memories()
    path = tmp_path / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        row["status"] = "outdated"  # a legacy reject from before tombstones existed
    raw_write_json(path, rows)

    assert eng.reconcile_memories()["imported"] == 0


# ---------------------------------------------------------------------------
# 5. local CLI: engram review export / apply / tombstone
# ---------------------------------------------------------------------------


def _cli(monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    from piia_engram import setup_wizard

    monkeypatch.setattr(sys, "argv", ["engram", "review", *argv])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    return int(exc.value.code or 0), capsys.readouterr().out


def test_cli_apply_is_dry_run_by_default_then_applies_with_yes(eng, tmp_path, monkeypatch, capsys):
    keep = eng.add_lesson("keep this one", domain="t")
    drop = eng.add_lesson("drop this one", domain="t")
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([
        {"id": keep["id"], "mark": "approve"},
        {"id": drop["id"], "mark": "reject"},
    ]), encoding="utf-8")
    before = _snapshot(tmp_path)

    code, out = _cli(monkeypatch, capsys, "apply", str(marks))
    assert code == 0 and "dry_run" in out
    assert _snapshot(tmp_path) == before

    code, out = _cli(monkeypatch, capsys, "apply", str(marks), "--yes")
    assert code == 0 and "applied" in out
    tiers = {r["id"]: r["tier"] for r in eng.get_lessons(limit=None, _update_access=False)}
    assert tiers == {keep["id"]: "verified"}
    assert [s["id"] for s in _tombstones(tmp_path)] == [drop["id"]]
    assert "keep this one" not in out and "drop this one" not in out


def test_cli_apply_edit_type_sets_the_domain_label(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("never install on C:", domain="feedback")
    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": row["id"], "mark": "edit-type:rule"}]), encoding="utf-8")

    code, _out = _cli(monkeypatch, capsys, "apply", str(marks), "--yes")

    assert code == 0
    (updated,) = eng.get_lessons(limit=None, _update_access=False)
    assert "type:rule" in updated["domain"].split(",")
    assert "feedback" in updated["domain"].split(",")


def test_cli_apply_rejects_an_unknown_type(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("x", domain="t")
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": row["id"], "mark": "edit-type:reference"}]), encoding="utf-8")
    before = _snapshot(tmp_path)

    code, _out = _cli(monkeypatch, capsys, "apply", str(marks), "--yes")

    assert code != 0
    assert _snapshot(tmp_path) == before


def test_cli_export_writes_cards_without_changing_the_store(eng, tmp_path, monkeypatch, capsys):
    eng.add_lesson("exported proposal", domain="t")
    out_dir = tmp_path / "review-out"
    before = _snapshot(tmp_path)

    code, _out = _cli(monkeypatch, capsys, "export", "--out", str(out_dir))

    assert code == 0
    after = {k: v for k, v in _snapshot(tmp_path).items() if not k.startswith("review-out")}
    assert after == before
    ids = json.loads((out_dir / "ids.json").read_text(encoding="utf-8"))
    assert len(ids) == 1
    assert "exported proposal" in (out_dir / "review.md").read_text(encoding="utf-8")


def test_cli_tombstone_backfill_is_idempotent(eng, tmp_path, monkeypatch, capsys):
    row = eng.add_lesson("rejected before tombstones existed", domain="t")
    path = tmp_path / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["status"] = "outdated"
    raw_write_json(path, rows)
    ids_file = tmp_path / "ids.json"
    ids_file.write_text(json.dumps([row["id"]]), encoding="utf-8")

    code, out = _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file))
    assert code == 0 and "dry_run" in out and _tombstones(tmp_path) == []

    _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file), "--yes")
    _cli(monkeypatch, capsys, "tombstone", "--ids-file", str(ids_file), "--yes")
    assert [s["id"] for s in _tombstones(tmp_path)] == [row["id"]]
    assert eng.add_lesson("rejected before tombstones existed", domain="t")["status"] == "rejected_before"


# ---------------------------------------------------------------------------
# 6. served MCP instructions: no auto-write lifecycle under strict
# ---------------------------------------------------------------------------


def test_strict_server_instructions_drop_the_auto_write_lifecycle(monkeypatch):
    import piia_engram.mcp_server as mcp_server

    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    text = mcp_server.server_instructions()

    assert "without waiting for the user to ask" not in text
    assert "wrap_up_session" not in text
    assert "propose" in text.lower()


def test_default_server_instructions_are_unchanged(monkeypatch):
    import piia_engram.mcp_server as mcp_server

    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)

    assert mcp_server.server_instructions() == mcp_server.mcp.instructions
    assert "wrap_up_session" in mcp_server.server_instructions()
