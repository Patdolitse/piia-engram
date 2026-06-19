"""Weekly recap (`engram weekly`) — Layer 3 of the presence loop.

Pure, read-only assembly of the past-7-days digest. Design locked with Codex:
the recap is STRICTLY READ-ONLY (the only write in Layer 3 is the SessionStart
hint dedup state, confined to the hook layer — see ``hooks/_weekly_hint.py``).

This module holds the pure formatter (``render_weekly_text``), the deterministic
resurface picker (``select_resurface``), and the gatherer (``build_weekly_recap``)
that composes existing Engram read APIs.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def render_weekly_text(recap: dict[str, Any]) -> str:
    """Render a recap dict as a ≤10-line text block. Missing sections are
    omitted entirely (no noise lines)."""
    counts = recap.get("counts", {}) if isinstance(recap, dict) else {}
    n_l = int(counts.get("lessons", 0) or 0)
    n_d = int(counts.get("decisions", 0) or 0)
    n_p = int(counts.get("playbooks", 0) or 0)
    n_r = int(counts.get("needs_review", 0) or 0)

    lines = [
        f"[Engram] Week of {recap.get('start', '')}-{recap.get('end', '')}: "
        f"+{_plural(n_l, 'lesson')}, +{_plural(n_d, 'decision')}, "
        f"+{_plural(n_p, 'playbook')} · {n_r} need review"
    ]

    top = recap.get("top_domains") or []
    if top:
        lines.append(
            "Top domains: "
            + ", ".join(f"{d.get('domain', '')} {d.get('count', 0)}" for d in top[:3])
        )

    growth = recap.get("growth") or []
    if growth:
        def _g(g: dict) -> str:
            delta = int(g.get("delta", 0) or 0)
            sign = "+" if delta >= 0 else ""
            return f"{g.get('stat', '')} {sign}{delta}"
        lines.append("Growth: " + ", ".join(_g(g) for g in growth[:2]))

    titles = recap.get("daily_log_titles") or []
    if titles:
        lines.append("Daily log: " + "; ".join(str(t) for t in titles[:3]))

    resurface = recap.get("resurface")
    if resurface:
        lines.append(f"Resurface: {resurface.get('summary', '')}")

    return "\n".join(lines[:10])


def select_resurface(
    lessons: list[dict[str, Any]],
    project_tokens: set[str] | None = None,
) -> dict[str, Any] | None:
    """Deterministically pick ONE old lesson to resurface (no random noise).

    Candidate pool = verified lessons (caller passes active items). Ordering:
      1. current-project relevance first (domain token overlap with project),
      2. oldest ``last_reviewed`` (fallback ``created_at``),
      3. oldest ``created_at``,
      4. stable ``id`` tie-break.
    Returns the chosen raw lesson dict, or None when the pool is empty.
    """
    pool = [
        L for L in (lessons or [])
        if isinstance(L, dict) and L.get("tier") == "verified"
    ]
    if not pool:
        return None
    ptokens = {t.lower() for t in (project_tokens or set()) if t}

    def _relevant(L: dict) -> bool:
        if not ptokens:
            return False
        dom = str(L.get("domain") or "")
        toks = {d.strip().lower() for d in dom.split(",") if d.strip()}
        return bool(toks & ptokens)

    def _key(L: dict):
        sort_ts = str(L.get("last_reviewed") or L.get("created_at") or "")
        created = str(L.get("created_at") or "")
        return (not _relevant(L), sort_ts, created, str(L.get("id") or ""))

    return min(pool, key=_key)


def _parse_ts(value: Any) -> datetime | None:
    """Best-effort ISO8601 parse (no-tz strings, optional trailing Z/offset)."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "")
        tail = s[10:]
        if "+" in tail:
            s = s[:10] + tail.split("+")[0]
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _project_tokens(project_folder: str) -> set[str]:
    """Lowercase alnum tokens from the project folder name (for resurface relevance)."""
    if not project_folder:
        return set()
    from pathlib import Path

    name = Path(project_folder).name
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}


def _extract_daily_titles(content: str, limit: int = 3) -> list[str]:
    """Pull the first content line under each ``## HH:MM:SS [tag]`` entry header
    in a daily-log markdown body; return the most recent ``limit`` of them."""
    titles: list[str] = []
    lines = (content or "").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("## "):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                titles.append(lines[j].strip()[:80])
            i = j + 1
        else:
            i += 1
    return titles[-limit:]


def build_weekly_recap(
    engram: Any, *, now: datetime | None = None, project_folder: str = ""
) -> dict[str, Any]:
    """Compose the past-7-days recap dict from existing Engram read APIs.

    STRICTLY READ-ONLY: passes ``_update_access=False`` everywhere, never writes
    state, never touches ``last_reviewed``. The only Layer-3 write is the
    SessionStart hint dedup, which lives in ``hooks/_weekly_hint.py``.

    Read-only contract is self-enforced: the two production callers pass
    ``Engram(read_only=True)``, but to stop a future entry point from silently
    re-exposing the audit read-write by handing in a writable ``Engram()`` (each
    ``get_*`` logs a ``read`` to ``audit.log``), this gatherer re-opens a
    guaranteed zero-write view on the same root before reading. Same root ⇒
    identical reads (lessons/decisions/playbooks/portraits/daily logs are all
    keyed off ``engram.root``), so injected writable engrams read exactly as
    before. Test doubles or anything without a usable ``root`` fall through
    unchanged.
    """
    if getattr(engram, "_read_only", False) is not True:
        root = getattr(engram, "root", None)
        if root is not None:
            try:
                engram = type(engram)(root, read_only=True)
            except Exception:
                pass  # fall back to the caller's engram; reads still work

    now = now or datetime.now()
    cutoff = now - timedelta(days=7)

    def _recent(items: list) -> list:
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            ts = _parse_ts(it.get("created_at"))
            if ts and ts >= cutoff:
                out.append(it)
        return out

    lessons_all = engram.get_lessons(
        limit=None, _update_access=False, _migrate_fields=False
    ) or []
    decisions_all = engram.get_decisions(
        limit=None, _update_access=False, _migrate_fields=False
    ) or []
    try:
        playbooks_all = engram.get_playbooks(limit=None, _update_access=False) or []
    except Exception:
        playbooks_all = []

    recent_lessons = _recent(lessons_all)
    recent_decisions = _recent(decisions_all)
    recent_playbooks = _recent(playbooks_all)

    needs_review = sum(
        1 for it in (lessons_all + decisions_all)
        if isinstance(it, dict) and it.get("tier") == "staging"
    )

    domain_counts: dict[str, int] = {}
    for it in recent_lessons + recent_decisions:
        for d in str(it.get("domain") or "").split(","):
            d = d.strip()
            if d:
                domain_counts[d] = domain_counts.get(d, 0) + 1
    top_domains = [
        {"domain": d, "count": c}
        for d, c in sorted(domain_counts.items(), key=lambda x: (-x[1], x[0]))[:3]
    ]

    growth: list[dict] = []
    try:
        latest = engram.get_latest_portrait()
        prev = engram.get_previous_portrait()
        if latest and prev:
            diff = engram.compare_user_portraits(prev, latest)
            deltas = diff.get("deltas", {}) if isinstance(diff, dict) else {}
            nonzero = [
                (k, v) for k, v in deltas.items()
                if isinstance(v, dict) and v.get("delta")
            ]
            nonzero.sort(key=lambda kv: -abs(int(kv[1].get("delta", 0) or 0)))
            for k, v in nonzero[:2]:
                growth.append({
                    "stat": k, "from": v.get("from", 0),
                    "to": v.get("to", 0), "delta": v.get("delta", 0),
                })
    except Exception:
        growth = []

    daily_log_titles: list[str] = []
    if project_folder:
        try:
            daily = engram.get_daily_log(project_folder)
            if daily.get("exists") and daily.get("content"):
                daily_log_titles = _extract_daily_titles(daily["content"])
        except Exception:
            daily_log_titles = []

    resurface = None
    picked = select_resurface(lessons_all, _project_tokens(project_folder))
    if picked:
        resurface = {
            "kind": "lesson",
            "id": picked.get("id"),
            "summary": picked.get("summary", ""),
            "domain": picked.get("domain", ""),
        }

    return {
        "start": cutoff.strftime("%Y-%m-%d"),
        "end": now.strftime("%Y-%m-%d"),
        "counts": {
            "lessons": len(recent_lessons),
            "decisions": len(recent_decisions),
            "playbooks": len(recent_playbooks),
            "needs_review": needs_review,
        },
        "top_domains": top_domains,
        "growth": growth,
        "daily_log_titles": daily_log_titles,
        "resurface": resurface,
    }
