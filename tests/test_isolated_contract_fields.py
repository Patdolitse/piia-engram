"""Persisted-field assertions for the v1 additions; no real user inputs."""

import json

import pytest

from piia_engram import write_provenance
from piia_engram.core import Engram
from piia_engram.isolated_store import GuardRefused, IDENTIFIER_RE, EVENT_KEY_RE
from test_isolated_field_audit import _disk
from test_isolated_store import ADMIT, _snap
from test_isolated_supersede import T2, admit, card, recall
from test_replay_experience import CLOCK, _world


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("key", [None, "sample:v2"])
def test_new_fields_on_disk_are_only_references_and_digests(tmp_path, monkeypatch, mode, key):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    replacement = card(2)
    marker = "SYNTHETIC private audit payload"
    replacement["notes"] = marker
    admission = dict(ADMIT, reason=marker)
    options = dict(supersedes=old["item_id"], event_key=key)
    if mode == "replay_experience":
        options.update(admitted_before=T2, now=CLOCK)
    receipt = w.pr.admit(replacement, "round", admission, **options)
    recalled = recall(w)
    ledger = [json.loads(line) for line in w.pr.receipts_path.read_text(encoding="utf-8").splitlines()]
    intent, completion = [r for r in ledger if r.get("supersedes")]
    for row in (intent, completion):
        assert row["supersedes"] == old["item_id"]
        assert row["superseded_by"] == receipt["item_id"]
        assert IDENTIFIER_RE.fullmatch(row["supersedes"])
        assert IDENTIFIER_RE.fullmatch(row["superseded_by"])
        assert EVENT_KEY_RE.fullmatch(row["event_key"])
        if key is None:
            assert row["event_key"].startswith("supersede:")
            assert len(row["event_key"]) == 74
        assert len(row["admission_sha256"]) == 64 and "reason" not in row
    assert ledger[-1]["result_sha256"] == recalled["result_sha256"]
    assert len(ledger[-1]["result_sha256"]) == 64
    stored = json.loads((w.pr.root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in stored}
    assert rows[old["item_id"]]["superseded_by"] == receipt["item_id"]
    assert rows[receipt["item_id"]]["supersedes"] == old["item_id"]
    assert rows[old["item_id"]]["status"] == "retired"
    for path, raw in _disk(w).items():
        assert marker.encode("utf-8") not in raw, path


@pytest.mark.parametrize("value", ["SYNTHETIC private audit payload", "x" * 129, "", 1, ["id"]])
def test_supersedes_free_text_never_reaches_any_disk_file(tmp_path, monkeypatch, value):
    w = _world(tmp_path, monkeypatch, mode="production")
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersedes_invalid"):
        w.pr.admit(card(2), "round", ADMIT, supersedes=value)
    assert _snap(w.area) == before


def test_internal_atomic_option_refuses_ordinary_roots_and_mcp(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production")
    old = admit(w, 1)
    for eng in (Engram(root=tmp_path / "ordinary"), w.pr._engram(read_only=False)):
        with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
            with pytest.raises(GuardRefused, match="isolated_event_parameters_not_supported"):
                eng.add_lesson(card(2), _isolated_event_key="sample:v2", _isolated_supersedes=old["item_id"])
    with pytest.raises(GuardRefused, match="isolated_event_parameters_not_supported"):
        Engram(root=tmp_path / "ordinary").add_lesson(card(2), _isolated_event_key="sample:v2",
                                                    _isolated_supersedes=old["item_id"])


def test_supersede_references_survive_actual_archive_files(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch, mode="production", limits={"r_max": 1})
    old = admit(w, 1)
    second = admit(w, 2, supersedes=old["item_id"])
    third = admit(w, 3, supersedes=second["item_id"])
    fourth = admit(w, 4, supersedes=third["item_id"])
    archived = []
    for path in (w.pr.root / "knowledge" / "overflow_archive").glob("*.jsonl"):
        archived.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    by_id = {row["id"]: row for row in archived if not row.get("_snapshot")}
    assert by_id[old["item_id"]]["superseded_by"] == second["item_id"]
    assert by_id[second["item_id"]]["supersedes"] == old["item_id"]
    assert by_id[second["item_id"]]["superseded_by"] == third["item_id"]
    assert w.pr.reconcile()["problems"] == []
