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
import re
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

LABEL_SOURCE_HUMAN = "human"
LABEL_SOURCE_AGENT = "agent"
LABEL_SOURCE_IMPORTED = "imported"
LABEL_SOURCE_UNKNOWN = "unknown"

LABEL_QUALITY_RAW = "raw"
LABEL_QUALITY_PARTIAL = "partial"
LABEL_QUALITY_MATURE = "mature"

LABEL_VALIDATION_UNREVIEWED = "unreviewed"
LABEL_VALIDATION_VALIDATED = "validated"
LABEL_VALIDATION_NEEDS_REVIEW = "needs_review"

CONFIRMATION_SOURCES = frozenset({"human", "test_signal", "anchor"})
ANCHOR_STATUSES = frozenset({"valid", "invalid", "unknown"})
ANCHOR_EVENTS = frozenset({"superseded"})

# Priority order of timestamp fields used to measure age.
FRESHNESS_BASES = ("last_validated_at", "last_reviewed", "created_at", "timestamp")

# Optional provenance identifier fields and their caps.
_MAX_ID_LEN = 120
_MAX_REF_LEN = 240
_MAX_LABEL_SIGNAL_LEN = 80
_CONFIRMATION_SOURCE_MAP = {
    "human": SOURCE_HUMAN,
    "test_signal": SOURCE_SIGNAL,
    "anchor": SOURCE_ANCHOR,
}
_HUMAN_SOURCE_AGENTS = {"owner", "human", "user", "manual", "self"}
_METADATA_SUBTREES = ("provenance", "labeling")
_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*"
    r"(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?$"
)
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9_.@:/-]*$")
_CREDENTIAL_SHAPE_RE = re.compile(
    r"(?:"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|pypi-[A-Za-z0-9_-]{16,}"
    r"|cfut_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)

DEFAULT_FRESHNESS_POLICIES = {
    SOURCE_HUMAN: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
    SOURCE_AGENT: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
    SOURCE_SIGNAL: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TRIGGER),
    SOURCE_ANCHOR: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TRIGGER),
    SOURCE_UNKNOWN: (FRESH_MAX_DAYS, AGING_MAX_DAYS, DECAY_POLICY_TIME),
}


def _looks_credential_shaped(text: str) -> bool:
    return bool(_CREDENTIAL_SHAPE_RE.search(text))


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
    if _looks_credential_shaped(text):
        return None
    if not _IDENTIFIER_RE.fullmatch(text):
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


def _has_unsafe_ref_shape(text: str, *, max_len: int = _MAX_REF_LEN) -> bool:
    """Reject references that look like paths, credentials, or free text."""
    if not text or len(text) > max_len:
        return True
    if "\n" in text or "\r" in text or "\0" in text or "\\" in text:
        return True
    if _looks_credential_shaped(text):
        return True
    if not _REFERENCE_RE.fullmatch(text):
        return True
    if text in {".", "..", "~"} or text.startswith(("..", "~", "/", ":")):
        return True
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return True
    return False


def _segments_are_safe(text: str) -> bool:
    parts = text.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _clean_anchor_ref(value: Any) -> str | None:
    """Return a safe owner/internal anchor ref for recall trust projection."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _has_unsafe_ref_shape(text):
        return None
    if ":" not in text:
        return None
    namespace, ref = text.split(":", 1)
    namespace = namespace.strip().lower()
    ref = ref.strip()
    if namespace not in {"dep", "file", "github"}:
        return None
    if _has_unsafe_ref_shape(ref):
        return None
    if ":" in ref:
        return None
    if not _segments_are_safe(ref):
        return None
    if namespace == "dep" and any(ch.isspace() for ch in ref):
        return None
    return f"{namespace}:{ref}"


def _clean_anchor_project_id(value: Any) -> str | None:
    """Return a safe project identity string for owner-only trust projection."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _has_unsafe_ref_shape(text):
        return None
    if not _segments_are_safe(text):
        return None
    if ":" in text:
        namespace, rest = text.split(":", 1)
        if not namespace or not rest or any(ch.isspace() for ch in namespace):
            return None
        if _has_unsafe_ref_shape(rest) or not _segments_are_safe(rest):
            return None
    return text


def _clean_confirmation_source(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if text in CONFIRMATION_SOURCES else None


def _clean_anchor_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if text in ANCHOR_STATUSES else None


def _clean_anchor_event(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if text in ANCHOR_EVENTS else None


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


def derive_labeling_source_kind(entry: dict[str, Any]) -> str:
    """Derive source_kind for system labeling without reading external state."""
    if not isinstance(entry, dict):
        return LABEL_SOURCE_UNKNOWN
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    source_tool = str(
        entry.get("source_tool") or provenance.get("source_tool") or ""
    ).strip().lower()
    source_agent = str(provenance.get("source_agent") or "").strip().lower()
    source = f"{source_tool} {source_agent}".strip()
    if not source or source == "unknown":
        return LABEL_SOURCE_UNKNOWN
    if any(token in source for token in (
        "import", "migration", "migrate", "sync", "bulk", "bootstrap", "seed",
    )):
        return LABEL_SOURCE_IMPORTED
    if any(token in source for token in (
        "codex", "claude", "cursor", "windsurf", "agent", "gpt", "sonnet", "opus",
    )):
        return LABEL_SOURCE_AGENT
    if any(token in source for token in ("human", "manual", "owner", "user", "self")):
        return LABEL_SOURCE_HUMAN
    return LABEL_SOURCE_AGENT if source_agent else LABEL_SOURCE_UNKNOWN


def derive_labeling(entry: dict[str, Any]) -> dict[str, Any]:
    """Derive non-authoritative data-label maturity metadata.

    Contract matrix:
    - lifecycle tier/status says whether the item is staging/verified/archived.
    - validation maturity says unreviewed/validated/needs_review.
    - confirmation evidence says human/test_signal/anchor/none.
    - temporal freshness says fresh/aging/stale/unknown.

    These dimensions are intentionally independent: verified tier does not imply
    evidence-backed validation, and fresh timestamps do not imply trust.
    """
    safe_entry = entry if isinstance(entry, dict) else {}
    provenance = safe_entry.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    signals: set[str] = set()

    source_tool = str(
        safe_entry.get("source_tool") or provenance.get("source_tool") or ""
    ).strip()
    if source_tool and source_tool != "unknown":
        signals.add("has_source_tool")
    if str(provenance.get("source_agent") or "").strip():
        signals.add("has_source_agent")
    if str(provenance.get("run_id") or "").strip():
        signals.add("has_run_id")
    if str(provenance.get("last_validated_at") or "").strip():
        signals.add("has_last_validated_at")
    if str(safe_entry.get("domain") or "").strip():
        signals.add("has_domain")
    if str(safe_entry.get("project") or safe_entry.get("source_project") or "").strip():
        signals.add("has_project")
    if str(safe_entry.get("source_url") or "").strip():
        signals.add("has_source_url")

    if safe_entry.get("risk_level") == "high":
        signals.add("high_risk")
    needs_review = (
        safe_entry.get("tier") == "staging"
        or safe_entry.get("memory_state") == "staging"
        or safe_entry.get("approval_required") is True
        or safe_entry.get("approval_status") == "pending"
    )
    if needs_review:
        signals.add("needs_owner_review")

    if needs_review or safe_entry.get("risk_level") == "high":
        validation_state = LABEL_VALIDATION_NEEDS_REVIEW
    elif "has_last_validated_at" in signals:
        validation_state = LABEL_VALIDATION_VALIDATED
    else:
        validation_state = LABEL_VALIDATION_UNREVIEWED

    has_explainable_source = bool({"has_source_tool", "has_source_agent"} & signals)
    has_context_label = bool({"has_domain", "has_project", "has_source_url"} & signals)
    if (
        validation_state == LABEL_VALIDATION_VALIDATED
        and has_explainable_source
        and has_context_label
        and "has_run_id" in signals
    ):
        annotation_quality = LABEL_QUALITY_MATURE
    elif has_explainable_source or has_context_label or "has_run_id" in signals:
        annotation_quality = LABEL_QUALITY_PARTIAL
    else:
        annotation_quality = LABEL_QUALITY_RAW

    if validation_state == LABEL_VALIDATION_NEEDS_REVIEW and annotation_quality == LABEL_QUALITY_MATURE:
        annotation_quality = LABEL_QUALITY_PARTIAL

    return {
        "source_kind": derive_labeling_source_kind(safe_entry),
        "annotation_quality": annotation_quality,
        "validation_state": validation_state,
        "signals": sorted(signals),
    }


def project_recall_provenance(entry: dict[str, Any]) -> dict[str, Any]:
    """Project the safe recall provenance subset, failing closed on unsafe IDs."""
    safe_entry = entry if isinstance(entry, dict) else {}
    out: dict[str, Any] = {}
    source_agent = resolve_source_agent(safe_entry)
    if source_agent:
        out["source_agent"] = source_agent
    raw_prov = safe_entry.get("provenance")
    if not isinstance(raw_prov, dict):
        return out

    run_id = _clean_identifier(raw_prov.get("run_id"))
    if run_id:
        out["run_id"] = run_id

    validated = raw_prov.get("last_validated_at")
    if _parse_iso(validated) is not None:
        out["last_validated_at"] = validated.strip()
    return out


def project_labeling(entry: dict[str, Any]) -> dict[str, Any]:
    """Project stored labeling through a small enum allowlist."""
    safe_entry = entry if isinstance(entry, dict) else {}
    labeling = safe_entry.get("labeling")
    if not isinstance(labeling, dict):
        return {}
    out: dict[str, Any] = {}
    source_kind = labeling.get("source_kind")
    if isinstance(source_kind, str) and source_kind.strip() in {
        LABEL_SOURCE_HUMAN,
        LABEL_SOURCE_AGENT,
        LABEL_SOURCE_IMPORTED,
        LABEL_SOURCE_UNKNOWN,
    }:
        out["source_kind"] = source_kind.strip()
    quality = labeling.get("annotation_quality")
    if isinstance(quality, str) and quality.strip() in {
        LABEL_QUALITY_RAW,
        LABEL_QUALITY_PARTIAL,
        LABEL_QUALITY_MATURE,
    }:
        out["annotation_quality"] = quality.strip()
    validation = labeling.get("validation_state")
    if isinstance(validation, str) and validation.strip() in {
        LABEL_VALIDATION_UNREVIEWED,
        LABEL_VALIDATION_VALIDATED,
        LABEL_VALIDATION_NEEDS_REVIEW,
    }:
        out["validation_state"] = validation.strip()
    signals = labeling.get("signals")
    if isinstance(signals, list):
        clean: list[str] = []
        for value in signals:
            text = str(value).strip()
            if not text or len(text) > _MAX_LABEL_SIGNAL_LEN:
                continue
            if _clean_identifier(text) is None:
                continue
            clean.append(text)
        if clean:
            out["signals"] = clean[:20]
    return out


def project_trust(
    entry: dict[str, Any],
    *,
    freshness: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project owner-only trust metadata with strict enum/reference allowlists."""
    safe_entry = entry if isinstance(entry, dict) else {}
    raw = safe_entry.get("provenance")
    raw = raw if isinstance(raw, dict) else {}
    trust: dict[str, Any] = {}

    confirmation = _clean_confirmation_source(raw.get("confirmation_source"))
    if confirmation:
        trust["confirmation_source"] = confirmation

    anchor_ref = _clean_anchor_ref(raw.get("anchor_ref"))
    if anchor_ref:
        trust["anchor"] = anchor_ref

    anchor_status = _clean_anchor_status(raw.get("anchor_status"))
    if anchor_status:
        trust["anchor_status"] = anchor_status

    anchor_project_id = _clean_anchor_project_id(raw.get("anchor_project_id"))
    if anchor_project_id:
        trust["anchor_project_id"] = anchor_project_id

    validated_at = raw.get("last_validated_at")
    if _parse_iso(validated_at) is not None:
        trust["validated_at"] = validated_at.strip()

    fr = freshness if isinstance(freshness, dict) else compute_freshness(safe_entry, now=now)
    if isinstance(fr, dict):
        decay_policy = fr.get("decay_policy")
        if decay_policy in {DECAY_POLICY_TIME, DECAY_POLICY_TRIGGER}:
            trust["decay_policy"] = decay_policy
        skip_decay = fr.get("skip_decay")
        if isinstance(skip_decay, bool):
            trust["skip_decay"] = skip_decay
        freshness_status = fr.get("freshness_status")
        if freshness_status in {FRESH, AGING, STALE, UNKNOWN}:
            trust["freshness_status"] = freshness_status

    if _clean_anchor_event(raw.get("anchor_event")) == "superseded":
        successor = _clean_anchor_ref(raw.get("anchor_successor_ref"))
        if successor:
            trust["superseded_by"] = successor
    return trust


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
