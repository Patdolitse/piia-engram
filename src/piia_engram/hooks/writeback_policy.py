"""Shared opt-in policy for tool writeback hooks.

All bridge writeback hooks must stay disabled by default, must write only to
staging/pending review paths, and must avoid recursive self-triggering.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUTHY = {"1", "true", "on", "yes"}


def check_writeback_allowed(
    env_var: str,
    *,
    staging_gate: bool,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return True only for explicit, staging-only, non-recursive writeback."""
    env = os.environ if env is None else env
    if not staging_gate:
        return False
    if env.get(f"{env_var}_ACTIVE", "").strip() == "1":
        return False
    return env.get(env_var, "").strip().lower() in _TRUTHY
