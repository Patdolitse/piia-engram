"""Context Compression Replay packet helper.

Builds a compact, redacted packet that can be replayed into a resumed context
after model/client compression. The packet is not applied or stored here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .export_redaction import redact_export_text


def _clean_source(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 80 or "\n" in text or "\r" in text:
        return "unknown"
    return text


def build_replay_packet(
    compact_summary: str,
    *,
    source: str = "postcompact",
    max_summary_chars: int = 1200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a redacted replay packet; never writes or applies it."""
    raw = compact_summary if isinstance(compact_summary, str) else str(compact_summary or "")
    redacted = redact_export_text(raw).strip()
    limit = max(0, int(max_summary_chars))
    truncated = len(redacted) > limit
    summary = redacted[:limit].rstrip() if truncated else redacted
    digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:12] if summary else ""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return {
        "source": _clean_source(source),
        "summary": summary,
        "summary_truncated": truncated,
        "summary_sha256_12": digest,
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "applied": False,
        "invariant": "replay_packet_only",
    }
