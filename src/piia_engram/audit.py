"""Engram access audit log.

Records all identity and knowledge data read/write operations.
Writes to ~/.engram/audit.log in JSON-lines format (one JSON object per line).
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class AuditLogger:
    """Lightweight audit logger."""

    _read_suppression_depth: ContextVar[int] = ContextVar(
        "piia_engram_audit_read_suppression_depth",
        default=0,
    )

    def __init__(self, log_path: Path | None = None, enabled: bool = True):
        self.enabled = enabled
        self.log_path = log_path

    @contextmanager
    def suppress_reads(self) -> Iterator[None]:
        """Suppress read audit entries in the current execution context only."""
        token = self._read_suppression_depth.set(
            self._read_suppression_depth.get() + 1
        )
        try:
            yield
        finally:
            self._read_suppression_depth.reset(token)

    def log(
        self,
        action: str,
        resource: str,
        detail: str = "",
        source_tool: str = "",
    ) -> None:
        """Record an audit entry.

        Args:
            action: "read" | "write" | "delete" | "export" | "import"
            resource: Resource accessed, e.g. "identity/profile", "knowledge/lessons"
            detail: Extra detail, e.g. modified field names
            source_tool: Calling tool identifier
        """
        if not self.enabled or not self.log_path:
            return
        if action == "read" and self._read_suppression_depth.get() > 0:
            return
        if action == "read":
            try:
                from . import governance_runtime as _gov_rt

                root = self.log_path.parent
                if _gov_rt.governance_enabled() and not _gov_rt.caller_is_owner(root):
                    return
            except Exception:
                return
        entry = {
            "timestamp": datetime.now().replace(microsecond=0).isoformat(),
            "action": action,
            "resource": resource,
            "detail": detail[:200] if detail else "",
            "source_tool": source_tool,
            "pid": os.getpid(),
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("audit write failed: %s", exc)
