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

from copy import deepcopy
from datetime import datetime, timezone
import math
from typing import Any

# --- freshness thresholds (days) -------------------------------------------

FRESH_MAX_DAYS = 30.0
AGING_MAX_DAYS = 90.0
_FUTURE_SKEW_TOLERANCE_SECONDS = 120.0

FRESH = "fresh"
AGING = "aging"
STALE = "stale"
UNKNOWN = "unknown"

BASIS_NONE = "none"

SOURCE_HUMAN = "human"
SOURCE_AGENT = "agent"
SOURCE_SIGNAL = "signal"
SOURCE_ANCHOR = "anchor"
SOURCE_UNKNOWN = "unknown"

DECAY_POLICY_TIME = "time"
DECAY_POLICY_TRIGGER = "trigger"

# Priority order of timestamp fields used to measure age.
FRESHNESS_BASES = ("last_validated_at", "last_reviewed", "created_at", "timestamp")

# Optional provenance identifier fields and their caps.
_MAX_ID_LEN = 120
_CONFIRMATION_SOURCE_MAP = {
    "human": SOURCE_HUMAN,
    "test_signal": SOURCE_SIGNAL,
    "anchor": SOURCE_ANCHOR,
}
_HUMAN_SOURCE_AGENTS = {"owner", "human", "user", "manual", "self"}
_METADATA_SUBTREES = ("provenance", "labeling")

DEFAULT_FRESHNESS_POLICIES = {
    SOURCE_HUMAN: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
    SOURCE_AGENT: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
    SOURCE_SIGNAL: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TRIGGER),
    SOURCE_ANCHOR: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TRIGGER),
    SOURCE_UNKNOWN: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
}


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
    """Return a safe short identifier; reject free text and path shapes."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_ID_LEN:
        return None
    # Identifiers are not free text or filesystem-ish paths.
    if "\n" in text or "\r" in text or "\\" in text:
        return None
    if text in {".", "..", "~"} or text.startswith(("..", "~", "/", ":")):
        return None
    if text.endswith(("/", ":")):
        return None
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return None
    segments = text.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    if ":" in text:
        namespace, rest = text.split(":", 1)
        if not namespace or not rest:
            return None
        if "/" not in text and namespace != "github":
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


def _is_verified_active(entry: dict[str, Any]) -> bool:
    """Whether entry-carried metadata says this item is verified and active."""
    return entry.get("tier") == "verified" and entry.get("status") == "active"


def classify_freshness_source(entry: dict[str, Any]) -> str:
    """Classify the metadata source that should drive freshness policy.

    Explicit ``provenance.confirmation_source`` wins for the supported enum.
    Legacy owner/user/manual/self source-agent names count as human only once
    the entry itself is already verified and active. Test signals and anchors
    are never inferred from agent names.
    """
    if not isinstance(entry, dict):
        return SOURCE_UNKNOWN

    prov = entry.get("provenance")
    if isinstance(prov, dict):
        raw_confirmation = prov.get("confirmation_source")
        if isinstance(raw_confirmation, str):
            confirmation = raw_confirmation.strip().lower()
            mapped = _CONFIRMATION_SOURCE_MAP.get(confirmation)
            if mapped:
                return mapped

    source_agent = resolve_source_agent(entry).strip().lower()
    if source_agent in _HUMAN_SOURCE_AGENTS and _is_verified_active(entry):
        return SOURCE_HUMAN
    if source_agent:
        return SOURCE_AGENT
    return SOURCE_UNKNOWN


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
    return None, BASIS_NONE


def _status_for_age(age_days: float, fresh_days: float, aging_days: float) -> str:
    if age_days <= fresh_days:
        return FRESH
    if age_days <= aging_days:
        return AGING
    return STALE


def _valid_thresholds(fresh_days: Any, aging_days: Any) -> tuple[float, float] | None:
    if isinstance(fresh_days, bool) or isinstance(aging_days, bool):
        return None
    if not isinstance(fresh_days, (int, float)) or not isinstance(aging_days, (int, float)):
        return None
    fresh = float(fresh_days)
    aging = float(aging_days)
    if not math.isfinite(fresh) or not math.isfinite(aging):
        return None
    if fresh < 0 or aging < fresh:
        return None
    return fresh, aging


def _resolve_policy(
    source_class: str, policies: dict[str, Any] | None
) -> tuple[float, float, str]:
    default = DEFAULT_FRESHNESS_POLICIES.get(
        source_class, DEFAULT_FRESHNESS_POLICIES[SOURCE_UNKNOWN]
    )
    if not isinstance(policies, dict):
        return default

    raw = policies.get(source_class)
    if raw is None:
        raw = policies.get("default")
    if raw is None:
        return default

    fresh_days: Any
    aging_days: Any
    decay_policy: Any
    if isinstance(raw, dict):
        fresh_days = raw.get("fresh_days", raw.get("fresh", default[0]))
        aging_days = raw.get("aging_days", raw.get("aging", default[1]))
        decay_policy = raw.get("decay_policy", raw.get("policy", default[2]))
    elif isinstance(raw, (tuple, list)) and len(raw) == 3:
        fresh_days, aging_days, decay_policy = raw
    elif isinstance(raw, (tuple, list)) and len(raw) == 2:
        first, second = raw
        if isinstance(first, (tuple, list)) and len(first) == 2:
            fresh_days, aging_days = first
            decay_policy = second
        else:
            fresh_days, aging_days = first, second
            decay_policy = default[2]
    else:
        return default

    thresholds = _valid_thresholds(fresh_days, aging_days)
    if thresholds is None:
        return default
    fresh, aging = thresholds

    if decay_policy not in {DECAY_POLICY_TIME, DECAY_POLICY_TRIGGER}:
        decay_policy = default[2]
    return fresh, aging, decay_policy


def _anchor_is_valid(entry: dict[str, Any]) -> bool:
    prov = entry.get("provenance") if isinstance(entry, dict) else None
    if not isinstance(prov, dict):
        return False
    status = prov.get("anchor_status")
    return isinstance(status, str) and status.strip().lower() == "valid"


def _decay_policy_for_entry(
    entry: dict[str, Any],
    source_class: str,
    policies: dict[str, Any] | None,
) -> tuple[float, float, str, bool]:
    fresh_days, aging_days, decay_policy = _resolve_policy(source_class, policies)
    if source_class == SOURCE_ANCHOR and not _anchor_is_valid(entry):
        decay_policy = DECAY_POLICY_TIME
    skip_decay = decay_policy == DECAY_POLICY_TRIGGER
    return fresh_days, aging_days, decay_policy, skip_decay


def compute_freshness(
    entry: dict[str, Any],
    now: datetime | None = None,
    *,
    policies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a freshness annotation for an entry. Pure; reads nothing else.

    Returns the v1 fields (freshness_status, age_days, basis, as_of) plus
    additive source-aware v2 fields. ``policies`` may override per-source
    ``(fresh_days, aging_days, decay_policy)`` tuples without reading any store.
    An entry with no parseable timestamp yields status ``unknown`` (never raises).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    safe_entry = entry if isinstance(entry, dict) else {}
    source_class = classify_freshness_source(safe_entry)
    fresh_days, aging_days, decay_policy, skip_decay = _decay_policy_for_entry(
        safe_entry, source_class, policies
    )

    ts, basis = _best_timestamp(safe_entry)
    common = {
        "basis": basis,
        "as_of": now.isoformat(),
        "source_class": source_class,
        "decay_policy": decay_policy,
        "skip_decay": skip_decay,
        "clock_skewed": False,
    }
    if ts is None:
        return {
            "freshness_status": UNKNOWN,
            "age_days": None,
            "temporal_status": UNKNOWN,
            **common,
        }

    raw_age_seconds = (now - ts).total_seconds()
    if raw_age_seconds < 0 and abs(raw_age_seconds) > _FUTURE_SKEW_TOLERANCE_SECONDS:
        return {
            "freshness_status": UNKNOWN,
            "age_days": None,
            "temporal_status": UNKNOWN,
            "basis": basis,
            "as_of": now.isoformat(),
            "source_class": source_class,
            "decay_policy": decay_policy,
            "skip_decay": skip_decay,
            "clock_skewed": True,
            "skew_days": round(abs(raw_age_seconds) / 86400.0, 1),
            "reason": "timestamp_in_future",
        }
    raw_age_days = max(0.0, raw_age_seconds / 86400.0)

    age_days = round(raw_age_days, 1)
    temporal_status = _status_for_age(raw_age_days, fresh_days, aging_days)

    return {
        "freshness_status": temporal_status,
        "age_days": age_days,
        "temporal_status": temporal_status,
        **common,
    }


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
        copy = deepcopy(item)
        copy["freshness"] = compute_freshness(item, now=now)
        annotated.append(copy)
    return annotated
