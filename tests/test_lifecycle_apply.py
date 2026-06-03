"""Tests for the owner-confirmed lifecycle archive *apply* path (N1).

The lifecycle scorer (``lifecycle.py``) is proposal-only and never mutates. This
suite covers the separate, explicit, owner-gated step that acts on those
proposals by moving selected entries to the ``archived`` tier - a reversible
soft archive, never a hard delete. Invariants exercised here:

- default / dry-run mutates nothing;
- apply without explicit confirm fails closed (``requires_confirmation``);
- a confirmed apply archives only proposed *eligible* ids;
- verified / trusted entries are protected from this path;
- already-archived ids are idempotent no-ops (``changed`` is False);
- the audit surface is metadata-only (no bodies / no paths);
- archive -> restore round-trips.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


SECRET = "ZZ_LIFECYCLE_APPLY_SECRET_BODY"


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _lessons_path(root: Path) -> Path:
    return root / "knowledge" / "lessons.json"


def _make_engine(tmp_path: Path):
    from piia_engram.core import Engram

    return Engram(root=tmp_path)


def _add_stale_staging(eng, summary: str):
    """A stale, never-used staging lesson -> archive/prune candidate (eligible)."""
    return eng.add_lesson(
        summary,
        detail=f"{SECRET} detail for {summary}",
        tier="staging",
        created_at=_iso(400),
        last_reviewed=_iso(400),
        access_count=0,
    )


def _add_fresh_keep(eng, summary: str):
    """A fresh, used, verified lesson -> keep (never a candidate)."""
    return eng.add_lesson(
        summary,
        detail=f"{SECRET} detail for {summary}",
        tier="verified",
        created_at=_iso(1),
        last_reviewed=_iso(1),
        access_count=25,
    )


def _add_stale_verified(eng, summary: str):
    """A stale, unused *verified* lesson -> protected (must never be archived)."""
    return eng.add_lesson(
        summary,
        detail=f"{SECRET} detail for {summary}",
        tier="verified",
        created_at=_iso(60),
        last_reviewed=_iso(60),
        access_count=0,
    )


# --- pure selection helper -------------------------------------------------


def test_select_archive_candidate_ids_excludes_verified_and_keep():
    from piia_engram import lifecycle

    report = lifecycle.build_lifecycle_proposal(
        [
            {"id": "stale-staging", "summary": "x" * 30,
             "created_at": _iso(400), "access_count": 0, "tier": "staging"},
            {"id": "fresh-keep", "summary": "y" * 30,
             "last_validated_at": _iso(1), "access_count": 30, "tier": "verified"},
            {"id": "stale-verified", "summary": "z" * 30,
             "created_at": _iso(800), "access_count": 0, "tier": "verified"},
        ],
        now=datetime.now(timezone.utc),
    )
    eligible = lifecycle.select_archive_candidate_ids(report)
    assert "stale-staging" in eligible
    assert "fresh-keep" not in eligible
    assert "stale-verified" not in eligible


def test_select_archive_candidate_ids_intersects_requested():
    from piia_engram import lifecycle

    report = lifecycle.build_lifecycle_proposal(
        [
            {"id": "a", "summary": "x" * 30,
             "created_at": _iso(400), "access_count": 0, "tier": "staging"},
            {"id": "b", "summary": "y" * 30,
             "created_at": _iso(400), "access_count": 0, "tier": "staging"},
        ],
        now=datetime.now(timezone.utc),
    )
    eligible = lifecycle.select_archive_candidate_ids(report, requested_ids=["a", "missing"])
    assert eligible == ["a"]


# --- apply path: dry-run / fail-closed / confirmed -------------------------


def test_dry_run_mutates_nothing(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    _add_stale_staging(eng, "stale staging note alpha for archive")
    before = _lessons_path(tmp_path).read_bytes()

    payload = apply_lifecycle_archive(eng)  # default = dry-run

    assert payload["dry_run"] is True
    assert payload["changed"] is False
    assert payload["status"] == "dry_run"
    assert payload["counts"]["eligible"] >= 1
    assert payload["counts"]["archived"] == 0
    assert _lessons_path(tmp_path).read_bytes() == before


def test_apply_without_confirm_fails_closed(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    _add_stale_staging(eng, "stale staging note beta for archive")
    before = _lessons_path(tmp_path).read_bytes()

    payload = apply_lifecycle_archive(eng, dry_run=False, confirm=False)

    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert payload["status"] == "confirmation_required"
    assert payload["counts"]["archived"] == 0
    assert _lessons_path(tmp_path).read_bytes() == before


def test_confirmed_apply_archives_only_eligible(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    candidate = _add_stale_staging(eng, "stale staging candidate gamma archive")
    keep = _add_fresh_keep(eng, "fresh verified keeper delta untouched")
    protected = _add_stale_verified(eng, "stale verified protected epsilon")

    payload = apply_lifecycle_archive(eng, dry_run=False, confirm=True)

    assert payload["status"] == "applied"
    assert payload["changed"] is True
    assert payload["counts"]["archived"] >= 1

    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    # Only the eligible staging candidate is archived.
    assert stored[candidate["id"]]["tier"] == "archived"
    assert stored[candidate["id"]]["archived_at"]
    # Fresh keeper and verified entry are untouched.
    assert stored[keep["id"]]["tier"] == "verified"
    assert "archived_at" not in stored[keep["id"]]
    assert stored[protected["id"]]["tier"] == "verified"
    assert "archived_at" not in stored[protected["id"]]


def test_confirmed_apply_respects_requested_id_subset(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    a = _add_stale_staging(eng, "stale staging subset one zeta archive")
    b = _add_stale_staging(eng, "stale staging subset two eta archive")

    payload = apply_lifecycle_archive(eng, ids=[a["id"]], dry_run=False, confirm=True)

    assert payload["status"] == "applied"
    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[a["id"]]["tier"] == "archived"
    # b was eligible but not requested -> left alone.
    assert stored[b["id"]]["tier"] == "staging"


def test_verified_entry_is_protected_when_requested_explicitly(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    protected = _add_stale_verified(eng, "stale verified explicit theta protected")

    payload = apply_lifecycle_archive(
        eng, ids=[protected["id"]], dry_run=False, confirm=True
    )

    assert payload["counts"]["archived"] == 0
    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[protected["id"]]["tier"] == "verified"
    outcomes = {item["id"]: item["outcome"] for item in payload["items"]}
    assert outcomes[protected["id"]] == "protected"


def test_already_archived_is_idempotent_noop(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    candidate = _add_stale_staging(eng, "stale staging idempotent iota archive")

    first = apply_lifecycle_archive(
        eng, ids=[candidate["id"]], dry_run=False, confirm=True
    )
    assert first["counts"]["archived"] == 1
    stored_first = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    archived_at_first = stored_first[candidate["id"]]["archived_at"]

    # Re-running must not mutate the already-archived entry.
    second = apply_lifecycle_archive(
        eng, ids=[candidate["id"]], dry_run=False, confirm=True
    )
    assert second["counts"]["archived"] == 0
    assert second["counts"]["already_archived"] == 1
    assert second["changed"] is False
    assert second["items"][0]["outcome"] == "already_archived"
    stored_second = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored_second[candidate["id"]]["archived_at"] == archived_at_first


def test_core_soft_archive_is_idempotent(tmp_path: Path):
    eng = _make_engine(tmp_path)
    candidate = _add_stale_staging(eng, "stale staging core idempotent kappa")

    first = eng.soft_archive_knowledge_tier(candidate["id"])
    second = eng.soft_archive_knowledge_tier(candidate["id"])

    assert first["changed"] is True
    assert first["to_tier"] == "archived"
    assert second["changed"] is False
    assert second["to_tier"] == "archived"


def test_core_soft_archive_refuses_verified(tmp_path: Path):
    eng = _make_engine(tmp_path)
    protected = _add_stale_verified(eng, "stale verified core refuse lambda")

    result = eng.soft_archive_knowledge_tier(protected["id"])

    assert result["changed"] is False
    assert result.get("error") == "protected_verified"
    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[protected["id"]]["tier"] == "verified"


# --- audit surface: metadata only ------------------------------------------


def test_apply_audit_is_metadata_only(tmp_path: Path, monkeypatch):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = _make_engine(tmp_path)
    candidate = _add_stale_staging(eng, "stale staging audited mu archive note")

    payload = apply_lifecycle_archive(
        eng, ids=[candidate["id"]], dry_run=False, confirm=True
    )

    # The structured payload itself leaks no body text.
    assert SECRET not in json.dumps(payload, ensure_ascii=False)

    audit_log = tmp_path / "audit.log"
    assert audit_log.exists()
    audit_text = audit_log.read_text(encoding="utf-8")
    assert SECRET not in audit_text
    # The transition IS recorded as metadata (id + tier transition).
    assert candidate["id"] in audit_text
    assert "archived" in audit_text


# --- archive -> restore round-trip -----------------------------------------


def test_archive_restore_round_trip(tmp_path: Path):
    from piia_engram.lifecycle_apply import apply_lifecycle_archive

    eng = _make_engine(tmp_path)
    candidate = _add_stale_staging(eng, "stale staging round trip nu archive")

    apply_lifecycle_archive(eng, ids=[candidate["id"]], dry_run=False, confirm=True)
    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[candidate["id"]]["tier"] == "archived"

    restored = eng.restore_lifecycle_archive(candidate["id"])
    assert restored["changed"] is True
    assert restored["to_tier"] == "staging"

    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[candidate["id"]]["tier"] == "staging"
    assert "archived_at" not in stored[candidate["id"]]


def test_restore_non_archived_is_noop(tmp_path: Path):
    eng = _make_engine(tmp_path)
    keep = _add_fresh_keep(eng, "fresh verified restore noop xi entry")

    result = eng.restore_lifecycle_archive(keep["id"])
    assert result["changed"] is False
    assert result["from_tier"] == "verified"


# --- CLI wrappers ----------------------------------------------------------


def test_cli_lifecycle_apply_dry_run_json(tmp_path: Path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_lifecycle

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    Engram().add_lesson(
        "cli dry run stale staging omicron archive",
        detail=f"{SECRET} cli detail",
        tier="staging",
        created_at=_iso(400),
        last_reviewed=_iso(400),
        access_count=0,
    )

    assert _run_lifecycle(["apply", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["changed"] is False
    assert SECRET not in out


def test_cli_lifecycle_apply_commit_requires_confirm(tmp_path: Path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_lifecycle

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    Engram().add_lesson(
        "cli commit stale staging pi archive note",
        detail=f"{SECRET} cli detail",
        tier="staging",
        created_at=_iso(400),
        last_reviewed=_iso(400),
        access_count=0,
    )
    before = _lessons_path(tmp_path).read_bytes()

    # --commit without --yes must fail closed.
    assert _run_lifecycle(["apply", "--commit", "--json"]) == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert _lessons_path(tmp_path).read_bytes() == before


def test_cli_lifecycle_apply_commit_confirmed(tmp_path: Path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_lifecycle

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    lesson = Engram().add_lesson(
        "cli confirmed stale staging rho archive note",
        detail=f"{SECRET} cli detail",
        tier="staging",
        created_at=_iso(400),
        last_reviewed=_iso(400),
        access_count=0,
    )

    assert _run_lifecycle(["apply", "--commit", "--yes", "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "applied"
    assert payload["changed"] is True
    assert SECRET not in out

    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[lesson["id"]]["tier"] == "archived"


def test_cli_lifecycle_restore_requires_confirm(tmp_path: Path, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _run_lifecycle

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram()
    lesson = eng.add_lesson(
        "cli restore stale staging sigma archive note",
        detail=f"{SECRET} cli detail",
        tier="staging",
        created_at=_iso(400),
        last_reviewed=_iso(400),
        access_count=0,
    )
    eng.soft_archive_knowledge_tier(lesson["id"])

    # Restore without --yes fails closed.
    assert _run_lifecycle(["restore", lesson["id"], "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False

    # Restore with --yes round-trips.
    assert _run_lifecycle(["restore", lesson["id"], "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    stored = {
        e["id"]: e
        for e in json.loads(_lessons_path(tmp_path).read_text(encoding="utf-8"))
    }
    assert stored[lesson["id"]]["tier"] == "staging"
