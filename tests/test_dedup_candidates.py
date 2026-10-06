"""Duplicate handling: refuse only the same claim, queue near-duplicates for review.

* same normalized text hash (same scope)  -> refused (status "duplicate")
* similarity >= 0.95 but not the same     -> stored as a pending duplicate
  candidate, also outside strict mode, pointing at the earlier id
* similarity 0.55 .. 0.95                 -> stored as before, cross-linked
* the review card shows the candidate and a sentence-level diff
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import dedup_review
from piia_engram import mcp_server
from piia_engram.core import Engram
from piia_engram.staging_review import list_pending_staging

BASE = ("Before every release pin the mcp dependency below version two "
        "and run the full sanity suite on a clean checkout")
NEAR = BASE + " twice"  # bigram similarity ~0.97
OPPOSITE_A = "Should the nightly job upload anonymous crash reports from every desktop install by default"
OPPOSITE_B = "Should the nightly job not upload anonymous crash reports from every desktop install by default"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=tmp_path)


def _lessons(eng: Engram) -> list[dict]:
    return eng.get_lessons(limit=None, _update_access=False)


def _decisions(eng: Engram) -> list[dict]:
    return eng.get_decisions(limit=None, _update_access=False)


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------


def test_identical_lesson_is_refused(eng):
    first = eng.add_lesson({"summary": BASE, "domain": "release"})

    again = eng.add_lesson({"summary": BASE, "domain": "release"})

    assert again["status"] == "duplicate"
    assert again["existing_id"] == first["id"]
    assert len(_lessons(eng)) == 1


def test_lesson_identical_after_normalization_is_refused(eng):
    first = eng.add_lesson({"summary": "Run the migrations before deploying the API.", "domain": "ops"})

    again = eng.add_lesson({"summary": "Lesson: run the migrations before deploying the api", "domain": "ops"})

    assert again["status"] == "duplicate"
    assert again["existing_id"] == first["id"]


def test_near_duplicate_lesson_is_queued_as_a_candidate(eng):
    first = eng.add_lesson({"summary": BASE, "detail": "d1", "domain": "release"})
    assert first["tier"] == "verified"

    second = eng.add_lesson({"summary": NEAR, "detail": "d2", "domain": "release"})

    assert second.get("status") != "duplicate"
    assert second["tier"] == "staging"
    assert second["approval_status"] == "pending"
    assert second["approval_required"] is True
    candidate = second["duplicate_candidate"]
    assert candidate["existing_id"] == first["id"]
    assert 0.95 <= candidate["similarity"] < 1.0
    assert first["id"] in second["related_ids"]
    stored = {row["id"]: row for row in _lessons(eng)}
    assert stored[second["id"]]["duplicate_candidate"] == candidate
    assert stored[first["id"]]["tier"] == "verified"  # the earlier entry is untouched


def test_candidate_is_pending_under_strict_too(eng, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    first = eng.add_lesson({"summary": BASE, "domain": "release"})

    second = eng.add_lesson({"summary": NEAR, "domain": "release"})

    assert second["tier"] == "staging"
    assert second["duplicate_candidate"]["existing_id"] == first["id"]


def test_allow_similar_new_keeps_the_related_tier(eng):
    first = eng.add_lesson({"summary": BASE, "domain": "release"})

    second = eng.add_lesson({"summary": NEAR, "domain": "release"}, allow_similar_new=True)

    assert second["tier"] == "verified"
    assert "duplicate_candidate" not in second
    assert first["id"] in second["related_ids"]


def test_related_lesson_keeps_its_tier_and_note(eng):
    first = eng.add_lesson({"summary": "run the database migrations before deploying the api service",
                            "domain": "ops"})

    second = eng.add_lesson({"summary": "run the database migrations after deploying the web service",
                             "domain": "ops"})

    assert second["tier"] == "verified"
    assert "duplicate_candidate" not in second
    assert second["_dedup_note"].startswith(f"related to {first['id']}")


def test_candidate_in_another_project_scope_is_not_compared(eng, tmp_path):
    eng.add_lesson({"summary": BASE, "domain": "release", "project_folder": str(tmp_path / "a")})

    other = eng.add_lesson({"summary": NEAR, "domain": "release", "project_folder": str(tmp_path / "b")})

    assert "duplicate_candidate" not in other


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def test_identical_decision_is_refused(eng):
    first = eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    again = eng.add_decision({"question": OPPOSITE_A, "choice": "Yes."})

    assert again["status"] == "duplicate"
    assert again["existing_id"] == first["id"]


def test_opposite_high_similarity_decision_is_not_refused(eng):
    first = eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    second = eng.add_decision({"question": OPPOSITE_B, "choice": "yes"})

    assert second.get("status") != "duplicate"
    assert second["tier"] == "staging"
    assert second["duplicate_candidate"]["existing_id"] == first["id"]
    assert len(_decisions(eng)) == 2


def test_same_question_other_choice_still_revises(eng):
    first = eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    second = eng.add_decision({"question": OPPOSITE_A, "choice": "no, opt-in only"})

    from piia_engram.governance_store import RelationStore

    assert "duplicate_candidate" not in second
    assert second["tier"] == "verified"
    edges = RelationStore(eng.root).all_edges()
    assert {"src": second["id"], "rel": "supersedes", "dst": first["id"]}.items() <= next(
        e for e in edges if e["src"] == second["id"]
    ).items()


# ---------------------------------------------------------------------------
# what the writing agent is told
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_eng(eng, monkeypatch):
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    monkeypatch.setattr(mcp_server, "_engram", eng)
    return eng


def test_mcp_add_lesson_reports_the_candidate(mcp_eng):
    first = mcp_eng.add_lesson({"summary": BASE, "domain": "release"})

    out = _run(mcp_server.add_lesson(summary=NEAR, domain="release", user_confirmed=True))

    assert "重复候选" in out and "possible duplicate" in out
    assert first["id"] in out
    assert "supersedes" in out


def test_mcp_memory_store_decision_reports_the_candidate(mcp_eng):
    first = mcp_eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    out = _run(mcp_server.memory_store(
        kind="decision", content_json=json.dumps({"question": OPPOSITE_B, "choice": "yes"}),
        user_confirmed=True,
    ))

    assert "重复候选" in out and first["id"] in out


def test_bulk_results_name_the_candidate(mcp_eng):
    first = mcp_eng.add_lesson({"summary": BASE, "domain": "release"})

    result = mcp_eng.bulk_add_knowledge([{"summary": NEAR, "domain": "release"}], item_type="lesson")

    (entry,) = result["results"]
    assert entry["duplicate_candidate"]["existing_id"] == first["id"]


def test_review_staging_list_carries_the_candidate(eng):
    first = eng.add_lesson({"summary": BASE, "domain": "release"})
    eng.add_lesson({"summary": NEAR, "domain": "release"})

    listing = list_pending_staging(eng, limit=10)

    (item,) = listing["items"]
    assert item["duplicate_candidate"]["existing_id"] == first["id"]


# ---------------------------------------------------------------------------
# review card
# ---------------------------------------------------------------------------


def test_diff_is_sentence_level_and_capped():
    old = {"id": "a", "summary": "Keep it short. Use tabs.", "detail": "One. Two. Three."}
    new = {"id": "b", "summary": "Keep it short. Use spaces.", "detail": "One. Two. Three."}
    lines = dedup_review.text_diff("lesson", old, new)
    assert "-summary: Use tabs." in lines and "+summary: Use spaces." in lines
    assert "+summary: Keep it short." not in lines

    long_old = {"id": "a", "detail": " ".join(f"Sentence {i}." for i in range(100))}
    long_new = {"id": "b", "detail": " ".join(f"Other {i}." for i in range(100))}
    capped = dedup_review.text_diff("lesson", long_old, long_new)
    assert len(capped) == dedup_review.DIFF_MAX_LINES + 1
    assert capped[-1].startswith("... diff truncated")


def test_fence_cannot_be_closed_by_content():
    block = dedup_review.fenced(["+summary: use ``` and ```` fences"])
    assert block[0] == "`````diff" and block[-1] == "`````"


def _export(tmp_path: Path) -> str:
    from piia_engram import review_cli

    assert review_cli.run_export(["--out", str(tmp_path / "out")]) == 0
    return (tmp_path / "out" / "review.md").read_text(encoding="utf-8")


def test_review_card_shows_candidate_and_diff(eng, tmp_path):
    first = eng.add_lesson({"summary": BASE, "detail": "Pin it. Then run the suite.", "domain": "type:lesson"})
    second = eng.add_lesson({"summary": NEAR, "detail": "Pin it. Then run the suite twice.",
                             "domain": "type:lesson"})

    text = _export(tmp_path)

    assert f"possible duplicate of `{first['id']}` (similarity 97%)" in text
    assert "重复候选" in text
    assert f"--- earlier {first['id']}" in text and f"+++ proposed {second['id']}" in text
    assert "-detail: Then run the suite." in text
    assert "+detail: Then run the suite twice." in text


def test_review_card_shows_near_duplicate_under_strict(eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    first = eng.add_lesson({"summary": "run the database migrations before deploying the api service",
                            "domain": "type:lesson"})
    eng.add_lesson({"summary": "run the database migrations after deploying the web service",
                    "domain": "type:lesson"})

    text = _export(tmp_path)

    assert f"near-duplicate: related to `{first['id']}`" in text
    assert "近重复" in text
    assert "-summary: run the database migrations before deploying the api service" in text


def test_memory_lens_names_the_candidate_without_bodies(eng):
    from piia_engram.context_preview import build_context_preview, render_context_preview_text

    first = eng.add_lesson({"summary": BASE, "detail": "SECRET-BODY-ONE", "domain": "release"})
    eng.add_lesson({"summary": NEAR, "detail": "SECRET-BODY-TWO", "domain": "release"})

    preview = build_context_preview(eng, query="release sanity suite")
    withheld = [w for w in preview["knowledge"]["withheld"] if w.get("duplicate_of")]

    assert withheld and withheld[0]["duplicate_of"] == first["id"]
    text = render_context_preview_text(preview)
    assert first["id"] in text
    assert "SECRET-BODY" not in json.dumps(preview) and "SECRET-BODY" not in text


def test_memory_lens_html_escapes_the_self_reported_client(eng):
    from piia_engram import write_provenance as wp
    from piia_engram.context_preview import build_context_preview, render_context_preview_html

    eng.add_lesson({"summary": BASE, "domain": "release"})
    with wp.origin_scope(wp.ORIGIN_MCP, client_name="<script>alert(1)</script>", client_version="1"):
        eng.add_lesson({"summary": NEAR, "domain": "release"})

    page = render_context_preview_html(build_context_preview(eng, query="release sanity suite"))

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
