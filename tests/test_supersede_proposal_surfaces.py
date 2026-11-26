"""Where a replacement proposal is shown: ``engram review show`` and the reply to the AI.

A decision or lesson that would replace a reviewed one waits for the Owner.
``engram review show <id>`` printed such a proposal like any other item, and
the AI that proposed it was only told ``tier=staging``. Both now say what it
would replace and that the old entry stays in use until the Owner approves.

Every store here is a temporary one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.cli_commands import run_review
from piia_engram.core import Engram
from piia_engram.write_provenance import ORIGIN_MCP, origin_scope

OLD_Q = "Choose the storage engine"
NEW_Q = "Pick the release cadence"


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def old(eng):
    return eng.add_decision({"question": OLD_Q, "choice": "SQLite", "tier": "verified"})


@pytest.fixture()
def proposal(eng, old):
    with origin_scope(ORIGIN_MCP, client_name="agent"):
        return eng.add_decision({"question": NEW_Q, "choice": "monthly", "supersedes": old["id"]})


def _show(capsys, *args):
    capsys.readouterr()
    rc = run_review(["show", *args])
    return rc, capsys.readouterr().out


class TestReviewShow:
    def test_a_replacement_proposal_shows_what_it_would_replace(self, proposal, old, capsys):
        rc, out = _show(capsys, proposal["id"])

        assert rc == 0
        assert f"proposes to supersede: {old['id']}" in out
        assert f"would replace: {OLD_Q} -> SQLite" in out
        assert "why proposed: named by the proposing tool" in out
        assert "if the two are unrelated, reject this proposal" in out

    def test_an_inferred_proposal_says_it_was_inferred(self, eng, old, capsys):
        new = eng.add_decision({"question": OLD_Q, "choice": "PostgreSQL", "tier": "verified"})
        rc, out = _show(capsys, new["id"])
        assert "why proposed: inferred: same question, different choice" in out
        assert "would replace:" in out and "SQLite" in out

    def test_an_ordinary_item_has_no_such_lines(self, eng, old, capsys):
        # Negative control.
        plain = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "staging"})
        rc, out = _show(capsys, plain["id"])
        assert rc == 0 and "would replace" not in out and "supersede" not in out

    def test_json_carries_the_item_and_the_replacement(self, proposal, old, capsys):
        rc, out = _show(capsys, proposal["id"], "--json")

        payload = json.loads(out)
        assert rc == 0 and payload["type"] == "decision"
        assert payload["item"]["id"] == proposal["id"]
        assert payload["would_replace"] == {
            "id": old["id"], "kind": "decision", "found": True,
            "claim": f"{OLD_Q} -> SQLite",
            "why_proposed": "named by the proposing tool; nothing checked that the two are related",
            "note": "if the two are unrelated, reject this proposal",
        }

    def test_json_of_an_ordinary_item_replaces_nothing(self, eng, old, capsys):
        plain = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "staging"})
        rc, out = _show(capsys, plain["id"], "--json")
        payload = json.loads(out)
        assert payload["item"]["id"] == plain["id"] and payload["would_replace"] is None

    def test_show_writes_nothing_the_review_decides(self, proposal, old, eng, capsys):
        before = (eng._knowledge_dir / "decisions.json").read_bytes()
        _show(capsys, proposal["id"])
        _show(capsys, proposal["id"], "--json")
        assert (eng._knowledge_dir / "decisions.json").read_bytes() == before

    def test_the_card_lines_are_unchanged(self, proposal, old, eng):
        from piia_engram import review_cli

        row = eng._find_item_by_id(proposal["id"])[1]
        assert review_cli.supersede_target_lines(eng, row) == [
            f"would replace: {OLD_Q} -> SQLite",
            "why proposed: named by the proposing tool; nothing checked that the two are related",
            "if the two are unrelated, reject this proposal",
        ]


class TestReplyToTheAi:
    def _assert_proposal_reply(self, reply: str, old_id: str, title: str):
        assert "tier=staging" in reply
        assert "待主人批准" in reply and "awaiting the Owner's approval" in reply
        assert old_id in reply and title in reply
        assert "批准前" in reply and "stays in effect" in reply

    def test_add_decision_with_supersedes_says_it_is_a_proposal(self, eng, old):
        reply = _run(mcp_server.add_decision(
            question=NEW_Q, choice="monthly", supersedes=old["id"],
            supersedes_expected_version=1, user_confirmed=True))
        self._assert_proposal_reply(reply, old["id"], OLD_Q)

    def test_an_inferred_replacement_says_so_too(self, eng, old):
        reply = _run(mcp_server.add_decision(question=OLD_Q, choice="PostgreSQL", user_confirmed=True))
        self._assert_proposal_reply(reply, old["id"], OLD_Q)

    def test_memory_store_gives_the_same_reply(self, eng, old):
        content = {"question": NEW_Q, "choice": "monthly", "supersedes": old["id"],
                   "supersedes_expected_version": 1}
        reply = _run(mcp_server.memory_store(kind="decision", content_json=json.dumps(content),
                                             user_confirmed=True))
        self._assert_proposal_reply(reply, old["id"], OLD_Q)

    def test_a_lesson_revision_says_it_is_a_proposal(self, eng):
        first = eng.add_lesson({"summary": "Always run the linter before committing changes.",
                                "tier": "verified"})
        reply = _run(mcp_server.add_lesson(
            summary="Run the formatter and the linter before every commit, in that order.",
            supersedes=first["id"], supersedes_expected_version=1, user_confirmed=True))
        self._assert_proposal_reply(reply, first["id"], "Always run the linter before committing changes.")

    def test_an_ordinary_decision_reply_is_unchanged(self, eng, old):
        # Negative control.
        reply = _run(mcp_server.add_decision(question=NEW_Q, choice="monthly", user_confirmed=True))
        assert reply == f"[Engram] 决策已记录 · tier=verified · 可召回: {NEW_Q} → monthly"

    def test_the_reply_changes_nothing_that_is_written(self, eng, old):
        _run(mcp_server.add_decision(question=NEW_Q, choice="monthly", supersedes=old["id"],
                                     supersedes_expected_version=1, user_confirmed=True))
        rows = json.loads((eng._knowledge_dir / "decisions.json").read_text(encoding="utf-8"))
        new = next(row for row in rows if row["id"] != old["id"])
        assert new["tier"] == "staging" and new["pending_supersedes"] == old["id"]
        assert next(row for row in rows if row["id"] == old["id"])["status"] == "active"

    def test_a_lower_trust_caller_gets_the_id_without_the_title(self, eng, old, monkeypatch):
        from piia_engram import mcp_tools_write

        monkeypatch.setattr(mcp_tools_write, "_may_echo_stored_titles", lambda: False)
        reply = _run(mcp_server.add_decision(
            question=NEW_Q, choice="monthly", supersedes=old["id"],
            supersedes_expected_version=1, user_confirmed=True))
        assert old["id"] in reply and OLD_Q not in reply
        assert "awaiting the Owner's approval" in reply
