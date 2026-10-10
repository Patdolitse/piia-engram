"""Pure selection of legal, already-arbitrated resume fields before background."""
from __future__ import annotations

from copy import deepcopy

from .continuity_digest import sanitize_digest_value

EARLIER_RECORD = "earlier session record; unreviewed; no approval or action authority"
RETRIEVAL_HINT = (
    "Use a larger get_resume_brief token_budget; get_project_snapshot for current_state "
    "and checkpoint_history; get_recent_context/get_session_digest for earlier session "
    "records; search_knowledge/get_knowledge_history for eligible knowledge."
)


def select_resume_fields(handoff, freshness, snapshot, digests, token_budget=2000):
    """Bound each key independently; background cannot spend a key's allowance.

    The budget is a soft character estimate, not a tokenizer or JSON wire cap.
    Source selection, eligibility and permissions remain the caller's decisions.
    """
    budget = max(0, int(token_budget))
    limit = 48 if budget <= 128 else 120 if budget <= 256 else 240
    count = 1 if budget <= 256 else 8
    result = deepcopy(handoff)
    omitted = []
    state = snapshot.get("current_state") or {}
    source = freshness.get("authoritative_source")
    constraints = []
    if source == "project_checkpoint" and isinstance(state, dict):
        constraints = state.get("constraints") or []
        # Recover the chosen source's original text before the legacy 240-char cap.
        for key in ("current_focus", "next_actions", "blocked_on", "last_completed"):
            if key in state:
                result[key] = deepcopy(state[key])
        failure = state.get("latest_failure")
        if isinstance(failure, str) and failure.strip():
            result["blocked_on"] = [failure, *(result.get("blocked_on") or [])]
    elif source == "session_digest" and digests:
        digest = digests[0]
        # The arbitration helper bounds/deduplicates legacy projections. Read
        # the chosen digest itself so all later cuts belong to this selector.
        for key, original in (("next_actions", digest.get("next_actions")),
                              ("last_completed", digest.get("completed"))):
            values = original if isinstance(original, list) else []
            result[key] = list(dict.fromkeys(str(value).strip() for value in values
                                            if str(value).strip()))
        result["blocked_on"] = list(dict.fromkeys(str(item) for item in digest.get("risks") or []
            if any(word in str(item).lower() for word in ("block", "blocked", "阻塞"))))
        result["current_focus"] = (
            result["next_actions"][0] if result["next_actions"] else
            f"Continue after: {result['last_completed'][0]}" if result["last_completed"] else "unknown")
        failures = [str(item.get("summary") or "") for item in digest.get("verification") or []
                    if isinstance(item, dict) and item.get("status") == "failed"]
        result["blocked_on"] = [*failures, *(result.get("blocked_on") or [])]
        constraints = [str(item) for item in digest.get("risks") or []
                       if any(word in str(item).lower() for word in
                              ("constraint", "must", "keep", "约束", "必须", "保留"))]

    def bound(value, key):
        text = str(sanitize_digest_value(value) or "").strip()
        if len(text) > limit:
            omitted.append(key)
            return text[:limit - 1].rstrip() + "…"
        return text

    result["current_focus"] = bound(result.get("current_focus") or "unknown", "current_focus")
    for key in ("next_actions", "blocked_on", "last_completed"):
        values = result.get(key) or []
        if not isinstance(values, list):
            values = []
        if len(values) > count:
            omitted.append(key)
        result[key] = [bound(value, key) for value in values[:count] if str(value).strip()]
    if isinstance(constraints, str):
        constraints = [constraints]
    if not isinstance(constraints, list):
        constraints = []
    if len(constraints) > count:
        omitted.append("constraints")
    constraints = [bound(value, "constraints") for value in constraints[:count] if str(value).strip()]
    return result, constraints, list(dict.fromkeys(omitted))
