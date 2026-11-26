"""Replacing a reviewed decision is the Owner's call, and never half done.

An AI client that names an existing decision in ``supersedes`` used to
replace it at once when the store did not run in strict mode, with no check
that the two had anything to do with each other. Now it is a proposal in
every approval mode, the review shows both decisions, a same-question
inference is a proposal for every caller, and a relation whose retirement
step failed is taken back instead of leaving a hidden active decision.

Every store here is a temporary one.
"""

from __future__ import annotations

import json

import pytest

from piia_engram import Engram
from piia_engram.governance_store import RelationStore, ResolutionStore
from piia_engram.write_provenance import ORIGIN_MCP, origin_scope

OLD_Q = "Choose the storage engine"
NEW_Q = "Pick the release cadence"


def _raw(eng, item_id):
    rows = json.loads((eng._knowledge_dir / "decisions.json").read_text(encoding="utf-8"))
    return next(row for row in rows if row["id"] == item_id)


def _edges(eng):
    return [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]


def _visible(eng):
    return {row["id"] for row in eng.get_decisions(limit=None, _update_access=False)}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)  # not strict: the mode that used to write at once
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": OLD_Q, "choice": "SQLite", "tier": "verified"})
    return eng, old


def _audit(eng):
    return [json.loads(line) for line in
            (eng.root / "audit.log").read_text(encoding="utf-8").splitlines()]


class TestExplicitSupersedeFromAnAiClient:
    def test_it_is_a_proposal_in_every_mode(self, store):
        eng, old = store

        with origin_scope(ORIGIN_MCP, client_name="agent"):
            new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "supersedes": old["id"]})

        assert new["tier"] == "staging" and new["approval_status"] == "pending"
        assert new["approval_reason"] == "explicit_supersede"
        assert new["pending_supersedes"] == old["id"]
        assert _edges(eng) == []
        assert _raw(eng, old["id"])["status"] == "active"
        assert old["id"] in _visible(eng)  # still in use until the Owner decides

    def test_approval_writes_the_relation_and_retires_the_old_one(self, store):
        eng, old = store
        with origin_scope(ORIGIN_MCP, client_name="agent"):
            new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "supersedes": old["id"]})

        eng.apply_review({"promote": [{"id": new["id"]}]})

        assert _edges(eng) == [(new["id"], old["id"])]
        assert _raw(eng, old["id"])["status"] == "superseded"
        assert _visible(eng) == {new["id"]}

    def test_an_ordinary_decision_from_an_ai_client_is_unchanged(self, store):
        # Negative control: only the replacement is held.
        eng, old = store
        with origin_scope(ORIGIN_MCP, client_name="agent"):
            new = eng.add_decision({"question": NEW_Q, "choice": "monthly"})
        assert new["tier"] == "verified" and not new.get("pending_supersedes")
        assert _visible(eng) == {old["id"], new["id"]}

    def test_the_owners_own_replacement_still_applies_at_once(self, store):
        # Negative control: a local caller that names the target is not an AI proposal.
        eng, old = store
        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "verified",
                                "supersedes": old["id"]})
        assert _edges(eng) == [(new["id"], old["id"])]
        assert _raw(eng, old["id"])["status"] == "superseded"


class TestInference:
    def test_same_question_other_choice_is_a_proposal_for_a_local_caller_too(self, store):
        eng, old = store

        new = eng.add_decision({"question": OLD_Q, "choice": "PostgreSQL", "tier": "verified"})

        assert new["tier"] == "staging" and new["approval_reason"] == "inferred_supersede"
        assert new["pending_supersedes"] == old["id"]
        assert _edges(eng) == []
        assert old["id"] in _visible(eng)

    def test_the_proposal_becomes_the_replacement_on_approval(self, store):
        eng, old = store
        new = eng.add_decision({"question": OLD_Q, "choice": "PostgreSQL", "tier": "verified"})

        eng.apply_review({"promote": [{"id": new["id"]}]})

        assert _edges(eng) == [(new["id"], old["id"])]
        assert _visible(eng) == {new["id"]}

    def test_a_shared_title_with_different_questions_is_not_a_replacement(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
        eng = Engram(root=tmp_path / "store")
        old = eng.add_decision({"title": "Tooling choice", "question": "Which test runner do we use?",
                                "choice": "pytest", "tier": "verified"})

        new = eng.add_decision({"title": "Tooling choice", "question": "Which package manager do we use?",
                                "choice": "uv", "tier": "verified"})

        assert not new.get("pending_supersedes") and new.get("approval_reason") != "inferred_supersede"
        assert _edges(eng) == []
        assert _visible(eng) >= {old["id"]}

    def test_a_shared_title_and_question_still_infers(self, tmp_path, monkeypatch):
        # Negative control for the question check.
        monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
        eng = Engram(root=tmp_path / "store")
        old = eng.add_decision({"title": "Tooling choice", "question": "Which test runner do we use?",
                                "choice": "pytest", "tier": "verified"})
        new = eng.add_decision({"title": "Tooling choice", "question": "Which test runner do we use?",
                                "choice": "unittest", "tier": "verified"})
        assert new["pending_supersedes"] == old["id"]


class TestNoHalfDoneReplacement:
    def _fail_retirement(self, eng, monkeypatch):
        def fail(old_id, new_id):
            raise OSError("disk went away")

        monkeypatch.setattr(eng, "_retire_superseded_decision", fail)

    def test_a_failed_retirement_takes_the_relation_back(self, store, monkeypatch):
        eng, old = store
        self._fail_retirement(eng, monkeypatch)

        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "verified",
                                "supersedes": old["id"]})

        assert _edges(eng) == []
        assert _raw(eng, old["id"])["status"] == "active"
        assert _visible(eng) == {old["id"], new["id"]}
        details = [e["detail"] for e in _audit(eng) if e["resource"] == "knowledge/relations"]
        assert details == [f"{new['id']} supersedes {old['id']} rolled back (predecessor not retired)"]

    def test_a_failed_retirement_on_approval_takes_the_relation_back(self, store, monkeypatch):
        eng, old = store
        with origin_scope(ORIGIN_MCP, client_name="agent"):
            new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "supersedes": old["id"]})
        self._fail_retirement(eng, monkeypatch)

        eng.apply_review({"promote": [{"id": new["id"]}]})

        assert _edges(eng) == []
        assert _visible(eng) == {old["id"], new["id"]}

    def test_revision_history_edges_are_not_taken_back(self, store, monkeypatch):
        # Negative control: head -> snapshot edges have nothing to retire.
        eng, old = store
        eng.update_decision(old["id"], {"choice": "PostgreSQL"})
        ((src, _snapshot),) = _edges(eng)
        assert src == old["id"]

    def test_the_audit_names_a_relation_only_when_it_stands(self, store):
        eng, old = store
        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "verified",
                                "supersedes": old["id"]})
        details = [e["detail"] for e in _audit(eng) if e["resource"] == "knowledge/relations"]
        assert details == [f"{new['id']} supersedes {old['id']} (decision thread)"]


class TestConflictsResolve:
    def test_a_refused_retirement_writes_no_relation(self, store, monkeypatch):
        from piia_engram.cli_commands import _run_conflicts_resolve

        eng, old = store
        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "verified"})
        monkeypatch.setattr(eng, "update_decision",
                            lambda item_id, updates, **kw: {"error": "version_conflict", "item_id": item_id})

        rc, payload = _run_conflicts_resolve(
            eng, [new["id"], old["id"], "--action", "supersede", "--keep", new["id"], "--commit", "--yes"])

        assert rc == 1 and payload["error"] == "version_conflict" and payload["changed"] is False
        assert _edges(eng) == []
        assert ResolutionStore(eng.root).all_records() == {}
        assert _raw(eng, old["id"])["status"] == "active"

    def test_the_ordinary_run_still_applies(self, store):
        # Negative control.
        from piia_engram.cli_commands import _run_conflicts_resolve

        eng, old = store
        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "verified"})
        rc, payload = _run_conflicts_resolve(
            eng, [new["id"], old["id"], "--action", "supersede", "--keep", new["id"], "--commit", "--yes"])
        assert rc == 0 and payload["changed"] is True
        assert _edges(eng) == [(new["id"], old["id"])]
        assert _raw(eng, old["id"])["status"] == "outdated"


class TestReviewShowsBoth:
    @pytest.fixture
    def proposal(self, store):
        eng, old = store
        with origin_scope(ORIGIN_MCP, client_name="agent"):
            new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "supersedes": old["id"],
                                    "reasoning": "we ship more often now"})
        return eng, old, eng._find_item_by_id(new["id"])[1]

    def test_the_interactive_card_shows_the_decision_it_would_replace(self, proposal):
        from piia_engram import review_interactive

        eng, old, row = proposal
        lines, _ = review_interactive.card(1, 1, "decision", row, eng=eng, lookup={}, edges=[])
        text = "\n".join(lines)

        assert old["id"] in text
        assert OLD_Q in text and "SQLite" in text
        assert "named by the proposing tool" in text

    def test_the_mark_file_card_shows_it_too(self, proposal):
        from piia_engram import review_cli

        eng, old, row = proposal
        text = "\n".join(review_cli._card(1, "decision", row, eng))

        assert f"SUPERSEDES {old['id']}" in text
        assert OLD_Q in text and "SQLite" in text
        assert "named by the proposing tool" in text

    def test_an_inferred_proposal_says_so(self, store):
        from piia_engram import review_interactive

        eng, old = store
        new = eng.add_decision({"question": OLD_Q, "choice": "PostgreSQL", "tier": "verified"})
        row = eng._find_item_by_id(new["id"])[1]
        text = "\n".join(review_interactive.card(1, 1, "decision", row, eng=eng, lookup={}, edges=[])[0])

        assert "same question, different choice" in text
        assert "SQLite" in text

    def test_a_proposal_that_replaces_nothing_has_no_such_lines(self, store):
        # Negative control.
        from piia_engram import review_interactive

        eng, old = store
        new = eng.add_decision({"question": NEW_Q, "choice": "monthly", "tier": "staging"})
        row = eng._find_item_by_id(new["id"])[1]
        text = "\n".join(review_interactive.card(1, 1, "decision", row, eng=eng, lookup={}, edges=[])[0])
        assert "SQLite" not in text and "proposing tool" not in text
