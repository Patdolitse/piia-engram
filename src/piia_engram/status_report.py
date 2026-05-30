"""Redacted local status report for the Engram CLI."""

from __future__ import annotations

import html
import json
import locale
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from .storage import _engram_root


def _read_json_quiet(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _package_version() -> str:
    try:
        return metadata.version("piia-engram")
    except metadata.PackageNotFoundError:
        try:
            from . import __version__

            return __version__
        except Exception:
            return "unknown"


def _entry_tier(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "verified"
    tier = entry.get("tier")
    return tier if isinstance(tier, str) and tier else "verified"


def _count_entries(path: Path) -> dict[str, int]:
    data = _read_json_quiet(path)
    if not isinstance(data, list):
        return {"total": 0, "verified": 0, "staging": 0, "archived": 0}
    counts = {"total": len(data), "verified": 0, "staging": 0, "archived": 0}
    for entry in data:
        tier = _entry_tier(entry)
        if tier not in counts:
            counts[tier] = 0
        counts[tier] += 1
    return counts


def _count_playbooks(root: Path) -> dict[str, int]:
    counts = {"total": 0, "verified": 0, "staging": 0, "archived": 0}
    playbooks_dir = root / "playbooks"
    for path in playbooks_dir.glob("*.json"):
        if path.name == "_index.json":
            continue
        data = _read_json_quiet(path)
        if not isinstance(data, dict):
            continue
        counts["total"] += 1
        tier = _entry_tier(data)
        if tier not in counts:
            counts[tier] = 0
        counts[tier] += 1
    return counts


def _storage_summary(root: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    skipped = 0
    if root.exists():
        try:
            paths = root.rglob("*")
            for path in paths:
                try:
                    if not path.is_file():
                        continue
                    file_count += 1
                    total_bytes += path.stat().st_size
                except OSError:
                    skipped += 1
        except OSError:
            skipped += 1
    return {
        "path": str(root),
        "exists": root.exists(),
        "file_count": file_count,
        "bytes": total_bytes,
        "skipped": skipped,
    }


def _session_summary(root: Path) -> dict[str, Any]:
    contexts_dir = root / "contexts"
    files = list(contexts_dir.glob("*/*.md")) if contexts_dir.exists() else []
    latest: dict[str, Any] | None = None
    if files:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        latest = {
            "tool": newest.parent.name,
            "modified_at": datetime.fromtimestamp(newest.stat().st_mtime)
            .replace(microsecond=0)
            .isoformat(),
            "size_bytes": newest.stat().st_size,
        }
    return {"count": len(files), "latest": latest}


def _knowledge_summary(root: Path) -> dict[str, Any]:
    lessons = _count_entries(root / "knowledge" / "lessons.json")
    decisions = _count_entries(root / "knowledge" / "decisions.json")
    playbooks = _count_playbooks(root)
    total = lessons["total"] + decisions["total"] + playbooks["total"]
    staging = lessons.get("staging", 0) + decisions.get("staging", 0) + playbooks.get("staging", 0)
    archived = lessons.get("archived", 0) + decisions.get("archived", 0) + playbooks.get("archived", 0)
    verified = lessons.get("verified", 0) + decisions.get("verified", 0) + playbooks.get("verified", 0)
    return {
        "total": total,
        "verified": verified,
        "staging": staging,
        "archived": archived,
        "lessons": lessons["total"],
        "decisions": decisions["total"],
        "playbooks": playbooks["total"],
    }


def _encoding_summary() -> dict[str, Any]:
    stdout = sys.stdout.encoding or ""
    stderr = sys.stderr.encoding or ""
    pythonio = os.environ.get("PYTHONIOENCODING", "")
    preferred = locale.getpreferredencoding(False)
    filesystem = sys.getfilesystemencoding()
    utf8ish = ("utf" in stdout.lower() and "utf" in stderr.lower()) or "utf" in pythonio.lower()
    return {
        "stdout": stdout,
        "stderr": stderr,
        "pythonioencoding": pythonio or "(not set)",
        "preferred": preferred,
        "filesystem": filesystem,
        "ok": utf8ish,
    }


def _telemetry_summary() -> dict[str, Any]:
    try:
        from .telemetry import get_status

        status = get_status()
        return {
            "local_enabled": bool(status.get("enabled")),
            "remote_enabled": bool(status.get("remote_enabled")),
            "phase": status.get("phase", "unknown"),
        }
    except Exception as exc:
        return {"error": str(exc), "local_enabled": False, "remote_enabled": False, "phase": "unknown"}


def _probe_mcp_entry() -> dict[str, Any]:
    command = shutil.which("piia-engram-mcp") or "piia-engram-mcp"
    try:
        result = subprocess.run(
            [command, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except Exception as exc:
        return {"ok": False, "command": command, "message": str(exc)}
    return {
        "ok": result.returncode == 0,
        "command": command,
        "message": "help probe passed" if result.returncode == 0 else "help probe failed",
    }


def build_status(*, probe: bool = True) -> dict[str, Any]:
    """Build a metadata-only status object. Never includes memory bodies."""
    root = _engram_root()
    status = {
        "version": _package_version(),
        "platform": platform.system().lower() or sys.platform,
        "root": str(root),
        "storage": _storage_summary(root),
        "knowledge": _knowledge_summary(root),
        "sessions": _session_summary(root),
        "encoding": _encoding_summary(),
        "telemetry": _telemetry_summary(),
        "mcp_entry": {"ok": None, "command": "piia-engram-mcp", "message": "probe skipped"},
        "warnings": [],
    }
    if probe:
        status["mcp_entry"] = _probe_mcp_entry()
    if status["knowledge"]["staging"]:
        status["warnings"].append(
            f"{status['knowledge']['staging']} staging item(s) need review"
        )
    if status["storage"].get("skipped"):
        status["warnings"].append(
            f"{status['storage']['skipped']} storage file(s) could not be scanned"
        )
    if not status["encoding"]["ok"]:
        status["warnings"].append("terminal is not reporting UTF-8 stdout/stderr")
    return status


def _bytes_label(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def render_status_text(status: dict[str, Any]) -> str:
    storage = status["storage"]
    knowledge = status["knowledge"]
    sessions = status["sessions"]
    encoding = status["encoding"]
    telemetry = status["telemetry"]
    mcp = status["mcp_entry"]
    session_tail = "none"
    if sessions["latest"]:
        latest = sessions["latest"]
        session_tail = f"{latest['tool']} @ {latest['modified_at']}"
    mcp_mark = "ok" if mcp.get("ok") is True else "--" if mcp.get("ok") is None else "!!"
    storage_mark = "!!" if storage.get("skipped") else "ok"
    knowledge_mark = "!!" if knowledge.get("staging") else "ok"
    encoding_mark = "ok" if encoding.get("ok") else "!!"
    lines = [
        "Engram status",
        f"  [ok] Version: {status['version']}",
        f"  [{storage_mark}] Storage: {storage['path']} ({storage['file_count']} files, {_bytes_label(storage['bytes'])})",
        (
            f"  [{knowledge_mark}] Knowledge: "
            f"{knowledge['total']} total, {knowledge['verified']} verified, "
            f"{knowledge['staging']} staging, {knowledge['archived']} archived"
        ),
        f"  [ok] Agent sessions: {sessions['count']} saved; latest {session_tail}",
        f"  [{mcp_mark}] MCP entry: {mcp.get('command')} - {mcp.get('message')}",
        (
            f"  [{encoding_mark}] Terminal encoding: "
            f"stdout={encoding['stdout']}, stderr={encoding['stderr']}, "
            f"PYTHONIOENCODING={encoding['pythonioencoding']}"
        ),
        (
            "  [ok] Telemetry: "
            f"local={'on' if telemetry.get('local_enabled') else 'off'}, "
            f"remote={'on' if telemetry.get('remote_enabled') else 'off'}, "
            f"{telemetry.get('phase')}"
        ),
    ]
    if status["warnings"]:
        lines.append("Next:")
        for warning in status["warnings"]:
            lines.append(f"  - {warning}")
    return "\n".join(lines) + "\n"


def render_status_html(status: dict[str, Any]) -> str:
    text = html.escape(render_status_text(status))
    version = html.escape(str(status["version"]))
    generated_at = html.escape(datetime.now().replace(microsecond=0).isoformat())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Engram Status</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; color: #1f2937; }}
    main {{ max-width: 880px; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    .meta {{ color: #64748b; margin-bottom: 24px; }}
    pre {{ background: #f8fafc; border: 1px solid #dbe3ee; border-radius: 8px; padding: 18px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <main>
    <h1>Engram Status</h1>
    <div class="meta">Version {version} - generated {generated_at} - redacted metadata only</div>
    <pre>{text}</pre>
  </main>
</body>
</html>
"""


def write_status_html(status: dict[str, Any], output: Path | None = None) -> Path:
    root = _engram_root()
    path = output or (root / "reports" / "status.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_status_html(status), encoding="utf-8")
    return path
