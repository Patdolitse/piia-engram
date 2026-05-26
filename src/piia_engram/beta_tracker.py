"""Beta event tracker — governance lifecycle instrumentation.

Records discrete events at key governance nodes to a local JSONL file.
Default-on in beta builds. Contains NO knowledge content, only metadata:
event type, timestamp, source tool, domain, tier transitions.

Events tracked:
- knowledge_created: add_lesson / add_decision succeeded
- knowledge_promoted: staging → verified (auto or manual)
- knowledge_reviewed: user reviewed a knowledge item
- knowledge_rejected: user deleted/archived a staging item
- cold_start: get_user_context called
- session_end: wrap_up_session called
- reconcile: cross-tool memory sync

Output: ~/.engram/beta_events.jsonl
Each line: {"event": "...", "ts": "ISO8601", "d": {...}}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _engram_root() -> Path:
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".engram"


def _events_path() -> Path:
    return _engram_root() / "beta_events.jsonl"


def _is_beta_tracking_enabled() -> bool:
    """Beta tracking is ON by default. Set ENGRAM_BETA_TRACKING=0 to disable."""
    env = os.environ.get("ENGRAM_BETA_TRACKING", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    return True


def track_event(event: str, **data: Any) -> None:
    """Append one event to beta_events.jsonl. Never raises."""
    if not _is_beta_tracking_enabled():
        return
    try:
        entry = {
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if data:
            # Sanitize: only keep short string/int/float/bool values
            safe = {}
            for k, v in data.items():
                if isinstance(v, (int, float, bool)):
                    safe[k] = v
                elif isinstance(v, str) and len(v) <= 100:
                    safe[k] = v
                # skip anything else (no content leakage)
            if safe:
                entry["d"] = safe
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.debug("beta_tracker: failed to write event: %s", exc)


def read_events() -> list[dict]:
    """Read all beta events. Returns empty list if file missing."""
    path = _events_path()
    if not path.is_file():
        return []
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return events


def aggregate_events() -> dict[str, Any]:
    """Aggregate beta events into a summary for the feedback report.

    Returns a dict with event counts, timing, and governance flow metrics.
    No content is included — only counts and durations.
    """
    events = read_events()
    if not events:
        return {}

    # Count by event type
    event_counts: dict[str, int] = {}
    for ev in events:
        name = ev.get("event", "unknown")
        event_counts[name] = event_counts.get(name, 0) + 1

    # Knowledge creation by source tool
    created_by_tool: dict[str, int] = {}
    created_by_domain: dict[str, int] = {}
    created_tiers: dict[str, int] = {"staging": 0, "verified": 0}
    for ev in events:
        if ev.get("event") != "knowledge_created":
            continue
        d = ev.get("d", {})
        tool = d.get("source_tool", "unknown")
        created_by_tool[tool] = created_by_tool.get(tool, 0) + 1
        domain = d.get("domain", "")
        if domain:
            for dom in domain.split(","):
                dom = dom.strip()
                if dom:
                    created_by_domain[dom] = created_by_domain.get(dom, 0) + 1
        tier = d.get("tier", "staging")
        if tier in created_tiers:
            created_tiers[tier] += 1

    # Promotion events
    total_promoted = 0
    promotion_methods: dict[str, int] = {}
    for ev in events:
        if ev.get("event") != "knowledge_promoted":
            continue
        d = ev.get("d", {})
        count = d.get("count", 1)
        total_promoted += count
        method = d.get("method", "unknown")
        promotion_methods[method] = promotion_methods.get(method, 0) + count

    # Cold start levels
    cold_start_levels: dict[str, int] = {}
    for ev in events:
        if ev.get("event") != "cold_start":
            continue
        d = ev.get("d", {})
        level = d.get("level", "standard")
        cold_start_levels[level] = cold_start_levels.get(level, 0) + 1

    # Session end stats
    session_tools: dict[str, int] = {}
    for ev in events:
        if ev.get("event") != "session_end":
            continue
        d = ev.get("d", {})
        tool = d.get("source_tool", "unknown")
        session_tools[tool] = session_tools.get(tool, 0) + 1

    # Time span
    timestamps = []
    for ev in events:
        ts = ev.get("ts", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
            except Exception:
                pass

    result: dict[str, Any] = {
        "total_events": len(events),
        "event_counts": event_counts,
    }

    if timestamps:
        result["first_event"] = min(timestamps).strftime("%Y-%m-%d %H:%M")
        result["last_event"] = max(timestamps).strftime("%Y-%m-%d %H:%M")
        span = (max(timestamps) - min(timestamps)).total_seconds() / 86400
        result["tracking_days"] = round(span, 1)

    if created_by_tool:
        result["created_by_tool"] = created_by_tool
    if created_by_domain:
        top = sorted(created_by_domain.items(), key=lambda x: -x[1])[:10]
        result["created_by_domain"] = dict(top)
    if any(v > 0 for v in created_tiers.values()):
        result["created_tiers"] = created_tiers

    if total_promoted > 0:
        result["promotions"] = {
            "total": total_promoted,
            "methods": promotion_methods,
        }

    if cold_start_levels:
        result["cold_starts"] = cold_start_levels

    if session_tools:
        result["sessions_by_tool"] = session_tools

    # Reconcile counts
    reconcile_count = event_counts.get("reconcile", 0)
    if reconcile_count > 0:
        total_imported = 0
        for ev in events:
            if ev.get("event") == "reconcile":
                total_imported += ev.get("d", {}).get("imported", 0)
        result["reconcile"] = {
            "sync_count": reconcile_count,
            "total_imported": total_imported,
        }

    return result
