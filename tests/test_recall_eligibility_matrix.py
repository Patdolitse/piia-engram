"""Recall eligibility matrix: every recall entry point x every eligibility state.

States: approved (trusted) / pending / superseded / archived / withheld.
Entries: get_user_context, get_resume_brief, search_knowledge,
get_relevant_knowledge, get_recall, Memory Lens (context preview) and the two
session-start hooks; each in default and strict approval mode.

Expected (one table, asserted below):
  * auto-inject entries (cold start, resume brief, hooks, get_recall,
    get_relevant_knowledge, Memory Lens) return trusted rows only;
  * search_knowledge returns trusted rows in the result lists and pending rows
    in a separate ``pending`` group (each flagged ``pending_untrusted``);
    superseded rows only with ``include_superseded=True``, in their own group;
  * archived rows never appear; withheld rows follow governance / the
    sensitivity ceiling exactly as before.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from knowledge_seed import raw_write_json
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore

TOPIC = "matrixtopic"

APPROVED = "MXAPPROVED"
NEWVERSION = "MXNEWVERSION"
GUARDED = "MXGUARDED"
SECRET = "MXSECRETITEM"
PENDING = "MXPENDING"
HIDER = "MXHIDER"
OLDVERSION = "MXOLDVERSION"
ARCHIVED = "MXARCHIVED"

TRUSTED_TOKENS = {APPROVED, NEWVERSION, GUARDED, SECRET}
PENDING_TOKENS = {PENDING, HIDER}
NEVER_AUTO = {PENDING, HIDER, OLDVERSION, ARCHIVED}

MODES = ["default", "strict"]


def _lesson(rid: str, token: str, **kw) -> dict:
    row = {
        "id": rid,
        "summary": f"{TOPIC} {token} lesson",
        "detail": f"{TOPIC} detail",
        "domain": "testing",
        "status": "active",
        "tier": "verified",
        "timestamp": "2026-10-01T00:00:00",
    }
    row.update(kw)
    return row


def _seed(root: Path) -> None:
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}), encoding="utf-8"
    )
    (root / "knowledge").mkdir(parents=True)
    # File order matters for the resume brief (newest = last, top 3 shown).
    rows = [
        _lesson("l-pending", PENDING, tier="staging"),
        _lesson("l-old", OLDVERSION),
        _lesson("l-archived", ARCHIVED, status="archived"),
        _lesson("l-hider", HIDER, tier="staging"),
        _lesson("l-guarded", GUARDED),
        _lesson("l-secret", SECRET, sensitivity="secret"),
        _lesson("l-new", NEWVERSION),
        _lesson("l-approved", APPROVED),
    ]
    raw_write_json(root / "knowledge" / "lessons.json", rows)
    raw_write_json(root / "knowledge" / "decisions.json", [])
    relations = RelationStore(root)
    relations.add_relation("l-new", "supersedes", "l-old")
    # an unreviewed row must never hide a reviewed one
    relations.add_relation("l-hider", "supersedes", "l-guarded")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(params=MODES)
def env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engram"
    _seed(root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    if request.param == "strict":
        monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    else:
        monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    import piia_engram.mcp_server as m

    eng = Engram(root)
    monkeypatch.setattr(m, "_engram", eng)
    return m, eng, root, tmp_path


# ---------------------------------------------------------------------------
# entry adapters: each returns the text an AI would receive
# ---------------------------------------------------------------------------


def _user_context(m, eng, tmp_path, **kw):
    return _run(m.get_user_context(**kw))


def _resume_brief(m, eng, tmp_path, **kw):
    return _run(m.get_resume_brief(**kw))


def _relevant(m, eng, tmp_path, **kw):
    folder = tmp_path / "proj"
    folder.mkdir(exist_ok=True)
    return _run(m.get_relevant_knowledge(project_folder=str(folder), **kw))


def _recall(m, eng, tmp_path, **kw):
    return _run(m.get_recall(query=TOPIC, **kw))


def _hook_claude(m, eng, tmp_path, monkeypatch=None, capsys=None):
    from piia_engram.hooks import auto_inject_resume_brief as hook

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": ""})))
    monkeypatch.setattr("sys.argv", ["hook"])
    hook.main()
    out = json.loads(capsys.readouterr().out)
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def _hook_cursor(m, eng, tmp_path, monkeypatch=None, capsys=None):
    from piia_engram.hooks import cursor_inject_resume_brief as hook

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({})))
    monkeypatch.setattr("sys.argv", ["hook"])
    hook.main()
    out = json.loads(capsys.readouterr().out)
    return out.get("additional_context", "")


AUTO_ENTRIES = {
    "get_user_context": _user_context,
    "get_resume_brief": _resume_brief,
    "get_relevant_knowledge": _relevant,
    "get_recall": _recall,
}
HOOKS = {"hook_claude": _hook_claude, "hook_cursor": _hook_cursor}


# ---------------------------------------------------------------------------
# 1. auto-inject entries: trusted only, in both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", sorted(AUTO_ENTRIES))
def test_auto_inject_entries_return_trusted_only(env, entry):
    m, eng, root, tmp_path = env
    text = AUTO_ENTRIES[entry](m, eng, tmp_path)
    assert APPROVED in text, entry
    assert NEWVERSION in text, entry
    for token in NEVER_AUTO:
        assert token not in text, f"{entry} returned {token}"


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_inject_trusted_only(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert APPROVED in text and NEWVERSION in text, entry
    for token in NEVER_AUTO:
        assert token not in text, f"{entry} returned {token}"


@pytest.mark.parametrize("entry", ["get_user_context", "get_relevant_knowledge", "get_recall"])
def test_unreviewed_row_cannot_hide_reviewed_row(env, entry):
    m, eng, root, tmp_path = env
    text = AUTO_ENTRIES[entry](m, eng, tmp_path)
    assert GUARDED in text, entry
    assert HIDER not in text, entry


def test_get_relevant_knowledge_items_are_exactly_the_trusted_rows(env):
    m, eng, root, tmp_path = env
    data = json.loads(_relevant(m, eng, tmp_path))
    ids = {item["id"] for item in data["items"]}
    assert ids == {"l-approved", "l-new", "l-guarded", "l-secret"}


def test_memory_lens_owner_exposes_trusted_only(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(eng, role="owner", query=TOPIC)
    exposed = json.dumps(preview["knowledge"]["exposed"], ensure_ascii=False)
    for token in TRUSTED_TOKENS:
        assert token in exposed, token
    for token in NEVER_AUTO:
        assert token not in exposed, token
    # the owner sees why each kept-out row is kept out (summary + reason only)
    reasons = {}
    for item in preview["knowledge"]["withheld"]:
        for token in NEVER_AUTO | TRUSTED_TOKENS:
            if token in item["summary"]:
                reasons[token] = item["withheld_reason"]
    assert reasons == {
        PENDING: "pending_review",
        HIDER: "pending_review",
        OLDVERSION: "superseded",
    }


def test_memory_lens_assistant_withholds_secret(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(eng, role="assistant", query=TOPIC)
    exposed = json.dumps(preview["knowledge"]["exposed"], ensure_ascii=False)
    assert SECRET not in exposed
    reasons = {
        item["withheld_reason"] for item in preview["knowledge"]["withheld"]
        if SECRET in item.get("summary", "")
    }
    assert reasons == {"sensitivity_above_ceiling"}
    for token in NEVER_AUTO:
        assert token not in exposed, token
    # a governance reason wins over "awaiting review" for this caller
    pending_reasons = {
        item["withheld_reason"] for item in preview["knowledge"]["withheld"]
        if PENDING in item.get("summary", "")
    }
    assert pending_reasons == {"staging_excluded"}


# ---------------------------------------------------------------------------
# 2. explicit search: trusted list + separate pending group
# ---------------------------------------------------------------------------


def _ids(items):
    return {item.get("id") for item in items}


def test_search_groups_trusted_and_pending_separately(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC)))
    assert _ids(data["lessons"]) == {"l-approved", "l-new", "l-guarded", "l-secret"}
    assert all(not item.get("pending_untrusted") for item in data["lessons"])
    pending = data["pending"]
    assert _ids(pending["lessons"]) == {"l-pending", "l-hider"}
    assert all(item["pending_untrusted"] is True for item in pending["lessons"])
    assert pending["decisions"] == [] and pending["playbooks"] == []
    assert "superseded" not in data
    text = json.dumps(data, ensure_ascii=False)
    assert OLDVERSION not in text and ARCHIVED not in text


def test_search_include_superseded_returns_its_own_group(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC, include_superseded=True)))
    assert "l-old" not in _ids(data["lessons"])
    assert "l-old" not in _ids(data["pending"]["lessons"])
    group = data["superseded"]["lessons"]
    assert _ids(group) == {"l-old"}
    assert group[0]["superseded_by"] == "l-new"
    assert ARCHIVED not in json.dumps(data, ensure_ascii=False)


def test_search_staging_filter_lands_in_pending_group(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC, filters_json='{"tier": "staging"}')))
    assert data["lessons"] == []
    assert _ids(data["pending"]["lessons"]) == {"l-pending", "l-hider"}


def test_core_search_keeps_its_three_key_contract(env):
    m, eng, root, tmp_path = env
    result = eng.search_knowledge(TOPIC)
    assert set(result) == {"lessons", "decisions", "playbooks"}
    assert _ids(result["lessons"]) == {"l-approved", "l-new", "l-guarded", "l-secret"}


# ---------------------------------------------------------------------------
# 3. withheld (governance on, non-owner caller): existing semantics, unchanged
# ---------------------------------------------------------------------------


def test_withheld_secret_never_reaches_a_non_owner(env, monkeypatch):
    m, eng, root, tmp_path = env
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    search = _run(m.search_knowledge(query=TOPIC))
    assert SECRET not in search
    relevant = _relevant(m, eng, tmp_path)
    assert SECRET not in relevant
    for entry in ("get_user_context", "get_resume_brief", "get_recall"):
        assert SECRET not in AUTO_ENTRIES[entry](m, eng, tmp_path), entry


def test_withheld_pending_group_is_governed_too(env, monkeypatch):
    m, eng, root, tmp_path = env
    rows = json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    for row in rows:
        if row["id"] == "l-pending":
            row["sensitivity"] = "secret"
    raw_write_json(root / "knowledge" / "lessons.json", rows)
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    data = json.loads(_run(m.search_knowledge(query=TOPIC)))
    assert PENDING not in json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 4. cycles, by-id reads, decision auto-supersede
# ---------------------------------------------------------------------------


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


def test_supersede_cycle_keeps_both_rows_and_warns_once(env, monkeypatch):
    m, eng, root, tmp_path = env
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = Engram(root)
    monkeypatch.setattr(m, "_engram", eng)
    relations = RelationStore(root)
    relations.add_relation("l-approved", "supersedes", "l-guarded")
    relations.add_relation("l-guarded", "supersedes", "l-approved")

    for _ in range(2):
        context = _user_context(m, eng, tmp_path)
        assert APPROVED in context and GUARDED in context
        data = json.loads(_run(m.search_knowledge(query=TOPIC)))
        assert {"l-approved", "l-guarded"} <= _ids(data["lessons"])

    warnings = [
        e for e in _audit(root)
        if e.get("action") == "warn" and "supersede_cycle" in str(e.get("detail"))
    ]
    assert len(warnings) == 1
    assert "l-approved" in warnings[0]["detail"] and "l-guarded" in warnings[0]["detail"]


def test_by_id_history_returns_superseded_row_with_successor(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.get_knowledge_history(item_id="l-old")))
    assert data["id"] == "l-old"
    assert data["eligibility"] == "superseded"
    assert data["superseded_by"] == "l-new"
    current = json.loads(_run(m.get_knowledge_history(item_id="l-new")))
    assert current["eligibility"] == "trusted"
    assert "superseded_by" not in current


def test_by_id_history_snapshots_name_their_successor(env):
    m, eng, root, tmp_path = env
    eng.update_lesson("l-approved", {"summary": f"{TOPIC} {APPROVED} lesson, revised"})
    data = json.loads(_run(m.get_knowledge_history(item_id="l-approved")))
    assert data["snapshots"], data
    assert all(node["superseded_by"] == "l-approved" for node in data["snapshots"])


def test_explore_related_annotates_superseded_source(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.explore_knowledge(mode="related", item_id="l-old")))
    assert data["source"]["superseded_by"] == "l-new"
    assert data["source"]["eligibility"] == "superseded"


def test_decision_auto_supersede_hides_the_old_choice(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    import piia_engram.mcp_server as m

    eng = Engram(root)
    monkeypatch.setattr(m, "_engram", eng)
    first = eng.add_decision({"question": "Which matrixqueue backend should we use",
                              "choice": "MXCHOICEOLD redis"})
    second = eng.add_decision({"question": "Which matrixqueue backend should we use",
                               "choice": "MXCHOICENEW postgres"})
    old_id = first.get("id") or first.get("decision_id")
    new_id = second.get("id") or second.get("decision_id")
    assert old_id and new_id and old_id != new_id, (first, second)

    context = _run(m.get_user_context())
    assert "MXCHOICENEW" in context and "MXCHOICEOLD" not in context
    data = json.loads(_run(m.search_knowledge(query="matrixqueue backend")))
    assert _ids(data["decisions"]) == {new_id}
    history = json.loads(_run(m.get_knowledge_history(item_id=old_id)))
    assert history["superseded_by"] == new_id


def test_resume_pack_keeps_superseded_rows_out_of_trusted_context(env):
    m, eng, root, tmp_path = env
    brief = json.loads(_resume_brief(m, eng, tmp_path, include_resume_pack=True,
                                     include_agent_context_pack=True))
    pack = brief["resume_pack"]
    trusted = json.dumps(pack.get("trusted_context"), ensure_ascii=False)
    assert APPROVED in trusted
    assert OLDVERSION not in trusted and PENDING not in trusted
    assert {"kind": "lesson", "reason": "superseded", "source": "knowledge"} in pack["omitted"]
    assert OLDVERSION not in json.dumps(brief["agent_context_pack"], ensure_ascii=False)
