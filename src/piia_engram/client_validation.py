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

LEVEL_EVIDENCE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "L0": ("run_meta_complete", "tool_locations_recorded"),
    "L1": ("client_config_summary", "prompts_recorded"),
    "L2": ("raw_artifacts", "parsed_artifacts"),
    "L3": ("ab_control", "signal_differential", "zero_pollution_clean"),
    "L4": ("cross_client_marker",),
    "L5": ("public_safe_summary", "claim_guard_passed"),
}


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


def evidence_readiness(evidence: dict[str, Any], *, target_level: str = "L5") -> dict[str, Any]:
    """Evaluate the highest public validation level supported by evidence.

    The function is deliberately pure and metadata-only. Callers can pass a
    small evidence summary assembled from a run directory, then gate public
    wording before writing ``verified_level`` into reports or README copy.
    """
    target = (target_level or "").strip().upper()
    if target not in _LEVEL_ORDER:
        return {
            "allowed": False,
            "target_level": target,
            "highest_ready_level": "",
            "required": [],
            "satisfied": [],
            "missing": ["target_level must be one of L0-L5"],
        }

    required: list[str] = []
    satisfied: list[str] = []
    missing: list[str] = []
    highest_ready_level = ""
    cumulative_missing = False

    for level, order in sorted(_LEVEL_ORDER.items(), key=lambda item: item[1]):
        if order > _LEVEL_ORDER[target]:
            continue
        level_requirements = list(LEVEL_EVIDENCE_REQUIREMENTS[level])
        required.extend(level_requirements)
        level_missing: list[str] = []
        for requirement in level_requirements:
            if _evidence_requirement_met(evidence, requirement):
                satisfied.append(requirement)
            else:
                missing.append(requirement)
                level_missing.append(requirement)
        if level_missing:
            cumulative_missing = True
        if not cumulative_missing:
            highest_ready_level = level

    return {
        "allowed": not missing,
        "target_level": target,
        "highest_ready_level": highest_ready_level,
        "required": required,
        "satisfied": satisfied,
        "missing": missing,
    }


def _evidence_requirement_met(evidence: dict[str, Any], requirement: str) -> bool:
    if requirement in evidence:
        value = evidence.get(requirement)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value > 0
        return bool(value)

    if requirement == "run_meta_complete":
        meta = evidence.get("run_meta")
        return isinstance(meta, dict) and not missing_run_meta_keys(meta)
    if requirement == "tool_locations_recorded":
        return bool(evidence.get("tool_locations"))
    if requirement == "signal_differential":
        return int(evidence.get("signal_differential", 0) or 0) > 0
    if requirement == "zero_pollution_clean":
        return bool(evidence.get("zero_pollution", {}).get("clean"))
    if requirement == "claim_guard_passed":
        return bool(evidence.get("claim_allowed")) or bool(evidence.get("claim_guard", {}).get("allowed"))
    return False


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


def snapshot_tree(root: str | Path) -> dict[str, str]:
    """Map every file under *root* to its sha256, keyed by POSIX-style relpath.

    Deterministic and order-independent (the dict is built from a sorted walk).
    Used for directory-level zero-pollution: comparing two snapshots proves a
    copied store arm was not mutated by a read-only run, without ever reading a
    knowledge body into the report.
    """
    base = Path(root)
    out: dict[str, str] = {}
    if not base.exists():
        return out
    for path in sorted(base.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(base)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return out


def tree_digest(tree: dict[str, str]) -> str:
    """Single rolled-up sha256 over a :func:`snapshot_tree` map.

    Stable across runs and machines: derived only from sorted ``(relpath, hash)``
    pairs, so two byte-identical stores in different temp dirs produce the same
    digest. Empty tree → sha256 of the empty string marker.
    """
    h = hashlib.sha256()
    for rel in sorted(tree):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(tree[rel].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest().upper()


def build_ab_arm(
    *,
    arm: str,
    engram_enabled: bool,
    surfaced_signal_count: int,
    before_tree: dict[str, str],
    after_tree: dict[str, str],
) -> dict[str, Any]:
    """Build one metadata-only A/B arm record from before/after tree snapshots.

    Records the arm name, whether the Engram MCP read surface was enabled, the
    number of knowledge *signals* a read-only recall surfaced (a count, never a
    body), and a directory-level zero-pollution verdict (digest before == after).
    Pure: derives everything from its inputs, mutates nothing.
    """
    before_digest = tree_digest(before_tree)
    after_digest = tree_digest(after_tree)
    return {
        "arm": str(arm),
        "engram_enabled": bool(engram_enabled),
        "surfaced_signal_count": max(0, int(surfaced_signal_count)),
        "store_digest_before": before_digest,
        "store_digest_after": after_digest,
        "zero_pollution_clean": before_digest == after_digest,
        "checked_file_count": len(before_tree),
    }


def build_ab_evidence(
    *,
    on_arm: dict[str, Any],
    off_arm: dict[str, Any],
    client_id: str = "synthetic-offline",
    live_store_digest_before: str = "",
    live_store_digest_after: str = "",
) -> dict[str, Any]:
    """Combine an Engram-on and Engram-off arm into a deterministic A/B report.

    The report is metadata-only and byte-stable: it carries counts, booleans and
    content digests, never knowledge bodies or absolute paths. The core claim it
    supports is a *signal differential* — the Engram-on arm surfaces strictly
    more knowledge signals than the Engram-off arm — proven without any live
    provider auth or network. ``live_store_digest_*`` are optional; when both are
    supplied they assert the real user store fingerprint was unchanged.
    """
    on_count = max(0, int(on_arm.get("surfaced_signal_count", 0) or 0))
    off_count = max(0, int(off_arm.get("surfaced_signal_count", 0) or 0))
    arms_clean = bool(on_arm.get("zero_pollution_clean")) and bool(
        off_arm.get("zero_pollution_clean")
    )
    if live_store_digest_before or live_store_digest_after:
        live_untouched = bool(
            live_store_digest_before
            and live_store_digest_after
            and live_store_digest_before == live_store_digest_after
        )
    else:
        live_untouched = None

    signal_differential = on_count - off_count
    return {
        "schema": 1,
        "harness": "client_ab_offline_v1",
        "synthetic_only": True,
        "live_provider_auth": False,
        "network_used": False,
        "client_id": str(client_id),
        "on_arm": dict(on_arm),
        "off_arm": dict(off_arm),
        "signal_differential": signal_differential,
        "differential_positive": signal_differential > 0,
        "arms_zero_pollution_clean": arms_clean,
        "live_store_untouched": live_untouched,
        "overall_passed": (
            signal_differential > 0
            and arms_clean
            and (live_untouched is not False)
        ),
    }


def build_public_safe_summary(
    evidence: dict[str, Any],
    *,
    claimed_level: str = "L3",
    claim: str = "",
    evidence_mode: str = "static offline A/B (copied synthetic store)",
) -> dict[str, Any]:
    """Project A/B evidence to a public-safe summary, gated by the claim guard.

    Strips everything but counts/booleans and routes the headline claim through
    :func:`validate_public_claim` so an offline static A/B run cannot be dressed
    up as live-agent or universal-compatibility evidence. ``claim_allowed`` and
    ``claim_problems`` make any overclaim explicit instead of silently passing.
    """
    client_id = str(evidence.get("client_id") or "")
    default_claim = (
        f"{client_id or 'client'} offline A/B shows an Engram signal differential "
        "of "
        f"{int(evidence.get('signal_differential', 0) or 0)} with zero copied-store "
        "pollution"
    )
    guard = validate_public_claim(
        client_id=client_id,
        claimed_level=claimed_level,
        claim=claim or default_claim,
        evidence_mode=evidence_mode,
        live_agent_verified=False,
    )
    on_arm = evidence.get("on_arm") or {}
    off_arm = evidence.get("off_arm") or {}
    return {
        "schema": 1,
        "client_id": client_id,
        "evidence_mode": evidence_mode,
        "claimed_level": guard.get("claimed_level", ""),
        "engram_on_signal_count": int(on_arm.get("surfaced_signal_count", 0) or 0),
        "engram_off_signal_count": int(off_arm.get("surfaced_signal_count", 0) or 0),
        "signal_differential": int(evidence.get("signal_differential", 0) or 0),
        "arms_zero_pollution_clean": bool(evidence.get("arms_zero_pollution_clean")),
        "live_provider_auth": False,
        "network_used": False,
        "claim_allowed": bool(guard.get("allowed")),
        "claim_problems": list(guard.get("problems", [])),
    }


def render_public_safe_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a public-safe A/B summary as a compact, user-facing report."""
    verdict = "通过" if summary.get("claim_allowed") and summary.get(
        "arms_zero_pollution_clean"
    ) else "未通过"
    lines = [
        "# 离线客户端 A/B 证据（合成、零网络）",
        "",
        f"- 客户端：{summary.get('client_id', '')}",
        f"- 证据模式：{summary.get('evidence_mode', '')}",
        f"- Engram 开启信号数：{summary.get('engram_on_signal_count', 0)}",
        f"- Engram 关闭信号数：{summary.get('engram_off_signal_count', 0)}",
        f"- 信号差：{summary.get('signal_differential', 0)}",
        f"- 拷贝库零污染：{'是' if summary.get('arms_zero_pollution_clean') else '否'}",
        f"- 实时 Provider 认证：{'有' if summary.get('live_provider_auth') else '无（离线）'}",
        f"- 公开声称许可：{'允许' if summary.get('claim_allowed') else '拒绝'}",
        f"- 结论：{verdict}",
    ]
    problems = summary.get("claim_problems") or []
    if problems:
        lines.append("")
        lines.append("## 声称护栏拦截")
        for problem in problems:
            lines.append(f"- {problem}")
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
