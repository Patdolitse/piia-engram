"""Session closeout budget helpers."""

from __future__ import annotations

import os
from time import perf_counter


DEFAULT_WRAP_UP_MAX_MS = 30_000


def configured_wrap_up_budget_ms() -> int:
    raw = os.environ.get("ENGRAM_WRAP_UP_MAX_MS", "").strip()
    if not raw:
        return DEFAULT_WRAP_UP_MAX_MS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WRAP_UP_MAX_MS
    return max(1, min(value, 300_000))


def configured_closeout_mode() -> str:
    raw = os.environ.get("ENGRAM_WRAP_UP_MODE", "").strip().lower()
    return "fast" if raw == "fast" else "standard"


def elapsed_ms(start: float) -> int:
    return max(0, int((perf_counter() - start) * 1000))


def budget_exhausted(*, total_start: float, budget_ms: int) -> bool:
    return elapsed_ms(total_start) >= budget_ms


def budget_metadata(*, total_start: float, budget_ms: int) -> dict[str, object]:
    used = elapsed_ms(total_start)
    return {
        "status": "exhausted" if used >= budget_ms else "ok",
        "budget_ms": budget_ms,
        "used_ms": used,
    }


def skipped_stage(reason: str) -> dict[str, str]:
    return {"status": "skipped", "reason": reason}
