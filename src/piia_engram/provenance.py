"""Provenance & Freshness Contract v1 — additive metadata helpers.

This module implements the *pure, non-destructive* core of the Provenance &
Freshness Contract (see ``docs/specs/provenance-freshness-contract-v1.md``):

- ``normalize_provenance_fields`` validates the optional provenance fields
  (``source_agent``, ``run_id``, ``last_validated_at``) coming in on a new
  entry, dropping anything malformed.
- ``compute_freshness`` derives a freshness annotation from whatever timestamps
  an entry already has, without storing anything.
- ``annotate_freshness`` returns copies of entries with a ``freshness`` key
  added — it never mutates the caller's dicts.
- ``resolve_source_agent`` answers "who produced this" with a backward-compatible
  fallback to ``source_tool``.

Everything here is deliberately import-light (stdlib only) and side-effect free
so it is safe to call from the read path. It does **not** change any existing
storage or tool behavior on its own; wiring is a separate, reviewed step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# --- freshness thresholds (days) -------------------------------------------

FRESH_MAX_DAYS = 30.0
AGING_MAX_DAYS = 90.0

FRESH = "fresh"
AGING = "aging"
STALE = "stale"
UNKNOWN = "unknown"
TRIGGER_BOUND = "trigger_bound"

_TRIGGER_BOUND_VALIDATION_KINDS = {"test", "tests", "ci", "check", "anchor"}
_HUMAN_VALIDATION_KINDS = {"human", "user", "manual"}

# Priority order of timestamp fields used to measure age.
FRESHNESS_BASES = ("last_validated_at", "last_reviewed", "created_at", "timestamp")

# Optional provenance identifier fields and their caps.
_MAX_ID_LEN = 120


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_identifier(value: Any) -> str | None:
    """Return a safe short identifier, or None if it looks like content/path."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_ID_LEN:
        return None
    # Identifiers are not free text or paths.
    if "\n" in text or "\r" in text:
        return None
    return text


def normalize_provenance_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the optional provenance fields from an incoming entry/kwargs.

    Returns a dict containing only the fields that are present AND valid. Callers
    can merge this into an entry's ``provenance`` object. Malformed values are
    dropped rather than raised, so this never blocks a write.
    """
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out

    source_agent = _clean_identifier(raw.get("source_agent"))
    if source_agent:
        out["source_agent"] = source_agent

    run_id = _clean_identifier(raw.get("run_id"))
    if run_id:
        out["run_id"] = run_id

    # last_validated_at must be a parseable ISO timestamp; store the normalized
    # UTC ISO form so downstream comparisons are consistent.
    validated = _parse_iso(raw.get("last_validated_at"))
    if validated is not None:
        out["last_validated_at"] = validated.isoformat()

    return out


def resolve_source_agent(entry: dict[str, Any]) -> str:
    """Who produced this entry — provenance.source_agent, else source_tool."""
    if not isinstance(entry, dict):
        return ""
    prov = entry.get("provenance")
    if isinstance(prov, dict):
        agent = _clean_identifier(prov.get("source_agent"))
        if agent:
            return agent
    return _clean_identifier(entry.get("source_tool")) or ""


def _validation_kind(entry: dict[str, Any]) -> str:
    """Return a normalized validation kind, if the entry declares one.

    This is intentionally advisory. Older entries do not have this field and
    keep the previous time-based freshness behavior. Newer entries can mark a
    fact as held by a re-runnable signal (test/check/anchor) so it does not
    become stale only because the wall clock moved.
    """
    if not isinstance(entry, dict):
        return ""
    prov = entry.get("provenance")
    candidates = []
    if isinstance(prov, dict):
        candidates.extend(
            [
                prov.get("validation_kind"),
                prov.get("validation_source"),
                prov.get("confirmed_by"),
            ]
        )
    candidates.extend(
        [
            entry.get("validation_kind"),
            entry.get("validation_source"),
            entry.get("confirmed_by"),
        ]
    )
    for value in candidates:
        cleaned = _clean_identifier(value)
        if cleaned:
            return cleaned.lower().replace("-", "_")
    return ""


def _best_timestamp(entry: dict[str, Any]) -> tuple[datetime | None, str]:
    """Return (datetime, basis) using the documented priority order."""
    prov = entry.get("provenance") if isinstance(entry, dict) else None
    for basis in FRESHNESS_BASES:
        # last_validated_at may live in provenance or top-level.
        candidates = []
        if isinstance(prov, dict) and basis in prov:
            candidates.append(prov.get(basis))
        if isinstance(entry, dict) and basis in entry:
            candidates.append(entry.get(basis))
        for value in candidates:
            parsed = _parse_iso(value)
            if parsed is not None:
                return parsed, basis
    return None, "none"


def compute_freshness(
    entry: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Derive a freshness annotation for an entry. Pure; reads nothing else.

    Returns a dict with freshness_status, age_days (or None), basis, and as_of.
    An entry with no parseable timestamp yields status ``unknown`` (never raises).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    ts, basis = _best_timestamp(entry if isinstance(entry, dict) else {})
    if ts is None:
        return {
            "freshness_status": UNKNOWN,
            "age_days": None,
            "basis": "none",
            "as_of": now.isoformat(),
        }

    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    validation_kind = _validation_kind(entry if isinstance(entry, dict) else {})

    # Facts confirmed by a re-runnable signal should be invalidated by that
    # signal changing/failing, not by a wall-clock TTL. Keep the age visible for
    # observability, but mark the freshness policy as trigger-bound instead of
    # aging/stale. Human/manual confirmations keep the legacy time-decay path.
    if validation_kind in _TRIGGER_BOUND_VALIDATION_KINDS:
        return {
            "freshness_status": TRIGGER_BOUND,
            "age_days": round(age_days, 1),
            "basis": basis,
            "validation_kind": validation_kind,
            "as_of": now.isoformat(),
        }

    if age_days <= FRESH_MAX_DAYS:
        status = FRESH
    elif age_days <= AGING_MAX_DAYS:
        status = AGING
    else:
        status = STALE

    out = {
        "freshness_status": status,
        "age_days": round(age_days, 1),
        "basis": basis,
        "as_of": now.isoformat(),
    }
    if validation_kind in _HUMAN_VALIDATION_KINDS:
        out["validation_kind"] = validation_kind
    return out


def annotate_freshness(
    items: list[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return copies of ``items`` each with a ``freshness`` key added.

    Non-destructive: the input dicts are not mutated. Non-dict items are passed
    through unchanged.
    """
    annotated: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            annotated.append(item)
            continue
        copy = dict(item)
        copy["freshness"] = compute_freshness(item, now=now)
        annotated.append(copy)
    return annotated
