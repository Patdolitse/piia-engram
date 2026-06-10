"""Lightweight, privacy-preserving update reminder for the Engram CLI.

Mirrors the well-loved pip / GitHub-CLI pattern: at most one anonymous check
per 24h against the PyPI JSON API, cached locally, fail-silent, opt-out via an
env var, and skipped entirely in non-interactive / CI contexts.

Design constraints specific to Engram:

* **MCP-safe.** This module is only ever called from the human-facing CLI
  (``piia_engram.setup_wizard:main``). The MCP server (``mcp_server:main``)
  is a *separate* console-script entry point and never imports this, so the
  notice can never reach the stdio JSON-RPC channel. Even so, all output here
  goes to **stderr**, never stdout, so a stray call could not corrupt a
  protocol stream or a ``--json`` payload.
* **Privacy-first.** The only network call is an anonymous ``GET`` to the
  public PyPI JSON endpoint. No user data, identifiers, or telemetry are sent.
  It is still a network call, so it is opt-out (``ENGRAM_NO_UPDATE_CHECK``)
  and auto-disabled when stderr is not a TTY or a CI marker is present.
* **Never fails loud.** Any exception (network, parse, filesystem) is
  swallowed; the CLI command always runs regardless.

Public surface:
    check_for_update(current, *, force=False) -> str | None
    maybe_print_update_notice(current=None, *, stream=sys.stderr) -> str | None
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

__all__ = [
    "check_for_update",
    "maybe_print_update_notice",
    "is_disabled",
]

# PyPI distribution name (see pyproject.toml [project].name).
_PYPI_PROJECT = "piia-engram"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PYPI_PROJECT}/json"

# At most one network check per this many seconds (24h).
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
# Hard cap on the network call so the CLI never visibly stalls.
_NETWORK_TIMEOUT_SECONDS = 1.5

# Env markers that mean "automation / CI" — skip the reminder there.
_CI_ENV_MARKERS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILD_NUMBER", "TF_BUILD")


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

def _engram_root() -> Path:
    """Resolve the Engram data directory (mirrors telemetry._engram_root)."""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".engram"


def _cache_path() -> Path:
    return _engram_root() / ".update_check.json"


def is_disabled() -> bool:
    """True when the update reminder must not run.

    Disabled when:
    * ``ENGRAM_NO_UPDATE_CHECK`` is set to a truthy value, or
    * a CI / automation env marker is present.

    (The non-TTY check lives in :func:`maybe_print_update_notice` so that
    :func:`check_for_update` itself stays usable from tests and from
    ``engram doctor`` regardless of TTY state.)
    """
    flag = os.environ.get("ENGRAM_NO_UPDATE_CHECK", "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        return True
    for marker in _CI_ENV_MARKERS:
        if os.environ.get(marker, "").strip():
            return True
    return False


# ---------------------------------------------------------------------------
# Version comparison (no hard dependency on `packaging`)
# ---------------------------------------------------------------------------

def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a dotted release version into a comparable tuple of ints.

    Tolerant of pre-release/local suffixes: only the leading numeric
    dotted release segment is used (e.g. "3.56.0rc1" -> (3, 56, 0),
    "3.56.0+local" -> (3, 56, 0)). Non-numeric or empty input -> ().
    """
    if not text:
        return ()
    # Keep only the leading "release" segment before any pre/post/local marker.
    head = text.strip()
    for sep in ("+", "-", "rc", "a", "b", "post", "dev"):
        idx = head.find(sep)
        if idx > 0:
            head = head[:idx]
    parts: list[int] = []
    for chunk in head.split("."):
        chunk = chunk.strip()
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` is a strictly newer release than ``current``."""
    lt = _parse_version(latest)
    ct = _parse_version(current)
    if not lt or not ct:
        return False
    return lt > ct


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _read_cache() -> dict:
    try:
        path = _cache_path()
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(latest: str) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_check": time.time(), "latest": latest}
        path.write_text(
            json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8"
        )
    except Exception:
        # Caching is best-effort; a read-only home must not break the CLI.
        pass


def _cache_fresh(cache: dict) -> bool:
    try:
        last = float(cache.get("last_check", 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < _CHECK_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _fetch_latest_from_pypi() -> Optional[str]:
    """Anonymous GET of the latest released version from PyPI. Fail-silent."""
    try:
        from urllib.request import Request, urlopen

        req = Request(
            _PYPI_JSON_URL,
            headers={"User-Agent": "engram-update-check/1", "Accept": "application/json"},
            method="GET",
        )
        with urlopen(req, timeout=_NETWORK_TIMEOUT_SECONDS) as resp:
            if not (200 <= resp.status < 300):
                return None
            data = json.loads(resp.read().decode("utf-8"))
        version = str(data.get("info", {}).get("version", "")).strip()
        return version or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _current_version(current: Optional[str]) -> str:
    if current:
        return current
    try:
        from piia_engram import __version__

        return __version__
    except Exception:
        return ""


def check_for_update(
    current: Optional[str] = None, *, force: bool = False
) -> Optional[str]:
    """Return the latest PyPI version string iff it is newer than ``current``.

    Uses the 24h cache to avoid more than one network call per day. Returns
    ``None`` when up-to-date, disabled, offline, or on any error. Never raises.

    ``force=True`` bypasses both the disable gate and the cache freshness
    window (used by ``engram doctor`` so the check always reflects reality).
    """
    try:
        if not force and is_disabled():
            return None

        cur = _current_version(current)
        if not cur:
            return None

        cache = _read_cache()
        if not force and _cache_fresh(cache):
            latest = str(cache.get("latest", "")).strip()
        else:
            latest = _fetch_latest_from_pypi() or ""
            if latest:
                _write_cache(latest)
            elif cache.get("latest"):
                # Network failed but we have a prior cached value — reuse it
                # rather than silently going dark.
                latest = str(cache.get("latest", "")).strip()

        if latest and _is_newer(latest, cur):
            return latest
        return None
    except Exception:
        return None


def format_notice(latest: str, current: str) -> str:
    """One-line bilingual upgrade hint (no trailing newline)."""
    return (
        f"[engram] 新版本 {latest} 可用（当前 {current}）。"
        f"升级 / upgrade: pip install -U {_PYPI_PROJECT}"
    )


def maybe_print_update_notice(
    current: Optional[str] = None, *, stream=None
) -> Optional[str]:
    """Print a non-intrusive upgrade notice to stderr when a newer release exists.

    Safe to call unconditionally at CLI start-up:
    * returns ``None`` (prints nothing) when disabled, in CI, when stderr is
      not a TTY (piped / redirected / automation), when offline, or when
      already up-to-date;
    * never raises.

    Returns the latest version string when a notice was printed, else ``None``.
    """
    try:
        out = stream if stream is not None else sys.stderr
        if is_disabled():
            return None
        # Only nag in genuinely interactive terminals.
        isatty = getattr(out, "isatty", None)
        if not (callable(isatty) and out.isatty()):
            return None

        cur = _current_version(current)
        latest = check_for_update(cur)
        if not latest:
            return None
        print(format_notice(latest, cur), file=out)
        return latest
    except Exception:
        return None
