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
