import json


SECRET = "ZZ_STAGING_REVIEW_SECRET"


def _eng(tmp_path):
    from piia_engram.core import Engram

    return Engram(root=tmp_path)


def _lessons(eng):
    return eng.get_lessons(limit=None, _update_access=False)


def _decisions(eng):
    return eng.get_decisions(limit=None, _update_access=False)


def _all_decisions(tmp_path):
    path = tmp_path / "knowledge" / "decisions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def test_batch_review_dry_run_is_default_and_metadata_only(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    lesson = eng.add_lesson(f"approve candidate {SECRET}", tier="staging")

    payload = batch_review_staging(
        eng,
        [{"id": lesson["id"], "action": "approve"}],
    )

    assert payload["status"] == "dry_run"
    assert payload["changed"] is False
    assert payload["counts"]["planned"] == 1
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
    assert _lessons(eng)[0]["tier"] == "staging"


def test_batch_review_apply_requires_confirmation(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    lesson = eng.add_lesson("needs confirm", tier="staging")

    payload = batch_review_staging(
        eng,
        [{"id": lesson["id"], "action": "approve"}],
        dry_run=False,
        confirm=False,
    )

    assert payload["requires_confirmation"] is True
    assert payload["changed"] is False
    assert payload["items"][0]["status"] == "pending_confirmation"
    assert _lessons(eng)[0]["tier"] == "staging"


def test_batch_review_confirmed_approve_and_reject(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    lesson = eng.add_lesson("approve this staging lesson", tier="staging")
    decision = eng.add_decision(
        "reject this staging decision",
        choice="yes",
        reasoning=SECRET,
        tier="staging",
    )

    payload = batch_review_staging(
        eng,
        [
            {"id": lesson["id"], "action": "approve"},
            {"id": decision["id"], "action": "reject"},
        ],
        dry_run=False,
        confirm=True,
    )

    assert payload["changed"] is True
    assert payload["counts"]["applied"] == 2
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
    assert _lessons(eng)[0]["tier"] == "verified"
    assert _all_decisions(tmp_path)[0]["status"] == "outdated"


def test_batch_review_non_staging_is_noop(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    lesson = eng.add_lesson("already verified", tier="verified")

    payload = batch_review_staging(
        eng,
        [{"id": lesson["id"], "action": "approve"}],
        dry_run=False,
        confirm=True,
    )

    assert payload["changed"] is False
    assert payload["items"][0]["status"] == "not_staging"
    assert _lessons(eng)[0]["tier"] == "verified"


def test_list_pending_filters_and_prioritizes_metadata_only(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    low = eng.add_lesson(
        {"summary": f"low priority {SECRET}", "domain": "python", "tier": "staging"}
    )
    high = eng.add_decision(
        f"high priority {SECRET}",
        choice="yes",
        domain="release",
        tier="staging",
    )
    # Make the decision look urgent without relying on body text.
    decisions_path = tmp_path / "knowledge" / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    for item in decisions:
        if item["id"] == high["id"]:
            item["promotion_suggested"] = True
            item["access_count"] = 7
    decisions_path.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")

    payload = batch_review_staging(
        eng,
        [],
        operation="list_pending",
        filters={"domain": "release"},
        limit=10,
    )

    assert payload["status"] == "listed"
    assert payload["changed"] is False
    assert payload["counts"]["listed"] == 1
    assert payload["items"][0]["id"] == high["id"]
    assert payload["items"][0]["type"] == "decision"
    assert payload["items"][0]["domain"] == "release"
    assert payload["items"][0]["priority"] > 0
    assert "promotion_suggested" in payload["items"][0]["priority_reasons"]
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
    assert low["id"] not in json.dumps(payload, ensure_ascii=False)


def test_list_pending_limit_and_type_filter(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    eng.add_lesson("lesson candidate", domain="testing", tier="staging")
    decision = eng.add_decision(
        "decision candidate",
        choice="yes",
        domain="testing",
        tier="staging",
    )

    payload = batch_review_staging(
        eng,
        [],
        operation="list_pending",
        filters={"type": "decision"},
        limit=1,
    )

    assert payload["counts"]["total_pending"] == 2
    assert payload["counts"]["listed"] == 1
    assert payload["items"][0]["id"] == decision["id"]
    assert payload["items"][0]["type"] == "decision"


def test_list_pending_includes_labeling_metadata_only(tmp_path):
    from piia_engram.staging_review import batch_review_staging

    eng = _eng(tmp_path)
    lesson = eng.add_lesson({
        "summary": f"label me {SECRET}",
        "domain": "review",
        "source_tool": "codex",
        "tier": "staging",
    })

    payload = batch_review_staging(
        eng,
        [],
        operation="list_pending",
        limit=10,
    )

    row = next(item for item in payload["items"] if item["id"] == lesson["id"])
    assert row["labeling"]["source_kind"] == "agent"
    assert row["labeling"]["validation_state"] == "needs_review"
    assert "needs_owner_review" in row["labeling"]["signals"]
    assert SECRET not in json.dumps(payload, ensure_ascii=False)
