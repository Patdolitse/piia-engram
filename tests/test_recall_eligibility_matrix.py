"""Recall eligibility matrix: every recall entry point x every eligibility state.

States: approved (trusted) / pending / superseded / archived / withheld, for
lessons, decisions and playbooks. Entries: get_user_context, get_resume_brief,
search_knowledge, get_relevant_knowledge, get_recall, Memory Lens (context
preview) and the two session-start hooks; each in default and strict mode.

Expected (asserted as exact token sets below):
  * auto-inject entries (cold start, resume brief, hooks, get_recall,
    get_relevant_knowledge, Memory Lens) return trusted rows only;
  * search_knowledge returns trusted rows in the result lists and pending rows
    in a separate ``pending`` group (each flagged ``pending_untrusted``);
    superseded rows only with ``include_superseded=True``, in their own group;
    a pending playbook stays hidden under strict;
  * archived rows (non-active status, archived or unknown tier, rejected or
    deprecated labels) never appear; withheld rows follow governance / the
    sensitivity ceiling exactly as before.

Every row that must stay out is the NEWEST in its file, so a broken filter
would put it into the resume brief's top-3 window instead of a trusted row.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path

import pytest

from knowledge_seed import raw_write_json
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.storage import _write_json

TOPIC = "matrixtopic"
PROJECT_DIR = "proj"  # under tmp_path; holds the project-scoped secret lesson

# --- lessons ---------------------------------------------------------------
L_APPROVED, L_NEW, L_GUARDED = "zqlApproved", "zqlNewer", "zqlGuarded"
L_SECRET = "zqlSecret"                      # project-scoped, sensitivity=secret
L_PENDING, L_HIDER = "zqlPending", "zqlHider"  # hider: pending, supersedes guarded
L_CYC1, L_CYC2 = "zqlCycOne", "zqlCycTwo"   # two pending rows superseding each other
L_STAGING_CASE = "zqlStagingCase"           # tier="Staging"
L_OLD = "zqlOlder"                          # superseded by L_NEW
L_ARCHIVED, L_SOFT = "zqlArchived", "zqlSoftArchived"
L_UNVERIFIED, L_MREJECTED, L_MDEPRECATED = "zqlUnverified", "zqlMemRejected", "zqlMemDeprecated"

# --- decisions -------------------------------------------------------------
D_APPROVED, D_NEW, D_RSRC = "zqdApproved", "zqdNewer", "zqdReviewedSrc"
D_PENDING = "zqdPending"
D_OLD = "zqdOlder"                          # superseded by D_NEW
D_PTARGET = "zqdPendTarget"                 # pending, superseded by reviewed D_RSRC
D_ARCHIVED, D_SOFT = "zqdArchived", "zqdSoftArchived"

# --- playbooks -------------------------------------------------------------
P_APPROVED, P_NEW = "zqpApproved", "zqpNewer"
P_PENDING = "zqpPending"
P_OLD = "zqpOlder"                          # superseded by P_NEW
P_DELETED = "zqpDeleted"

TRUSTED_LESSONS = {L_APPROVED, L_NEW, L_GUARDED}
TRUSTED_DECISIONS = {D_APPROVED, D_NEW, D_RSRC}
TRUSTED_PLAYBOOKS = {P_APPROVED, P_NEW}
PENDING_LESSONS = {L_PENDING, L_HIDER, L_CYC1, L_CYC2, L_STAGING_CASE}
ARCHIVED_ALL = {L_ARCHIVED, L_SOFT, L_UNVERIFIED, L_MREJECTED, L_MDEPRECATED,
                D_ARCHIVED, D_SOFT, P_DELETED}
SUPERSEDED_ALL = {L_OLD, D_OLD, D_PTARGET, P_OLD}
NEVER_AUTO = PENDING_LESSONS | {D_PENDING, P_PENDING} | SUPERSEDED_ALL | ARCHIVED_ALL

ALL_TOKENS = (TRUSTED_LESSONS | TRUSTED_DECISIONS | TRUSTED_PLAYBOOKS | {L_SECRET}
              | NEVER_AUTO)

# kept for the budget-omission tests
APPROVED = L_APPROVED
TRUSTED_TOKENS = TRUSTED_LESSONS | TRUSTED_DECISIONS | TRUSTED_PLAYBOOKS | {L_SECRET}
TRUSTED_IDS = {"l-approved", "l-new", "l-guarded", "l-secret",
               "d-approved", "d-new", "d-rsrc", "pb-approved", "pb-new"}

MODES = ["default", "strict"]


def test_tokens_never_contain_each_other():
    for a in ALL_TOKENS:
        for b in ALL_TOKENS:
            assert a == b or a not in b, (a, b)


def tokens(text: str) -> set[str]:
    return {t for t in ALL_TOKENS if t in text}


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def _lesson(rid: str, token: str, **kw) -> dict:
    row = {"id": rid, "summary": f"{TOPIC} {token} lesson", "detail": f"{TOPIC} detail",
           "domain": "testing", "status": "active", "tier": "verified",
           "timestamp": "2026-10-01T00:00:00"}
    row.update(kw)
    return row


def _decision(rid: str, token: str, **kw) -> dict:
    row = {"id": rid, "question": f"{TOPIC} {token} question", "choice": f"{token} choice",
           "domain": "testing", "status": "active", "tier": "verified",
           "timestamp": "2026-10-01T00:00:00"}
    row.update(kw)
    return row


def _playbook(pid: str, token: str, reviewed_at: str, **kw) -> dict:
    pb = {"id": pid, "title": f"{TOPIC} {token} playbook", "triggers": [TOPIC],
          "domain": "testing", "steps": ["first step", "second step"],
          "status": "active", "tier": "verified", "version": 1,
          "created_at": "2026-10-01T00:00:00", "last_updated": "2026-10-01T00:00:00",
          "last_reviewed": reviewed_at}
    pb.update(kw)
    return pb


def _write_playbooks(root: Path, playbooks: list[dict]) -> None:
    folder = root / "playbooks"
    folder.mkdir(parents=True, exist_ok=True)
    index = []
    for pb in playbooks:
        _write_json(folder / f"{pb['id']}.json", pb)
        index.append({"id": pb["id"], "title": pb["title"], "triggers": pb["triggers"],
                      "domain": pb["domain"], "status": pb["status"],
                      "updated_at": pb["last_updated"]})
    _write_json(folder / "_index.json", index)


def _seed(root: Path, project: Path) -> None:
    (root / "identity").mkdir(parents=True)
    (root / "identity" / "profile.json").write_text(
        json.dumps({"role": "developer", "language": "en"}), encoding="utf-8"
    )
    (root / "knowledge").mkdir(parents=True)
    lessons = [
        # trusted first (oldest) ...
        _lesson("l-guarded", L_GUARDED),
        _lesson("l-new", L_NEW),
        _lesson("l-approved", L_APPROVED),
        _lesson("l-secret", L_SECRET, sensitivity="secret", project_folder=str(project)),
        # ... everything that must stay out is newer
        _lesson("l-pending", L_PENDING, tier="staging"),
        _lesson("l-old", L_OLD),
        _lesson("l-archived", L_ARCHIVED, status="archived"),
        _lesson("l-soft", L_SOFT, tier="archived"),
        _lesson("l-hider", L_HIDER, tier="staging"),
        _lesson("l-cyc1", L_CYC1, tier="staging"),
        _lesson("l-cyc2", L_CYC2, tier="staging"),
        _lesson("l-staging-case", L_STAGING_CASE, tier="Staging"),
        _lesson("l-unverified", L_UNVERIFIED, tier="unverified"),
        _lesson("l-mrejected", L_MREJECTED, tier="", memory_state="rejected"),
        _lesson("l-mdeprecated", L_MDEPRECATED, tier="", memory_state="deprecated",
                approval_status="deprecated"),
    ]
    decisions = [
        _decision("d-approved", D_APPROVED),
        _decision("d-new", D_NEW),
        _decision("d-rsrc", D_RSRC),
        _decision("d-pending", D_PENDING, tier="staging"),
        _decision("d-old", D_OLD),
        _decision("d-ptarget", D_PTARGET, tier="staging"),
        _decision("d-archived", D_ARCHIVED, status="archived"),
        _decision("d-soft", D_SOFT, tier="archived"),
    ]
    raw_write_json(root / "knowledge" / "lessons.json", lessons)
    raw_write_json(root / "knowledge" / "decisions.json", decisions)
    _write_playbooks(root, [
        _playbook("pb-approved", P_APPROVED, "2026-10-02T00:00:00"),
        _playbook("pb-new", P_NEW, "2026-10-03T00:00:00"),
        _playbook("pb-old", P_OLD, "2026-10-04T00:00:00"),
        _playbook("pb-pending", P_PENDING, "2026-10-05T00:00:00", tier="staging"),
        _playbook("pb-deleted", P_DELETED, "2026-10-06T00:00:00", status="deleted"),
    ])
    relations = RelationStore(root)
    relations.add_relation("l-new", "supersedes", "l-old")
    relations.add_relation("l-hider", "supersedes", "l-guarded")    # pending -> reviewed: ignored
    relations.add_relation("l-cyc1", "supersedes", "l-cyc2")        # pending cycle
    relations.add_relation("l-cyc2", "supersedes", "l-cyc1")
    relations.add_relation("d-new", "supersedes", "d-old")
    relations.add_relation("d-rsrc", "supersedes", "d-ptarget")     # reviewed -> pending: honored
    relations.add_relation("pb-new", "supersedes", "pb-old")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(params=MODES)
def env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engram"
    project = tmp_path / PROJECT_DIR
    project.mkdir()
    _seed(root, project)
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
    monkeypatch.setattr(m, "_session", m._SessionTracker())
    return m, eng, root, tmp_path


def _strict(env) -> bool:
    import os

    return os.environ.get("ENGRAM_APPROVAL") == "strict"


# ---------------------------------------------------------------------------
# entry adapters: each returns the text an AI would receive
# ---------------------------------------------------------------------------


def _user_context(m, eng, tmp_path, **kw):
    return _run(m.get_user_context(**kw))


def _resume_brief(m, eng, tmp_path, **kw):
    return _run(m.get_resume_brief(**kw))


def _relevant(m, eng, tmp_path, **kw):
    return _run(m.get_relevant_knowledge(project_folder=str(tmp_path / PROJECT_DIR), **kw))


def _recall(m, eng, tmp_path, **kw):
    kw.setdefault("include_playbooks", True)
    kw.setdefault("token_budget", 20000)
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


def _lens_text(m, eng, tmp_path, **kw):
    from piia_engram.context_preview import build_context_preview

    preview = build_context_preview(eng, role="owner", level="full", query=TOPIC, **kw)
    return json.dumps(preview["knowledge"]["exposed"], ensure_ascii=False)


AUTO_ENTRIES = {
    "get_user_context": _user_context,
    "get_resume_brief": _resume_brief,
    "get_relevant_knowledge": _relevant,
    "get_recall": _recall,
    "memory_lens": _lens_text,
}
HOOKS = {"hook_claude": _hook_claude, "hook_cursor": _hook_cursor}

# exact trusted token set each auto-inject entry shows (global scope unless noted)
EXPECTED = {
    "get_user_context": TRUSTED_LESSONS | TRUSTED_DECISIONS | TRUSTED_PLAYBOOKS,
    "get_resume_brief": TRUSTED_LESSONS | TRUSTED_DECISIONS,
    "get_relevant_knowledge": TRUSTED_LESSONS | {L_SECRET},  # project scope: + secret
    "get_recall": TRUSTED_LESSONS | TRUSTED_DECISIONS | TRUSTED_PLAYBOOKS,
    "memory_lens": TRUSTED_LESSONS | TRUSTED_DECISIONS,
    "hook_claude": TRUSTED_LESSONS | TRUSTED_DECISIONS,
    "hook_cursor": TRUSTED_LESSONS | TRUSTED_DECISIONS,
}


def _section_items(markdown: str, heading: str) -> list[str]:
    """Bullet lines of one '## heading' section of the resume brief."""
    match = re.search(rf"## {re.escape(heading)}\n(.*?)(?:\n## |\n</engram-resume>|\Z)",
                      markdown, re.S)
    if not match:
        return []
    return [line for line in match.group(1).splitlines() if line.startswith("- ")]


# ---------------------------------------------------------------------------
# 1. auto-inject entries: exactly the trusted rows, in both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", sorted(AUTO_ENTRIES))
def test_auto_inject_entries_show_exactly_the_trusted_rows(env, entry):
    m, eng, root, tmp_path = env
    text = AUTO_ENTRIES[entry](m, eng, tmp_path)
    assert tokens(text) == EXPECTED[entry], entry


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_hooks_show_exactly_the_trusted_rows(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert tokens(text) == EXPECTED[entry], entry
    # superseded rows stay out even though they are newer than their successors
    assert L_OLD not in text and D_OLD not in text and D_PTARGET not in text


@pytest.mark.parametrize("entry", ["get_resume_brief", "hook_claude", "hook_cursor"])
def test_brief_lists_exactly_the_trusted_count(env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = env
    if entry == "get_resume_brief":
        text = json.loads(_resume_brief(m, eng, tmp_path))["markdown"]
    else:
        text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert len(_section_items(text, "Recent verified lessons")) == len(TRUSTED_LESSONS)
    assert len(_section_items(text, "Recent verified decisions")) == len(TRUSTED_DECISIONS)
    assert f"Resumed {len(TRUSTED_LESSONS) + len(TRUSTED_DECISIONS)} memories" in text


def test_get_relevant_knowledge_items_are_exactly_the_trusted_rows(env):
    m, eng, root, tmp_path = env
    data = json.loads(_relevant(m, eng, tmp_path))
    ids = {item["id"] for item in data["items"]}
    assert ids == {"l-approved", "l-new", "l-guarded", "l-secret"}


def test_memory_lens_owner_names_why_rows_are_kept_out(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(eng, role="owner", level="full", query=TOPIC)
    reasons: dict[str, str] = {}
    for item in preview["knowledge"]["withheld"]:
        for token in tokens(item["summary"]):
            reasons[token] = item["withheld_reason"]
    assert reasons[L_PENDING] == "pending_review"
    assert reasons[D_PENDING] == "pending_review"
    assert reasons[L_OLD] == "superseded"
    assert reasons[D_PTARGET] == "superseded"
    assert not tokens(json.dumps(preview["knowledge"]["exposed"])) & NEVER_AUTO
    summaries = [item["summary"] for item in preview["knowledge"]["withheld"]]
    assert len(summaries) == len(set(summaries)), "a kept-out row is listed once"


def test_memory_lens_assistant_withholds_secret(env):
    from piia_engram.context_preview import build_context_preview

    m, eng, root, tmp_path = env
    preview = build_context_preview(
        eng, role="assistant", level="full", query=TOPIC,
        project_folder=str(tmp_path / PROJECT_DIR),
    )
    exposed = json.dumps(preview["knowledge"]["exposed"], ensure_ascii=False)
    assert L_SECRET not in exposed
    reasons = {
        item["withheld_reason"] for item in preview["knowledge"]["withheld"]
        if L_SECRET in item.get("summary", "")
    }
    assert reasons == {"sensitivity_above_ceiling"}
    assert not tokens(exposed) & NEVER_AUTO
    # a governance reason wins over "awaiting review" for this caller
    pending_reasons = {
        item["withheld_reason"] for item in preview["knowledge"]["withheld"]
        if L_PENDING in item.get("summary", "")
    }
    assert pending_reasons == {"staging_excluded"}


def test_recall_counts_each_superseded_row_once(env):
    m, eng, root, tmp_path = env
    payload = json.loads(_recall(m, eng, tmp_path))
    # l-old reaches recall twice (project lessons and the query search)
    assert payload["meta"]["collapsed_versions"] == len({"l-old", "d-old", "d-ptarget", "pb-old"})


# ---------------------------------------------------------------------------
# 2. explicit search: trusted list + separate pending group
# ---------------------------------------------------------------------------


def _toks(items) -> set[str]:
    return tokens(json.dumps(items, ensure_ascii=False))


def test_search_groups_every_state(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC)))
    assert _toks(data["lessons"]) == TRUSTED_LESSONS
    assert _toks(data["decisions"]) == TRUSTED_DECISIONS
    assert _toks(data["playbooks"]) == TRUSTED_PLAYBOOKS
    pending = data["pending"]
    assert _toks(pending["lessons"]) == PENDING_LESSONS
    assert _toks(pending["decisions"]) == {D_PENDING}
    expected_pb = set() if _strict(env) else {P_PENDING}
    assert _toks(pending["playbooks"]) == expected_pb
    for bucket in ("lessons", "decisions", "playbooks"):
        assert all(item["pending_untrusted"] is True for item in pending[bucket])
        assert all(not item.get("pending_untrusted") for item in data[bucket])
    assert "superseded" not in data
    assert not tokens(json.dumps(data, ensure_ascii=False)) & (ARCHIVED_ALL | SUPERSEDED_ALL)


def test_search_include_superseded_returns_its_own_group(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC, include_superseded=True)))
    group = data["superseded"]
    assert _toks(group["lessons"]) == {L_OLD}
    assert _toks(group["decisions"]) == {D_OLD, D_PTARGET}
    assert _toks(group["playbooks"]) == {P_OLD}
    successors = {item["id"]: item["superseded_by"]
                  for bucket in group.values() for item in bucket}
    assert successors == {"l-old": "l-new", "d-old": "d-new", "d-ptarget": "d-rsrc",
                          "pb-old": "pb-new"}
    rest = {k: v for k, v in data.items() if k != "superseded"}
    assert not tokens(json.dumps(rest, ensure_ascii=False)) & SUPERSEDED_ALL
    assert not tokens(json.dumps(data, ensure_ascii=False)) & ARCHIVED_ALL


def test_search_staging_filter_lands_in_pending_group(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.search_knowledge(query=TOPIC, filters_json='{"tier": "staging"}')))
    assert data["lessons"] == []
    assert {i["id"] for i in data["pending"]["lessons"]} == {
        "l-pending", "l-hider", "l-cyc1", "l-cyc2"}


def test_core_search_keeps_its_three_key_contract(env):
    m, eng, root, tmp_path = env
    result = eng.search_knowledge(TOPIC)
    assert set(result) == {"lessons", "decisions", "playbooks"}
    assert _toks(result["lessons"]) == TRUSTED_LESSONS


def test_core_search_archived_tier_filter_returns_nothing(env):
    m, eng, root, tmp_path = env
    result = eng.search_knowledge(TOPIC, filters={"tier": "archived"},
                                  include_pending=True, include_superseded=True)
    assert not tokens(json.dumps(result, ensure_ascii=False))


def test_pending_cycle_stays_pending(env):
    from piia_engram import recall_policy as rp

    m, eng, root, tmp_path = env
    index = eng._recall_supersede_index()
    assert {"l-cyc1", "l-cyc2"} <= index.cycle_ids
    rows = {r["id"]: r for r in eng.get_lessons(limit=None, _update_access=False)}
    assert rp.classify(rows["l-cyc1"], index).state == rp.PENDING
    assert rp.classify(rows["l-cyc2"], index).state == rp.PENDING
    data = json.loads(_run(m.search_knowledge(query=TOPIC)))
    cyc = [i for i in data["pending"]["lessons"] if i["id"] in {"l-cyc1", "l-cyc2"}]
    assert len(cyc) == 2 and all(i["pending_untrusted"] is True for i in cyc)


def test_dock_search_keeps_each_kind_within_the_limit(env, monkeypatch, capsys):
    from piia_engram import cli_commands

    m, eng, root, tmp_path = env
    assert cli_commands._run_dock_search(["--query", TOPIC, "--limit", "4", "--json"]) == 0
    results = json.loads(capsys.readouterr().out)["results"]
    lessons = [r for r in results if r["kind"] == "lesson"]
    assert len(lessons) == 4
    assert [r.get("pending_untrusted", False) for r in lessons] == [False, False, False, True]


# ---------------------------------------------------------------------------
# 3. withheld (governance on, non-owner caller): existing semantics, unchanged
# ---------------------------------------------------------------------------


def test_withheld_secret_never_reaches_a_non_owner(env, monkeypatch):
    m, eng, root, tmp_path = env
    project = str(tmp_path / PROJECT_DIR)
    owner_search = _run(m.search_knowledge(query=TOPIC, project_folder=project))
    assert L_SECRET in owner_search  # the check below is not vacuous
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    assert L_SECRET not in _run(m.search_knowledge(query=TOPIC, project_folder=project))
    assert L_SECRET not in _relevant(m, eng, tmp_path)
    for entry in ("get_user_context", "get_resume_brief", "get_recall"):
        assert L_SECRET not in AUTO_ENTRIES[entry](m, eng, tmp_path), entry


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
    assert L_PENDING not in json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 4. cycles, by-id reads, decision auto-supersede, reviewed playbooks
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


def _cycle_warnings(root: Path, member: str) -> list[dict]:
    return [e for e in _audit(root)
            if e.get("action") == "warn" and "supersede_cycle" in str(e.get("detail"))
            and member in str(e.get("detail"))]


def test_supersede_cycle_keeps_both_rows(env, monkeypatch):
    m, eng, root, tmp_path = env
    relations = RelationStore(root)
    relations.add_relation("l-approved", "supersedes", "l-guarded")
    relations.add_relation("l-guarded", "supersedes", "l-approved")
    for _ in range(2):
        context = _user_context(m, eng, tmp_path)
        assert L_APPROVED in context and L_GUARDED in context
        data = json.loads(_run(m.search_knowledge(query=TOPIC)))
        assert {L_APPROVED, L_GUARDED} <= _toks(data["lessons"])


def test_cycle_warning_is_written_once_and_only_by_a_writer(env, monkeypatch):
    m, eng, root, tmp_path = env
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    relations = RelationStore(root)
    relations.add_relation("l-approved", "supersedes", "l-new")
    relations.add_relation("l-new", "supersedes", "l-approved")

    reader = Engram(root, read_only=True)
    reader._recall_supersede_index()
    reader.search_knowledge(TOPIC)
    assert _cycle_warnings(root, "l-approved") == []  # a read-only open writes nothing

    writer = Engram(root)
    writer._recall_supersede_index()
    assert len(_cycle_warnings(root, "l-approved")) == 1  # the reader did not use it up
    writer._recall_supersede_index()
    writer.search_knowledge(TOPIC)
    Engram(root)._recall_supersede_index()
    assert len(_cycle_warnings(root, "l-approved")) == 1


def test_supersede_index_cache_follows_file_changes(env):
    m, eng, root, tmp_path = env
    first = eng._recall_supersede_index()
    assert eng._recall_supersede_index() is first  # unchanged files: cached
    RelationStore(root).add_relation("l-approved", "supersedes", "l-guarded")
    second = eng._recall_supersede_index()
    assert second is not first and second.successor("l-guarded") == "l-approved"


def test_by_id_history_returns_superseded_row_with_successor(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.get_knowledge_history(item_id="l-old")))
    assert data["id"] == "l-old"
    assert data["eligibility"] == "superseded"
    assert data["superseded_by"] == "l-new"
    current = json.loads(_run(m.get_knowledge_history(item_id="l-new")))
    assert current["eligibility"] == "trusted"
    assert "superseded_by" not in current
    pending = json.loads(_run(m.get_knowledge_history(item_id="d-ptarget")))
    assert pending["eligibility"] == "superseded" and pending["superseded_by"] == "d-rsrc"


def test_by_id_history_snapshots_name_their_successor(env):
    m, eng, root, tmp_path = env
    eng.update_lesson("l-approved", {"summary": f"{TOPIC} {L_APPROVED} lesson, revised"})
    data = json.loads(_run(m.get_knowledge_history(item_id="l-approved")))
    assert data["snapshots"], data
    assert all(node["superseded_by"] == "l-approved" for node in data["snapshots"])


def test_explore_related_annotates_superseded_source(env):
    m, eng, root, tmp_path = env
    data = json.loads(_run(m.explore_knowledge(mode="related", item_id="l-old")))
    assert data["source"]["superseded_by"] == "l-new"
    assert data["source"]["eligibility"] == "superseded"


def test_unreviewed_row_cannot_hide_a_reviewed_playbook(tmp_path, monkeypatch):
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    raw_write_json(root / "knowledge" / "lessons.json",
                   [_lesson("l-x", "zqlProposal", tier="staging")])
    raw_write_json(root / "knowledge" / "decisions.json", [])
    _write_playbooks(root, [_playbook("pb-guard", "zqpGuardedPb", "2026-10-02T00:00:00")])
    RelationStore(root).add_relation("l-x", "supersedes", "pb-guard")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root)
    assert "pb-guard" in eng._reviewed_ids()
    assert eng._recall_supersede_index().successor("pb-guard") == ""
    result = eng.search_knowledge(TOPIC)
    assert [pb["id"] for pb in result["playbooks"]] == ["pb-guard"]
    assert "zqpGuardedPb" in eng.generate_context(level="standard")


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
    assert {i["id"] for i in data["decisions"]} == {new_id}
    history = json.loads(_run(m.get_knowledge_history(item_id=old_id)))
    assert history["superseded_by"] == new_id


def test_resume_pack_keeps_superseded_rows_out_and_flags_review_items(env):
    m, eng, root, tmp_path = env
    brief = json.loads(_resume_brief(m, eng, tmp_path, include_resume_pack=True,
                                     include_agent_context_pack=True))
    pack = brief["resume_pack"]
    trusted = json.dumps(pack.get("trusted_context"), ensure_ascii=False)
    assert L_APPROVED in trusted
    assert not tokens(trusted) & NEVER_AUTO
    assert {"kind": "lesson", "reason": "superseded", "source": "knowledge"} in pack["omitted"]
    assert pack["review_needed"], pack
    assert all(item["pending_untrusted"] is True for item in pack["review_needed"])
    assert not tokens(json.dumps(brief["agent_context_pack"].get("trusted_context", []),
                                 ensure_ascii=False)) & NEVER_AUTO


# ---------------------------------------------------------------------------
# recall follow-ups: cache stamp, one trust rule for "reviewed", playbook window
# ---------------------------------------------------------------------------


def test_supersede_index_cache_sees_a_same_size_same_mtime_rewrite(env):
    """A rewrite that keeps size and mtime still refreshes the index.

    The new file is written beside the old one and moved over it, so only the
    file identity (inode / file index, ctime) tells the two apart.
    """
    import os

    m, eng, root, tmp_path = env
    path = root / "knowledge" / "relations.json"
    first = eng._recall_supersede_index()
    assert first.successor("l-old") == "l-new"

    before = path.stat()
    original = path.read_bytes()
    swapped = original.replace(b'"l-old"', b'"l-oXd"')
    assert swapped != original and len(swapped) == len(original)
    staged = path.with_name("relations.swap")
    staged.write_bytes(swapped)
    os.utime(staged, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(staged, path)
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    second = eng._recall_supersede_index()
    assert second is not first
    assert second.successor("l-old") == "" and second.successor("l-oXd") == "l-new"


def test_reviewed_row_with_a_cased_tier_supersedes_an_unreviewed_row(tmp_path, monkeypatch):
    """'reviewed' means the recall policy's whitelist, not an exact tier string."""
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    raw_write_json(root / "knowledge" / "lessons.json", [
        _lesson("l-cased", "zqlCased", tier="Verified"),
        _lesson("l-draft", "zqlDraft", tier="staging"),
    ])
    raw_write_json(root / "knowledge" / "decisions.json", [])
    RelationStore(root).add_relation("l-cased", "supersedes", "l-draft")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root)
    assert "l-cased" in eng._reviewed_ids()
    assert eng._recall_supersede_index().successor("l-draft") == "l-cased"


def test_superseded_playbooks_do_not_crowd_trusted_ones_out_of_get_recall(tmp_path, monkeypatch):
    """The recent-playbook window is filtered by the policy before it is cut to four."""
    root = tmp_path / "engram"
    (root / "knowledge").mkdir(parents=True)
    raw_write_json(root / "knowledge" / "lessons.json", [])
    raw_write_json(root / "knowledge" / "decisions.json", [])
    playbooks = [_playbook("pb-keeper", "zqpKeeper", "2026-10-01T00:00:00")]
    edges = []
    for i in range(1, 6):  # five newer playbooks, each superseded by pb-keeper
        playbooks.append(_playbook(f"pb-gone-{i}", f"zqpGone{i}", f"2026-10-0{i + 1}T00:00:00"))
        edges.append(f"pb-gone-{i}")
    _write_playbooks(root, playbooks)
    relations = RelationStore(root)
    for pid in edges:
        relations.add_relation("pb-keeper", "supersedes", pid)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    eng = Engram(root)
    from piia_engram import recall_service

    sources = recall_service.gather_recall_sources(eng, include_playbooks=True)
    assert [pb["id"] for pb in sources["playbooks"]] == ["pb-keeper"]
    assert sources["collapsed_count"] == 5  # the five superseded ones are still counted



# ---------------------------------------------------------------------------
# pinned dimension: a pin orders trusted rows first and never makes a row eligible
# ---------------------------------------------------------------------------

PINNED_TRUSTED = {"l-guarded": L_GUARDED, "d-new": D_NEW, "pb-approved": P_APPROVED}
PINNED_INELIGIBLE = {"l-pending": L_PENDING, "d-old": D_OLD, "l-archived": L_ARCHIVED}


def _pin_rows(root: Path) -> None:
    """Pin a trusted row of each kind that is NOT first without the pin, plus rows that are not trusted."""
    wanted = set(PINNED_TRUSTED) | set(PINNED_INELIGIBLE)
    for name in ("lessons.json", "decisions.json"):
        path = root / "knowledge" / name
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            if row["id"] in wanted:
                row["pinned"] = True
                row["pinned_at"] = "2026-10-01T00:00:00Z"
        raw_write_json(path, rows)
    path = root / "playbooks" / "pb-approved.json"
    pb = json.loads(path.read_text(encoding="utf-8"))
    pb["pinned"] = True
    pb["pinned_at"] = "2026-10-01T00:00:00Z"
    _write_json(path, pb)


@pytest.fixture(params=MODES)
def pinned_env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engram"
    project = tmp_path / PROJECT_DIR
    project.mkdir()
    _seed(root, project)
    _pin_rows(root)
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
    monkeypatch.setattr(m, "_session", m._SessionTracker())
    return m, eng, root, tmp_path


@pytest.mark.parametrize("entry", sorted(AUTO_ENTRIES))
def test_pinned_rows_keep_their_eligibility_in_every_entry(pinned_env, entry):
    m, eng, root, tmp_path = pinned_env
    text = AUTO_ENTRIES[entry](m, eng, tmp_path)
    assert tokens(text) == EXPECTED[entry], entry


@pytest.mark.parametrize("entry", sorted(HOOKS))
def test_pinned_rows_keep_their_eligibility_in_hooks(pinned_env, entry, monkeypatch, capsys):
    m, eng, root, tmp_path = pinned_env
    text = HOOKS[entry](m, eng, tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    assert tokens(text) == EXPECTED[entry], entry
    lessons = _section_items(text, "Recent verified lessons")
    assert L_GUARDED in lessons[0]  # the oldest trusted lesson is pinned: listed first


def test_pinned_rows_come_first_in_the_brief_and_cold_start(pinned_env):
    m, eng, root, tmp_path = pinned_env
    brief = json.loads(_resume_brief(m, eng, tmp_path))["markdown"]
    assert L_GUARDED in _section_items(brief, "Recent verified lessons")[0]
    assert D_NEW in _section_items(brief, "Recent verified decisions")[0]
    context = _user_context(m, eng, tmp_path)
    assert context.index(D_NEW) < context.index(D_APPROVED)  # cold start lists the oldest first otherwise
    assert context.index(P_APPROVED) < context.index(P_NEW)


def test_pinned_relevant_lessons_come_first(pinned_env):
    m, eng, root, tmp_path = pinned_env
    data = json.loads(_relevant(m, eng, tmp_path))
    assert data["items"][0]["id"] == "l-guarded"


def test_search_with_pins_keeps_the_groups(pinned_env):
    m, eng, root, tmp_path = pinned_env
    data = json.loads(_run(m.search_knowledge(query=TOPIC)))
    assert _toks(data["lessons"]) == TRUSTED_LESSONS
    assert _toks(data["decisions"]) == TRUSTED_DECISIONS
    assert _toks(data["pending"]["lessons"]) == PENDING_LESSONS
    assert not tokens(json.dumps(data, ensure_ascii=False)) & (ARCHIVED_ALL | SUPERSEDED_ALL)
