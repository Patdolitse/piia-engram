"""Once-per-week SessionStart "weekly hint" — the ONLY write in Layer 3.

Design locked with Codex: this write is confined to the HOOK layer (never
``get_resume_brief``, never the ``engram weekly`` command) so the read path
stays disk-side-effect free — the same write-boundary discipline that the
Build-1 governance fix enforced. Fail-silent by contract: any error returns the
original markdown so a broken hint can never block a SessionStart.

State: a tiny ``{"last_shown": <iso>}`` JSON under ``~/.engram/logs/`` (same
logs dir the cursor save-state uses), written atomically and best-effort.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_STATE_FILENAME = "weekly_hint_state.json"
_WINDOW_DAYS = 7


def _default_state_path() -> Path:
    from . import _cursor_payload

    return _cursor_payload.state_dir() / _STATE_FILENAME


def _load_last_shown(state_path: Path) -> datetime | None:
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        raw = data.get("last_shown")
        return datetime.fromisoformat(raw) if raw else None
    except Exception:
        return None


def _write_last_shown(state_path: Path, now: datetime) -> None:
    try:
        p = Path(state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"last_shown": now.isoformat()}), encoding="utf-8")
    except Exception:
        pass  # best-effort — a failed dedup write must never break the hook


def maybe_append_weekly_hint(
    markdown: str,
    project_folder: str = "",
    *,
    now: datetime | None = None,
    engram: Any = None,
    state_path: Path | None = None,
) -> str:
    """Append a one-line weekly hint to ``markdown`` at most once per 7 days.

    Returns ``markdown`` unchanged when: within the 7-day dedup window; there is
    nothing to nudge about (no new memories AND no review backlog — so the
    weekly slot is not consumed); or on ANY error (fail-silent).
    """
    try:
        now = now or datetime.now()
        if state_path is None:
            state_path = _default_state_path()

        last = _load_last_shown(state_path)
        if last is not None and (now - last) < timedelta(days=_WINDOW_DAYS):
            return markdown

        if engram is None:
            from ..core import Engram

            # read_only=True → zero-write: the recap reads must not write audit.log
            # or create the store (Codex final review — the only Layer-3 write is
            # this module's own dedup state, below).
            engram = Engram(read_only=True)
        from ..reports_weekly import build_weekly_recap

        recap = build_weekly_recap(engram, now=now, project_folder=project_folder)
        counts = recap.get("counts", {})
        n_new = (
            int(counts.get("lessons", 0) or 0)
            + int(counts.get("decisions", 0) or 0)
            + int(counts.get("playbooks", 0) or 0)
        )
        n_review = int(counts.get("needs_review", 0) or 0)
        if n_new <= 0 and n_review <= 0:
            return markdown  # nothing worth a nudge — don't consume the weekly slot

        hint = (
            f"[Engram Weekly] +{n_new} this week, {n_review} need review "
            "— run 'engram weekly'"
        )
        _write_last_shown(state_path, now)
        sep = "\n\n" if markdown else ""
        return f"{markdown}{sep}{hint}"
    except Exception:
        return markdown  # fail-silent: never break SessionStart
