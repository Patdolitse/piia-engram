"""Evidence scaffolding for live AI client validation runs.

This module is intentionally separate from ``continuity_harness``. The
continuity harness is a pure simulated memory cycle; this module models the
evidence contract around real client runs such as Hermes CLI or OpenClaw file
bridge validation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVIDENCE_FILES = (
    "run_meta.json",
    "tool_locations.json",
    "test-materials/",
    "client_version.txt",
    "client_config_summary.txt",
    "prompts/",
    "raw/",
    "parsed/",
    "timings.json",
    "zero_pollution.txt",
    "REPORT.md",
    "OPTIMIZATION_NOTES.md",
)

REQUIRED_RUN_META_KEYS = (
    "client_id",
    "client_version",
    "surface",
    "model",
    "engram_mode",
    "environment_arm",
    "workspace_isolated",
    "home_isolated",
    "write_tools_allowed",
    "known_limitations",
)

_LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}


@dataclass(frozen=True)
class FileSnapshot:
    """Minimal metadata needed for zero-pollution comparisons."""

    path: str
    exists: bool
    sha256: str = ""
    size_bytes: int = 0
    mtime_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
        }


def evidence_dir_layout() -> list[str]:
    """Return the required evidence files/directories for every client run."""
    return list(EVIDENCE_FILES)


def build_run_meta(
    *,
    client_id: str,
    client_version: str,
    surface: str,
    model: str,
    engram_mode: str,
    environment_arm: str,
    workspace_isolated: bool,
    home_isolated: bool,
    write_tools_allowed: bool,
    known_limitations: list[str] | tuple[str, ...] | str,
    run_root: str = "",
    timestamp: str = "",
    verified_level: str = "",
) -> dict[str, Any]:
    """Build a normalized ``run_meta.json`` payload."""
    if isinstance(known_limitations, str):
        limitations = [known_limitations] if known_limitations else []
    else:
        limitations = [str(item) for item in known_limitations]

    meta: dict[str, Any] = {
        "client_id": client_id,
        "client_version": client_version,
        "surface": surface,
        "model": model,
        "engram_mode": engram_mode,
        "environment_arm": environment_arm,
        "workspace_isolated": bool(workspace_isolated),
        "home_isolated": bool(home_isolated),
        "write_tools_allowed": bool(write_tools_allowed),
        "known_limitations": limitations,
    }
    if run_root:
        meta["run_root"] = run_root
    if timestamp:
        meta["timestamp"] = timestamp
    if verified_level:
        meta["verified_level"] = verified_level
    return meta


def missing_run_meta_keys(meta: dict[str, Any]) -> list[str]:
    """Return required run-meta keys absent from ``meta``."""
    return [key for key in REQUIRED_RUN_META_KEYS if key not in meta]


def build_tool_locations(
    *,
    client_executable: str,
    run_root: str,
    isolated_workspace: str,
    client_runtime: str = "",
    engram_mcp_executable: str = "",
    file_bridge_command: str = "",
    copied_client_home: str = "",
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a normalized ``tool_locations.json`` payload."""
    payload: dict[str, Any] = {
        "client_executable": client_executable,
        "client_runtime": client_runtime,
        "engram_mcp_executable": engram_mcp_executable,
        "file_bridge_command": file_bridge_command,
        "copied_client_home": copied_client_home,
        "isolated_workspace": isolated_workspace,
        "run_root": run_root,
    }
    if extra:
        payload["extra"] = {str(k): str(v) for k, v in extra.items()}
    return payload


def snapshot_file(path: str | Path) -> FileSnapshot:
    """Read file metadata and content hash for a zero-pollution snapshot."""
    p = Path(path)
    if not p.is_file():
        return FileSnapshot(path=str(p), exists=False)
    h = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    stat = p.stat()
    return FileSnapshot(
        path=str(p),
        exists=True,
        sha256=h.hexdigest().upper(),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def snapshot_files(paths: list[str | Path] | tuple[str | Path, ...]) -> list[dict[str, Any]]:
    """Snapshot multiple files as plain dictionaries for JSON output."""
    return [snapshot_file(path).to_dict() for path in paths]


def zero_pollution_report(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare before/after file snapshots and return a structured report."""
    before_map = {str(item.get("path", "")): item for item in before}
    after_map = {str(item.get("path", "")): item for item in after}
    all_paths = sorted(set(before_map) | set(after_map))

    files: list[dict[str, Any]] = []
    for path in all_paths:
        b = before_map.get(path, {"path": path, "exists": False})
        a = after_map.get(path, {"path": path, "exists": False})
        before_exists = bool(b.get("exists"))
        after_exists = bool(a.get("exists"))
        unchanged = (
            before_exists
            and after_exists
            and b.get("sha256", "") == a.get("sha256", "")
            and int(b.get("size_bytes", 0) or 0) == int(a.get("size_bytes", 0) or 0)
        )
        status = "unchanged"
        if before_exists and not after_exists:
            status = "removed"
        elif not before_exists and after_exists:
            status = "added"
        elif before_exists and after_exists and not unchanged:
            status = "changed"
        files.append({
            "path": path,
            "status": status,
            "unchanged": unchanged,
            "before": b,
            "after": a,
        })

    changed = [item for item in files if item["status"] != "unchanged"]
    return {
        "clean": not changed,
        "checked_files": len(files),
        "changed_files": len(changed),
        "files": files,
    }


def render_zero_pollution_markdown(report: dict[str, Any], *, title: str = "零污染校验") -> str:
    """Render a Chinese, user-facing zero-pollution report."""
    lines = [
        f"# {title}",
        "",
        f"- 检查文件数：{int(report.get('checked_files', 0) or 0)}",
        f"- 变更文件数：{int(report.get('changed_files', 0) or 0)}",
        f"- 结论：{'通过，未发现真实数据变化' if report.get('clean') else '未通过，发现文件变化'}",
        "",
        "## 文件明细",
    ]
    for item in report.get("files", []):
        lines.append(f"- {item.get('path', '')}: {item.get('status', '')}")
    return "\n".join(lines) + "\n"


def validate_public_claim(
    *,
    client_id: str,
    claimed_level: str,
    claim: str,
    evidence_mode: str,
    live_agent_verified: bool = False,
) -> dict[str, Any]:
    """Guard public client-validation claims against known evidence boundaries."""
    client = (client_id or "").strip().lower()
    level = (claimed_level or "").strip().upper()
    text = (claim or "").strip().lower()
    mode = (evidence_mode or "").strip().lower()
    problems: list[str] = []

    if level not in _LEVEL_ORDER:
        problems.append("claimed_level must be one of L0-L5")

    if client == "openclaw":
        if _LEVEL_ORDER.get(level, -1) >= 4 and not live_agent_verified:
            problems.append("OpenClaw live/cross-client continuity is not verified; current evidence supports L3 static snapshot A/B only")
        if "live" in text and not live_agent_verified:
            problems.append("Do not claim OpenClaw live agent behavior without provider-authenticated evidence")
        if "agent" in text and "verified" in text and not live_agent_verified:
            problems.append("Do not describe OpenClaw agent behavior as verified from static oc-path evidence")
        if "model" in text and ("verified" in text or "continuity" in text) and not live_agent_verified:
            problems.append("Do not describe OpenClaw model continuity as verified from static oc-path evidence")
        if "static" not in mode and not live_agent_verified:
            problems.append("OpenClaw claims must identify static/file-bridge evidence unless live validation passed")

    if "works with every ai tool" in text or "full context is shared" in text:
        problems.append("Broad universal compatibility claims are not supported by client validation evidence")

    return {
        "allowed": not problems,
        "client_id": client_id,
        "claimed_level": level,
        "problems": problems,
    }
