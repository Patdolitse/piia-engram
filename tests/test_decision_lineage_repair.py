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
def audited(monkeypatch):
    """The audit log is off under tests; these stores write one."""
    monkeypatch.setenv("ENGRAM_AUDIT", "1")


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


# -- engram lineage unlink ----------------------------------------------------


def _raw(eng, item_id):
    rows = json.loads((eng._knowledge_dir / "decisions.json").read_text(encoding="utf-8"))
    return next(row for row in rows if row["id"] == item_id)


def _edges(eng):
    return [(e["src"], e["dst"]) for e in RelationStore(eng.root).all_edges() if e["rel"] == "supersedes"]


def _visible(eng):
    return {row["id"] for row in Engram(root=eng.root).get_decisions(limit=None, _update_access=False)}


def _same_but_touched(before: dict, after: dict) -> bool:
    drop = {"last_updated"}
    return ({k: v for k, v in before.items() if k not in drop}
            == {k: v for k, v in after.items() if k not in drop})


@pytest.fixture
def superseded(tmp_path):
    """A reviewed decision retired by an unrelated one that named it in ``supersedes``."""
    eng = Engram(root=tmp_path / "store")
    old = eng.add_decision({"question": OLD_TITLE, "choice": "SQLite", "tier": "verified"})
    original = _raw(eng, old["id"])
    new = eng.add_decision({"question": NEW_TITLE, "choice": "monthly", "tier": "verified",
                            "supersedes": old["id"]})
    assert _raw(eng, old["id"])["status"] == "superseded"
    return eng, old, new, original


@pytest.fixture
def resolved(unrelated):
    """The stray edge, after the owner ran the command doctor used to print."""
    from piia_engram.cli_commands import _run_conflicts_resolve

    eng, old, new = unrelated
    original = _raw(eng, old["id"])
    rc, _ = _run_conflicts_resolve(eng, [new["id"], old["id"], "--action", "supersede",
                                         "--keep", new["id"], "--commit", "--yes"])
    assert rc == 0 and _raw(eng, old["id"])["status"] == "outdated"
    return eng, old, new, original


class TestUnlink:
    def test_preview_writes_nothing_and_shows_both(self, superseded):
        eng, old, new, _ = superseded
        before = _files(eng.root)

        result = eng.unlink_decision_lineage(new["id"], old["id"])

        assert result["status"] == "preview" and result["dry_run"] is True
        assert result["changed"] is False and result["restore"] is True
        assert result["predecessor_title"] == OLD_TITLE and result["successor_title"] == NEW_TITLE
        assert result["predecessor_status"] == "superseded"
        assert _files(eng.root) == before

    def test_an_ai_client_cannot_call_it(self, superseded):
        from piia_engram.write_provenance import ORIGIN_MCP, origin_scope

        eng, old, new, _ = superseded
        before = _files(eng.root)

        with origin_scope(ORIGIN_MCP, client_name="some-agent"):
            result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["error"] == "owner_cli_only" and result["changed"] is False
        assert _files(eng.root) == before

    def test_a_superseded_decision_comes_back(self, superseded):
        eng, old, new, original = superseded

        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["status"] == "applied" and result["changed"] is True
        assert result["restored"] is True
        assert _edges(eng) == []
        assert _same_but_touched(original, _raw(eng, old["id"]))
        assert _visible(eng) == {old["id"], new["id"]}
        assert decision_consistency_check(eng)["status"] == "PASS"

    def test_a_decision_retired_by_conflicts_resolve_comes_back(self, resolved):
        eng, old, new, original = resolved

        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["restored"] is True and _edges(eng) == []
        assert _same_but_touched(original, _raw(eng, old["id"]))
        assert _visible(eng) == {old["id"], new["id"]}

    def test_a_decision_the_owner_marked_outdated_stays_outdated(self, unrelated):
        eng, old, new = unrelated
        eng.update_decision(old["id"], {"status": "outdated"})

        preview = eng.unlink_decision_lineage(new["id"], old["id"])
        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert preview["restore"] is False and preview["reason"] == "outdated_by_owner"
        assert result["changed"] is True and result["restored"] is False
        assert _edges(eng) == []
        assert _raw(eng, old["id"])["status"] == "outdated"

    def test_an_active_decision_only_loses_the_edge(self, unrelated):
        eng, old, new = unrelated
        row = _raw(eng, old["id"])
        assert old["id"] not in _visible(eng)  # the stray edge hides it

        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["changed"] is True and result["restored"] is False
        assert _edges(eng) == [] and _raw(eng, old["id"]) == row
        assert old["id"] in _visible(eng)

    def test_revision_history_is_refused(self, tmp_path):
        eng = Engram(root=tmp_path / "store")
        head = eng.add_decision({"question": OLD_TITLE, "choice": "SQLite", "tier": "verified"})
        eng.update_decision(head["id"], {"choice": "PostgreSQL"})
        ((src, snapshot),) = _edges(eng)
        assert src == head["id"]
        before = _files(eng.root)

        result = eng.unlink_decision_lineage(head["id"], snapshot, dry_run=False)

        assert result["error"] == "version_snapshot" and result["changed"] is False
        assert _files(eng.root) == before

    def test_an_edge_that_does_not_exist_is_refused(self, tmp_path):
        # Negative control: nothing to undo, nothing written.
        eng = Engram(root=tmp_path / "store")
        old = eng.add_decision({"question": OLD_TITLE, "choice": "SQLite", "tier": "verified"})
        new = eng.add_decision({"question": NEW_TITLE, "choice": "monthly", "tier": "verified"})
        before = _files(eng.root)

        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["error"] == "edge_not_found" and result["changed"] is False
        assert _files(eng.root) == before

    def test_the_direction_matters(self, superseded):
        eng, old, new, _ = superseded
        before = _files(eng.root)
        result = eng.unlink_decision_lineage(old["id"], new["id"], dry_run=False)
        assert result["error"] == "edge_not_found"
        assert _files(eng.root) == before

    def test_the_pin_is_not_put_back_but_named(self, audited, unrelated):
        from piia_engram import pinning
        from piia_engram.cli_commands import _run_conflicts_resolve

        eng, old, new = unrelated
        RelationStore(eng.root).remove_relation(new["id"], "supersedes", old["id"])
        assert pinning.pin(eng, old["id"]).get("error") is None
        RelationStore(eng.root).add_relation(new["id"], "supersedes", old["id"])
        _run_conflicts_resolve(eng, [new["id"], old["id"], "--action", "supersede",
                                     "--keep", new["id"], "--commit", "--yes"])
        assert not pinning.has_pin(_raw(eng, old["id"]))

        result = eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)

        assert result["restored"] is True and result["was_pinned"] is True
        assert not pinning.has_pin(_raw(eng, old["id"]))

    def test_it_is_recorded_and_audited_by_id_only(self, audited, superseded):
        from piia_engram.governance_store import ResolutionStore

        eng, old, new, _ = superseded
        eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False, note="unrelated topics")

        (record,) = ResolutionStore(eng.root).all_records().values()
        assert record["action"] == "unlinked"
        assert (record["id1"], record["id2"]) == (new["id"], old["id"])
        lines = [json.loads(line) for line in
                 (eng.root / "audit.log").read_text(encoding="utf-8").splitlines()]
        (entry,) = [e for e in lines if e["detail"].startswith("lineage unlink")]
        assert entry["resource"] == "knowledge/relations"
        assert entry["detail"] == (f"lineage unlink {new['id']} supersedes {old['id']} "
                                   "restored=superseded->active")
        assert OLD_TITLE not in json.dumps(entry) and NEW_TITLE not in json.dumps(entry)

    def test_an_import_does_not_bring_the_edge_back(self, superseded):
        eng, old, new, _ = superseded
        edge = {"src": new["id"], "rel": "supersedes", "dst": old["id"]}
        other = {"src": new["id"], "rel": "led_to", "dst": old["id"]}

        # Negative control: before the unlink the same import keeps the edge.
        eng._import_relations_locked([edge], [], merge=True)
        assert _edges(eng) == [(new["id"], old["id"])]

        eng.unlink_decision_lineage(new["id"], old["id"], dry_run=False)
        eng._import_relations_locked([edge, other], [(new["id"], old["id"])], merge=True)

        assert _edges(eng) == []
        assert other in RelationStore(eng.root).all_edges()

    def test_doctor_offers_the_unlink_preview(self, unrelated):
        eng, old, new = unrelated
        finding = decision_consistency_check(eng)
        assert f"engram lineage unlink {new['id']} {old['id']}" in finding["hint"]
        assert "--commit" not in finding["hint"]


class TestLineageCli:
    @pytest.fixture
    def cli(self, superseded, monkeypatch, capsys):
        from piia_engram.cli_commands import run_lineage

        eng, old, new, original = superseded
        monkeypatch.setenv("ENGRAM_DIR", str(eng.root))

        def run(*args):
            capsys.readouterr()
            rc = run_lineage(list(args))
            return rc, capsys.readouterr().out

        return eng, old, new, run

    def test_the_default_is_a_preview(self, cli):
        eng, old, new, run = cli
        before = _files(eng.root)

        rc, out = run("unlink", new["id"], old["id"])

        assert rc == 0
        assert OLD_TITLE in out and NEW_TITLE in out
        assert "preview" in out.lower() and "--commit --yes" in out
        assert _files(eng.root) == before

    def test_commit_alone_asks_for_confirmation(self, cli):
        eng, old, new, run = cli
        before = _files(eng.root)

        rc, out = run("unlink", new["id"], old["id"], "--commit", "--json")

        assert rc == 1
        assert json.loads(out)["status"] == "confirmation_required"
        assert _files(eng.root) == before

    def test_commit_and_yes_apply(self, cli):
        eng, old, new, run = cli

        rc, out = run("unlink", new["id"], old["id"], "--commit", "--yes", "--json")

        payload = json.loads(out)
        assert rc == 0 and payload["status"] == "applied" and payload["restored"] is True
        assert _edges(eng) == [] and old["id"] in _visible(eng)

    def test_an_unknown_edge_fails_and_writes_nothing(self, cli):
        eng, old, new, run = cli
        before = _files(eng.root)
        rc, out = run("unlink", old["id"], new["id"], "--commit", "--yes")
        assert rc == 1 and "edge_not_found" in out
        assert _files(eng.root) == before

    @pytest.mark.parametrize("args", [(), ("unlink",), ("unlink", "a"), ("unlink", "a", "b", "--nope"),
                                      ("link", "a", "b")])
    def test_usage_errors(self, cli, args):
        eng, old, new, run = cli
        before = _files(eng.root)
        rc, out = run(*args)
        assert rc == (0 if not args else 2)
        assert "engram lineage unlink" in out
        assert _files(eng.root) == before
