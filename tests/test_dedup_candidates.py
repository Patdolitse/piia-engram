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


# ---------------------------------------------------------------------------
# the refusal of identical content names what exists, never a bypass it lacks
# ---------------------------------------------------------------------------


def test_identical_lesson_refusal_points_at_the_existing_entry(eng):
    first = eng.add_lesson({"summary": BASE, "detail": "d1", "domain": "release"})

    for detail in ("d1", "d2"):  # same body, and a probable revision
        res = eng.add_lesson({"summary": BASE, "detail": detail, "domain": "release"})
        assert res["status"] == "duplicate"
        assert "allow_similar_new" not in json.dumps(res, ensure_ascii=False)
        existing = res["guidance"]["existing"]
        assert existing["existing_id"] == first["id"]
        assert "supersedes" in existing["note"]
    assert res["guidance"]["revision"]["target_id"] == first["id"]


def test_identical_decision_refusal_points_at_the_existing_entry(eng):
    first = eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    res = eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})

    assert res["status"] == "duplicate"
    assert "allow_similar_new" not in json.dumps(res, ensure_ascii=False)
    assert res["guidance"]["existing"]["existing_id"] == first["id"]


def test_identical_playbook_title_refusal_does_not_offer_the_bypass(eng):
    first = eng.add_playbook({"title": "Identical playbook title", "steps": ["a"]})

    res = eng.add_playbook({"title": "Identical playbook title", "steps": ["b"]}, allow_similar_new=True)

    assert res["status"] == "duplicate"
    assert "allow_similar_new" not in json.dumps(res, ensure_ascii=False)
    assert res["guidance"]["existing"]["existing_id"] == first["id"]
    assert res["guidance"]["revision"]["target_id"] == first["id"]


def test_mcp_identical_lesson_reply_has_no_bypass_hint(mcp_eng):
    first = mcp_eng.add_lesson({"summary": BASE, "detail": "d1", "domain": "release"})

    out = _run(mcp_server.add_lesson(summary=BASE, detail="d2", domain="release", user_confirmed=True))

    assert "allow_similar_new" not in out
    assert first["id"] in out


def test_allow_similar_new_descriptions_say_it_cannot_bypass_identical():
    for tool in (mcp_server.add_lesson, mcp_server.add_playbook):
        doc = " ".join((tool.__doc__ or "").split())
        assert "cannot bypass" in doc, tool.__name__


# ---------------------------------------------------------------------------
# caller-supplied dedup fields are dropped; stored values are shown defensively; one claim hash
# ---------------------------------------------------------------------------

_FORGED_ID = "abc`](javascript:x) **owned**"


def _forged(real_id: str) -> dict:
    return {
        "duplicate_candidate": {"existing_id": _FORGED_ID, "similarity": "n/a"},
        "_dedup_note": f"related to {real_id} (sim=99%)",
    }


def test_core_inserts_drop_caller_dedup_fields(eng):
    unrelated = eng.add_lesson({"summary": "an unrelated reviewed lesson about tabs", "domain": "t"})

    lesson = eng.add_lesson({"summary": "a fresh lesson about caching layers", "domain": "t",
                             **_forged(unrelated["id"])})
    decision = eng.add_decision({"question": "which cache backend", "choice": "redis",
                                 **_forged(unrelated["id"])})
    playbook = eng.add_playbook({"title": "Cache warmup procedure", "steps": ["warm"],
                                 **_forged(unrelated["id"])})

    for row in (lesson, decision, eng._read_playbook_by_id(playbook["id"])):
        assert "duplicate_candidate" not in row
        assert "_dedup_note" not in row
    assert lesson["tier"] == "verified"


def test_forged_dedup_fields_over_mcp_are_dropped_and_harmless(mcp_eng, tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    unrelated = mcp_eng.add_lesson({"summary": "an unrelated lesson about tab indentation",
                                    "domain": "type:lesson"})
    content = {"summary": "forged candidate lesson about caching", "domain": "type:lesson",
               **_forged(unrelated["id"])}

    out = _run(mcp_server.memory_store(kind="lesson", content_json=json.dumps(content), user_confirmed=True))

    assert "教训已记录" in out and "重复候选" not in out
    row = next(r for r in _lessons(mcp_eng) if r["summary"] == content["summary"])
    assert "duplicate_candidate" not in row and "_dedup_note" not in row
    text = _export(tmp_path)
    assert "possible duplicate" not in text and "near-duplicate" not in text
    assert "javascript" not in text and "**owned**" not in text
    assert "difference (earlier" not in text


def test_display_ignores_malformed_or_unlinked_stored_values(eng):
    real = eng.add_lesson({"summary": BASE, "domain": "t"})
    real_id = real["id"]
    lookup = {real_id: real}
    base = {"id": "pending00001", "tier": "staging", "summary": NEAR, "related_ids": []}

    bad_id = dict(base, duplicate_candidate={"existing_id": _FORGED_ID, "similarity": 0.97})
    assert dedup_review.card_lines("lesson", bad_id, lookup) == []
    assert dedup_review.candidate_message(bad_id["duplicate_candidate"]) == ""

    bad_score = dict(base, related_ids=[real_id],
                     duplicate_candidate={"existing_id": real_id, "similarity": "n/a"})
    lines = dedup_review.card_lines("lesson", bad_score, lookup)
    assert "(similarity unknown)" in lines[0]
    assert dedup_review.safe_similarity(7) == 1.0 and dedup_review.safe_similarity(-1) == 0.0
    assert dedup_review.safe_similarity(float("nan")) is None
    assert dedup_review.safe_similarity(True) is None

    unlinked_note = dict(base, _dedup_note=f"related to {real_id} (sim=99%)")
    assert dedup_review.card_lines("lesson", unlinked_note, lookup) == []

    approved = dict(base, tier="verified", related_ids=[real_id],
                    duplicate_candidate={"existing_id": real_id, "similarity": 0.97})
    assert dedup_review.card_lines("lesson", approved, lookup) == []
    assert dedup_review.pending_candidate(approved) is None


def test_decision_exact_key_prefers_question_and_separates_fields(eng):
    first = eng.add_decision({"title": "Shared title", "question": "Use the cache for builds", "choice": "yes"})

    other_question = eng.add_decision({"title": "Shared title", "question": "Use the cache for tests",
                                       "choice": "yes"})
    assert other_question.get("status") != "duplicate"

    split_a = eng.add_decision({"question": "deploy on friday afternoons", "choice": "never"})
    split_b = eng.add_decision({"question": "deploy on friday", "choice": "afternoons never"})
    assert split_a.get("status") != "duplicate" and split_b.get("status") != "duplicate"

    title_only = eng.add_decision({"title": "Use the cache for builds", "choice": "yes"})
    assert title_only["status"] == "duplicate" and title_only["existing_id"] == first["id"]


def test_rejection_tombstones_use_the_same_claim_and_old_records_still_refuse(eng, monkeypatch):
    from piia_engram import tombstones
    from piia_engram.staging_review import batch_review_staging

    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    row = eng.add_decision({"title": "Adopt nightly exports", "choice": "yes"})
    batch_review_staging(eng, [{"id": row["id"], "action": "reject"}], dry_run=False, confirm=True)
    (stone,) = tombstones.load(eng.root)
    assert stone["hv"] == tombstones.HASH_VERSION == 3

    again = eng.add_decision({"title": "adopt nightly exports.", "choice": "Yes"})
    assert again["status"] == "rejected_before"

    # a record written before this change (v2 hashing) still refuses its claim
    legacy_row = {"question": "keep the legacy gate", "choice": "yes"}
    h1, h2 = tombstones._hashes_v2(tombstones.claim_text("decision", legacy_row))
    path = Path(eng.root) / "knowledge" / "tombstones.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "legacy000001", "kind": "decision", "scope": "global",
                             "h1": h1, "h2": h2, "hv": 2}) + "\n")
    legacy = eng.add_decision({"question": "keep the legacy gate", "choice": "yes"})
    assert legacy["status"] == "rejected_before"
    assert tombstones.stale_version_ids(eng.root) == []


def test_candidate_hold_clears_preapproval_fields(eng):
    eng.add_lesson({"summary": BASE, "domain": "release"})

    second = eng.add_lesson({"summary": NEAR, "domain": "release", "user_confirmed": True,
                             "promotion_reason": "agent says fine", "promoted_at": "2026-01-01",
                             "approval_note": "pre-approved"})

    assert second["tier"] == "staging" and second["approval_status"] == "pending"
    for key in ("user_confirmed", "promotion_reason", "promoted_at", "approval_note"):
        assert key not in second


def test_approval_clears_the_candidate(eng):
    from piia_engram.staging_review import batch_review_staging

    eng.add_lesson({"summary": BASE, "domain": "release"})
    by_update = eng.add_lesson({"summary": NEAR, "domain": "release"})
    eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})
    by_review = eng.add_decision({"question": OPPOSITE_B, "choice": "yes"})
    assert by_update.get("duplicate_candidate") and by_review.get("duplicate_candidate")

    eng.update_knowledge(by_update["id"], {"tier": "verified"})
    batch_review_staging(eng, [{"id": by_review["id"], "action": "approve"}], dry_run=False, confirm=True)

    rows = {r["id"]: r for r in _lessons(eng) + _decisions(eng)}
    for item_id in (by_update["id"], by_review["id"]):
        assert rows[item_id]["tier"] == "verified"
        assert "duplicate_candidate" not in rows[item_id]


def test_identical_after_case_punctuation_whitespace_is_refused_even_when_bigrams_differ(eng):
    first = eng.add_lesson({"summary": "Note: re-run CI", "domain": "t"})
    assert eng._bigram_similarity("Note: re-run CI", "rerun   ci") < 0.55

    again = eng.add_lesson({"summary": "rerun   ci", "domain": "t"})

    assert again["status"] == "duplicate" and again["existing_id"] == first["id"]


def test_claim_hashes_are_cached_by_content():
    from piia_engram import tombstones

    row = {"summary": "a cached claim for the hash cache"}
    tombstones.claim_hashes("lesson", row)
    before = tombstones._hashes_v3.cache_info().hits
    tombstones.claim_hashes("lesson", dict(row))
    assert tombstones._hashes_v3.cache_info().hits == before + 1


# ---------------------------------------------------------------------------
# claim hash versions, archived twins, and candidate markers through approval
# ---------------------------------------------------------------------------

# Fixed (h1, h2) per claim: any change to the separator or to normalize()
# must show up here.
_HASH_CASES = {
    "lesson": ("lesson", {"summary": "Lesson: Run the migrations before deploying the API."}),
    "decision": ("decision", {"question": "Which database for the cache?", "choice": "SQLite"}),
    "playbook": ("playbook", {"title": "Release flow", "steps": [{"action": "Run tests"}, "Tag the build"]}),
    "cjk": ("lesson", {"summary": "教训：先备份再删除原文件，确认备份可读。"}),
}
_EXPECTED_V3 = {
    "lesson": ("888cebcb1104a8ab1f33acdec8bb3b92471bb5d92b8147c2eefb39c47aa170a8",
               "5ec8084306c6af66d280522b15c48a10072a112d10b223a164f5064793c30025"),
    "decision": ("5b9978cfed1462c66fac79209a2065b8b4a7d249711b69e34ea45f3c2024bef9",
                 "76f9989c821180e3fc57bd255cec1693658d5b550b65cf907031aba55a7bb082"),
    "playbook": ("8fbae061b77bdc670a355173c52abc282b0c0c88b595c2b2735b55f1309347ec",
                 "6612a93dba90d759fe6d3ca6524ba7d2299cee7a9ac8e580f3da077eba80eec4"),
    "cjk": ("034a630aa50303f76dc3df2158f8902add823b8398142985ad99833fae6de8ea",
            "cab7f3bb344c0ab9e257856e11084e8eadf1726c8ca23d42c9a14514dbe3b4b7"),
}
_EXPECTED_V2 = {
    "lesson": _EXPECTED_V3["lesson"],
    "decision": ("25ff629bb1307ff2a40179ade799b179d7c3cc8aaf8bb8df8da90446a76a293c",
                 "76f9989c821180e3fc57bd255cec1693658d5b550b65cf907031aba55a7bb082"),
    "playbook": ("60c97b65ef12e6e1e1e24b3036a33e081fde1a36b4ea9f7ae1637f6dc878963a",
                 "6612a93dba90d759fe6d3ca6524ba7d2299cee7a9ac8e580f3da077eba80eec4"),
    "cjk": _EXPECTED_V3["cjk"],
}


@pytest.mark.parametrize("name", sorted(_HASH_CASES))
def test_claim_hashes_are_pinned_per_version(name):
    from piia_engram import tombstones

    kind, row = _HASH_CASES[name]
    assert tombstones._FIELD_SEP == chr(0x1F)
    assert tombstones.claim_hashes_for_version(kind, row, 3) == _EXPECTED_V3[name]
    assert tombstones.claim_hashes_for_version(kind, row, 2) == _EXPECTED_V2[name]


def _write_tombstone(root, kind: str, row: dict, *, version: int, item_id: str) -> None:
    from piia_engram import tombstones

    if version == 2:
        h1, h2 = tombstones._hashes_v2(tombstones.claim_text(kind, row))
    else:
        h1, h2 = tombstones.claim_hashes(kind, row)
    path = Path(root) / "knowledge" / "tombstones.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": item_id, "kind": kind, "scope": "global",
                             "h1": h1, "h2": h2, "hv": version}) + "\n")


def test_v2_record_of_a_question_less_decision_refuses_nothing(eng):
    from piia_engram import tombstones

    rejected = eng.add_decision({"title": "Adopt nightly exports", "choice": "yes"})
    eng.update_decision(rejected["id"], {"status": "outdated"})  # the retired original
    _write_tombstone(eng.root, "decision", rejected, version=2, item_id=rejected["id"])
    assert tombstones.claim_hashes_for_version("decision", {"title": "x", "choice": "yes"}, 2) is None

    other = eng.add_decision({"title": "Use tabs not spaces", "choice": "yes"})
    assert other.get("status") not in ("rejected_before", "duplicate", "duplicate_retired")

    again = eng.add_decision({"title": "Adopt nightly exports", "choice": "yes"})
    assert again["status"] == "duplicate_retired" and again["existing_id"] == rejected["id"]


def test_v2_records_for_lessons_and_playbooks_still_refuse(eng):
    _write_tombstone(eng.root, "lesson", {"summary": "Never force-push to main"}, version=2, item_id="oldlesson001")
    pb = {"title": "Rotate the signing key", "steps": [{"action": "Revoke the old key"}, "Publish the new key"]}
    _write_tombstone(eng.root, "playbook", pb, version=2, item_id="oldplaybook1")

    lesson = eng.add_lesson({"summary": "NEVER force-push to main.", "domain": "t"})
    playbook = eng.add_playbook(dict(pb, title="rotate the signing key"))

    assert lesson["status"] == "rejected_before" and lesson["rejection_id"] == "oldlesson001"
    assert playbook["status"] == "rejected_before" and playbook["rejection_id"] == "oldplaybook1"


def _archive_rows(eng, kind: str, rows: list[dict]) -> None:
    from piia_engram.storage import _append_jsonl_lines

    _append_jsonl_lines(eng._overflow_archive_path(kind), [json.dumps(r) for r in rows])


def test_archived_twin_uses_the_claim_hash(eng):
    _archive_rows(eng, "lesson", [{"id": "L-arch", "summary": "Re-run CI", "domain": "t"}])
    _archive_rows(eng, "decision", [{"id": "D-arch", "title": "Shared title", "question": "Cache builds?",
                                     "choice": "yes"}])

    lesson = eng.add_lesson({"summary": "rerun ci", "domain": "t", "tier": "staging"})
    decision = eng.add_decision({"title": "Shared title", "question": "Cache tests?", "choice": "yes",
                                 "tier": "staging"})

    assert lesson["status"] == "duplicate" and lesson["existing_id"] == "L-arch"
    assert lesson.get("in_overflow_archive") is True
    assert decision.get("status") != "duplicate"


def test_candidate_heading_stays_when_the_link_moved(eng):
    real = eng.add_lesson({"summary": BASE, "domain": "t"})
    row = {"id": "pending00002", "tier": "staging", "summary": NEAR, "related_ids": ["someoneelse1"],
           "duplicate_candidate": {"existing_id": real["id"], "similarity": 0.97}}

    lines = dedup_review.card_lines("lesson", row, {real["id"]: real})

    assert lines and lines[0].startswith(f"- possible duplicate of `{real['id']}`")
    assert not any("difference" in line for line in lines)


def test_candidate_marker_is_not_written_on_an_inactive_row(eng):
    eng.add_lesson({"summary": BASE, "domain": "t"})

    archived = eng.add_lesson({"summary": NEAR, "domain": "t", "status": "outdated"})

    assert "duplicate_candidate" not in archived


def test_decision_tier_update_clears_the_candidate(eng):
    eng.add_decision({"question": OPPOSITE_A, "choice": "yes"})
    second = eng.add_decision({"question": OPPOSITE_B, "choice": "yes"})
    assert second.get("duplicate_candidate")

    eng.update_knowledge(second["id"], {"tier": "verified"})

    row = next(r for r in _decisions(eng) if r["id"] == second["id"])
    assert row["tier"] == "verified" and "duplicate_candidate" not in row


def test_onboard_accept_clears_the_candidate(eng):
    eng.add_lesson({"summary": BASE, "domain": "t"})
    second = eng.add_lesson({"summary": NEAR, "domain": "t",
                             "provenance": {"anchor_ref": "file:README.md"}},
                            _allow_internal_provenance=True)
    assert second.get("duplicate_candidate") and second["tier"] == "staging"

    result = eng.accept_onboard_candidate(second["id"])

    assert not result.get("error")
    row = next(r for r in _lessons(eng) if r["id"] == second["id"])
    assert row["tier"] == "verified" and "duplicate_candidate" not in row
