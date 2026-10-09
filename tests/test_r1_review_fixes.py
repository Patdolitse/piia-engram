"""Regression coverage for protected replacements and read-only diagnostics."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_seed import raw_write_json
from piia_engram import Engram, recall_policy, write_provenance


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("strict", [False, True])
def test_mcp_explicit_replacement_of_pending_decision_stays_proposal(tmp_path, monkeypatch, strict):
    from piia_engram import mcp_server as server
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": "Choose cache policy", "choice": "ttl", "tier": "staging"})
    monkeypatch.setattr(server, "_engram", eng)
    monkeypatch.setattr(server, "_track", lambda *a, **k: None)
    if strict:
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    result = asyncio.run(server.memory_store(
        kind="decision", user_confirmed=True,
        content_json=json.dumps({"question": "Choose cache policy", "choice": "bounded",
                                 "supersedes": old["id"], "supersedes_expected_version": eng.mcp_entry_version(old["id"])})))
    assert "失败" not in result and "version_conflict" not in result and "version_required" not in result, result
    prior = eng._find_item_by_id(old["id"])[1]
    assert prior["status"] == "active" and prior["tier"] == "staging"
    rows = eng._read_entries(eng._knowledge_dir / "decisions.json", "decision", migrate=False)
    successor = next(row for row in rows if row["id"] != old["id"])
    assert successor["tier"] == "staging"
    assert successor["pending_supersedes"] == old["id"]


@pytest.mark.parametrize("labels", [{"tier": "staging"}, {"tier": "unknown"},
                                    {"memory_state": "staging"}, {"approval_status": "rejected"}])
@pytest.mark.parametrize("strict", [False, True])
def test_retirement_mutation_protects_every_untrusted_predecessor(tmp_path, monkeypatch, labels, strict):
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": "Choose cache policy", "choice": "ttl", "tier": "verified"})
    new = eng.add_decision({"question": "Choose queue policy", "choice": "bounded", "tier": "verified"})
    path = eng._knowledge_dir / "decisions.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    next(row for row in rows if row["id"] == old["id"]).update(labels)
    raw_write_json(path, rows)
    before = path.read_bytes()
    if strict:
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
        eng._retire_superseded_decision(old["id"], new["id"])
    assert path.read_bytes() == before


def _historical_three_generations(tmp_path, *, pending=False):
    from piia_engram.governance_store import RelationStore
    eng = Engram(root=tmp_path / "store")
    a = eng.add_decision({"question": "Choose cache policy", "choice": "ttl", "tier": "verified"})
    b = eng.add_decision({"question": "Choose queue policy", "choice": "bounded", "tier": "verified"})
    RelationStore(eng.root).add_relation(b["id"], "supersedes", a["id"])
    c = eng.add_decision({"question": "Choose retry policy", "choice": "backoff", "tier": "verified",
                         "supersedes": b["id"]})
    assert eng._find_item_by_id(b["id"])[1]["status"] == "superseded"
    if pending:
        path = eng._knowledge_dir / "decisions.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        next(row for row in rows if row["id"] == b["id"])["tier"] = "staging"
        raw_write_json(path, rows)
    return eng, a, b, c


def test_retired_intermediate_keeps_ancestor_out_of_current_decisions_and_recall(tmp_path):
    eng, a, b, c = _historical_three_generations(tmp_path)
    read = Engram(root=eng.root, read_only=True)
    assert {row["id"] for row in read.get_decisions(limit=None, _update_access=False)} == {c["id"]}
    index = read._recall_supersede_index()
    assert recall_policy.classify(a, index).state == recall_policy.SUPERSEDED
    assert index.successor(a["id"]) == b["id"]


def test_doctor_detects_resurrected_ancestor_through_retired_intermediate(tmp_path):
    from piia_engram.doctor import decision_consistency_check
    eng, a, b, c = _historical_three_generations(tmp_path)
    before = _files(eng.root)
    finding = decision_consistency_check(Engram(root=eng.root, read_only=True))
    assert finding["status"] == "WARN", finding
    assert finding["predecessors"] == [a["id"]]
    assert c["id"] in finding["hint"]
    assert _files(eng.root) == before


def test_pending_retired_intermediate_cannot_suppress_trusted_ancestor(tmp_path):
    from piia_engram.doctor import decision_consistency_check
    eng, a, b, c = _historical_three_generations(tmp_path, pending=True)
    assert a["id"] in {row["id"] for row in eng.get_decisions(limit=None, _update_access=False)}
    assert decision_consistency_check(eng)["status"] == "PASS"


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("bad_side", ["existing", "incoming"])
@pytest.mark.parametrize("layout", [{"snapshot": {"title": "legacy"}},
                                    {"snapshot": {"title": "legacy"}, "title": "mixed"},
                                    {"schema_version": 999, "title": "future"}, "{broken"])
def test_backup_import_refuses_invalid_projects_before_any_mutation(tmp_path, merge, bad_side, layout):
    eng = Engram(root=tmp_path / "store")
    existing = layout if bad_side == "existing" else {"title": "current"}
    incoming = layout if bad_side == "incoming" else {"title": "incoming"}
    path = eng._projects_dir / "example.json"
    path.write_text(existing if isinstance(existing, str) else json.dumps(existing), encoding="utf-8")
    backup = tmp_path / "backup.json"
    backup.write_text(json.dumps({"schema_version": "2.0", "identity": {"profile": {"role": "changed"}},
                                 "knowledge": {"lessons": [{"id": "imported", "summary": "Keep retry budgets bounded", "tier": "verified"}]},
                                 "projects": {"example": incoming}}), encoding="utf-8")
    before = _files(eng.root)
    result = eng.import_all(str(backup), merge=merge)
    assert result.get("error") == "migration_required", result
    assert "migrate-project" in result.get("hint", "")
    assert _files(eng.root) == before


@pytest.mark.parametrize("version", [999, "999", None, True, {}, "2.0"])
@pytest.mark.parametrize("nested", [False, True])
def test_migration_refuses_unknown_schema_version_without_relabelling(tmp_path, version, nested):
    from piia_engram.storage import _project_id
    eng = Engram(root=tmp_path / "store")
    path = eng._projects_dir / (_project_id("example") + ".json")
    body = {"schema_version": version, "title": "future semantics"}
    path.write_text(json.dumps({"snapshot": body} if nested else body), encoding="utf-8")
    before = _files(eng.root)
    assert eng.migrate_project_snapshot("example")["error"] == "migration_required"
    assert eng.migrate_project_snapshot("example", apply=True)["error"] == "migration_required"
    assert _files(eng.root) == before


def test_complete_ordinary_doctor_never_attempts_corruption_writes(tmp_path, monkeypatch, capsys):
    from piia_engram import doctor, setup_wizard, storage
    from piia_engram import mcp_server  # import before monitoring diagnostic-only writes
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    for folder, names in (("knowledge", ["lessons.json", "decisions.json", "relations.json", "tombstones.json"]),
                          ("identity", ["profile.json", "preferences.json", "trust_boundaries.json"]),
                          ("playbooks", ["_index.json"]), ("contexts", ["_index.json"])):
        base = eng.root / folder
        base.mkdir(exist_ok=True)
        for name in names:
            (base / name).write_bytes(b"{broken")
    monkeypatch.setattr(doctor, "_detect_installed_tools", lambda: [{
        "tool_id": "fixture", "name": "Fixture", "status": "installed", "verified": False,
        "config_path": tmp_path / "fixture.json", "config": {}, "servers": {}}])
    attempts = []
    def refuse(*args, **kwargs):
        attempts.append("mutation")
        raise AssertionError("doctor attempted a mutation")
    monkeypatch.setattr(storage.shutil, "copy2", refuse)
    original_open = Path.open
    def checked_open(path, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            return refuse()
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    monkeypatch.setattr(os, "replace", refuse)
    monkeypatch.setattr(os, "rename", refuse)
    monkeypatch.setattr(sys, "argv", ["engram", "doctor"])
    before = _files(eng.root)
    with pytest.raises(SystemExit):
        setup_wizard.main()
    out = capsys.readouterr().out
    assert "Functional Checks" in out and "Capacity check failed" in out
    assert "Activation:" in out, out
    assert attempts == []
    assert _files(eng.root) == before


@pytest.mark.parametrize("reply", [json.dumps({"status": "success", "entry": {"summary": "transport_unavailable: literal text"}}),
                                   "Saved entry: transport_unavailable: literal text",
                                   json.dumps(["transport_unavailable: literal text"])])
def test_transport_literal_in_success_reply_is_unchanged(reply):
    from piia_engram.transport_errors import guarded_tool
    async def success():
        return reply
    assert asyncio.run(guarded_tool(success)()) == reply


@pytest.mark.parametrize("name, phrases", [
    ("docs/user-guide.md", ["long-term knowledge", "session checkpoints", "activity records", "without owner review"]),
    ("docs/user-guide.zh-CN.md", ["长期知识", "会话检查点", "活动记录", "无需 Owner 审核"]),
])
def test_public_guides_distinguish_knowledge_review_from_session_continuity(name, phrases):
    text = (Path(__file__).parents[1] / name).read_text(encoding="utf-8")
    for phrase in phrases:
        assert phrase in text, (name, phrase)


def _guard_fixture(tmp_path, monkeypatch):
    import test_mcp_tool_surface_classification as guard
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("Engram ships 59 MCP tools; 19 core tools.", encoding="utf-8")
    (tmp_path / "README.zh-CN.md").write_text("Engram ships 59 MCP tools; 19 core tools.", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "_load_counter", lambda: SimpleNamespace(derive=lambda root: {"total": 59, "core": 19, "advanced": 40}))
    return guard


def test_tool_count_guard_ignores_untracked_and_internal_drafts(tmp_path, monkeypatch):
    guard = _guard_fixture(tmp_path, monkeypatch)
    for folder in ("_drafts", "internal"):
        path = tmp_path / "docs" / folder
        path.mkdir()
        (path / "history.md").write_text("17 core tools", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("docs/_drafts/\ndocs/internal/\n", encoding="utf-8")
    guard.test_current_docs_use_canonical_core_and_total_counts()


@pytest.mark.parametrize("text", ["See the full 53-tool inventory.", "包含完整的 53 工具清单。"])
def test_tool_count_guard_catches_current_migration_inventory(tmp_path, monkeypatch, text):
    guard = _guard_fixture(tmp_path, monkeypatch)
    path = tmp_path / "docs" / "migration-v4.md"
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/migration-v4.md"], check=True)
    with pytest.raises(AssertionError):
        guard.test_current_docs_use_canonical_core_and_total_counts()
