"""Engram reconcile layer — sync external AI memory and config files into Engram.

ReconcileMixin provides:
- reconcile_memories: scan ~/.claude/projects/*/memory/*.md and import unique items
- reconcile_ai_configs: scan CLAUDE.md / .cursorrules / AGENT.md etc. and import rules
- helpers: _decode_claude_project_name, _discover_project_roots, _parse_config_sections
"""

from __future__ import annotations

import re
from pathlib import Path

from .storage import SIMILARITY_THRESHOLD


class ReconcileMixin:
    """Auto-reconcile external AI memory & configs into Engram."""

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    _CLAUDE_MEMORY_GLOBS = [
        # Claude Code auto-memory (all projects)
        "~/.claude/projects/*/memory/*.md",
    ]

    _RECONCILE_MAX_FILE_SIZE = 10_240  # 10 KB — memory files should be small

    # Config file names to look for in each discovered project root
    _AI_CONFIG_FILENAMES = [
        # Claude Code / Codex
        "CLAUDE.md",
        "AGENTS.md",
        # Cursor
        ".cursorrules",
        # Windsurf (Codeium)
        ".windsurfrules",
        # GitHub Copilot (VS Code / JetBrains)
        ".github/copilot-instructions.md",
        # Trae (ByteDance IDE)
        ".trae/rules",
        # OpenClaw / Hermes
        "SOUL.md",
        "USER.md",
        # Generic agent configs
        "AGENT.md",
        "codex.md",
    ]

    # Global config paths to scan (in addition to per-project files)
    _AI_GLOBAL_CONFIGS = [
        "~/.claude/CLAUDE.md",
        "~/.cursor/rules",
        "~/.trae/rules",
        "~/.codeium/windsurf/rules",
    ]

    # ------------------------------------------------------------------
    # Memory file sync
    # ------------------------------------------------------------------

    def reconcile_memories(self) -> dict:
        """Scan external AI tool memory dirs and auto-import missing items.

        Returns a dict with sync stats.  Designed to be called silently
        during cold-start (generate_context) and session wrap-up.
        """
        imported = 0
        duplicates = 0
        scanned_files = 0
        skipped_large = 0
        sources: list[str] = []

        existing_lessons = self.get_lessons(limit=None, _update_access=False)
        existing_decisions = self.get_decisions(limit=None, _update_access=False)
        existing_summaries = {
            lesson.get("summary", "")
            for lesson in existing_lessons
        }
        # Also include decision texts for dedup
        for d in existing_decisions:
            existing_summaries.add(d.get("question", ""))
            existing_summaries.add(d.get("choice", ""))
        existing_summaries.discard("")

        for glob_pattern in self._CLAUDE_MEMORY_GLOBS:
            expanded = Path(glob_pattern.replace("~", str(Path.home())))
            # Use the parent with glob since Path.glob needs a relative pattern
            base = Path(str(expanded).split("*")[0])
            if not base.exists():
                continue

            # Reconstruct relative glob from base
            rel_pattern = str(expanded).replace(str(base), "").lstrip("/\\")
            if not rel_pattern:
                continue

            for mem_file in base.glob(rel_pattern):
                if mem_file.name == "MEMORY.md":
                    continue  # Index file, not a memory
                scanned_files += 1
                try:
                    fsize = mem_file.stat().st_size
                    if fsize > self._RECONCILE_MAX_FILE_SIZE:
                        skipped_large += 1
                        self._audit.log("warn", "reconcile/skip_large",
                                        detail=f"{mem_file.name} ({fsize}B)")
                        continue
                    content = mem_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                # Extract core content (skip YAML frontmatter)
                body_lines = []
                fm_type = ""
                lines = content.splitlines()
                start_idx = 0
                # YAML frontmatter: only valid at the very beginning of file
                if lines and lines[0].strip() == "---":
                    for i, fmline in enumerate(lines[1:], 1):
                        fms = fmline.strip()
                        if fms == "---":
                            start_idx = i + 1
                            break
                        if fms.startswith("type:"):
                            fm_type = fms.split(":", 1)[1].strip()
                    else:
                        # No closing --- found, treat entire file as content
                        start_idx = 0
                for line in lines[start_idx:]:
                    stripped = line.strip()
                    if (
                        stripped
                        and not stripped.startswith("#")
                        and not stripped.startswith("```")
                        and stripped != "---"  # skip horizontal rules
                    ):
                        body_lines.append(stripped)

                if not body_lines:
                    continue

                # Use first meaningful line as summary candidate
                # Strip markdown formatting for better similarity matching
                summary_candidate = body_lines[0][:200]
                clean_candidate = re.sub(r"[*_`\[\]()]", "", summary_candidate).strip()

                # Skip entries with no meaningful text after cleanup
                if len(clean_candidate) < 5:
                    continue

                # Check similarity against existing Engram knowledge
                is_dup = False
                for existing in existing_summaries:
                    clean_existing = re.sub(r"[*_`\[\]()]", "", existing).strip()
                    sim = self._bigram_similarity(clean_candidate, clean_existing)
                    if sim >= SIMILARITY_THRESHOLD:
                        is_dup = True
                        duplicates += 1
                        break

                if is_dup:
                    continue

                # Auto-import as lesson
                domain = "auto_reconcile"
                if fm_type == "project":
                    domain = "project"
                elif fm_type == "feedback":
                    domain = "feedback"
                elif fm_type == "reference":
                    domain = "reference"

                detail = "\n".join(body_lines[1:])[:500] if len(body_lines) > 1 else ""
                result = self.add_lesson(
                    summary_candidate,
                    domain=domain,
                    detail=detail,
                    source_tool="auto_reconcile",
                    tier="staging",
                )
                if result.get("status") != "duplicate":
                    imported += 1
                    sources.append(mem_file.name)
                    existing_summaries.add(summary_candidate)
                else:
                    duplicates += 1

        self._audit.log("read", "reconcile_memories",
                        detail=f"scanned={scanned_files} imported={imported} "
                               f"dup={duplicates} skipped_large={skipped_large}")
        return {
            "scanned_files": scanned_files,
            "imported": imported,
            "duplicates": duplicates,
            "skipped_large": skipped_large,
            "sources": sources,
        }

    # ------------------------------------------------------------------
    # Project discovery from Claude Code state
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_claude_project_name(name: str) -> Path | None:
        """Decode a Claude Code project directory name back to a real path.

        Claude encodes absolute paths by replacing every non-alphanumeric
        character with ``-``.  E.g. ``E:\\Personal Intelligence Identity Asset``
        becomes ``E--Personal-Intelligence-Identity-Asset``.

        We reverse this by: drive letter + walk the filesystem, greedily
        matching directory names against remaining encoded segments.
        """
        if len(name) < 3 or name[1:3] != "--":
            return None
        drive = name[0]
        rest = name[3:]  # encoded remainder after drive letter
        if not rest:
            return None
        drive_root = Path(f"{drive}:/")
        if not drive_root.exists():
            return None

        # Greedy walk: at each level try to match the longest dir name
        current = drive_root
        remaining = rest
        while remaining:
            matched = False
            try:
                candidates = sorted(
                    (d for d in current.iterdir() if d.is_dir()),
                    key=lambda d: len(d.name),
                    reverse=True,  # longest name first → greedy match
                )
            except PermissionError:
                return None
            for d in candidates:
                encoded = re.sub(r"[^a-zA-Z0-9]", "-", d.name)
                if remaining == encoded:
                    return d  # exact match → done
                if remaining.startswith(encoded + "-"):
                    current = d
                    remaining = remaining[len(encoded) + 1:]
                    matched = True
                    break
            if not matched:
                return None  # no directory matched → give up
        return current

    def _discover_project_roots(self) -> list[Path]:
        """Discover project root dirs from Claude Code project entries."""
        claude_projects = Path.home() / ".claude" / "projects"
        roots: list[Path] = []
        if not claude_projects.exists():
            return roots
        seen: set[str] = set()
        for entry in claude_projects.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if "--claude-worktrees-" in name:
                continue
            resolved = self._decode_claude_project_name(name)
            if resolved and resolved.exists():
                key = str(resolved).lower()
                if key not in seen:
                    seen.add(key)
                    roots.append(resolved)
        return roots

    # ------------------------------------------------------------------
    # AI config file sync
    # ------------------------------------------------------------------

    def reconcile_ai_configs(self) -> dict:
        """Scan AI tool config files and import unique rules into Engram.

        Discovers project roots from Claude Code project entries, then
        looks for CLAUDE.md, .cursorrules, AGENT.md, etc. in each.
        Parses markdown sections and imports meaningful directives as lessons.
        """
        imported = 0
        duplicates = 0
        scanned_files = 0
        sources: list[str] = []

        existing_lessons = self.get_lessons(limit=None, _update_access=False)
        existing_decisions = self.get_decisions(limit=None, _update_access=False)
        existing_summaries = {
            lesson.get("summary", "") for lesson in existing_lessons
        }
        for d in existing_decisions:
            existing_summaries.add(d.get("question", ""))
            existing_summaries.add(d.get("choice", ""))
        existing_summaries.discard("")

        # Collect all config files to scan
        config_files: list[Path] = []

        # Global configs (all AI tools)
        for gpath in self._AI_GLOBAL_CONFIGS:
            resolved = Path(gpath.replace("~", str(Path.home())))
            if resolved.is_file():
                config_files.append(resolved)
            elif resolved.is_dir():
                # Glob for rule files inside directories (e.g. ~/.cursor/rules/*.mdc)
                for ext in ("*.md", "*.mdc", "*.txt"):
                    config_files.extend(sorted(resolved.glob(ext))[:10])

        # Project-level configs
        for root in self._discover_project_roots():
            for fname in self._AI_CONFIG_FILENAMES:
                candidate = root / fname
                if candidate.is_file():
                    config_files.append(candidate)

        _MAX_CFG = 50_000  # 50 KB — config files can be larger than memory
        for cfg in config_files:
            scanned_files += 1
            try:
                fsize = cfg.stat().st_size
                if fsize > _MAX_CFG:
                    self._audit.log("warn", "reconcile_config/skip_large",
                                    detail=f"{cfg.name} ({fsize}B)")
                    continue
                content = cfg.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Parse into sections by ## headers
            sections = self._parse_config_sections(content, cfg.name)
            for section_title, section_body in sections:
                clean_body = re.sub(r"[*_`\[\]()]", "", section_body).strip()
                if len(clean_body) < 15:
                    continue

                # Use section title + first line as summary
                first_line = clean_body.split("\n")[0][:150]
                summary_candidate = (
                    f"[{cfg.name}] {section_title}: {first_line}"
                    if section_title
                    else f"[{cfg.name}] {first_line}"
                )

                # Dedup check
                is_dup = False
                clean_summary = re.sub(
                    r"[*_`\[\]()]", "", summary_candidate
                ).strip()
                for existing in existing_summaries:
                    clean_existing = re.sub(
                        r"[*_`\[\]()]", "", existing
                    ).strip()
                    sim = self._bigram_similarity(clean_summary, clean_existing)
                    if sim >= SIMILARITY_THRESHOLD:
                        is_dup = True
                        duplicates += 1
                        break

                if is_dup:
                    continue

                result = self.add_lesson(
                    summary_candidate,
                    domain="ai_config",
                    detail=section_body[:500],
                    source_tool="config_scan",
                    tier="staging",
                )
                if result.get("status") != "duplicate":
                    imported += 1
                    sources.append(f"{cfg.parent.name}/{cfg.name}")
                    existing_summaries.add(summary_candidate)
                else:
                    duplicates += 1

        return {
            "scanned_files": scanned_files,
            "imported": imported,
            "duplicates": duplicates,
            "sources": sources,
        }

    @staticmethod
    def _parse_config_sections(
        content: str, filename: str
    ) -> list[tuple[str, str]]:
        """Parse a markdown config file into (title, body) sections."""
        lines = content.splitlines()

        # Skip YAML frontmatter (only at file start)
        start = 0
        if lines and lines[0].strip() == "---":
            for i, fl in enumerate(lines[1:], 1):
                if fl.strip() == "---":
                    start = i + 1
                    break
            else:
                start = 0  # no closing ---, treat as content

        sections: list[tuple[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in lines[start:]:
            stripped = line.strip()
            if re.match(r"^#{1,6}\s", stripped):
                if current_lines:
                    body = "\n".join(current_lines).strip()
                    if body:
                        sections.append((current_title, body))
                current_title = stripped.lstrip("#").strip()
                current_lines = []
            elif stripped and stripped != "---":
                current_lines.append(stripped)

        if current_lines:
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_title, body))

        return sections
