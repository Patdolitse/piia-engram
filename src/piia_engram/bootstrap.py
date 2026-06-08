"""Auto-bootstrap: ingest existing CLAUDE.md / AGENTS.md on first MCP call.

When ``get_user_context`` or ``get_resume_brief`` detects an empty store
(no lessons, no decisions, minimal profile), this module scans the local
machine for AI tool rule files, classifies their content, and imports
preferences and project rules — so the user experiences "it already knows me"
without running ``engram setup`` first.

The scan reuses the same classification logic as ``setup_wizard`` (user vs.
project keywords, language extraction) but is stripped of all CLI/printing
dependencies so it can run silently inside the MCP server.

Safety:
- Only triggers ONCE per store (writes a ``_bootstrap_done`` marker).
- All imported content goes through the N3 risk-based write gate: low/medium
  risk auto-verifies, high-risk (value-bearing credentials etc.) routes to
  staging for owner review.
- Never reads files larger than 1500 lines (same limit as setup_wizard).
- Never modifies any file outside the Engram store.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from piia_engram.core import Engram

logger = logging.getLogger(__name__)

# Same keyword patterns as setup_wizard (kept in sync via shared constants).
_USER_KEYWORDS = re.compile(
    r"(语言|language|中文|english|角色|role|我是|i am|偏好|prefer|always|never"
    r"|禁止|必须|风格|style|tone|沟通|communicate|所有沟通|技术水平|technical.?level"
    r"|工作方式|work.?style|习惯|habit)",
    re.IGNORECASE,
)
_PROJECT_KEYWORDS = re.compile(
    r"(这个.?repo|this.?repo|测试|test|build|deploy|ci/cd|ci |cd "
    r"|pre.?commit|hook|lint|tailwind|webpack|vite|docker|makefile"
    r"|\.env|package\.json|tsconfig|eslint|prettier|migration"
    r"|数据库|database|schema|endpoint|路由|route|api)",
    re.IGNORECASE,
)

_MAX_RULE_LINES = 1500
_MAX_DETAIL_CHARS = 20000

_MARKER_FILENAME = ".bootstrap_done"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def needs_bootstrap(engram: "Engram") -> bool:
    """Return True if the store is effectively empty and hasn't been bootstrapped."""
    # Already bootstrapped?
    marker = engram.root / _MARKER_FILENAME
    if marker.exists():
        return False

    # Store has content?
    try:
        lessons = engram.get_lessons(limit=3, _update_access=False, _migrate_fields=False)
        if lessons:
            return False
        decisions = engram.get_decisions(limit=3, _update_access=False, _migrate_fields=False)
        if decisions:
            return False
    except Exception:
        return False

    return True


def run_bootstrap(engram: "Engram") -> dict[str, Any]:
    """Scan for rule files and import them. Returns a result summary.

    Idempotent: marks the store as bootstrapped regardless of whether files
    were found, so this never fires twice.
    """
    result: dict[str, Any] = {
        "bootstrapped": True,
        "files_scanned": 0,
        "user_rules_imported": 0,
        "project_rules_imported": 0,
        "language_detected": "",
    }

    try:
        rule_files = _scan_rule_files()
        result["files_scanned"] = len(rule_files)

        if rule_files:
            import_result = _import_rules(engram, rule_files)
            result["user_rules_imported"] = import_result.get("user_count", 0)
            result["project_rules_imported"] = import_result.get("project_count", 0)
            result["language_detected"] = import_result.get("language", "")
    except Exception as exc:
        logger.debug("bootstrap scan failed: %s", exc)
        result["error"] = str(exc)
    finally:
        # Always mark bootstrapped — don't retry on errors.
        _mark_bootstrapped(engram)

    return result


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _mark_bootstrapped(engram: "Engram") -> None:
    """Write a flag so bootstrap doesn't re-trigger."""
    try:
        marker = engram.root / _MARKER_FILENAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
    except Exception:
        pass


def _scan_rule_files() -> list[dict]:
    """Discover CLAUDE.md / AGENTS.md / .cursorrules on this machine."""
    home = Path.home()
    found: list[dict] = []

    # Global files
    global_candidates: list[Path] = []

    claude_md = home / ".claude" / "CLAUDE.md"
    if claude_md.is_file():
        global_candidates.append(claude_md)

    # Cursor global rules
    cursor_rules_dir = home / ".cursor" / "rules"
    if cursor_rules_dir.is_dir():
        global_candidates.extend(sorted(cursor_rules_dir.glob("*.mdc"))[:5])

    # Claude Code project-level instructions
    claude_projects = home / ".claude" / "projects"
    if claude_projects.is_dir():
        for proj_claude in sorted(claude_projects.glob("*/CLAUDE.md"))[:10]:
            global_candidates.append(proj_claude)

    for path in global_candidates:
        entry = _read_rule_file(path, "global")
        if entry:
            found.append(entry)

    # Project-level files in common locations
    # (CWD is often meaningless for MCP servers, so check well-known roots)
    codex_agents = home / ".codex" / "AGENTS.md"
    if codex_agents.is_file():
        entry = _read_rule_file(codex_agents, "global")
        if entry:
            found.append(entry)

    return found


def _read_rule_file(path: Path, scope: str) -> dict | None:
    """Read a single rule file, respecting size limits."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    lines = text.splitlines()[:_MAX_RULE_LINES]
    content_lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if len(content_lines) < 2:
        return None

    return {"path": path, "scope": scope, "lines": lines}


def _classify_line(line: str, scope: str) -> str:
    """Classify a line as 'user' / 'project' / 'skip'."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("---"):
        return "skip"
    has_cjk = any("一" <= ch <= "鿿" for ch in stripped)
    min_len = 4 if has_cjk else 8
    if len(stripped) < min_len:
        return "skip"
    if stripped.startswith("```") or stripped.startswith("<!--"):
        return "skip"

    has_user = bool(_USER_KEYWORDS.search(stripped))
    has_project = bool(_PROJECT_KEYWORDS.search(stripped))

    if has_user and not has_project:
        return "user"
    if has_project and not has_user:
        return "project"
    if has_user and has_project:
        return "user" if scope == "global" else "project"
    return "user" if scope == "global" else "project"


def _src_label(path: Path) -> str:
    """Short label for provenance: parent_dir/filename."""
    parent = path.parent.name or "."
    return f"{parent}/{path.name}"


def _import_rules(engram: "Engram", rule_files: list[dict]) -> dict[str, Any]:
    """Classify and import rule files into the store."""
    user_sections: dict[str, list[str]] = {}
    project_sections: dict[str, list[str]] = {}
    user_count = 0
    project_count = 0
    skipped = 0
    language = ""

    for rf in rule_files:
        scope = rf["scope"]
        label = _src_label(rf["path"])
        for line in rf["lines"]:
            category = _classify_line(line, scope)
            stripped = line.strip()
            if category == "user":
                user_sections.setdefault(label, []).append(stripped)
                user_count += 1
                lower = stripped.lower()
                if "中文" in stripped and not language:
                    language = "zh-CN"
                elif "english" in lower and not language:
                    language = "en"
            elif category == "project":
                project_sections.setdefault(label, []).append(stripped)
                project_count += 1
            else:
                skipped += 1

    # Write language to profile
    if language:
        try:
            engram.update_profile({"language": language})
        except Exception:
            pass

    # Write grouped lessons (same pattern as setup_wizard)
    if user_sections:
        detail = _build_grouped_detail(user_sections)
        engram.add_lesson(
            {"summary": "用户身份与偏好（首次连接自动导入）",
             "domain": "user_preference",
             "detail": detail,
             "source_tool": "engram_bootstrap"},
        )

    if project_sections:
        detail = _build_grouped_detail(project_sections)
        engram.add_lesson(
            {"summary": "项目规则（首次连接自动导入）",
             "domain": "project_rules",
             "detail": detail,
             "source_tool": "engram_bootstrap"},
        )

    return {
        "user_count": user_count,
        "project_count": project_count,
        "skipped": skipped,
        "language": language,
    }


def _build_grouped_detail(sections: dict[str, list[str]]) -> str:
    """Render {source_label: [rules]} as sectioned markdown detail."""
    parts: list[str] = []
    for label, rules in sections.items():
        parts.append(f"## {label}")
        parts.extend(f"- {r}" for r in rules)
        parts.append("")
    detail = "\n".join(parts).strip()
    if len(detail) > _MAX_DETAIL_CHARS:
        clipped = detail[:_MAX_DETAIL_CHARS]
        last_nl = clipped.rfind("\n")
        if last_nl > 0:
            clipped = clipped[:last_nl]
        detail = clipped.rstrip() + "\n\n…(truncated)"
    return detail
