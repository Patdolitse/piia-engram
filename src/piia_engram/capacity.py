"""Capacity rules for the lessons and decisions files.

Pure logic, no I/O. ``Engram._update_entries`` calls :func:`plan_capacity` with
the rows before and after a mutation, inside the knowledge write lock, and
gets back the rows to keep, the rows to move to the overflow archive (each
with a reason) and the ids of new rows that went straight to the archive.

Pools (counted per kind):

- ``V``  verified and active. Never moved by capacity. Together with ``Qd``
  it may not grow beyond ``hard_cap``.
- ``Q``  staging and active: the review queue. A soft quota, a minimum stay
  before a row can be moved, and a ceiling.
- ``Qd`` staging and active and demoted from V (``demoted_at`` is set). Never
  moved; counts against the V budget.
- ``R``  everything else (retired, rejected, archived tier, snapshots, unknown
  status). Moved beyond ``r_max`` or after a grace period.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

POOL_V = "V"
POOL_Q = "Q"
POOL_QD = "Qd"
POOL_R = "R"

# Written only by the capacity core; callers can never set them.
SYSTEM_FIELDS = (
    "ingested_at",
    "queued_at",
    "retired_at",
    "demoted_at",
    "rejected_at",
    "approval_reason",
    "pending_supersedes",
)

# Status values a caller may set through an update.
UPDATABLE_STATUSES = frozenset({"active", "outdated", "rejected"})

REASON_QUEUE_QUOTA = "review_queue_quota"
REASON_QUEUE_FULL = "review_queue_full"
REASON_RETIRED_OVERFLOW = "retired_overflow"
REASON_RETIRED_GRACE = "retired_grace"
REASON_REMOVED = "removed"
REASON_SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class Limits:
    soft_cap: int = 200
    hard_cap: int = 1000
    review_queue_max: int = 100
    review_queue_ceiling: int = 200
    review_min_stay_days: int = 7
    retired_grace_days: int = 30
    r_max: int = 100


_ENV_LIMITS = {
    "soft_cap": "ENGRAM_CAP_SOFT",
    "hard_cap": "ENGRAM_CAP_HARD",
    "review_queue_max": "ENGRAM_REVIEW_QUEUE_MAX",
    "review_queue_ceiling": "ENGRAM_REVIEW_QUEUE_CEILING",
    "review_min_stay_days": "ENGRAM_REVIEW_MIN_STAY_DAYS",
    "retired_grace_days": "ENGRAM_RETIRED_GRACE_DAYS",
    "r_max": "ENGRAM_RETIRED_MAX",
}


def limits_are_valid(limits: Limits) -> bool:
    return (
        limits.hard_cap >= limits.soft_cap > 0
        and limits.review_queue_ceiling >= limits.review_queue_max > 0
        and limits.r_max > 0
        and limits.review_min_stay_days >= 0
        and limits.retired_grace_days >= 0
    )


def limits_from_env(env: Mapping[str, str] | None = None) -> Limits:
    """Limits from the environment; the defaults when any value is invalid."""
    source = os.environ if env is None else env
    values: dict[str, int] = {}
    for name, var in _ENV_LIMITS.items():
        raw = str(source.get(var, "") or "").strip()
        if not raw:
            continue
        try:
            values[name] = int(raw)
        except ValueError:
            return Limits()
    limits = replace(Limits(), **values)
    return limits if limits_are_valid(limits) else Limits()


def pool_of(row: Mapping[str, Any]) -> str:
    status = row.get("status") or "active"
    tier = row.get("tier")
    if status == "active" and tier == "verified":
        return POOL_V
    if status == "active" and tier == "staging":
        return POOL_QD if row.get("demoted_at") else POOL_Q
    return POOL_R


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp_time(value: Any, now: datetime) -> str:
    """``value`` as an ISO time, never later than ``now``; ``now`` when unparsable."""
    parsed = parse_time(value)
    if parsed is None or parsed > now:
        return iso(now)
    return iso(parsed)


def _latest_time(row: Mapping[str, Any], now: datetime) -> str:
    moments = [
        parse_time(row.get(name))
        for name in ("last_updated", "superseded_at", "archived_at", "created_at")
    ]
    moments = [m for m in moments if m is not None]
    return clamp_time(iso(max(moments)) if moments else None, now)


def queue_category(row: Mapping[str, Any]) -> int:
    """Move order inside the review queue: 1 first, 3 last."""
    if row.get("approval_reason") == "capacity":
        return 1
    if str(row.get("risk_level") or "") == "high":
        return 3
    return 2


@dataclass
class CapacityContext:
    import_mode: bool = False
    on_queue_full: str = "archive"  # "archive" or "refuse"
    supersede_target: str = ""
    owner_override: bool = False
    source_tool: str = ""  # audit attribution only
    # Removed rows the caller archived itself (the positional import split)
    # or is rolling back; the removed-row backstop skips them.
    skip_archive_ids: frozenset = frozenset()


@dataclass
class CapacityPlan:
    rows: list[dict]
    archive: list[tuple[dict, str]] = field(default_factory=list)
    placed_ids: list[str] = field(default_factory=list)
    # (row id, target id): pending supersedes of rows that entered V in this write.
    promoted_supersedes: list[tuple[str, str]] = field(default_factory=list)


class CapacityRefused(Exception):
    """The write would grow the verified pool beyond ``hard_cap``."""

    def __init__(self, kind: str, hard_cap: int, verified_active: int):
        super().__init__(f"{kind}: verified rows would exceed the limit of {hard_cap}")
        self.kind = kind
        self.hard_cap = hard_cap
        self.verified_active = verified_active


class QueueFull(Exception):
    """The review queue is at its ceiling and the caller asked for a refusal."""

    def __init__(self, kind: str, ceiling: int, queued: int):
        super().__init__(f"{kind}: the review queue is full ({queued} of {ceiling})")
        self.kind = kind
        self.ceiling = ceiling
        self.queued = queued


def _keyed(rows: list[dict]) -> list[tuple[tuple[str, int], dict]]:
    """Pair each row with (id, occurrence) so duplicate ids stay distinct."""
    seen: dict[str, int] = {}
    keyed = []
    for row in rows:
        rid = str(row.get("id") or "")
        count = seen.get(rid, 0)
        seen[rid] = count + 1
        keyed.append(((rid, count), row))
    return keyed


def _backfill(row: dict, now: datetime, *, clamp_existing: bool) -> None:
    """Fill system times a row does not have yet (legacy rows, imported rows)."""
    def _set(name: str, fallback: Any) -> None:
        if row.get(name):
            if clamp_existing:
                row[name] = clamp_time(row[name], now)
            return
        row[name] = fallback

    _set("ingested_at", clamp_time(row.get("created_at"), now))
    status = row.get("status") or "active"
    if status == "rejected":
        _set("rejected_at", clamp_time(row.get("last_updated") or row.get("created_at"), now))
    if row.get("tier") == "staging" and status == "active" and not row.get("demoted_at"):
        provenance = row.get("provenance")
        if isinstance(provenance, dict) and provenance.get("anchor_status") == "invalid":
            row["demoted_at"] = clamp_time(
                provenance.get("anchor_checked_at") or row.get("last_updated") or row.get("created_at"),
                now,
            )
    pool = pool_of(row)
    if pool == POOL_Q:
        _set("queued_at", clamp_time(row.get("created_at"), now))
    elif pool == POOL_R:
        _set("retired_at", _latest_time(row, now))


def _stamp_transition(row: dict, old_pool: str | None, now: datetime) -> None:
    """System times for a new row (outside import mode) or a pool change."""
    stamp = iso(now)
    raw_pool = pool_of(row)
    if old_pool is None:
        for name in ("queued_at", "retired_at", "demoted_at", "rejected_at"):
            row.pop(name, None)
        row["ingested_at"] = stamp
        raw_pool = pool_of(row)
    if raw_pool == POOL_V:
        for name in ("demoted_at", "retired_at", "queued_at", "approval_reason"):
            row.pop(name, None)
    elif raw_pool == POOL_Q and old_pool == POOL_V:
        row["demoted_at"] = stamp  # demotion from V: the row becomes Qd
        row.pop("queued_at", None)
    elif raw_pool == POOL_Q:
        row["queued_at"] = stamp
        row.pop("retired_at", None)
    elif raw_pool == POOL_QD:
        row.pop("retired_at", None)
    elif raw_pool == POOL_R:
        row["retired_at"] = stamp


def plan_capacity(
    before: list[dict],
    after: list[dict],
    *,
    kind: str,
    now: datetime,
    limits: Limits,
    ctx: CapacityContext,
) -> CapacityPlan:
    """Decide which rows stay, which move to the archive, and why.

    ``before`` is a private copy of the rows before the mutation and may be
    changed freely. Rows in ``after`` are stamped in place.
    """
    before_keyed = _keyed(before)
    after_keyed = _keyed(after)
    before_map = dict(before_keyed)
    after_keys = {key for key, _ in after_keyed}

    removed = [row for key, row in before_keyed if key not in after_keys]
    raw_change = bool(removed) or any(
        key not in before_map or pool_of(before_map[key]) != pool_of(row)
        for key, row in after_keyed
    )
    if not raw_change:
        return CapacityPlan(rows=after)

    for _, row in before_keyed:
        _backfill(row, now, clamp_existing=False)
    for key, row in after_keyed:
        if key in before_map:
            _backfill(row, now, clamp_existing=False)
        elif ctx.import_mode:
            _backfill(row, now, clamp_existing=True)

    before_pool = {key: pool_of(row) for key, row in before_keyed}
    changed: set[tuple[str, int]] = set()
    for key, row in after_keyed:
        old = before_pool.get(key)
        if old is None and ctx.import_mode:
            changed.add(key)
            continue
        if old is None or old != pool_of(row):
            _stamp_transition(row, old, now)
            changed.add(key)

    # A row entering V hands its pending supersede to the caller, which writes the edge.
    promoted: list[tuple[str, str]] = []
    for key, row in after_keyed:
        old = before_pool.get(key)
        if old not in (None, POOL_V) and pool_of(row) == POOL_V and row.get("pending_supersedes"):
            promoted.append((key[0], str(row.pop("pending_supersedes"))))

    v_before = sum(1 for pool in before_pool.values() if pool in (POOL_V, POOL_QD))
    v_after = sum(1 for _, row in after_keyed if pool_of(row) in (POOL_V, POOL_QD))
    if v_after > limits.hard_cap and v_after > v_before and not ctx.owner_override:
        raise CapacityRefused(kind, limits.hard_cap, v_after)

    exempt = set() if ctx.import_mode else set(changed)
    if ctx.supersede_target:
        exempt |= {key for key, _ in after_keyed if key[0] == ctx.supersede_target}

    indexed = [(index, key, row) for index, (key, row) in enumerate(after_keyed)]
    moves: list[tuple[tuple[str, int], dict, str]] = []
    moved: set[tuple[str, int]] = set()
    placed: list[str] = []

    # A new history snapshot never stays in the active file.
    for _index, key, row in indexed:
        if key not in before_pool and row.get("snapshot_of"):
            moves.append((key, row, REASON_SNAPSHOT))
            moved.add(key)

    def _queued_order(item):
        index, _key, row = item
        return (queue_category(row), parse_time(row.get("queued_at")) or now, index)

    # Review queue (ordinary Q only; Qd is never moved).
    queue = [item for item in indexed if pool_of(item[2]) == POOL_Q]
    excess = len(queue) - limits.review_queue_max
    if excess > 0:
        cutoff = now - timedelta(days=limits.review_min_stay_days)
        eligible = [
            item for item in queue
            if item[1] not in exempt
            and (parse_time(item[2].get("queued_at")) or now) <= cutoff
        ]
        for _index, key, row in sorted(eligible, key=_queued_order)[:excess]:
            moves.append((key, row, REASON_QUEUE_QUOTA))
            moved.add(key)
    remaining = [item for item in queue if item[1] not in moved]
    over = len(remaining) - limits.review_queue_ceiling
    if over > 0:
        incoming = [
            item for item in remaining
            if item[1] not in before_pool or before_pool[item[1]] != POOL_Q
        ]
        if incoming and ctx.on_queue_full == "refuse":
            raise QueueFull(kind, limits.review_queue_ceiling, len(remaining))
        for _index, key, row in sorted(incoming, key=_queued_order)[:over]:
            moves.append((key, row, REASON_QUEUE_FULL))
            moved.add(key)
            placed.append(key[0])

    # Retired rows.
    retired = [item for item in indexed if pool_of(item[2]) == POOL_R and item[1] not in moved]
    movable = sorted(
        (item for item in retired if item[1] not in exempt),
        key=lambda item: (parse_time(item[2].get("retired_at")) or now, item[0]),
    )
    over_r = len(retired) - limits.r_max
    for _index, key, row in movable[:max(over_r, 0)]:
        moves.append((key, row, REASON_RETIRED_OVERFLOW))
        moved.add(key)
    total = len(after_keyed) - len(moved)
    if total > limits.soft_cap:
        grace_cutoff = now - timedelta(days=limits.retired_grace_days)
        for _index, key, row in movable:
            if total <= limits.soft_cap:
                break
            if key in moved:
                continue
            if (parse_time(row.get("retired_at")) or now) <= grace_cutoff:
                moves.append((key, row, REASON_RETIRED_GRACE))
                moved.add(key)
                total -= 1

    archive = [
        (row, REASON_REMOVED) for row in removed
        if str(row.get("id") or "") not in ctx.skip_archive_ids
    ]
    archive += [(row, reason) for _key, row, reason in moves]
    rows = [row for key, row in after_keyed if key not in moved]
    return CapacityPlan(rows=rows, archive=archive, placed_ids=placed, promoted_supersedes=promoted)
