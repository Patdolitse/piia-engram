"""Unit tests for the pure capacity rules (no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from piia_engram import capacity as cap

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
SMALL = cap.Limits(soft_cap=6, hard_cap=8, review_queue_max=3, review_queue_ceiling=5,
                   review_min_stay_days=7, retired_grace_days=30, r_max=3)


def _iso(dt):
    return cap.iso(dt)


def _row(i, tier="verified", status="active", **extra):
    row = {"id": f"r{i}", "tier": tier, "status": status, "created_at": _iso(NOW - timedelta(days=60))}
    row.update(extra)
    return row


def _plan(before, after, limits=SMALL, **ctx):
    return cap.plan_capacity(before, after, kind="lesson", now=NOW, limits=limits,
                             ctx=cap.CapacityContext(**ctx))


def test_pools_cover_every_row():
    assert cap.pool_of(_row(1)) == cap.POOL_V
    assert cap.pool_of(_row(2, tier="staging")) == cap.POOL_Q
    assert cap.pool_of(_row(3, tier="staging", demoted_at="2026-01-01T00:00:00Z")) == cap.POOL_QD
    assert cap.pool_of(_row(4, status="outdated")) == cap.POOL_R
    assert cap.pool_of(_row(5, tier="archived")) == cap.POOL_R
    assert cap.pool_of(_row(6, status="done")) == cap.POOL_R


def test_clamp_time_caps_future_and_bad_values_at_now():
    assert cap.clamp_time("2999-01-01T00:00:00Z", NOW) == _iso(NOW)
    assert cap.clamp_time("not a time", NOW) == _iso(NOW)
    assert cap.clamp_time(None, NOW) == _iso(NOW)
    assert cap.clamp_time("2026-09-01T00:00:00Z", NOW) == "2026-09-01T00:00:00Z"


def test_limits_from_env_reads_and_validates():
    env = {"ENGRAM_REVIEW_QUEUE_MAX": "3", "ENGRAM_REVIEW_QUEUE_CEILING": "5"}
    limits = cap.limits_from_env(env)
    assert (limits.review_queue_max, limits.review_queue_ceiling) == (3, 5)
    assert cap.limits_from_env({"ENGRAM_REVIEW_QUEUE_CEILING": "2"}) == cap.Limits()
    assert cap.limits_from_env({"ENGRAM_CAP_HARD": "x"}) == cap.Limits()


def test_no_pool_change_means_no_capacity_action():
    before = [_row(i) for i in range(10)]
    after = [dict(r, access_count=1) for r in before]
    plan = _plan(before, after)
    assert plan.archive == [] and [r["id"] for r in plan.rows] == [r["id"] for r in after]
    assert "ingested_at" not in plan.rows[0]


def test_verified_rows_are_never_moved():
    before = [_row(i) for i in range(7)]
    after = [dict(r) for r in before] + [_row(7)]
    plan = _plan([dict(r) for r in before], after)
    assert plan.archive == []
    assert len(plan.rows) == 8


def test_v_postcondition_refuses_growth_beyond_hard_cap():
    before = [_row(i) for i in range(8)]
    after = [dict(r) for r in before] + [_row(8)]
    with pytest.raises(cap.CapacityRefused) as err:
        _plan([dict(r) for r in before], after)
    assert err.value.hard_cap == 8 and err.value.verified_active == 9


def test_v_postcondition_allows_non_increasing_changes_and_override():
    before = [_row(i) for i in range(9)]
    after = [dict(r) for r in before]
    after[0]["tier"] = "staging"  # demotion: V -> Qd, the budget does not grow
    plan = _plan([dict(r) for r in before], after)
    assert plan.rows[0]["demoted_at"] == _iso(NOW)
    grown = [dict(r) for r in before] + [_row(9)]
    assert len(_plan([dict(r) for r in before], grown, owner_override=True).rows) == 10


def test_new_rows_get_system_times_and_legacy_rows_are_backfilled():
    before = [_row(1, tier="staging"), _row(2, status="outdated", last_updated="2026-09-01T00:00:00Z")]
    after = [dict(r) for r in before] + [_row(3, tier="staging", created_at="2999-01-01T00:00:00Z")]
    plan = _plan([dict(r) for r in before], after)
    new = plan.rows[2]
    assert new["ingested_at"] == _iso(NOW) and new["queued_at"] == _iso(NOW)
    assert plan.rows[0]["queued_at"] == before[0]["created_at"]
    # retired_at is the latest of the row's own times (last_updated here).
    assert plan.rows[1]["retired_at"] == "2026-09-01T00:00:00Z"


def test_legacy_rejected_and_anchor_demoted_rows_are_backfilled():
    before = [
        _row(1, tier="staging", status="rejected", last_updated="2026-07-01T00:00:00Z"),
        _row(2, tier="staging", provenance={"anchor_status": "invalid"},
             last_updated="2026-07-02T00:00:00Z"),
    ]
    after = [dict(r) for r in before] + [_row(3)]
    plan = _plan([dict(r) for r in before], after)
    assert plan.rows[0]["rejected_at"] == "2026-07-01T00:00:00Z"
    assert plan.rows[1]["demoted_at"] == "2026-07-02T00:00:00Z"
    assert cap.pool_of(plan.rows[1]) == cap.POOL_QD


def test_queue_quota_moves_oldest_rows_past_min_stay_by_category():
    old = _iso(NOW - timedelta(days=30))
    young = _iso(NOW - timedelta(days=1))
    before = [
        _row(1, tier="staging", queued_at=old, ingested_at=old),
        _row(2, tier="staging", queued_at=old, ingested_at=old, approval_reason="capacity"),
        _row(3, tier="staging", queued_at=young, ingested_at=young),
    ]
    after = [dict(r) for r in before] + [_row(4, tier="staging")]
    plan = _plan([dict(r) for r in before], after)
    assert [(r["id"], reason) for r, reason in plan.archive] == [("r2", cap.REASON_QUEUE_QUOTA)]
    assert [r["id"] for r in plan.rows] == ["r1", "r3", "r4"]


def test_rows_inside_min_stay_are_kept_until_the_ceiling():
    young = _iso(NOW - timedelta(days=1))
    before = [_row(i, tier="staging", queued_at=young, ingested_at=young) for i in range(5)]
    after = [dict(r) for r in before] + [_row(5, tier="staging")]
    plan = _plan([dict(r) for r in before], after)
    assert [(r["id"], reason) for r, reason in plan.archive] == [("r5", cap.REASON_QUEUE_FULL)]
    assert plan.placed_ids == ["r5"]
    before4 = [dict(r) for r in before[:4]]
    plan4 = _plan([dict(r) for r in before4], [dict(r) for r in before4] + [_row(5, tier="staging")])
    assert plan4.archive == []


def test_queue_full_can_refuse_instead_of_placing():
    young = _iso(NOW - timedelta(days=1))
    before = [_row(i, tier="staging", queued_at=young, ingested_at=young) for i in range(5)]
    after = [dict(r) for r in before] + [_row(5, tier="staging")]
    with pytest.raises(cap.QueueFull):
        _plan([dict(r) for r in before], after, on_queue_full="refuse")


def test_demoted_rows_are_never_moved_by_the_queue_quota():
    old = _iso(NOW - timedelta(days=30))
    before = [_row(i, tier="staging", demoted_at=old, queued_at=old, ingested_at=old) for i in range(6)]
    after = [dict(r) for r in before] + [_row(6, tier="staging")]
    plan = _plan([dict(r) for r in before], after)
    assert plan.archive == []


def test_retired_rows_move_beyond_r_max_and_after_grace():
    old = _iso(NOW - timedelta(days=60))
    young = _iso(NOW - timedelta(days=1))
    retired = [_row(i, status="outdated", retired_at=old if i < 2 else young, ingested_at=old) for i in range(4)]
    active = [_row(10 + i, ingested_at=old) for i in range(3)]
    before = retired + active
    after = [dict(r) for r in before] + [_row(20)]
    plan = _plan([dict(r) for r in before], after)
    reasons = [(r["id"], reason) for r, reason in plan.archive]
    assert ("r0", cap.REASON_RETIRED_OVERFLOW) in reasons
    assert ("r1", cap.REASON_RETIRED_GRACE) in reasons
    assert all(rid not in {"r2", "r3"} for rid, _ in reasons)


def test_removed_rows_are_archived_as_a_backstop():
    before = [_row(i) for i in range(3)]
    after = [dict(before[0]), dict(before[2])]
    plan = _plan([dict(r) for r in before], after)
    assert [(r["id"], reason) for r, reason in plan.archive] == [("r1", cap.REASON_REMOVED)]


def test_the_caller_names_the_removal_reason_and_extra_archived_rows():
    before = [_row(i) for i in range(3)]
    replaced = dict(before[1])
    after = [dict(before[0]), dict(before[1], summary="new body")]
    plan = _plan([dict(r) for r in before], after, removed_reason=cap.REASON_IMPORT_REPLACE,
                 extra_archive=[(replaced, cap.REASON_IMPORT_REPLACE)])
    assert [(r["id"], reason) for r, reason in plan.archive] == [
        ("r2", cap.REASON_IMPORT_REPLACE), ("r1", cap.REASON_IMPORT_REPLACE)
    ]


def test_extra_archived_rows_are_kept_when_no_pool_changes():
    before = [_row(0)]
    plan = _plan([dict(r) for r in before], [dict(before[0], summary="new body")],
                 extra_archive=[(dict(before[0]), cap.REASON_IMPORT_REPLACE)])
    assert [(r["id"], reason) for r, reason in plan.archive] == [("r0", cap.REASON_IMPORT_REPLACE)]


def test_a_new_snapshot_goes_straight_to_the_archive_and_a_legacy_one_stays():
    legacy = _row(8, tier="archived", status="superseded", snapshot_of="r0")
    before = [_row(0), legacy]
    head = dict(before[0], summary="edited")
    snapshot = _row(9, tier="archived", status="superseded", snapshot_of="r0")
    plan = _plan([dict(r) for r in before], [head, dict(legacy), snapshot])
    assert [(r["id"], reason) for r, reason in plan.archive] == [("r9", cap.REASON_SNAPSHOT)]
    assert [r["id"] for r in plan.rows] == ["r0", "r8"]


def test_a_row_entering_v_hands_over_its_pending_supersede():
    before = [_row(0, tier="staging", pending_supersedes="r5"), _row(1, tier="staging", pending_supersedes="r6")]
    after = [dict(before[0], tier="verified"), dict(before[1])]
    plan = _plan([dict(r) for r in before], after)
    assert plan.promoted_supersedes == [("r0", "r5")]
    assert "pending_supersedes" not in plan.rows[0]
    assert plan.rows[1]["pending_supersedes"] == "r6"


def test_supersede_target_and_changed_rows_are_exempt():
    old = _iso(NOW - timedelta(days=30))
    before = [_row(i, tier="staging", queued_at=old, ingested_at=old) for i in range(3)]
    after = [dict(r) for r in before] + [_row(3, tier="staging")]
    plan = _plan([dict(r) for r in before], after, supersede_target="r0")
    assert [r["id"] for r, _ in plan.archive] == ["r1"]


def test_import_mode_rows_compete_and_keep_their_clamped_times():
    old = _iso(NOW - timedelta(days=90))
    local_young = _iso(NOW - timedelta(days=1))
    before = [_row(i, tier="staging", queued_at=local_young, ingested_at=local_young) for i in range(3)]
    imported = [_row(10 + i, tier="staging", created_at=old) for i in range(2)]
    after = [dict(r) for r in before] + imported
    plan = _plan([dict(r) for r in before], after, import_mode=True)
    assert sorted(r["id"] for r, _ in plan.archive) == ["r10", "r11"]


def test_stable_order_after_moves():
    old = _iso(NOW - timedelta(days=30))
    before = [_row(0), _row(1, tier="staging", queued_at=old, ingested_at=old), _row(2),
              _row(3, tier="staging", queued_at=old, ingested_at=old),
              _row(4, tier="staging", queued_at=old, ingested_at=old)]
    after = [dict(r) for r in before] + [_row(5, tier="staging")]
    plan = _plan([dict(r) for r in before], after)
    assert [r["id"] for r in plan.rows] == ["r0", "r2", "r3", "r4", "r5"]
