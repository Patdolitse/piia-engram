"""Tests for the owner-confirmed reconcile *apply* path (N2).

``reconcile_proposal.build_reconcile_proposal`` classifies external candidates
as ``import`` / ``duplicate`` / ``conflict`` / ``skip`` but never writes. This
suite covers the separate, explicit, owner-gated step that acts on that proposal
- and only on the ``import`` verdicts. It is deliberately **import-only**:

- default / dry-run mutates nothing;
- apply without explicit confirm fails closed (``requires_confirmation``);
- a confirmed apply imports ONLY ``import``-classified candidates;
- ``duplicate`` / ``conflict`` / ``skip`` candidates are surfaced as metadata
  no-ops and NEVER mutate an existing lesson or decision (conflict->supersede
  resolution is deferred);
- the payload is metadata-only (actions, scores, ids) - no candidate bodies.
"""

from __future__ import annotations

import json
from pathlib import Path


SECRET = "ZZ_RECONCILE_APPLY_SECRET_BODY"


def _make_engine(tmp_path: Path):
    from piia_engram.core import Engram

    return Engram(root=tmp_path)


def _active_lessons(eng):
    return [l for l in (eng.get_lessons(limit=None, _update_access=False) or [])
            if l.get("status") == "active"]


def _active_decisions(eng):
    return [d for d in (eng.get_decisions(limit=None, _update_access=False) or [])
            if d.get("status") == "active"]


# --- dry-run (default) -----------------------------------------------------


def test_dry_run_is_default_and_imports_nothing(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    candidates = [{"summary": "prefer pure store-free helpers for the recall surface",
                   "detail": f"{SECRET} novel"}]

    payload = apply_reconcile(eng, candidates)
    assert payload["dry_run"] is True
    assert payload["changed"] is False
    assert payload["status"] == "dry_run"
    assert payload["counts"]["import"] == 1
    assert len(_active_lessons(eng)) == 0


def test_apply_without_confirm_fails_closed(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    candidates = [{"summary": "prefer pure store-free helpers for the recall surface",
                   "detail": f"{SECRET} novel"}]

    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=False)
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert payload["status"] == "confirmation_required"
    assert len(_active_lessons(eng)) == 0


# --- confirmed import-only --------------------------------------------------


def test_confirmed_apply_imports_only_novel(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    candidates = [
        {"summary": "prefer pure store-free helpers for the recall surface",
         "detail": f"{SECRET} novel"},
    ]

    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)
    assert payload["changed"] is True
    assert payload["status"] == "applied"
    assert payload["counts"]["imported"] == 1
    lessons = _active_lessons(eng)
    assert len(lessons) == 1
    # The new entry records a metadata-only id we can echo back.
    imported = [it for it in payload["items"] if it["outcome"] == "imported"]
    assert imported and imported[0]["imported_id"]


def test_duplicate_candidate_is_noop(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    eng.add_lesson("always rebuild the search index after a bulk knowledge import",
                   detail=f"{SECRET} existing", tier="verified")
    before = len(_active_lessons(eng))

    # Near-identical wording -> classified duplicate -> must NOT import.
    candidates = [{"summary": "always rebuild the search index after a bulk knowledge import",
                   "detail": f"{SECRET} dup"}]
    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)
    assert payload["counts"].get("imported", 0) == 0
    assert payload["counts"]["duplicate"] == 1
    assert len(_active_lessons(eng)) == before
    assert payload["items"][0]["outcome"] == "noop"


def test_conflict_candidate_does_not_mutate_existing(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    eng.add_decision(
        "which interpreter runs the release pytest suite",
        choice="use the codex primary runtime python",
        reasoning=f"{SECRET} existing reasoning",
        tier="verified",
    )
    existing_before = _active_decisions(eng)
    assert len(existing_before) == 1
    original_choice = existing_before[0]["choice"]

    # Same question, different choice -> conflict. Import-only path must leave the
    # existing decision UNTOUCHED (no supersede, no overwrite).
    candidates = [{
        "question": "which interpreter runs the release pytest suite",
        "choice": "use the windows store python alias",
        "reasoning": f"{SECRET} conflicting",
    }]
    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)

    assert payload["counts"]["conflict"] == 1
    assert payload["counts"].get("imported", 0) == 0
    assert payload["items"][0]["outcome"] == "noop"

    after = _active_decisions(eng)
    assert len(after) == 1  # no new decision imported
    assert after[0]["choice"] == original_choice  # existing not mutated
    assert after[0]["status"] == "active"


def test_imports_a_novel_decision(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    candidates = [{
        "question": "where should owner dashboard readiness counts live",
        "choice": "in build_owner_dashboard as a metadata-only readiness block",
        "reasoning": f"{SECRET} decision",
    }]
    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)
    assert payload["counts"]["imported"] == 1
    assert len(_active_decisions(eng)) == 1


# --- safety: metadata only --------------------------------------------------


def test_payload_is_metadata_only(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile

    eng = _make_engine(tmp_path)
    candidates = [{"summary": "prefer pure store-free helpers for the recall surface",
                   "detail": f"{SECRET} novel"}]
    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)
    assert SECRET not in json.dumps(payload, ensure_ascii=False)


def test_render_text_is_metadata_only(tmp_path):
    from piia_engram.reconcile_apply import apply_reconcile, render_reconcile_apply_text

    eng = _make_engine(tmp_path)
    candidates = [{"summary": "prefer pure store-free helpers for the recall surface",
                   "detail": f"{SECRET} novel"}]
    payload = apply_reconcile(eng, candidates)
    text = render_reconcile_apply_text(payload)
    assert SECRET not in text
    assert "dry-run" in text


def test_reconcile_apply_audit_is_metadata_only(tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import apply_reconcile

    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = _make_engine(tmp_path)
    candidates = [{"summary": "prefer pure store-free helpers for the recall surface",
                   "detail": f"{SECRET} novel"}]

    payload = apply_reconcile(eng, candidates, dry_run=False, confirm=True)

    assert payload["changed"] is True
    audit_log = tmp_path / "audit.log"
    assert audit_log.exists()
    audit_text = audit_log.read_text(encoding="utf-8")
    assert SECRET not in audit_text
    assert "knowledge/reconcile_import" in audit_text
    assert payload["items"][0]["imported_id"] in audit_text


# --- CLI surface (owner-only) ----------------------------------------------


def _fixed_candidates(self):
    return [{"summary": "prefer pure store-free helpers for the recall surface",
             "detail": f"{SECRET} novel", "domain": "auto_reconcile",
             "source": "mem.md"}]


def test_cli_reconcile_apply_commit_requires_confirm(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_reconcile

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(Engram, "collect_memory_candidates", _fixed_candidates)

    # --commit without --yes must fail closed and import nothing.
    assert _run_reconcile(["apply", "--commit", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert len(_active_lessons(Engram())) == 0


def test_cli_reconcile_apply_commit_confirmed(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_reconcile

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(Engram, "collect_memory_candidates", _fixed_candidates)

    assert _run_reconcile(["apply", "--commit", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    assert payload["counts"]["imported"] == 1
    assert SECRET not in json.dumps(payload)
    assert len(_active_lessons(Engram())) == 1


# --- conflict preview v2 (metadata-only; no mutation) ----------------------


def _conflict_candidates(self):
    return [{
        "question": "which interpreter runs the release pytest suite",
        "choice": "use the windows store python alias",
        "reasoning": f"{SECRET} conflicting",
        "source": "mem.md",
    }]


def test_cli_reconcile_conflicts_preview_is_metadata_only(tmp_path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_reconcile

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    eng.add_decision(
        "which interpreter runs the release pytest suite",
        choice="use the codex primary runtime python",
        reasoning=f"{SECRET} existing",
        tier="verified",
    )
    monkeypatch.setattr(Engram, "collect_memory_candidates", _conflict_candidates)

    assert _run_reconcile(["conflicts", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["action"] == "reconcile_conflicts_preview"
    assert payload["counts"]["conflict"] == 1
    assert payload["items"][0]["action"] == "conflict"
    assert payload["items"][0]["match_id"]
    assert SECRET not in out
    assert "windows store python alias" not in out
    assert len(_active_decisions(Engram())) == 1
