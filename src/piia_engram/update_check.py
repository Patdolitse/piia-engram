"""Lightweight, privacy-preserving update reminder for the Engram CLI and the resume brief.

Mirrors the well-loved pip / GitHub-CLI pattern: at most one anonymous check
per 24h against the PyPI JSON API, cached locally, fail-silent, opt-out via an
env var, and skipped entirely in non-interactive / CI contexts.

Design constraints specific to Engram:

* **MCP-safe.** The CLI prints its notice to **stderr**, never stdout, so it
  cannot corrupt a protocol stream or a ``--json`` payload. The MCP server
  prints nothing: it only refreshes the cache in a daemon thread, at most once
  a day and never on its start path (:func:`maybe_refresh_in_background`).
* **Brief line from the cache only.** The resume brief and the SessionStart
  hooks call :func:`append_update_notice`, which reads the cached answer and
  never opens the network. The line is fixed text around two version numbers
  that must match a strict pattern, so nothing a server sent can be injected
  into the brief.
* **Privacy-first.** The only network call is an anonymous ``GET`` to the
  public PyPI JSON endpoint. No user data, identifiers, or telemetry are sent.
  It is still a network call, so it is opt-out (``ENGRAM_NO_UPDATE_CHECK``)
  and auto-disabled when stderr is not a TTY or a CI marker is present.
* **Never fails loud.** Any exception (network, parse, filesystem) is
  swallowed; the CLI command always runs regardless.

Public surface:
    check_for_update(current, *, force=False) -> str | None
    maybe_print_update_notice(current=None, *, stream=sys.stderr) -> str | None
    cached_update_notice(current=None) -> str | None        (no network)
    append_update_notice(markdown) -> str                    (no network)
    maybe_refresh_in_background() -> threading.Thread | None
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

__all__ = [
    "check_for_update",
    "maybe_print_update_notice",
    "is_disabled",
    "cached_update_notice",
    "append_update_notice",
    "maybe_refresh_in_background",
]

# PyPI distribution name (see pyproject.toml [project].name).
_PYPI_PROJECT = "piia-engram"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PYPI_PROJECT}/json"

# At most one network check per this many seconds (24h).
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
# Hard cap on the network call so the CLI never visibly stalls.
_NETWORK_TIMEOUT_SECONDS = 1.5

# Env markers that mean "automation / CI" — skip the reminder there. A superset of
# the daily ping's list (usage_ping._CI_VARS), so both are off in the same places.
_CI_ENV_MARKERS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILD_NUMBER", "TF_BUILD",
                   "BUILDKITE", "JENKINS_URL", "CIRCLECI", "TEAMCITY_VERSION")
# Larger answers than this are treated as a failed check.
_MAX_RESPONSE_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

def _engram_root() -> Path:
    """Resolve the Engram data directory (mirrors telemetry._engram_root)."""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".engram"


def _cache_dir() -> Path:
    """Per-user cache directory for the reminder -- never inside the memory store.

    ``ENGRAM_CACHE_DIR`` wins; else the platform cache location
    (%LOCALAPPDATA%/piia-engram/Cache, ~/Library/Caches/piia-engram,
    $XDG_CACHE_HOME/piia-engram or ~/.cache/piia-engram). Keeping it out of
    ENGRAM_DIR is what lets a read-only ``engram doctor`` leave the store untouched.
    """
    custom = os.environ.get("ENGRAM_CACHE_DIR", "").strip()
    if custom:
        return Path(custom).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "piia-engram" / "Cache"
        return Path.home() / "AppData" / "Local" / "piia-engram" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "piia-engram"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "piia-engram"


def _cache_path() -> Path:
    return _cache_dir() / ".update_check.json"


def is_disabled() -> bool:
    """True when the update reminder must not run.

    Disabled when:
    * ``ENGRAM_NO_UPDATE_CHECK`` is set to a truthy value, or
    * a CI / automation env marker is present, or
    * the process runs in a container or with ``ENGRAM_EPHEMERAL`` set (the
      same test the daily ping uses).

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
    try:
        from . import usage_ping

        if usage_ping._in_container() or usage_ping._flag_on("ENGRAM_EPHEMERAL"):
            return True
    except Exception:
        pass
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
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return None
            data = json.loads(raw.decode("utf-8"))
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


# ---------------------------------------------------------------------------
# Resume-brief line (cache only) and the MCP server's background refresh
# ---------------------------------------------------------------------------

# A plain release number and nothing else. Anything a server answered that does
# not match is neither stored by the refresh nor shown in a brief.
_NOTICE_VERSION_RE = re.compile(r"\d{1,4}(?:\.\d{1,4}){1,3}")
_RESUME_CLOSE_TAG = "</engram-resume>"

_refresh_lock = threading.Lock()
_refresh_started = False  # one attempt per process


def _clean_version(value: object) -> str:
    return value if isinstance(value, str) and _NOTICE_VERSION_RE.fullmatch(value) else ""


def _quiet_environment() -> bool:
    """Where neither the brief line nor the background refresh runs: wherever
    the reminder is disabled (ENGRAM_NO_UPDATE_CHECK, CI markers, containers,
    ENGRAM_EPHEMERAL) and in tests."""
    if is_disabled():
        return True
    try:
        from . import usage_ping

        return bool(usage_ping._in_test())
    except Exception:
        return True


def cached_update_notice(current: Optional[str] = None) -> Optional[str]:
    """One fixed line when the cached latest release is newer than the installed
    one, else None. Reads the cache file only: no network, no write, never raises."""
    try:
        if _quiet_environment():
            return None
        cache = _read_cache()
        if not isinstance(cache, dict):
            return None
        latest = _clean_version(cache.get("latest"))
        installed = _clean_version(_current_version(current))
        if not latest or not installed or not _is_newer(latest, installed):
            return None
        return (f"[Engram] Engram {latest} is available (installed {installed}): "
                f"run `pip install -U {_PYPI_PROJECT}` to upgrade.")
    except Exception:
        return None


def append_update_notice(markdown):
    """Put the upgrade line at the end of a resume brief (inside its wrapper).
    Returns the brief unchanged when there is nothing to say or on any error."""
    try:
        if not isinstance(markdown, str) or not markdown.strip():
            return markdown
        line = cached_update_notice()
        if not line:
            return markdown
        at = markdown.rfind(_RESUME_CLOSE_TAG)
        if at == -1:
            return f"{markdown}\n\n{line}"
        head = markdown[:at].rstrip("\n")
        return f"{head}\n\n{line}\n{markdown[at:]}"
    except Exception:
        return markdown


def _attempt_fresh(cache: dict) -> bool:
    try:
        last = float(cache.get("last_attempt", 0))
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < _CHECK_INTERVAL_SECONDS


def _write_attempt(cache: dict) -> None:
    """Remember that a refresh was tried, keeping only a clean earlier answer."""
    try:
        payload: dict = {"last_attempt": time.time()}
        try:
            payload["last_check"] = float(cache.get("last_check", 0))
        except (TypeError, ValueError):
            pass
        latest = _clean_version(cache.get("latest"))
        if latest:
            payload["latest"] = latest
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _refresh(cache: dict) -> None:
    try:
        _write_attempt(cache)  # a failed try is not repeated the same day
        latest = _clean_version(_fetch_latest_from_pypi())
        if latest:
            _write_cache(latest)
    except Exception:
        pass


def maybe_refresh_in_background() -> Optional[threading.Thread]:
    """Refresh the cached answer in a daemon thread: once per process and at
    most once a day. For the MCP server, whose users never run the CLI. Prints
    nothing and never raises."""
    global _refresh_started
    try:
        with _refresh_lock:
            if _refresh_started:
                return None
            _refresh_started = True
        if _quiet_environment():
            return None
        cache = _read_cache()
        if not isinstance(cache, dict):
            cache = {}
        if _cache_fresh(cache) or _attempt_fresh(cache):
            return None
        thread = threading.Thread(target=_refresh, args=(cache,), name="engram-update-check",
                                  daemon=True)
        thread.start()
        return thread
    except Exception:
        return None
