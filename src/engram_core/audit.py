"""Engram access audit log.

Records all identity and knowledge data read/write operations.
Writes to ~/.engram/audit.log in JSON-lines format (one JSON object per line).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


class AuditLogger:
    """Lightweight audit logger."""

    def __init__(self, log_path: Path | None = None, enabled: bool = True):
        self.enabled = enabled
        self.log_path = log_path

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
        except Exception:
            pass  # Audit log failure must not block main flow
