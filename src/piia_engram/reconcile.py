"""Import engine for other AI tools' memory and config files.

Nothing here runs on its own: the Owner's ``engram import-memories`` command
(see ``memory_import``) is the only caller, and every item lands in the review
queue (staging tier).

ReconcileMixin provides:
- reconcile_memories: scan ~/.claude/projects/*/memory/*.md and import unique items
- reconcile_ai_configs: scan CLAUDE.md / .cursorrules / AGENT.md etc. and import rules
- both take dry_run=True for a zero-write plan of the same items
- helpers: _decode_claude_project_name, _discover_project_roots, _parse_config_sections
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from contextlib import contextmanager
from contextvars import ContextVar

from .storage import SIMILARITY_THRESHOLD, _project_id, overflow_batch


def _reconcile_env() -> str:
    env = os.environ.get("ENGRAM_RECONCILE", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return "off"
    if env in ("1", "true", "on", "yes"):
        return "on"
    return ""


def _reconcile_config_value(root=None):
    """``reconcile_authorized`` from ``<root>/telemetry_config.json``, or None.

    ``root`` is the store being imported into; without it the ENGRAM_DIR /
    ``~/.engram`` store is used.
    """
    base = Path(root) if root is not None else Path(
        os.environ.get("ENGRAM_DIR", "").strip() or Path.home() / ".engram"
    )
    cfg_path = base / "telemetry_config.json"
    if not cfg_path.is_file():
        return None
    try:
        import json as _json
        cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(cfg, dict) or "reconcile_authorized" not in cfg:
        return None
    return cfg.get("reconcile_authorized")


# 4.21.2: an imported memory keeps its whole body. The source files are capped at
# _RECONCILE_MAX_FILE_SIZE, so this only bites on config sections; when it does,
# the cut is marked in the text itself, with the source hash, never silent.
_RECONCILE_DETAIL_MAX = 10_240


def _bounded_detail(text: str, *, source: str = "") -> str:
    text = str(text or "")
    if len(text) <= _RECONCILE_DETAIL_MAX:
        return text
    import hashlib

    digest = hashlib.sha256((source or text).encode("utf-8")).hexdigest()[:16]
    return (
        text[:_RECONCILE_DETAIL_MAX]
        + f"\n[truncated: kept {_RECONCILE_DETAIL_MAX} of {len(text)} characters; source sha256 {digest}]"
    )


def _parse_memory_file(content: str) -> dict | None:
    """Summary, full detail and frontmatter type of one AI memory file.

    The summary is the frontmatter ``description`` when there is one, else the first
    paragraph (not the first hard-wrapped line); the detail is the whole body.
    ``legacy_summary`` is what 4.21.1 and earlier used as the summary (the first
    line) -- dedup and rejection checks look at it too, so a reworded summary
    cannot bring back a memory already imported or rejected.
    """
    lines = content.splitlines()
    start_idx = 0
    front: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        for i, fmline in enumerate(lines[1:], 1):
            fms = fmline.strip()
            if fms == "---":
                start_idx = i + 1
                break
            key, sep, value = fms.partition(":")
            if sep and key.strip():
                front.setdefault(key.strip().lower(), value.strip().strip("\"'"))
        else:
            start_idx = 0
            front = {}

    def _keep(stripped: str) -> bool:
        return bool(stripped) and not stripped.startswith("#") and not stripped.startswith("```") and stripped != "---"

    body_lines = [line.strip() for line in lines[start_idx:] if _keep(line.strip())]
    if not body_lines:
        return None
    paragraph: list[str] = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        if _keep(stripped):
            paragraph.append(stripped)
        elif paragraph:
            break
    description = front.get("description", "")
    if len(re.sub(r"[*_`\[\]()]", "", description).strip()) >= 5:
        summary = description
    else:
        summary = " ".join(paragraph)
    return {
        "summary": summary[:200],
        "legacy_summary": body_lines[0][:200],
        "detail": _bounded_detail("\n".join(body_lines), source=content),
        "fm_type": front.get("type", ""),
    }


def _rejected_before(root, summary: str, *, project_folder: str | None = None) -> bool:
    """A lesson tombstone for this exact summary in the scope the row would get.

    Same rule as the insert guard (tombstones.lookup: same h1, same scope), applied
    to the 4.21.1-style first-line summary.
    """
    from . import tombstones as _tombstones
    from .storage import _project_id

    if not summary:
        return False
    row = {"summary": summary}
    if isinstance(project_folder, str) and project_folder.strip():
        row["project_id"] = _project_id(project_folder)
    return _tombstones.lookup(root, "lesson", row) is not None


# Insert outcomes that add no row: an existing duplicate, a tombstoned claim, or a
# retired one. Reconcile counts them as duplicates.
_NOT_IMPORTED = frozenset({"duplicate", "rejected_before", "duplicate_retired"})

RECONCILE_ENV_OVERRIDDEN = "reconcile_env_overridden_by_config"


# Set while the Owner has just asked for an import in `engram setup` but the
# store still carries an earlier reconcile_authorized=false: the list may be
# shown, and the stored "no" is lifted only when they confirm that list.
# ENGRAM_RECONCILE=0 is never overridden.
_OWNER_PREVIEW_CONSENT: ContextVar[bool] = ContextVar("engram_owner_preview_consent", default=False)


@contextmanager
def owner_preview_consent():
    token = _OWNER_PREVIEW_CONSENT.set(True)
    try:
        yield
    finally:
        _OWNER_PREVIEW_CONSENT.reset(token)


def reconcile_env_conflict_note(root=None) -> str:
    """Non-empty when ENGRAM_RECONCILE=1 is set but the config keeps reconcile off."""
    if _reconcile_env() == "on" and _reconcile_config_value(root) is False:
        return (
            "ENGRAM_RECONCILE=1 is set, but reconcile_authorized=false in "
            "telemetry_config.json wins: reconcile stays off. Remove the variable, "
            "or change the config key if reconcile should run."
        )
    return ""


def _display_path(path: Path) -> str:
    """``~/...`` for files under the home directory, else the full path."""
    try:
        return "~/" + Path(path).resolve().relative_to(Path.home().resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def _file_sha256(path: Path) -> str:
    import hashlib

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _import_item(
    source: str,
    path: Path,
    summary: str,
    detail: str,
    *,
    domain: str = "",
    source_tool: str = "",
    project_folder: str = "",
    label: str = "",
) -> dict:
    """One planned import: the exact row to write, where it comes from, and hashes.

    ``content_sha256`` hashes the text that will be written; ``source_sha256``
    the source file as it was when the plan was made, so the writer can tell
    (and the receipt can record) that the file changed after the Owner
    confirmed the list. The writer always writes the confirmed text.
    """
    import hashlib

    digest = hashlib.sha256(f"{summary}\n\n{detail}".encode("utf-8")).hexdigest()
    return {
        "source": source,
        "file": _display_path(path),
        "label": label or Path(path).name,
        "summary": summary,
        "content_sha256": digest,
        "status": "planned",
        "id": "",
        "path": str(path),
        "source_sha256": _file_sha256(path),
        "detail": detail,
        "domain": domain,
        "source_tool": source_tool,
        "project_folder": project_folder,
    }


_ITEM_METADATA_FIELDS = ("id", "source", "file", "content_sha256", "status")


def _item_metadata(item: dict) -> dict:
    """An import item without its text: id, source file, hash and status."""
    return {key: item.get(key, "") for key in _ITEM_METADATA_FIELDS}


def _insert_outcome(result) -> tuple[str, str]:
    """("imported", id) when add_lesson stored a row, else (its status, "")."""
    from .storage import NOT_ADDED_STATUSES

    if not isinstance(result, dict):
        return "declined", ""
    status = str(result.get("status") or "")
    new_id = result.get("id")
    if status in NOT_ADDED_STATUSES or not (isinstance(new_id, str) and new_id):
        return status or "declined", ""
    return "imported", new_id


class ReconcileMixin:
    """Import other AI tools' memory and config files on explicit request."""

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
        "~/.codex/AGENTS.md",
        "~/.cursor/rules",
        "~/.trae/rules",
        "~/.codeium/windsurf/rules",
    ]

    # ------------------------------------------------------------------
    # Memory file sync
    # ------------------------------------------------------------------

    def _note_reconcile_env_conflict(self) -> None:
        """Receipt for an ignored ENGRAM_RECONCILE=1, so the override is never silent."""
        if not reconcile_env_conflict_note(getattr(self, "root", None)):
            return
        audit = getattr(self, "_audit", None)
        if audit is not None:
            audit.log("warn", "reconcile", detail=RECONCILE_ENV_OVERRIDDEN)

    @staticmethod
    def _reconcile_authorized(root=None) -> bool:
        """May Engram read other AI tools' memory and config files at all?

        Nothing reads them automatically any more; this gates the explicit
        ``engram import-memories`` command and the read-only count that
        ``engram doctor`` / ``engram status`` show.

        - ENGRAM_RECONCILE=0 (or false/off/no) always disables it.
        - ``"reconcile_authorized": false`` in telemetry_config.json disables it
          too, and wins over ENGRAM_RECONCILE=1: an env var may turn reconcile
          off, never on against the config (see reconcile_env_conflict_note).
        - Otherwise ENGRAM_RECONCILE=1 or the config value enables it; with
          neither set it defaults to enabled for existing users.
        """
        env = _reconcile_env()
        if env == "off":
            return False
        configured = _reconcile_config_value(root)
        if configured is False:
            return _OWNER_PREVIEW_CONSENT.get()
        if env == "on":
            return True
        return True if configured is None else bool(configured)

    @overflow_batch
    def reconcile_memories(
        self,
        *,
        project_folder: str = "",
        dry_run: bool = False,
        also_existing: "set[str] | frozenset[str]" = frozenset(),
    ) -> dict:
        """Import other AI tools' memory files into the review queue.

        No server start, cold start, read or session close-out calls this; the
        Owner's ``engram import-memories`` plans with ``_plan_memory_import`` and
        writes that confirmed plan. ``dry_run=True`` returns the plan and writes
        nothing at all (no rows, no audit line). Without it the plan is written
        right away through ``memory_import.write_items`` (review queue, receipt,
        audit line); as a library call it keeps the capacity rules' overflow
        archive behaviour instead of stopping at a full review queue.
        ``also_existing`` adds texts planned elsewhere in the same run to the
        dedup set.

        Returns counts plus ``items``: one ``{id, source, file, content_sha256,
        status}`` dict per planned or imported memory -- no item text.

        Honours the off switches (ENGRAM_RECONCILE=0 or
        ``reconcile_authorized: false`` in telemetry_config.json).
        """
        plan = ReconcileMixin._scan_memories(
            self, project_folder=project_folder, also_existing=also_existing, audit=not dry_run,
        )
        return self._finish_reconcile(plan, dry_run=dry_run, source="memories",
                                      name="reconcile_memories")

    def _scan_memories(
        self,
        *,
        project_folder: str = "",
        also_existing: "set[str] | frozenset[str]" = frozenset(),
        audit: bool = False,
    ) -> dict:
        """Scan memory files into a full plan (item text included). Writes rows never;
        ``audit`` only allows the skip-large audit lines of a real run."""
        dry_run = not audit
        if not self._reconcile_authorized(self.root):
            result = {"imported": 0, "duplicates": 0, "scanned_files": 0,
                      "skipped_large": 0, "sources": [], "items": [],
                      "skipped_reason": "reconcile not authorized"}
            if project_folder:
                result["scope"] = self._reconcile_scope_metadata(project_folder)
                result["skipped_scope"] = 0
            return result
        duplicates = 0
        rejected_old_summary = 0
        scanned_files = 0
        skipped_large = 0
        skipped_scope = 0
        items: list[dict] = []
        target_project_id = _project_id(project_folder) if project_folder else ""
        target_claude_project = (
            self._encode_claude_project_name(str(Path(project_folder).resolve()))
            if project_folder
            else ""
        )

        existing_summaries = self._reconcile_existing_summaries(dry_run=dry_run)
        existing_summaries |= set(also_existing)
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

            for mem_file in sorted(base.glob(rel_pattern)):
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
                        if not dry_run:
                            self._audit.log("warn", "reconcile/skip_large",
                                            detail=f"{mem_file.name} ({fsize}B)")
                        continue
                    content = mem_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                from .isolated_store import refuse_replay_import

                mode_refusal = refuse_replay_import(self, content)
                if mode_refusal is not None:
                    return {**mode_refusal, "items": [], "imported": 0}
                parsed = _parse_memory_file(content)
                if parsed is None:
                    continue
                summary_candidate = parsed["summary"]
                fm_type = parsed["fm_type"]
                # Strip markdown formatting for better similarity matching
                clean_candidate = re.sub(r"[*_`\[\]()]", "", summary_candidate).strip()
                clean_legacy = re.sub(r"[*_`\[\]()]", "", parsed["legacy_summary"]).strip()

                # Skip entries with no meaningful text after cleanup
                if len(clean_candidate) < 5:
                    continue

                # Check similarity against existing Engram knowledge
                is_dup = False
                for existing in existing_summaries:
                    clean_existing = re.sub(r"[*_`\[\]()]", "", existing).strip()
                    sim = max(
                        self._bigram_similarity(clean_candidate, clean_existing),
                        self._bigram_similarity(clean_legacy, clean_existing),
                    )
                    if sim >= SIMILARITY_THRESHOLD:
                        is_dup = True
                        duplicates += 1
                        break

                if is_dup:
                    continue
                if _rejected_before(self.root, parsed["legacy_summary"], project_folder=project_folder):
                    rejected_old_summary += 1  # rejected under its 4.21.1 summary
                    continue
                if _rejected_before(self.root, summary_candidate, project_folder=project_folder):
                    rejected_old_summary += 1  # the insert would refuse it as well
                    continue

                domain = "auto_reconcile"
                if fm_type == "project":
                    domain = "project"
                elif fm_type == "feedback":
                    domain = "feedback"
                elif fm_type == "reference":
                    domain = "reference"

                items.append(_import_item(
                    "memories", mem_file, summary_candidate, parsed["detail"],
                    domain=domain, source_tool="auto_reconcile",
                    project_folder=project_folder, label=mem_file.name,
                ))
                existing_summaries.add(summary_candidate)

        result = {
            "scanned_files": scanned_files,
            "imported": 0,
            "duplicates": duplicates,
            "queue_full": 0,
            "rejected_under_old_summary": rejected_old_summary,
            "skipped_large": skipped_large,
            "sources": [],
            "items": items,
        }
        if project_folder:
            result["skipped_scope"] = skipped_scope
            result["scope"] = self._reconcile_scope_metadata(project_folder)
        return result

    def _finish_reconcile(self, plan: dict, *, dry_run: bool, source: str, name: str) -> dict:
        """Public result of reconcile_*: the dry-run plan or the written result,
        with items reduced to id, source, file, hash and status (no text)."""
        if plan.get("error") == "replay_experience_import_refused":
            return plan
        if plan.get("skipped_reason"):
            if not dry_run:
                self._note_reconcile_env_conflict()
            return plan
        if dry_run:
            result = dict(plan)
            result["dry_run"] = True
        else:
            result = self._write_reconcile_plan(plan, source=source, name=name)
        result["items"] = [_item_metadata(item) for item in result.get("items") or []]
        return result

    def _write_reconcile_plan(self, plan: dict, *, source: str, name: str) -> dict:
        """Library write path of the import engine: the shared writer, receipt, audit."""
        from .memory_import import write_items

        written = write_items(
            self, plan["items"], sources=[source], command=f"Engram.{name}",
            resource=f"knowledge/{name}", source_tool="engram_library",
            stop_when_queue_full=False,
        )
        result = dict(plan)
        result.update(
            imported=written["imported"],
            duplicates=plan["duplicates"] + written["duplicates"],
            queue_full=written["queue_full"],
            not_written=written["not_written"],
            partial=written["partial"],
            receipt=written["receipt"],
            items=written["items"],
            sources=[item["label"] for item in written["items"]],
        )
        self._audit.log("read", name,
                        detail=f"scanned={plan['scanned_files']} imported={result['imported']} "
                               f"dup={result['duplicates']} skipped_large={plan.get('skipped_large', 0)}")
        return result

    def _plan_memory_import(self, *, also_existing: "set[str] | frozenset[str]" = frozenset()) -> dict:
        """The full plan (with item text) behind :meth:`reconcile_memories`.

        Zero-write, works on a read-only handle; used by ``memory_import`` to
        show the Owner the list and then write exactly that list.
        """
        return ReconcileMixin._scan_memories(self, project_folder="", also_existing=also_existing,
                                             audit=False)

    def _plan_config_import(
        self,
        *,
        also_existing: "set[str] | frozenset[str]" = frozenset(),
        extra_project_roots: "tuple | list" = (),
    ) -> dict:
        """The full plan (with item text) behind :meth:`reconcile_ai_configs` (zero-write)."""
        return ReconcileMixin._scan_configs(
            self, search_roots=None, max_imports=25, project_folder="",
            also_existing=also_existing, extra_project_roots=extra_project_roots, audit=False,
        )

    def _reconcile_existing_summaries(self, *, dry_run: bool = False) -> set[str]:
        """Texts an import is deduplicated against (access-neutral read).

        Active lessons of every tier (so the review queue counts), decision
        questions and choices, and rows the capacity cap moved to the overflow
        archive (they were captured once already; re-importing them would grow
        the archive without bound). A dry run never migrates legacy fields on
        disk.
        """
        migrate = not dry_run
        existing_lessons = self.get_lessons(limit=None, _update_access=False, _migrate_fields=migrate)
        existing_decisions = self.get_decisions(limit=None, _update_access=False, _migrate_fields=migrate)
        summaries = {lesson.get("summary", "") for lesson in existing_lessons}
        for d in existing_decisions:
            summaries.add(d.get("question", ""))
            summaries.add(d.get("choice", ""))
        summaries |= self._overflow_archive_texts()
        return summaries

    def collect_memory_candidates(self) -> list[dict]:
        """Read-only scan of external AI memory files into reconcile candidates.

        Mirrors the parsing half of :meth:`reconcile_memories` but performs **no
        writes and no dedup decisions** - it only extracts ``{summary, detail,
        domain, source}`` candidate dicts for the owner-confirmed reconcile apply
        path (``reconcile_apply``) to classify. Honors the same authorization
        gate and per-file size cap. Returns ``[]`` when not authorized.
        """
        if not self._reconcile_authorized(self.root):
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

                from .isolated_store import refuse_replay_import

                mode_refusal = refuse_replay_import(self, content)
                if mode_refusal is not None:
                    from .isolated_store import GuardRefused

                    raise GuardRefused("replay_experience_import_refused")
                parsed = _parse_memory_file(content)
                if parsed is None:
                    continue
                summary_candidate = parsed["summary"]
                fm_type = parsed["fm_type"]
                clean_candidate = re.sub(r"[*_`\[\]()]", "", summary_candidate).strip()
                if len(clean_candidate) < 5:
                    continue

                domain = "auto_reconcile"
                if fm_type in {"project", "feedback", "reference"}:
                    domain = fm_type
                candidates.append({
                    "summary": summary_candidate,
                    "legacy_summary": parsed["legacy_summary"],
                    "detail": parsed["detail"],
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

    @overflow_batch
    def reconcile_ai_configs(
        self,
        *,
        search_roots: list[str] | None = None,
        max_imports: int = 25,
        project_folder: str = "",
        dry_run: bool = False,
        also_existing: "set[str] | frozenset[str]" = frozenset(),
        extra_project_roots: "tuple | list" = (),
    ) -> dict:
        """Import rules from other AI tools' config files into the review queue.

        Planned by ``_plan_config_import`` for ``engram import-memories``; without
        ``dry_run`` the plan is written at once through the shared writer (see
        :meth:`reconcile_memories`). Rule-file sections are capped at
        ``max_imports`` (25) per run; run again after an import to continue.
        Discovers project roots from Claude Code project entries, then looks
        for CLAUDE.md, .cursorrules, AGENT.md, etc. in each, parses markdown
        sections and imports each meaningful one as a staging lesson, at most
        ``max_imports`` per run. ``dry_run=True`` returns the same plan and
        writes nothing; ``also_existing`` adds texts planned elsewhere in the
        same run to the dedup set. ``extra_project_roots`` adds project folders
        (e.g. the current directory during ``engram setup``) whose CLAUDE.md /
        AGENTS.md / .cursorrules ... are read like discovered project roots.
        ``items`` lists one dict per planned or
        imported section (see :meth:`reconcile_memories`).

        Honours the off switches (ENGRAM_RECONCILE=0 or
        ``reconcile_authorized: false`` in telemetry_config.json).
        """
        plan = ReconcileMixin._scan_configs(
            self, search_roots=search_roots, max_imports=max_imports,
            project_folder=project_folder, also_existing=also_existing,
            extra_project_roots=extra_project_roots, audit=not dry_run,
        )
        return self._finish_reconcile(plan, dry_run=dry_run, source="configs",
                                      name="reconcile_ai_configs")

    def _scan_configs(
        self,
        *,
        search_roots: list[str] | None = None,
        max_imports: int = 25,
        project_folder: str = "",
        also_existing: "set[str] | frozenset[str]" = frozenset(),
        extra_project_roots: "tuple | list" = (),
        audit: bool = False,
    ) -> dict:
        """Scan rule files into a full plan (item text included); writes no rows."""
        dry_run = not audit
        if not self._reconcile_authorized(self.root):
            result = {"imported": 0, "duplicates": 0, "scanned_files": 0,
                      "sources": [], "items": [],
                      "skipped_reason": "reconcile not authorized",
                      "budget_exhausted": False}
            if project_folder:
                result["scope"] = self._reconcile_scope_metadata(project_folder)
            return result
        planned = 0
        duplicates = 0
        scanned_files = 0
        items: list[dict] = []
        budget_exhausted = False
        import_budget = max(0, int(max_imports))

        existing_summaries = self._reconcile_existing_summaries(dry_run=dry_run)
        existing_summaries |= set(also_existing)
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
            project_roots = list(self._discover_project_roots())
            project_roots += [Path(root) for root in extra_project_roots]
            for root in project_roots:
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
                    if not dry_run:
                        self._audit.log("warn", "reconcile_config/skip_large",
                                        detail=f"{cfg.name} ({fsize}B)")
                    continue
                content = cfg.read_text(encoding="utf-8", errors="replace")
                if "\ufffd" in content:
                    continue
            except OSError:
                continue

            from .isolated_store import refuse_replay_import

            mode_refusal = refuse_replay_import(self, content)
            if mode_refusal is not None:
                return {**mode_refusal, "items": [], "imported": 0}
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

                # Rule-file sections: at most max_imports (25) per run.
                if planned >= import_budget:
                    budget_exhausted = True
                    break
                if _rejected_before(self.root, summary_candidate, project_folder=project_folder):
                    continue  # the insert would refuse it

                planned += 1
                items.append(_import_item(
                    "configs", cfg, summary_candidate,
                    _bounded_detail(section_body, source=content),
                    domain="ai_config", source_tool="config_scan",
                    project_folder=project_folder, label=f"{cfg.parent.name}/{cfg.name}",
                ))
                existing_summaries.add(summary_candidate)
            if budget_exhausted:
                break

        result = {
            "scanned_files": scanned_files,
            "imported": 0,
            "duplicates": duplicates,
            "queue_full": 0,
            "sources": [],
            "items": items,
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
