"""Engram anonymous usage statistics — Phase 1: local-only logging.

This module implements opt-in anonymous usage statistics for Engram.
When enabled, it records aggregated daily counts to a local log file.
No network requests are made in Phase 1.

Data collected (when opted in):
1. Tool call distribution (name + success/fail count) — no arguments or content
2. Knowledge entry totals (lesson/decision counts) — no content
3. Engram version
4. Daily anonymous ID (HMAC-derived, cannot be linked across days)

Never collected: lesson/decision content, prompts, file paths, IP, email,
device fingerprint, OS details, or any user-identifiable information.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


def get_status() -> dict[str, Any]:
    """Return current telemetry status for display."""
    cfg = _load_config()
    return {
        "enabled": is_enabled(),
        "config_path": str(_config_path()),
        "log_path": str(_log_path()),
        "local_uuid": cfg.get("local_uuid", "(not set)"),
        "opted_in_at": cfg.get("opted_in_at", "(never)"),
        "opted_out_at": cfg.get("opted_out_at", "(never)"),
        "phase": "1 (local log only, no network)",
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


def _validate_payload(payload: dict) -> bool:
    """Recursively validate that no field contains natural-language content.

    Returns True if the payload is safe to log/send, False otherwise.
    """
    for key, value in payload.items():
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
) -> dict[str, Any] | None:
    """Build an anonymous usage statistics payload.

    Args:
        tool_calls: {tool_name: {"success": N, "error": N}}
        knowledge_counts: {"lessons": N, "decisions": N, "domains": N}
        engram_version: e.g. "3.15.0"

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
              engram_version: str = "") -> Path | None:
        """Flush current counts to the local log if enabled and due.

        Returns the log path if written, None otherwise.
        """
        if not is_enabled():
            return None
        if not self.should_flush():
            return None

        payload = build_payload(
            tool_calls=self.get_counts(),
            knowledge_counts=knowledge_counts,
            engram_version=engram_version,
        )
        if payload is None:
            return None

        result = log_payload(payload)
        self._last_flush_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._calls.clear()
        return result
