"""Engram reconcile layer - sync external AI memory and config files into Engram.

ReconcileMixin provides:
- reconcile_memories: scan ~/.claude/projects/*/memory/*.md and import unique items
- reconcile_ai_configs: scan CLAUDE.md / .cursorrules / AGENT.md etc. and import rules
- helpers: _decode_claude_project_name, _discover_project_roots, _parse_config_sections
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .storage import SIMILARITY_THRESHOLD, _project_id


class ReconcileMixin:
    """Auto-reconcile external AI memory & configs into Engram."""

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    _CLAUDE_MEMORY_GLOBS = [
        # Claude Code auto-memory (all projects)
        "~/.claude/projects/*/memory/*.md",
    ]

    _RECONCILE_MAX_FILE_SIZE = 10_240  # 10 KB - memory files should be small

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

    @staticmethod
    def _reconcile_authorized() -> bool:
        """Check if the user has authorized auto-reconcile of external AI files.

        Returns True if:
        - ENGRAM_RECONCILE env var is set to a truthy value, OR
        - ~/.engram/telemetry_config.json has "reconcile_authorized": true

        The authorization is requested during `engram setup` and can be
        changed with ENGRAM_RECONCILE=0 env var.
        """
        env = os.environ.get("ENGRAM_RECONCILE", "").strip().lower()
        if env in ("0", "false", "off", "no"):
            return False
        if env in ("1", "true", "on", "yes"):
            return True
        # Check persisted config
        cfg_path = Path(os.environ.get("ENGRAM_DIR", "").strip() or
                        Path.home() / ".engram") / "telemetry_config.json"
        if cfg_path.is_file():
            try:
                import json as _json
                cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
                return cfg.get("reconcile_authorized", True)  # default True for existing users
            except Exception:
                pass
        return True  # default True for backward compatibility

    def reconcile_memories(self, *, project_folder: str = "") -> dict:
        """Scan external AI tool memory dirs and auto-import missing items.

        Returns a dict with sync stats.  Designed to be called silently
        during cold-start (generate_context) and session wrap-up.

        Requires reconcile authorization (granted during setup or via
        ENGRAM_RECONCILE=1 env var).
        """
        if not self._reconcile_authorized():
            result = {"imported": 0, "duplicates": 0, "scanned_files": 0,
                      "skipped_large": 0, "sources": [],
                      "skipped_reason": "reconcile not authorized"}
            if project_folder:
                result["scope"] = self._reconcile_scope_metadata(project_folder)
                result["skipped_scope"] = 0
            return result
        imported = 0
        duplicates = 0
        scanned_files = 0
        skipped_large = 0
        skipped_scope = 0
        sources: list[str] = []
        target_project_id = _project_id(project_folder) if project_folder else ""
        target_claude_project = (
            self._encode_claude_project_name(str(Path(project_folder).resolve()))
            if project_folder
            else ""
        )

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
                if target_project_id:
                    project_entry = mem_file.parent.parent
                    if project_entry.name != target_claude_project:
                        skipped_scope += 1
                        continue
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
                    project_folder=project_folder or None,
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
        result = {
            "scanned_files": scanned_files,
            "imported": imported,
            "duplicates": duplicates,
            "skipped_large": skipped_large,
            "sources": sources,
        }
        if project_folder:
            result["skipped_scope"] = skipped_scope
            result["scope"] = self._reconcile_scope_metadata(project_folder)
        return result

    def collect_memory_candidates(self) -> list[dict]:
        """Read-only scan of external AI memory files into reconcile candidates.

        Mirrors the parsing half of :meth:`reconcile_memories` but performs **no
        writes and no dedup decisions** - it only extracts ``{summary, detail,
        domain, source}`` candidate dicts for the owner-confirmed reconcile apply
        path (``reconcile_apply``) to classify. Honors the same authorization
        gate and per-file size cap. Returns ``[]`` when not authorized.
        """
        if not self._reconcile_authorized():
            return []
        candidates: list[dict] = []
        for glob_pattern in self._CLAUDE_MEMORY_GLOBS:
            expanded = Path(glob_pattern.replace("~", str(Path.home())))
            base = Path(str(expanded).split("*")[0])
            if not base.exists():
                continue
            rel_pattern = str(expanded).replace(str(base), "").lstrip("/\\")
            if not rel_pattern:
                continue
            for mem_file in base.glob(rel_pattern):
                if mem_file.name == "MEMORY.md":
                    continue
                try:
                    if mem_file.stat().st_size > self._RECONCILE_MAX_FILE_SIZE:
                        continue
                    content = mem_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                body_lines: list[str] = []
                fm_type = ""
                lines = content.splitlines()
                start_idx = 0
                if lines and lines[0].strip() == "---":
                    for i, fmline in enumerate(lines[1:], 1):
                        fms = fmline.strip()
                        if fms == "---":
                            start_idx = i + 1
                            break
                        if fms.startswith("type:"):
                            fm_type = fms.split(":", 1)[1].strip()
                    else:
                        start_idx = 0
                for line in lines[start_idx:]:
                    stripped = line.strip()
                    if (
                        stripped
                        and not stripped.startswith("#")
                        and not stripped.startswith("```")
                        and stripped != "---"
                    ):
                        body_lines.append(stripped)
                if not body_lines:
                    continue

                summary_candidate = body_lines[0][:200]
                clean_candidate = re.sub(r"[*_`\[\]()]", "", summary_candidate).strip()
                if len(clean_candidate) < 5:
                    continue

                domain = "auto_reconcile"
                if fm_type in {"project", "feedback", "reference"}:
                    domain = fm_type
                detail = "\n".join(body_lines[1:])[:500] if len(body_lines) > 1 else ""
                candidates.append({
                    "summary": summary_candidate,
                    "detail": detail,
                    "domain": domain,
                    "source": mem_file.name,
                })
        return candidates

    # ------------------------------------------------------------------
    # Project discovery from Claude Code state
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_claude_project_name(path: str) -> str:
        """Encode a native absolute path the way Claude names project dirs."""
        return re.sub(r"[^a-zA-Z0-9]", "-", str(path))

    @staticmethod
    def _decode_claude_project_name(name: str) -> Path | None:
        """Decode a Claude Code project directory name back to a real path.

        Claude encodes absolute paths by replacing every non-alphanumeric
        character with ``-``.  E.g. ``Z:\\Example Workspace``
        becomes ``Z--Example-Workspace``.

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
                    reverse=True,  # longest name first -> greedy match
                )
            except PermissionError:
                return None
            for d in candidates:
                encoded = re.sub(r"[^a-zA-Z0-9]", "-", d.name)
                if remaining == encoded:
                    return d  # exact match -> done
                if remaining.startswith(encoded + "-"):
                    current = d
                    remaining = remaining[len(encoded) + 1:]
                    matched = True
                    break
            if not matched:
                return None  # no directory matched -> give up
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

    def reconcile_ai_configs(
        self,
        *,
        search_roots: list[str] | None = None,
        max_imports: int = 25,
        project_folder: str = "",
    ) -> dict:
        """Scan AI tool config files and import unique rules into Engram.

        Discovers project roots from Claude Code project entries, then
        looks for CLAUDE.md, .cursorrules, AGENT.md, etc. in each.
        Parses markdown sections and imports meaningful directives as lessons.

        Requires reconcile authorization (granted during setup or via
        ENGRAM_RECONCILE=1 env var).
        """
        if not self._reconcile_authorized():
            result = {"imported": 0, "duplicates": 0, "scanned_files": 0,
                      "sources": [],
                      "skipped_reason": "reconcile not authorized",
                      "budget_exhausted": False}
            if project_folder:
                result["scope"] = self._reconcile_scope_metadata(project_folder)
            return result
        imported = 0
        duplicates = 0
        scanned_files = 0
        sources: list[str] = []
        budget_exhausted = False
        import_budget = max(0, int(max_imports))

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

        # Global configs (all AI tools), or explicit roots for owner/test flows.
        if project_folder:
            root_candidates = [project_folder]
        elif search_roots is not None:
            root_candidates = list(search_roots)
        else:
            root_candidates = list(self._AI_GLOBAL_CONFIGS)
        for gpath in root_candidates:
            if gpath.startswith("~/") or gpath.startswith("~\\"):
                resolved = Path.home() / gpath[2:]
            elif gpath == "~":
                resolved = Path.home()
            else:
                resolved = Path(gpath)
            if resolved.is_file():
                config_files.append(resolved)
            elif resolved.is_dir():
                if search_roots is not None or project_folder:
                    for fname in self._AI_CONFIG_FILENAMES:
                        candidate = resolved / fname
                        if candidate.is_file():
                            config_files.append(candidate)
                    for ext in ("*.md", "*.mdc", "*.txt"):
                        config_files.extend(sorted(resolved.glob(ext))[:10])
                else:
                    # Glob for rule files inside directories (e.g. ~/.cursor/rules/*.mdc)
                    for ext in ("*.md", "*.mdc", "*.txt"):
                        config_files.extend(sorted(resolved.glob(ext))[:10])

        # Project-level configs
        if search_roots is None and not project_folder:
            for root in self._discover_project_roots():
                for fname in self._AI_CONFIG_FILENAMES:
                    candidate = root / fname
                    if candidate.is_file():
                        config_files.append(candidate)
        seen_config_files: set[str] = set()
        unique_config_files: list[Path] = []
        for cfg in config_files:
            key = str(cfg.resolve()).lower()
            if key in seen_config_files:
                continue
            seen_config_files.add(key)
            unique_config_files.append(cfg)
        config_files = unique_config_files

        _MAX_CFG = 50_000  # 50 KB - config files can be larger than memory
        for cfg in config_files:
            scanned_files += 1
            try:
                fsize = cfg.stat().st_size
                if fsize > _MAX_CFG:
                    self._audit.log("warn", "reconcile_config/skip_large",
                                    detail=f"{cfg.name} ({fsize}B)")
                    continue
                content = cfg.read_text(encoding="utf-8", errors="replace")
                if "\ufffd" in content:
                    continue
            except OSError:
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

                if imported >= import_budget:
                    budget_exhausted = True
                    break

                result = self.add_lesson(
                    summary_candidate,
                    domain="ai_config",
                    detail=section_body[:500],
                    source_tool="config_scan",
                    tier="staging",
                    project_folder=project_folder or None,
                )
                if result.get("status") != "duplicate":
                    imported += 1
                    sources.append(f"{cfg.parent.name}/{cfg.name}")
                    existing_summaries.add(summary_candidate)
                else:
                    duplicates += 1
            if budget_exhausted:
                break

        result = {
            "scanned_files": scanned_files,
            "imported": imported,
            "duplicates": duplicates,
            "sources": sources,
            "budget_exhausted": budget_exhausted,
        }
        if project_folder:
            result["scope"] = self._reconcile_scope_metadata(project_folder)
        return result

    @staticmethod
    def _reconcile_scope_metadata(project_folder: str) -> dict[str, str]:
        return {
            "mode": "project_exact" if project_folder else "global",
            "project_id": _project_id(project_folder) if project_folder else "",
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
