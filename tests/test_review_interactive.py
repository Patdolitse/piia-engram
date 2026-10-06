"""Interactive owner review: ``engram review interactive``.

* one item at a time, typed line by line (a / r / s / k / v / q), streams injectable;
* nothing is written until the summary is confirmed with ``y``; ``n``, EOF and
  Ctrl+C leave the store byte-for-byte unchanged;
* every decision becomes a mark and runs through the same apply function as
  ``engram review apply`` (same receipt, same audit event);
* a supersede target must exist, be trusted, be the same kind and scope, not be
  the item itself and not close a cycle; a bad id is refused and asked again;
* stored text is shown with control, bidi and zero-width characters removed;
* only a terminal on both ends gets the prompt; it is never reachable over MCP.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import unicodedata
from pathlib import Path

import pytest

from piia_engram import i18n
from piia_engram import mcp_server
from piia_engram import recall_policy
from piia_engram import review_cli
from piia_engram import review_interactive
from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
from piia_engram.staging_review import batch_review_staging


def _run(coro):
    return asyncio.run(coro)


class _Screen(io.StringIO):
    """stdout that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


class _Keys:
    """stdin that claims to be a terminal and replays typed lines.

    ``KeyboardInterrupt`` in the list raises it at that point; the end of the
    list is EOF. ``hooks[n]`` runs just before the n-th line (0-based) is read.
    """

    def __init__(self, lines, hooks=None):
        self.lines = list(lines)
        self.hooks = dict(hooks or {})
        self.read = 0

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        hook = self.hooks.pop(self.read, None)
        if hook is not None:
            hook()
        self.read += 1
        if not self.lines:
            return ""
        line = self.lines.pop(0)
        if line is KeyboardInterrupt:
            raise KeyboardInterrupt
        return line + "\n"


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A strict store with the MCP server bound to it and audit receipts on."""
    root = tmp_path / "engram"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "claude_code")
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.setattr(i18n, "_runtime_lang", "en")
    old_session = mcp_server._session
    try:
        old_session._stop_event.set()
    except Exception:
        pass
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_track_count", 0, raising=False)
    client = {"name": "claude-code", "version": "2.1.0"}
    monkeypatch.setattr(mcp_server, "_current_client_info", lambda: (client["name"], client["version"]))
    eng = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", eng)
    return eng, client


def _propose_lesson(summary: str, detail: str = "worth keeping", domain: str = "type:lesson") -> dict:
    # user_confirmed is the agent's own claim; under strict it never makes a row trusted
    _run(mcp_server.add_lesson(summary=summary, detail=detail, domain=domain, user_confirmed=True))
    row = _lesson(mcp_server._engram, summary)
    assert row is not None and row["tier"] == "staging", row
    return row


def _lesson(eng: Engram, summary: str) -> dict | None:
    for row in eng._read_entries(eng._knowledge_dir / "lessons.json", "lesson", migrate=False):
        if row.get("summary") == summary:
            return row
    return None


def _snapshot(root: Path) -> dict[str, str]:
    """Hash of every file in the store (logs included): zero writes means equal."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _review(lines, *, hooks=None, args=None) -> tuple[int, str]:
    screen = _Screen()
    code = review_interactive.run(list(args or ["--operator", "owner"]), stdin=_Keys(lines, hooks), stdout=screen)
    return code, screen.getvalue()


def _context() -> str:
    return _run(mcp_server.get_user_context(level="standard"))


def _tombstones(root: Path) -> list[dict]:
    path = root / "knowledge" / "tombstones.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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


def _receipts(root: Path) -> list[dict]:
    return [a for a in _audit(root) if a.get("action") == "owner_cli"]


def _receipt_json(out: str) -> dict:
    start = out.index("{\n")
    return json.loads(out[start:out.rindex("}") + 1])


# ---------------------------------------------------------------------------
# end to end: propose over MCP, decide in the terminal, recall
# ---------------------------------------------------------------------------


def test_approved_proposal_is_trusted_and_recalled_rejected_one_is_tombstoned(store):
    eng, _client = store
    keep = "Run the database migrations before restarting the API workers"
    drop = "Restart every API worker by hand after each deploy"
    keep_id = _propose_lesson(keep)["id"]
    drop_id = _propose_lesson(drop)["id"]
    assert keep not in _context() and drop not in _context()
    keys = {keep_id: ["a"], drop_id: ["r", "too manual"]}
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))

    code, out = _review([*keys[order[0]], *keys[order[1]], "y"])

    assert code == 0, out
    kept = _lesson(eng, keep)
    assert kept["tier"] == "verified"
    assert recall_policy.classify(kept, eng._recall_supersede_index()).state == recall_policy.TRUSTED
    context = _context()
    assert keep in context
    assert drop not in context
    (stone,) = _tombstones(eng.root)
    assert stone["id"] == _lesson(eng, drop)["id"]
    assert stone["via"] == "cli:owner"
    # The reason is the Owner's note for this run: in the receipt, never on the tombstone.
    assert "reason" not in stone
    assert "too manual" not in (eng.root / "knowledge" / "tombstones.jsonl").read_text(encoding="utf-8")
    (receipt,) = _receipts(eng.root)
    assert receipt["reject_reasons"] == {stone["id"]: "too manual"}
    assert '"reason": "too manual"' in out
    again = _run(mcp_server.add_lesson(summary=drop, detail="again", domain="type:lesson", user_confirmed=True))
    assert "rejected_before" in again or "rejected" in again.lower()
    assert [r for r in eng.get_lessons(limit=None, _update_access=False)
            if r["summary"] == drop and r.get("status", "active") == "active"] == []
    assert "approved 1" in out.lower() and "rejected 1" in out.lower()


def test_playbook_proposal_can_be_approved_interactively(store):
    eng, _client = store
    _run(mcp_server.add_playbook(
        title="Rotate the signing key", triggers="key rotation",
        steps_json=json.dumps([{"action": "Revoke the old key"}, {"action": "Publish the new key"}]),
        domain="type:lesson", user_confirmed=True,
    ))
    listing = eng.list_playbooks_for_management(status="active", include_content=True, include_pending=True)
    (pid,) = [pb["id"] for pb in listing["items"] if pb.get("title") == "Rotate the signing key"]
    assert eng._read_playbook_by_id(pid)["tier"] == "staging"

    code, screen = _review(["a", "y"])

    assert code == 0, screen
    assert eng._read_playbook_by_id(pid)["tier"] == "verified"


# ---------------------------------------------------------------------------
# receipt and audit match `engram review apply`
# ---------------------------------------------------------------------------


def test_interactive_receipt_matches_apply(store, tmp_path, monkeypatch, capsys):
    eng, _client = store
    _propose_lesson("Pin the toolchain version in the build image")
    _propose_lesson("Rebuild the image whenever a dependency changes")

    code, out = _review(["a", "r", "", "y"])
    assert code == 0, out
    interactive_payload = _receipt_json(out)
    (interactive_receipt,) = _receipts(eng.root)

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(other))
    other_eng = Engram(root=other)
    monkeypatch.setattr(mcp_server, "_engram", other_eng)
    _propose_lesson("Pin the toolchain version in the build image")
    _propose_lesson("Rebuild the image whenever a dependency changes")
    pending = [row for row in other_eng.get_lessons(limit=None, _update_access=False) if row["tier"] == "staging"]
    pending.sort(key=lambda r: r["summary"] != "Pin the toolchain version in the build image")
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": pending[0]["id"], "mark": "approve"},
                                 {"id": pending[1]["id"], "mark": "reject"}]), encoding="utf-8")
    capsys.readouterr()
    assert review_cli.run_apply([str(marks), "--operator", "owner", "--yes"]) == 0
    apply_payload = json.loads(capsys.readouterr().out)
    (apply_receipt,) = _receipts(other)

    assert set(interactive_payload) == set(apply_payload)
    assert set(interactive_payload["counts"]) == set(apply_payload["counts"])
    assert interactive_payload["counts"] == apply_payload["counts"]
    assert [set(i) for i in interactive_payload["items"]] == [set(i) for i in apply_payload["items"]]
    assert interactive_receipt["action"] == apply_receipt["action"] == "owner_cli"
    assert interactive_receipt["resource"] == apply_receipt["resource"] == "review/apply"
    assert set(interactive_receipt) == set(apply_receipt)
    assert set(interactive_receipt["counts"]) == set(apply_receipt["counts"])
    assert interactive_receipt["route"] == "interactive" and apply_receipt["route"] == "marks"
    assert interactive_receipt["isatty"] is True and interactive_receipt["operator"] == "owner"


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


def _trusted_lesson(eng: Engram, summary: str, **extra) -> dict:
    row = eng.add_lesson({"summary": summary, "domain": "type:lesson", **extra})
    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)
    stored = _lesson(eng, summary)
    assert stored["tier"] == "verified"
    return stored


def test_supersede_links_the_new_row_and_recall_shows_only_it(store):
    eng, _client = store
    old = _trusted_lesson(eng, "Deploy on Fridays only after the smoke suite passes")
    new_summary = "Deploy any weekday once the smoke suite and canary both pass"
    _propose_lesson(new_summary)
    assert "Fridays only" in _context()

    code, out = _review(["s", old["id"], "y"])

    assert code == 0, out
    new = _lesson(eng, new_summary)
    assert new["tier"] == "verified" and "pending_supersedes" not in new
    edges = RelationStore(eng.root).all_edges()
    assert {"src": new["id"], "dst": old["id"]} in [{"src": e["src"], "dst": e["dst"]} for e in edges
                                                   if e["rel"] == "supersedes"]
    index = eng._recall_supersede_index()
    assert recall_policy.classify(_lesson(eng, old["summary"]), index).state == recall_policy.SUPERSEDED
    context = _context()
    assert new_summary in context and "Fridays only" not in context
    assert "superseded 1" in out.lower()


@pytest.mark.parametrize("case", ["missing", "pending", "cross_type", "self", "cycle", "other_scope"])
def test_supersede_refuses_a_bad_target_and_writes_nothing(store, case):
    eng, _client = store
    new_summary = "Cache the dependency layer between CI runs"
    _propose_lesson(new_summary)
    new = _lesson(eng, new_summary)
    if case == "missing":
        target = "nosuchid0001"
    elif case == "pending":
        _propose_lesson("Another proposal still waiting for review")
        target = _lesson(eng, "Another proposal still waiting for review")["id"]
    elif case == "cross_type":
        decision = eng.add_decision({"question": "Which CI cache backend?", "choice": "local disk"})
        batch_review_staging(eng, [{"id": decision["id"], "action": "approve"}], dry_run=False, confirm=True)
        target = decision["id"]
    elif case == "self":
        target = new["id"]
    elif case == "cycle":
        old = _trusted_lesson(eng, "Never cache anything between CI runs")
        RelationStore(eng.root).add_relation(old["id"], "supersedes", new["id"])  # legacy edge
        target = old["id"]
    else:
        target = _trusted_lesson(eng, "Cache builds for the beta service", project="beta-service")["id"]
    before = _snapshot(eng.root)

    # the bad id is refused and asked again; Enter cancels; k skips; nothing to apply
    code, out = _review(["s", target, "", "q"])

    assert code == 0, out
    assert _snapshot(eng.root) == before
    assert "refused" in out.lower() or "拒绝" in out
    assert "nothing" in out.lower() or "未写入" in out


def test_supersede_bad_id_then_good_id_applies(store):
    eng, _client = store
    old = _trusted_lesson(eng, "Tag releases by hand")
    _propose_lesson("Tag releases from the release workflow")

    code, out = _review(["s", "../../etc", old["id"], "y"])

    assert code == 0, out
    assert _lesson(eng, "Tag releases from the release workflow")["tier"] == "verified"


# ---------------------------------------------------------------------------
# quitting, EOF, Ctrl+C
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [["a", "q", "n"], ["a", "q", ""], ["a"], ["a", KeyboardInterrupt], ["r", KeyboardInterrupt],
     ["a", "q", KeyboardInterrupt]],
    ids=["quit-no", "quit-enter", "eof", "ctrl-c", "ctrl-c-in-reason", "ctrl-c-at-confirm"],
)
def test_quit_eof_and_ctrl_c_write_nothing(store, lines):
    eng, _client = store
    _propose_lesson("Keep the release notes in the changelog")
    _propose_lesson("Write release notes per tag")
    before = _snapshot(eng.root)

    code, out = _review(lines)

    assert _snapshot(eng.root) == before
    if lines[-1] in ("n", ""):
        assert code == 0
    else:
        assert code != 0
    assert "nothing" in out.lower() or "未写入" in out


def test_quit_then_yes_applies_only_what_was_decided(store):
    eng, _client = store
    _propose_lesson("Keep the release notes in the changelog")
    _propose_lesson("Write release notes per tag")
    rows = sorted(
        (r for r in eng.get_lessons(limit=None, _update_access=False) if r["tier"] == "staging"),
        key=lambda r: (r.get("queued_at") or r.get("timestamp") or "", r["id"]),
    )

    code, out = _review(["a", "q", "y"])

    assert code == 0, out
    tiers = {r["id"]: r["tier"] for r in eng.get_lessons(limit=None, _update_access=False)}
    assert sorted(tiers.values()) == ["staging", "verified"]
    assert "skipped 1" in out.lower()
    assert rows  # both were listed


# ---------------------------------------------------------------------------
# not a terminal
# ---------------------------------------------------------------------------


def test_non_terminal_refuses_and_points_at_export_apply(store):
    eng, _client = store
    _propose_lesson("Use the lockfile for every install")
    before = _snapshot(eng.root)

    out = io.StringIO()
    code = review_interactive.run([], stdin=io.StringIO("a\ny\n"), stdout=out)

    assert code != 0
    assert _snapshot(eng.root) == before
    text = out.getvalue()
    assert "engram review export" in text and "engram review apply" in text


def test_cli_entry_refuses_without_a_terminal(store, monkeypatch, capsys):
    from piia_engram import setup_wizard

    eng, _client = store
    _propose_lesson("Use the lockfile for every install")
    before = _snapshot(eng.root)
    monkeypatch.setattr(sys, "stdin", io.StringIO("a\ny\n"))
    monkeypatch.setattr(sys, "argv", ["engram", "review", "interactive"])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()

    assert int(exc.value.code or 0) != 0
    assert _snapshot(eng.root) == before
    assert "engram review export" in capsys.readouterr().out


def test_review_without_arguments_still_lists(store, capsys):
    from piia_engram.setup_wizard import run_review

    _propose_lesson("Use the lockfile for every install")
    assert run_review([]) == 0
    assert "Use the lockfile for every install" in capsys.readouterr().out


def test_help_names_the_interactive_mode(capsys):
    from piia_engram.setup_wizard import run_review

    assert run_review(["--help"]) == 0
    assert "engram review interactive" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# terminal safety
# ---------------------------------------------------------------------------

_HOSTILE = (
    "Harmless start\x1b[2J\x1b[H\x1b]0;owned\x07 then\r[APPROVED by owner] "
    "\u202eevil\u202c zero\u200bwidth\u2066iso\u2069 \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\ end"
)


def _assert_terminal_safe(text: str) -> None:
    for ch in text:
        if ch == "\n":
            continue
        assert not unicodedata.category(ch).startswith("C"), repr(ch)


def test_hostile_content_is_shown_without_control_bytes(store):
    eng, client = store
    client.update(name="Owner\x1b[32m approved\r", version="9\u202e")
    stored = _propose_lesson("Lesson " + _HOSTILE, detail="Detail " + _HOSTILE * 3)
    assert "\x1b" in stored["summary"] and "\u202e" in stored["detail"]  # kept raw in the store

    code, out = _review(["v", "r", "bad\x1b[31m reason\u202e" + "x" * 500, "y"])

    assert code == 0, out
    _assert_terminal_safe(out)
    assert "Harmless start" in out
    assert "self-reported" in out or "客户端自报" in out
    (stone,) = _tombstones(eng.root)
    assert "reason" not in stone
    (receipt,) = _receipts(eng.root)
    (reason,) = receipt["reject_reasons"].values()
    _assert_terminal_safe(reason)
    assert len(reason) <= review_cli.REASON_MAX
    assert reason.startswith("bad [31m reason")


def test_duplicate_candidate_and_diff_are_shown(store):
    eng, _client = store
    base = ("Before every release pin the mcp dependency below version two "
            "and run the full sanity suite on a clean checkout")
    _trusted_lesson(eng, base)
    _propose_lesson(base + " twice")

    code, out = _review(["k"])

    assert code == 0, out
    assert "possible duplicate of" in out
    assert "earlier" in out and "proposed" in out


# ---------------------------------------------------------------------------
# concurrency: an item changed while it was on screen is skipped
# ---------------------------------------------------------------------------


def test_item_changed_during_review_is_skipped_and_reported(store):
    eng, _client = store
    first = "Run the linters before pushing"
    second = "Run the type checker before pushing"
    _propose_lesson(first)
    _propose_lesson(second)
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))
    changed_id = order[0]

    def _edit_elsewhere():
        result = Engram(root=eng.root).update_knowledge(changed_id, {"detail": "edited elsewhere"})
        assert not result.get("error"), result

    code, out = _review(["a", "a", "y"], hooks={2: _edit_elsewhere})

    assert code == 0, out
    rows = {r["id"]: r for r in eng.get_lessons(limit=None, _update_access=False)}
    assert rows[changed_id]["tier"] == "staging"
    assert rows[order[1]]["tier"] == "verified"
    assert "version_conflict" in out
    assert changed_id in out


# ---------------------------------------------------------------------------
# local CLI only
# ---------------------------------------------------------------------------


def test_no_mcp_tool_reaches_interactive_review():
    names = set(mcp_server.TOOL_GOVERNANCE_CLASS)
    assert not any("interactive" in name for name in names)
    src = Path(mcp_server.__file__).parent
    for path in sorted(src.glob("mcp*.py")):
        text = path.read_text(encoding="utf-8")
        assert "review_interactive" not in text, path.name
        assert "review_cli" not in text, path.name


# ---------------------------------------------------------------------------
# aliases and language
# ---------------------------------------------------------------------------


def test_short_alias_reaches_the_interactive_mode(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import run_review

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert run_review(["-i"]) == 2
    assert "needs a terminal" in capsys.readouterr().out


def test_chinese_screen_and_summary(store, monkeypatch):
    eng, _client = store
    monkeypatch.setattr(i18n, "_runtime_lang", "zh")
    _propose_lesson("Keep one owner per service")

    code, out = _review(["a", "y"])

    assert code == 0, out
    assert "汇总：批准 1，拒绝 0，取代 0，已批准但未写取代边 0，跳过 0，失败 0" in out
    assert "客户端自报" in out


# ---------------------------------------------------------------------------
# project-scoped proposals are part of the Owner's review
# ---------------------------------------------------------------------------


def _propose_project_lesson(summary: str, folder: Path) -> dict:
    _run(mcp_server.add_lesson(summary=summary, detail="worth keeping", domain="type:lesson",
                               project_folder=str(folder), user_confirmed=True))
    row = _lesson(mcp_server._engram, summary)
    assert row is not None and row["tier"] == "staging", row
    assert row.get("project_id") or row.get("project"), row
    return row


def test_project_proposal_is_reviewed_approved_and_recalled_in_its_project(store, tmp_path):
    eng, _client = store
    folder = tmp_path / "alpha-service"
    folder.mkdir()
    summary = "Run the alpha service migrations before its workers restart"
    _propose_project_lesson(summary, folder)

    code, out = _review(["a", "y"])

    assert code == 0, out
    assert "project:" in out
    assert _lesson(eng, summary)["tier"] == "verified"
    assert summary in _run(mcp_server.get_user_context(project_folder=str(folder), level="standard"))


def test_project_proposal_is_listed_and_exported(store, tmp_path, capsys):
    from piia_engram.setup_wizard import run_review

    eng, _client = store
    folder = tmp_path / "beta-service"
    folder.mkdir()
    row = _propose_project_lesson("Pin the beta service toolchain", folder)

    assert run_review([]) == 0
    listed = capsys.readouterr().out
    assert row["id"] in listed and "project:" in listed

    out_dir = tmp_path / "export"
    assert run_review(["export", "--out", str(out_dir)]) == 0
    card = (out_dir / "review.md").read_text(encoding="utf-8")
    assert row["id"] in card and "- scope: project:" in card
    assert row["id"] in json.loads((out_dir / "ids.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# one supersede target per review, including targets an agent proposed
# ---------------------------------------------------------------------------


def _trusted_decision(eng: Engram, question: str, choice: str) -> dict:
    row = eng.add_decision({"question": question, "choice": choice})
    batch_review_staging(eng, [{"id": row["id"], "action": "approve"}], dry_run=False, confirm=True)
    return eng._find_item_by_id(row["id"])[1]


def _agent_revision(eng: Engram, question: str, choice: str, old_id: str) -> dict:
    row = eng.add_decision({"question": question, "choice": choice, "supersedes": old_id})
    stored = eng._find_item_by_id(row["id"])[1]
    assert stored["tier"] == "staging" and stored.get("pending_supersedes") == old_id
    return stored


def _keys_in_order(eng: Engram, keys: dict[str, list]) -> list:
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))
    return [key for item_id in order for key in keys[item_id]]


def test_the_same_target_cannot_be_chosen_twice(store):
    eng, _client = store
    old = _trusted_lesson(eng, "Tag releases by hand")
    first = _propose_lesson("Tag releases from the release workflow")
    second = _propose_lesson("Tag releases from the nightly job")
    before = _snapshot(eng.root)

    # the first item takes the target; the second asks for it again and is refused
    code, out = _review(["s", old["id"], "s", old["id"], "", "k", "n"])

    assert "another item in this review already supersedes it" in out
    assert _snapshot(eng.root) == before
    assert code == 0


def test_an_agent_revision_and_an_owner_supersede_share_one_target(store):
    eng, _client = store
    old = _trusted_decision(eng, "Where do build caches live?", "on each runner")
    revision = _agent_revision(eng, "Where do build caches live now?", "in the shared bucket", old["id"])
    other = eng.add_decision({"question": "Which store holds shared build caches?", "choice": "object store"})
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))

    if order[0] == revision["id"]:
        # approving the revision reserves its target; the Owner cannot pick it again
        code, out = _review(["a", "s", old["id"], "", "k", "y"])
        assert "another item in this review already supersedes it" in out
        winner, waiting = revision["id"], other["id"]
    else:
        # the Owner picked the target first; the revision cannot be approved with it
        code, out = _review(["s", old["id"], "a", "k", "y"])
        assert "already superseded by another item in this review" in out
        winner, waiting = other["id"], revision["id"]

    assert code == 0, out
    edges = [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]
    assert edges == [(winner, old["id"])]
    assert eng._find_item_by_id(waiting)[1]["tier"] == "staging"


def test_an_agent_revision_whose_target_is_gone_is_approved_without_the_link(store):
    eng, _client = store
    old = _trusted_decision(eng, "Where do build caches live?", "on each runner")
    revision = _agent_revision(eng, "Where do build caches live now?", "in the shared bucket", old["id"])
    winner = eng.add_decision({"question": "Which store holds shared build caches?", "choice": "object store"})
    marks, _ = review_cli.validate_marks([{"id": winner["id"], "mark": f"supersede:{old['id']}"}])
    review_cli.apply_marks(Engram(root=eng.root), marks, review_cli.attribution_record("owner", mode="marks"))

    code, out = _review(["a", "y"])

    assert code == 0, out
    assert "approved without link 1" in out and "failed 0" in out
    assert eng._find_item_by_id(revision["id"])[1]["tier"] == "verified"


# ---------------------------------------------------------------------------
# stopping while applying; output encodings; line separators
# ---------------------------------------------------------------------------


def test_ctrl_c_while_applying_lists_what_was_applied(store, monkeypatch):
    eng, _client = store
    first = _propose_lesson("First proposal to approve")
    second = _propose_lesson("Second proposal to approve")
    real = Engram.promote_knowledge
    calls = {"n": 0}

    def _promote(self, item_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(self, item_id, **kwargs)

    monkeypatch.setattr(Engram, "promote_knowledge", _promote)
    code, out = _review(["a", "a", "y"])

    assert code == 130
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))
    assert order == [r for r in (first["id"], second["id"]) if r in order]  # the second one is still pending
    applied_id = ({first["id"], second["id"]} - set(order)).pop()
    assert f"approve {applied_id}: applied" in out
    (receipt,) = _receipts(eng.root)
    assert receipt["counts"]["aborted"] == 1 and receipt["total_marks"] == 2


def test_line_and_paragraph_separators_are_not_printed(store):
    eng, _client = store
    _propose_lesson("Line\u2028separator and paragraph\u2029separator in a proposal")

    code, out = _review(["v", "k"])

    assert code == 0, out
    assert "\u2028" not in out and "\u2029" not in out
    assert "Line separator and paragraph separator" in out


class _GbkScreen(io.TextIOWrapper):
    def isatty(self) -> bool:
        return True


class _StrictGbkScreen:
    """A terminal stream without reconfigure() that refuses what GBK cannot encode."""

    encoding = "gbk"

    def __init__(self):
        self.parts: list[str] = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        text.encode("gbk")  # raises UnicodeEncodeError on anything GBK cannot hold
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass


@pytest.mark.parametrize("make", [lambda: _GbkScreen(io.BytesIO(), encoding="gbk"), _StrictGbkScreen],
                         ids=["textio-gbk", "strict-gbk"])
def test_a_gbk_terminal_does_not_crash_the_review(store, make):
    eng, client = store
    client.update(name="claude-code \U0001F680", version="1")
    _propose_lesson("Ship it \U0001F680 when the canary is green")
    screen = make()

    code = review_interactive.run([], stdin=_Keys(["v", "a", "y"]), stdout=screen)

    assert code == 0
    assert _lesson(eng, "Ship it \U0001F680 when the canary is green")["tier"] == "verified"


# ---------------------------------------------------------------------------
# reasons stay with the Owner; default operator
# ---------------------------------------------------------------------------


def test_reject_reasons_are_not_returned_over_mcp(store):
    eng, _client = store
    _propose_lesson("Restart workers by hand")

    code, out = _review(["r", "PRIVATE OWNER NOTE", "y"])

    assert code == 0, out
    assert "PRIVATE OWNER NOTE" in (eng.root / "audit.log").read_text(encoding="utf-8")
    log = _run(mcp_server.get_audit_log(limit=50))
    assert "PRIVATE OWNER NOTE" not in log and "reject_reasons" not in log
    assert "review/apply" in log


def test_default_operator_is_owner(store):
    eng, _client = store
    _propose_lesson("Keep one owner per service")

    code, out = _review(["a", "y"], args=[])

    assert code == 0, out
    (receipt,) = _receipts(eng.root)
    assert receipt["operator"] == "owner"


# ---------------------------------------------------------------------------
# decisions made earlier in the session count when choosing a supersede target
# ---------------------------------------------------------------------------


def _edges_of(eng: Engram) -> list[tuple[str, str]]:
    return [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]


def test_approve_an_entry_then_its_agent_revision_in_one_session(store):
    eng, _client = store
    # a type label sorts the entry first, the unlabeled revision second
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner",
                              "domain": "type:rule"})
    revision = _agent_revision(eng, "Where do build caches live now?", "in the shared bucket", first["id"])
    assert review_interactive.pending_order(Engram(root=eng.root, read_only=True)) == [first["id"], revision["id"]]

    code, out = _review(["a", "a", "y"])

    assert code == 0, out
    assert _edges_of(eng) == [(revision["id"], first["id"])]
    assert "superseded 0" in out and "approved 2" in out and "failed 0" in out


def test_agent_revision_shown_before_its_entry_is_still_linked(store):
    eng, _client = store
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    revision = eng.add_decision({"question": "Where do build caches live now?", "choice": "in the shared bucket",
                                 "supersedes": first["id"], "domain": "type:rule"})  # sorts the revision first
    assert eng._find_item_by_id(revision["id"])[1].get("pending_supersedes") == first["id"]
    order = review_interactive.pending_order(Engram(root=eng.root, read_only=True))
    assert order == [revision["id"], first["id"]]

    code, out = _review(["a", "a", "y"])

    assert code == 0, out
    assert "approve it in this review to keep the link" in out
    assert _edges_of(eng) == [(revision["id"], first["id"])]


def test_approve_an_entry_then_supersede_it_in_one_session(store):
    eng, _client = store
    first = _propose_lesson("Tag releases by hand")  # type:lesson sorts before an unlabeled lesson
    second = _propose_lesson("Tag releases from the release workflow", domain="release")
    assert review_interactive.pending_order(Engram(root=eng.root, read_only=True)) == [first["id"], second["id"]]

    code, out = _review(["a", "s", first["id"], "y"])

    assert code == 0, out
    assert _edges_of(eng) == [(second["id"], first["id"])]
    assert "superseded 1" in out


def test_an_entry_rejected_in_the_session_cannot_be_a_target(store):
    eng, _client = store
    first = _propose_lesson("Tag releases by hand")
    second = _propose_lesson("Tag releases from the release workflow", domain="release")

    code, out = _review(["r", "", "s", first["id"], "", "a", "y"])

    assert code == 0, out
    assert "you rejected it in this review" in out
    assert _lesson(eng, "Tag releases from the release workflow")["tier"] == "verified"
    assert _edges_of(eng) == []


def test_a_chain_of_agent_revisions_is_linked_whatever_the_display_order(store):
    eng, _client = store
    first = eng.add_decision({"question": "Where do build caches live?", "choice": "on each runner"})
    middle = _agent_revision(eng, "Where do build caches live now?", "in the shared bucket", first["id"])
    newest = _agent_revision(eng, "Where do build caches live from now on?", "in the regional bucket", middle["id"])

    code, out = _review(["a", "a", "a", "y"])

    assert code == 0, out
    assert sorted(_edges_of(eng)) == sorted([(middle["id"], first["id"]), (newest["id"], middle["id"])])
    assert "approved without link 0" in out and "failed 0" in out
