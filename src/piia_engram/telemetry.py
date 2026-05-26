"""Engram anonymous usage statistics — Phase 1 & 2.

Phase 1 (local-only): records aggregated daily counts to a local log file.
Phase 2 (remote): sends the same payload to a Cloudflare Worker endpoint.
Remote sending requires separate opt-in (re-consent).

Data collected (when opted in):
1. Tool call distribution (name + success/fail count) — no arguments or content
2. Knowledge entry totals (lesson/decision counts) — no content
3. Engram version
4. Daily anonymous ID (HMAC-derived, cannot be linked across days)
5. OS platform (win32/darwin/linux) — no detailed version
6. Python major.minor version
7. Tool tier (core/all)

Never collected: lesson/decision content, prompts, file paths, IP, email,
device fingerprint, or any user-identifiable information.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Phase 2 remote endpoint (Cloudflare Worker)
_DEFAULT_ENDPOINT = "https://engram-telemetry.pp3x325.workers.dev/v1/events"
_DEFAULT_FEEDBACK_ENDPOINT = "https://engram-telemetry.pp3x325.workers.dev/v1/feedback"
_REMOTE_TIMEOUT = 3  # seconds — fail fast, never block MCP tools
_FEEDBACK_INTERVAL_DAYS = 7  # send feedback at most once per week


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _engram_root() -> Path:
    """Resolve the Engram data directory."""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return Path.home() / ".engram"


def _config_path() -> Path:
    return _engram_root() / "telemetry_config.json"


def _log_path() -> Path:
    return _engram_root() / "telemetry.log"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(cfg: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def is_enabled() -> bool:
    """Check if usage statistics are enabled.

    Respects ENGRAM_TELEMETRY env var (0/false/off = disabled) and
    the persisted config in ~/.engram/telemetry_config.json.
    """
    env = os.environ.get("ENGRAM_TELEMETRY", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    return _load_config().get("enabled", False)


def set_enabled(enabled: bool) -> None:
    """Persist the opt-in/opt-out choice."""
    cfg = _load_config()
    cfg["enabled"] = enabled
    if enabled and "local_uuid" not in cfg:
        cfg["local_uuid"] = str(uuid.uuid4())
        cfg["opted_in_at"] = datetime.now(timezone.utc).isoformat()
    if not enabled:
        cfg["opted_out_at"] = datetime.now(timezone.utc).isoformat()
    _save_config(cfg)


def is_remote_enabled() -> bool:
    """Check if remote usage statistics (Phase 2) are enabled.

    Requires BOTH local stats enabled AND explicit remote consent.
    Respects ENGRAM_TELEMETRY_REMOTE env var override.
    """
    if not is_enabled():
        return False
    env = os.environ.get("ENGRAM_TELEMETRY_REMOTE", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    return _load_config().get("remote_enabled", False)


def set_remote_enabled(enabled: bool) -> None:
    """Persist the remote sending opt-in/opt-out choice."""
    cfg = _load_config()
    cfg["remote_enabled"] = enabled
    if enabled:
        cfg["remote_opted_in_at"] = datetime.now(timezone.utc).isoformat()
    else:
        cfg["remote_opted_out_at"] = datetime.now(timezone.utc).isoformat()
    _save_config(cfg)


def get_endpoint() -> str:
    """Return the remote telemetry endpoint URL."""
    return os.environ.get("ENGRAM_TELEMETRY_URL", "").strip() or _DEFAULT_ENDPOINT


def get_status() -> dict[str, Any]:
    """Return current telemetry status for display."""
    cfg = _load_config()
    remote = is_remote_enabled()
    return {
        "enabled": is_enabled(),
        "remote_enabled": remote,
        "config_path": str(_config_path()),
        "log_path": str(_log_path()),
        "local_uuid": cfg.get("local_uuid", "(not set)"),
        "opted_in_at": cfg.get("opted_in_at", "(never)"),
        "opted_out_at": cfg.get("opted_out_at", "(never)"),
        "remote_opted_in_at": cfg.get("remote_opted_in_at", "(never)"),
        "endpoint": get_endpoint() if remote else "(disabled)",
        "phase": "2 (local + remote)" if remote else "1 (local log only)",
    }


# ---------------------------------------------------------------------------
# Daily anonymous ID — cannot be linked across days
# ---------------------------------------------------------------------------

def _daily_id(local_uuid: str) -> str:
    """Generate a daily anonymous ID: HMAC(local_uuid, date).

    This allows counting unique daily users without tracking individuals
    across days.  The local_uuid never leaves the machine.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hmac.new(
        local_uuid.encode(), today.encode(), hashlib.sha256
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Payload builder and validator
# ---------------------------------------------------------------------------

# Maximum string length in payload values.  Anything longer is suspicious
# and will be rejected to prevent accidental content leakage.
_MAX_FIELD_LEN = 200

# Maximum length for dictionary keys (tool names, field names).
_MAX_KEY_LEN = 80


def _validate_key(key: str) -> bool:
    """Validate a dictionary key is safe for telemetry.

    Keys must be short ASCII-ish identifiers (tool names, field names).
    Rejects keys that look like natural language, file paths, or content.
    """
    if not isinstance(key, str):
        return False
    if len(key) > _MAX_KEY_LEN:
        logger.warning("telemetry key rejected: %r too long (%d)", key[:40], len(key))
        return False
    # Keys should not contain spaces (natural language) or path separators
    if " " in key or "/" in key or "\\" in key:
        logger.warning("telemetry key rejected: %r looks like content or path", key[:40])
        return False
    return True


def _validate_payload(payload: dict) -> bool:
    """Recursively validate that no field contains natural-language content.

    Checks both keys and values. Keys must be short identifiers (tool names,
    field names). Values must not be long strings or natural language.

    Returns True if the payload is safe to log/send, False otherwise.
    """
    for key, value in payload.items():
        if not _validate_key(key):
            return False
        if isinstance(value, str):
            if len(value) > _MAX_FIELD_LEN:
                logger.warning("telemetry payload rejected: field %r too long (%d)",
                               key, len(value))
                return False
            # Natural language heuristic: >20% spaces and >200 chars
            if len(value) > 100:
                space_ratio = value.count(" ") / len(value) if value else 0
                if space_ratio > 0.2:
                    logger.warning("telemetry payload rejected: field %r looks like natural language",
                                   key)
                    return False
        elif isinstance(value, dict):
            if not _validate_payload(value):
                return False
    return True


def build_payload(
    *,
    tool_calls: dict[str, dict[str, int]] | None = None,
    knowledge_counts: dict[str, int] | None = None,
    engram_version: str = "",
    tools_tier: str = "",
) -> dict[str, Any] | None:
    """Build an anonymous usage statistics payload.

    Args:
        tool_calls: {tool_name: {"success": N, "error": N}}
        knowledge_counts: {"lessons": N, "decisions": N, "domains": N}
        engram_version: e.g. "3.15.0"
        tools_tier: "core" or "all"

    Returns:
        The payload dict, or None if validation fails or stats are disabled.
    """
    if not is_enabled():
        return None

    cfg = _load_config()
    local_uuid = cfg.get("local_uuid", "")
    if not local_uuid:
        return None

    payload: dict[str, Any] = {
        "schema": 1,
        "daily_id": _daily_id(local_uuid),
        "engram_version": engram_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os_platform": sys.platform,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "tools_tier": tools_tier or os.environ.get("ENGRAM_TOOLS", "core"),
    }

    if tool_calls:
        payload["tool_calls"] = tool_calls

    if knowledge_counts:
        payload["knowledge_counts"] = knowledge_counts

    if not _validate_payload(payload):
        return None

    return payload


# ---------------------------------------------------------------------------
# Local log writer (Phase 1: no network)
# ---------------------------------------------------------------------------

def log_payload(payload: dict[str, Any]) -> Path:
    """Append a validated payload to the local telemetry log.

    Returns the log file path.
    """
    log_file = _log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return log_file


def preview_payload(
    *,
    tool_calls: dict[str, dict[str, int]] | None = None,
    knowledge_counts: dict[str, int] | None = None,
    engram_version: str = "",
) -> str:
    """Return a human-readable preview of what would be logged/sent.

    Works even when telemetry is disabled, so users can inspect before opting in.
    """
    cfg = _load_config()
    local_uuid = cfg.get("local_uuid", str(uuid.uuid4()))
    daily_id = _daily_id(local_uuid)

    payload: dict[str, Any] = {
        "schema": 1,
        "daily_id": daily_id,
        "engram_version": engram_version or "(current version)",
        "timestamp": "(next daily report time)",
        "os_platform": sys.platform,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "tools_tier": os.environ.get("ENGRAM_TOOLS", "core"),
    }
    if tool_calls:
        payload["tool_calls"] = tool_calls
    else:
        payload["tool_calls"] = {
            "get_user_context": {"success": "N", "error": "N"},
            "add_lesson": {"success": "N", "error": "N"},
            "(other tools)": {"success": "N", "error": "N"},
        }
    if knowledge_counts:
        payload["knowledge_counts"] = knowledge_counts
    else:
        payload["knowledge_counts"] = {
            "lessons": "N",
            "decisions": "N",
            "domains": "N",
        }

    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Phase 2: Remote sender (fire-and-forget, never raises)
# ---------------------------------------------------------------------------

def _send_remote(payload: dict[str, Any]) -> bool:
    """Send payload to remote endpoint. Returns True on success.

    CRITICAL: This function NEVER raises. All errors are silently logged
    at DEBUG level. A failed remote send must NEVER affect MCP tool behavior.
    """
    if not is_remote_enabled():
        return False
    try:
        endpoint = get_endpoint()
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=_REMOTE_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except (URLError, OSError, ValueError, Exception) as exc:
        logger.debug("telemetry remote send failed (silent): %s", exc)
        return False


# ---------------------------------------------------------------------------
# Tool call tracker (in-memory, flushed daily)
# ---------------------------------------------------------------------------

class ToolCallTracker:
    """Track MCP tool call counts in memory for the current session.

    This is designed to be instantiated once per MCP server process and
    flushed to the local log at most once per day.
    """

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, int]] = {}
        self._last_flush_date: str = ""

    def record(self, tool_name: str, success: bool = True) -> None:
        """Record a tool call."""
        if tool_name not in self._calls:
            self._calls[tool_name] = {"success": 0, "error": 0}
        key = "success" if success else "error"
        self._calls[tool_name][key] += 1

    def get_counts(self) -> dict[str, dict[str, int]]:
        """Return a copy of current counts."""
        return {k: dict(v) for k, v in self._calls.items()}

    def should_flush(self) -> bool:
        """Check if we should flush (at most once per UTC day)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return today != self._last_flush_date

    def flush(self, *, knowledge_counts: dict[str, int] | None = None,
              engram_version: str = "",
              tools_tier: str = "",
              force: bool = False) -> Path | None:
        """Flush current counts to local log and optionally send remotely.

        Phase 1: always writes to local log.
        Phase 2: if remote is enabled, also sends to Cloudflare Worker.
        Remote failure is silently ignored — never affects MCP tools.

        Args:
            force: If True, skip the daily rate limit check. Used for
                   session end / process exit to avoid losing data.

        Returns the log path if written, None otherwise.
        """
        if not is_enabled():
            return None
        if not force and not self.should_flush():
            return None
        if not self._calls:
            return None

        payload = build_payload(
            tool_calls=self.get_counts(),
            knowledge_counts=knowledge_counts,
            engram_version=engram_version,
            tools_tier=tools_tier,
        )
        if payload is None:
            return None

        # Phase 1: local log (always)
        result = log_payload(payload)

        # Phase 2: remote send (if opted in, never raises)
        try:
            _send_remote(payload)
        except Exception:
            pass  # defense in depth — _send_remote already catches everything

        self._last_flush_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._calls.clear()
        return result


# ---------------------------------------------------------------------------
# Feedback reporting — richer anonymous governance/usage reports
# ---------------------------------------------------------------------------

def is_feedback_enabled() -> bool:
    """Check if anonymous feedback reporting is enabled.

    Requires BOTH local stats enabled AND explicit feedback consent.
    Respects ENGRAM_FEEDBACK env var override.
    """
    if not is_enabled():
        return False
    env = os.environ.get("ENGRAM_FEEDBACK", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    return _load_config().get("feedback_enabled", False)


def set_feedback_enabled(enabled: bool) -> None:
    """Persist the feedback reporting opt-in/opt-out choice."""
    cfg = _load_config()
    cfg["feedback_enabled"] = enabled
    if enabled:
        cfg["feedback_opted_in_at"] = datetime.now(timezone.utc).isoformat()
    else:
        cfg["feedback_opted_out_at"] = datetime.now(timezone.utc).isoformat()
    _save_config(cfg)


def _feedback_due() -> bool:
    """Check if enough time has passed since the last feedback send."""
    cfg = _load_config()
    last = cfg.get("last_feedback_sent", "")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
        return elapsed >= _FEEDBACK_INTERVAL_DAYS
    except (TypeError, ValueError):
        return True


def send_feedback(report: dict[str, Any]) -> bool:
    """Send a feedback report to the remote endpoint. Returns True on success.

    Injects the daily_id for anonymous tracking. NEVER raises.
    """
    if not is_feedback_enabled():
        return False
    try:
        cfg = _load_config()
        local_uuid = cfg.get("local_uuid", "")
        if not local_uuid:
            return False

        report["daily_id"] = _daily_id(local_uuid)

        endpoint = os.environ.get("ENGRAM_FEEDBACK_URL", "").strip() or _DEFAULT_FEEDBACK_ENDPOINT
        data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            success = 200 <= resp.status < 300
            if success:
                cfg["last_feedback_sent"] = datetime.now(timezone.utc).isoformat()
                _save_config(cfg)
            return success
    except (URLError, OSError, ValueError, Exception) as exc:
        logger.debug("feedback send failed (silent): %s", exc)
        return False
