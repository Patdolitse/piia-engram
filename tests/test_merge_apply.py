"""Tests for the owner-confirmed near-duplicate merge *apply* path (N4).

``reports_analytics.suggest_merges`` scores near-duplicate lesson/decision pairs
but never mutates. This suite covers the separate, explicit, owner-gated step
that acts on those suggestions by folding a secondary item into a primary via
the existing reviewed ``merge_knowledge`` primitive - a reversible soft archive
(secondary status -> ``outdated`` + ``merged_into``), never a hard delete.

Invariants exercised here:

- default / dry-run mutates nothing;
- apply without explicit confirm fails closed (``requires_confirmation``);
- a confirmed apply merges only proposed/eligible pairs;
- merge is a soft archive: the secondary item still EXISTS (never hard-deleted);
- self-merge / missing ids are reported, never crash;
- the payload is metadata-only (ids, type, similarity, outcome) - no bodies;
- re-applying an already-merged pair is an idempotent no-op (skipped).
"""

from __future__ import annotations

import json
from pathlib import Path


SECRET = "ZZ_MERGE_APPLY_SECRET_BODY"


def _make_engine(tmp_path: Path):
    from piia_engram.core import Engram

    return Engram(root=tmp_path)


def _add_dup_pair(eng):
    """Two near-duplicate active lessons (~0.56 sim) -> a merge candidate.

    Similar enough that ``suggest_merges`` flags them, but below the 0.95
    exact-duplicate reject threshold so both are actually stored.
    """
    a = eng.add_lesson(
        "always run the public fact sync guard before tagging a release build",
        detail=f"{SECRET} A", tier="verified",
    )
    b = eng.add_lesson(
        "remember to execute the public fact sync guard prior to creating a release tag",
        detail=f"{SECRET} B", tier="verified",
    )
    assert a.get("id") and b.get("id"), "fixture must store two distinct lessons"
    return a["id"], b["id"]


def _secondary_status(eng, item_id: str) -> str:
    _, item = eng._find_item_by_id(item_id)
    return item.get("status") if item else "MISSING"


# --- dry-run (default) -----------------------------------------------------


def test_dry_run_is_default_and_mutates_nothing(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    payload = apply_merge(eng, pairs=[(pid, sid)])

    assert payload["dry_run"] is True
    assert payload["changed"] is False
    assert payload["status"] == "dry_run"
    # Nothing mutated: both still active.
    assert _secondary_status(eng, sid) == "active"
    assert _secondary_status(eng, pid) == "active"


def test_apply_without_confirm_fails_closed(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    payload = apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=False)

    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert payload["status"] == "confirmation_required"
    assert _secondary_status(eng, sid) == "active"


# --- confirmed apply -------------------------------------------------------


def test_confirmed_apply_soft_archives_secondary(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    payload = apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=True)

    assert payload["changed"] is True
    assert payload["status"] == "applied"
    assert payload["counts"]["merged"] == 1
    # Soft archive - secondary still EXISTS, just outdated. No hard delete.
    assert _secondary_status(eng, sid) == "outdated"
    assert _secondary_status(eng, pid) == "active"
    _, sec = eng._find_item_by_id(sid)
    assert sec.get("merged_into") == pid


def test_idempotent_when_secondary_already_merged(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)
    apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=True)

    # Re-apply: secondary is no longer active -> skipped, not an error/crash.
    payload = apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=True)
    assert payload["changed"] is False
    assert payload["counts"]["skipped"] == 1
    assert _secondary_status(eng, sid) == "outdated"


def test_self_and_missing_pairs_are_skipped_not_crash(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, _ = _add_dup_pair(eng)

    payload = apply_merge(
        eng,
        pairs=[(pid, pid), ("missing-primary", "missing-secondary")],
        dry_run=False, confirm=True,
    )
    assert payload["changed"] is False
    assert payload["counts"]["skipped"] == 2
    for item in payload["items"]:
        assert item["outcome"] == "skipped"


# --- derive pairs from suggest_merges --------------------------------------


def test_pairs_default_to_suggest_merges(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    # No explicit pairs -> derive from suggest_merges.
    payload = apply_merge(eng, threshold=0.4)
    assert payload["dry_run"] is True
    assert payload["counts"]["planned"] >= 1
    ids = {(it["primary_id"], it["secondary_id"]) for it in payload["items"]}
    assert (pid, sid) in ids or (sid, pid) in ids


# --- safety: metadata only -------------------------------------------------


def test_payload_is_metadata_only(tmp_path):
    from piia_engram.merge_apply import apply_merge

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    payload = apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=True)
    blob = json.dumps(payload, ensure_ascii=False)
    assert SECRET not in blob
    # Only metadata keys present per item.
    for item in payload["items"]:
        assert set(item.keys()) <= {
            "primary_id", "secondary_id", "entry_type", "similarity",
            "outcome", "reason", "related_ids_transferred",
        }


def test_render_text_is_metadata_only(tmp_path):
    from piia_engram.merge_apply import apply_merge, render_merge_apply_text

    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)
    payload = apply_merge(eng, pairs=[(pid, sid)])
    text = render_merge_apply_text(payload)
    assert SECRET not in text
    assert "dry-run" in text
    assert pid in text or sid in text


def test_merge_apply_audit_is_metadata_only(tmp_path, monkeypatch):
    from piia_engram.merge_apply import apply_merge

    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = _make_engine(tmp_path)
    pid, sid = _add_dup_pair(eng)

    payload = apply_merge(eng, pairs=[(pid, sid)], dry_run=False, confirm=True)

    assert payload["changed"] is True
    audit_log = tmp_path / "audit.log"
    assert audit_log.exists()
    audit_text = audit_log.read_text(encoding="utf-8")
    assert SECRET not in audit_text
    assert "knowledge/merge" in audit_text
    assert pid in audit_text and sid in audit_text


# --- CLI surface (owner-only) ----------------------------------------------


def test_cli_merge_preview_json_is_metadata_only(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_merge

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    _add_dup_pair(Engram())

    assert _run_merge(["--threshold", "0.3", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert "items" in payload
    assert "primary_summary" not in out
    assert "secondary_summary" not in out
    assert SECRET not in out


def test_cli_merge_apply_commit_requires_confirm(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_merge

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    pid, sid = _add_dup_pair(Engram())
    before = (tmp_path / "knowledge" / "lessons.json").read_bytes()

    # --commit without --yes must fail closed and mutate nothing.
    assert _run_merge(["apply", "--pair", f"{pid}:{sid}", "--commit", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert (tmp_path / "knowledge" / "lessons.json").read_bytes() == before


def test_cli_merge_apply_commit_confirmed(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_merge

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    pid, sid = _add_dup_pair(Engram())

    assert _run_merge(
        ["apply", "--pair", f"{pid}:{sid}", "--commit", "--yes", "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    assert payload["counts"]["merged"] == 1
    assert SECRET not in json.dumps(payload)
