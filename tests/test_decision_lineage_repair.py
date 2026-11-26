"""A ``supersedes`` edge between two decisions that have nothing to do with each other.

Doctor reports a decision that is still active while a reviewed decision is
recorded as replacing it. It used to print a ready-to-run command that
retired the older one. The edge may be wrong, so doctor now shows both
titles and commands that only preview.

Every store here is a temporary one.
"""

from __future__ import annotations

import json

import pytest

from piia_engram import Engram
from piia_engram.doctor import decision_consistency_check
from piia_engram.governance_store import RelationStore

OLD_TITLE = "Choose the storage engine"
NEW_TITLE = "Pick the release cadence"


def _files(root):
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def unrelated(tmp_path):
    """Two reviewed, unrelated decisions and a stray edge between them."""
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": OLD_TITLE, "choice": "SQLite", "tier": "verified"})
    new = eng.add_decision({"question": NEW_TITLE, "choice": "monthly", "tier": "verified"})
    RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
    return eng, old, new


class TestDoctorHint:
    def test_the_hint_only_previews(self, unrelated):
        eng, old, new = unrelated
        finding = decision_consistency_check(Engram(root=eng.root, read_only=True))

        assert finding["status"] == "WARN"
        assert "--commit" not in finding["hint"] and "--yes" not in finding["hint"]
        assert (f"engram conflicts resolve {new['id']} {old['id']} --action supersede "
                f"--keep {new['id']}") in finding["hint"]

    def test_the_hint_names_both_decisions_and_warns(self, unrelated):
        eng, old, new = unrelated
        finding = decision_consistency_check(Engram(root=eng.root, read_only=True))

        assert OLD_TITLE in finding["hint"] and NEW_TITLE in finding["hint"]
        assert "unrelated" in finding["hint"]
        assert "hidden from recall" in finding["hint"]

    def test_pairs_carry_ids_and_titles(self, unrelated):
        eng, old, new = unrelated
        finding = decision_consistency_check(Engram(root=eng.root, read_only=True))

        (pair,) = finding["pairs"]
        assert pair["predecessor"] == old["id"] and pair["predecessor_title"] == OLD_TITLE
        assert pair["successor"] == new["id"] and pair["successor_title"] == NEW_TITLE
        assert all("--commit" not in command for command in pair["preview_commands"])
        json.dumps(finding)  # the --json report prints it as it is

    def test_a_title_is_one_short_line(self, tmp_path):
        eng = Engram(root=tmp_path / "store")
        old = eng.add_decision({"question": "Choose\nthe storage \x1b[31mengine " + "x" * 300,
                                "choice": "SQLite", "tier": "verified"})
        new = eng.add_decision({"question": NEW_TITLE, "choice": "monthly", "tier": "verified"})
        RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])

        (pair,) = decision_consistency_check(eng)["pairs"]

        assert "\n" not in pair["predecessor_title"] and "\x1b" not in pair["predecessor_title"]
        assert len(pair["predecessor_title"]) <= 80

    def test_the_check_writes_nothing(self, unrelated):
        eng, old, new = unrelated
        before = _files(eng.root)
        decision_consistency_check(Engram(root=eng.root, read_only=True))
        assert _files(eng.root) == before

    def test_without_such_an_edge_there_is_no_hint(self, tmp_path):
        # Negative control.
        eng = Engram(root=tmp_path / "store")
        eng.add_decision({"question": OLD_TITLE, "choice": "SQLite", "tier": "verified"})
        eng.add_decision({"question": NEW_TITLE, "choice": "monthly", "tier": "verified"})

        finding = decision_consistency_check(eng)

        assert finding["status"] == "PASS"
        assert finding["hint"] == "" and finding["pairs"] == []

    def test_doctor_prints_the_titles_and_no_ready_command(self, unrelated, capsys):
        from piia_engram import doctor

        eng, old, new = unrelated
        doctor._print_decision_consistency(decision_consistency_check(eng))
        out = capsys.readouterr().out

        assert "[WARN] Decision consistency: 1 active predecessor(s)" in out
        assert OLD_TITLE in out and NEW_TITLE in out
        assert "--commit" not in out and "--yes" not in out
        assert "do not apply" in out
