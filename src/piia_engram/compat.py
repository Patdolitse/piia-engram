"""Engram compatibility layer — migrations from legacy formats.

- migrate_from_oca_memory: one-time import from old .oca/memory/ directory
- export_to_openclaw / import_from_openclaw: bridge to SOUL.md / MEMORY.md / USER.md

All functions take an ``Engram`` instance as the first argument.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .storage import _now_iso, overflow_batch

logger = logging.getLogger(__name__)

OPENCLAW_MEMORY_MAX_BYTES = 32 * 1024
OPENCLAW_BRIDGE_LEVEL = "L3_STATIC_FILE_BRIDGE"
OPENCLAW_SUMMARY_MAX_CHARS = 240
OPENCLAW_REASONING_MAX_CHARS = 160
HERMES_HANDOFF_MAX_TEXT_CHARS = 160
HERMES_HANDOFF_MAX_DECISIONS = 8

if TYPE_CHECKING:  # pragma: no cover
    from .core import Engram


# ---------------------------------------------------------------------------
# Migration helper: import from old oca_memory.py
# ---------------------------------------------------------------------------

@overflow_batch
def migrate_from_oca_memory(oca_memory_dir: str, engram: "Engram") -> dict:
    """Import knowledge from old .oca/memory/ into Engram.

    One-time migration for existing OCA users.
    """
    from .memory_import import refusal

    refused = refusal(engram.root)  # ENGRAM_RECONCILE=0 / reconcile_authorized=false
    if refused is not None:
        return {"migrated": [], "status": "disabled", "disabled_by": refused["disabled_by"]}
    mem_dir = Path(oca_memory_dir)
    migrated: list[str] = []

    # Owner profile → Engram profile
    profile_path = mem_dir / "owner_profile.json"
    if profile_path.exists():
        try:
            old_profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if old_profile:
                engram.update_profile({
                    "language": old_profile.get("language", ""),
                    "migrated_from": "oca_memory",
                })
                prefs = old_profile.get("preferences", {})
                if prefs:
                    engram.update_work_style({"preferences": prefs})
                threshold = old_profile.get("quality_threshold")
                if threshold:
                    engram.update_quality_standards({"acceptance_threshold": threshold})
                migrated.append("owner_profile")
        except Exception as exc:
            logger.warning("migrate owner_profile failed: %s", exc)

    # Project patterns → Engram domains + quality standards
    patterns_path = mem_dir / "project_patterns.json"
    if patterns_path.exists():
        try:
            patterns = json.loads(patterns_path.read_text(encoding="utf-8"))
            file_types = patterns.get("common_file_types", {})
            for ext, count in file_types.items():
                domain_map = {
                    ".py": "python", ".js": "javascript", ".ts": "typescript",
                    ".html": "frontend", ".css": "frontend",
                    ".json": "config", ".md": "documentation",
                }
                domain = domain_map.get(ext)
                if domain:
                    engram.update_domain(domain, {"project_count": count})
            migrated.append("project_patterns")
        except Exception as exc:
            logger.warning("migrate project_patterns failed: %s", exc)

    # Near misses → Engram lessons
    nm_path = mem_dir / "near_misses.json"
    if nm_path.exists():
        try:
            near_misses = json.loads(nm_path.read_text(encoding="utf-8"))
            if isinstance(near_misses, list):
                from .memory_import import note_outcome, recording

                with recording(
                    engram, sources=["legacy_memory_migration"],
                    command="legacy_memory_migration",
                    resource="knowledge/import_legacy_memory", source_tool="legacy_memory_migration",
                ) as record:
                    for nm in near_misses[-20:]:
                        summary = nm.get("what_happened", "")[:80]
                        detail = nm.get("what_could_have_happened", "")
                        result = engram.add_lesson({
                            "summary": summary,
                            "detail": detail,
                            "domain": "safety",
                            "source_project": "migrated_from_oca_memory",
                            "tier": "staging",  # imported lessons wait for review
                        }, _audit_metadata_only=True)
                        note_outcome(record, result, source="legacy_memory_migration",
                                     file="near_misses.json", summary=summary, detail=detail)
                migrated.append(f"near_misses ({len(near_misses)} entries)")
        except Exception as exc:
            logger.warning("migrate near_misses failed: %s", exc)

    return {"migrated": migrated}


# ---------------------------------------------------------------------------
# OpenClaw Compatibility — SOUL.md / MEMORY.md / USER.md
# ---------------------------------------------------------------------------


def _is_verified_active(entry: dict) -> bool:
    """Only approved active knowledge may leave Engram through static bridges."""
    return entry.get("tier", "verified") == "verified" and entry.get("status", "active") == "active"


def _clip_text(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


_PATHISH_RE = re.compile(
    r"([A-Za-z]:\\|\\\\|/[^/\s]+/|~[/\\]|https?://|file://)",
    re.IGNORECASE,
)


def _handoff_text(value: object, max_chars: int = HERMES_HANDOFF_MAX_TEXT_CHARS) -> str:
    """Return a short bridge-safe text field with path-like values removed."""
    text = str(value or "").strip()
    if not text or _PATHISH_RE.search(text):
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def hermes_handoff_payload(engram: "Engram") -> dict:
    """Build a schema-stable, metadata-only handoff payload for Hermes-like agents.

    This is a compatibility bridge, not a live plugin: it exposes a compact
    identity summary and active verified decision summaries without raw paths,
    session IDs, full memory bodies, or decision reasoning.
    """
    try:
        profile = engram.get_profile(safe=True)
    except TypeError:  # pragma: no cover - older Engram-like facade
        profile = engram.get_profile()
    profile = profile if isinstance(profile, dict) else {}

    lessons = [
        lesson
        for lesson in engram.get_lessons(limit=None, _update_access=False)
        if _is_verified_active(lesson)
    ]
    decisions = [
        decision
        for decision in engram.get_decisions(limit=None, _update_access=False)
        if _is_verified_active(decision)
    ]

    active_decisions = []
    for decision in decisions[-HERMES_HANDOFF_MAX_DECISIONS:]:
        item = {
            "question": _handoff_text(decision.get("question")),
            "choice": _handoff_text(decision.get("choice")),
        }
        domain = _handoff_text(decision.get("domain"), 64)
        if domain:
            item["domain"] = domain
        if item["question"] or item["choice"]:
            active_decisions.append(item)

    identity_summary = {}
    for key in ("role", "language", "technical_level", "description"):
        value = _handoff_text(profile.get(key))
        if value:
            identity_summary[key] = value

    return {
        "schema": "hermes_handoff_v1",
        "source": "piia-engram",
        "identity_summary": identity_summary,
        "active_decisions": active_decisions,
        "lessons_count": len(lessons),
    }


def export_to_openclaw(engram: "Engram", output_dir: str) -> dict:
    """Export Engram data to OpenClaw format (SOUL.md + MEMORY.md + USER.md).

    Generates three Markdown files that OpenClaw can directly consume.
    This puts Engram at the asset layer — not competing on format, but bridging.

    Args:
        engram: Engram instance.
        output_dir: Directory to write the three files.

    Returns:
        Dict with file paths and status.
    """
    from .isolated_store import export_mode_prefix

    mode_prefix = export_mode_prefix(engram)
    out = Path(output_dir)
    from .store_paths import export_destination

    export_destination(engram.root, out)
    for name in ('SOUL.md', 'MEMORY.md', 'USER.md'):
        export_destination(engram.root, out / name)
    out.mkdir(parents=True, exist_ok=True)
    exported = []

    # --- SOUL.md: Agent identity, values, long-term directives ---
    profile = engram.get_profile()
    prefs = engram.get_preferences()
    standards = engram.get_quality_standards()

    soul_lines = [
        "# SOUL",
        "",
        "## Identity",
        f"- Role: {profile.get('role', 'N/A')}",
        f"- Language: {profile.get('language', 'N/A')}",
        f"- Technical Level: {profile.get('technical_level', 'N/A')}",
        f"- Description: {profile.get('description', '')}",
        "",
        "## Work Preferences",
    ]
    work_patterns = prefs.get("work_patterns", {})
    for k, v in work_patterns.items():
        soul_lines.append(f"- {k}: {v}")
    if prefs.get("communication"):
        soul_lines.append(f"- Communication: {prefs['communication']}")

    soul_lines.extend(["", "## Quality Standards"])
    for rule in standards.get("rules", []):
        soul_lines.append(f"- {rule}")

    soul_lines.extend([
        "",
        "## Tool Preferences",
    ])
    for k, v in prefs.get("tool_preferences", {}).items():
        soul_lines.append(f"- {k}: {v}")

    soul_lines.extend([
        "",
        f"_Exported from Engram at {_now_iso()}_",
    ])
    soul_path = out / "SOUL.md"
    soul_path.write_text(mode_prefix + "\n".join(soul_lines), encoding="utf-8")
    exported.append(str(soul_path))

    # --- USER.md: User personal info ---
    user_lines = [
        "# USER",
        "",
        f"- Role: {profile.get('role', '')}",
        f"- Language: {profile.get('language', '')}",
        f"- Technical Level: {profile.get('technical_level', '')}",
        "",
        f"_Source: Engram ({_now_iso()})_",
    ]
    user_path = out / "USER.md"
    user_path.write_text(mode_prefix + "\n".join(user_lines), encoding="utf-8")
    exported.append(str(user_path))

    # --- MEMORY.md: Long-term memory (verified lessons + decisions) ---
    source_footer = f"_Source: Engram ({_now_iso()})_"
    memory_lines = ["# MEMORY", ""]

    def _would_fit(candidate_lines: list[str]) -> bool:
        body = mode_prefix + "\n".join(candidate_lines + [source_footer])
        return len(body.encode("utf-8")) <= OPENCLAW_MEMORY_MAX_BYTES

    omitted = 0

    lessons = [
        lesson
        for lesson in engram.get_lessons(limit=None, _update_access=False)
        if _is_verified_active(lesson)
    ][-50:]
    if lessons:
        candidate = memory_lines + ["## Lessons Learned"]
        if _would_fit(candidate):
            memory_lines = candidate
        for l in lessons:
            domain = l.get("domain", "")
            prefix = f"[{domain}] " if domain else ""
            line = f"- {prefix}{_clip_text(l.get('summary', ''), OPENCLAW_SUMMARY_MAX_CHARS)}"
            candidate = memory_lines + [line]
            if _would_fit(candidate):
                memory_lines = candidate
            else:
                omitted += 1
        if memory_lines[-1] != "":
            memory_lines.append("")

    decisions = [
        decision
        for decision in engram.get_decisions(limit=None, _update_access=False)
        if _is_verified_active(decision)
    ][-30:]
    if decisions:
        candidate = memory_lines + ["## Key Decisions"]
        if _would_fit(candidate):
            memory_lines = candidate
        for d in decisions:
            entry_lines = [
                (
                    f"- **{_clip_text(d.get('question', ''), OPENCLAW_SUMMARY_MAX_CHARS)}**: "
                    f"{_clip_text(d.get('choice', ''), OPENCLAW_SUMMARY_MAX_CHARS)}"
                )
            ]
            if d.get("reasoning"):
                entry_lines.append(f"  - Why: {_clip_text(d['reasoning'], OPENCLAW_REASONING_MAX_CHARS)}")
            candidate = memory_lines + entry_lines
            if _would_fit(candidate):
                memory_lines = candidate
            else:
                omitted += 1
        if memory_lines[-1] != "":
            memory_lines.append("")

    if omitted:
        note = f"_OpenClaw export truncated to {OPENCLAW_MEMORY_MAX_BYTES} bytes; omitted {omitted} verified item(s)._"
        candidate = memory_lines + [note, ""]
        if _would_fit(candidate):
            memory_lines = candidate

    memory_lines.append(source_footer)
    memory_path = out / "MEMORY.md"
    memory_path.write_text(mode_prefix + "\n".join(memory_lines), encoding="utf-8")
    exported.append(str(memory_path))

    return {
        "status": "success",
        "bridge_level": OPENCLAW_BRIDGE_LEVEL,
        "files": exported,
    }


def openclaw_command(soul_path: str = "", memory_path: str = "", user_path: str = "", *, apply: bool = True) -> str:
    """The local ``engram import --format openclaw`` command line, with placeholders.

    The caller's paths are never echoed (they could carry shell syntax); each
    file that was given appears as ``<SOUL.md>`` / ``<MEMORY.md>`` / ``<USER.md>``.
    """
    parts = ["engram import --format openclaw"]
    for flag, value, holder in (("--soul", soul_path, "<SOUL.md>"), ("--memory", memory_path, "<MEMORY.md>"),
                                ("--user", user_path, "<USER.md>")):
        if value:
            parts.append(f"{flag} {holder}")
    if apply:
        parts.append("--apply --yes")
    return " ".join(parts)


def _read_openclaw_file(raw: str) -> tuple[dict, str | None]:
    """(metadata, text) for one OpenClaw file: ``~`` expanded, strict UTF-8."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in str(raw)):
        return {"error": "path contains a control character"}, None
    path = Path(raw).expanduser()
    if not path.exists():
        return {"exists": False}, None
    if not path.is_file():
        return {"exists": True, "error": "not a file"}, None
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return {"exists": True, "error": "not UTF-8 text"}, None
    except OSError as exc:
        return {"exists": True, "error": f"cannot read the file ({type(exc).__name__})"}, None
    bullets = sum(1 for line in text.splitlines() if line.strip().startswith("- "))
    return {"exists": True, "bullets": bullets}, text


def read_openclaw_files(soul_path: str = "", memory_path: str = "", user_path: str = "") -> tuple[dict, dict]:
    """Read every given OpenClaw file up front: ({name: text}, {name: metadata}).

    The preview and the import use this one reader. A missing file is
    reported as ``{"exists": False}`` and skipped; any other problem carries
    ``error``, and the import then writes nothing.
    """
    texts: dict[str, str] = {}
    files: dict[str, dict] = {}
    for name, raw in (("soul", soul_path), ("memory", memory_path), ("user", user_path)):
        if not raw:
            continue
        info, text = _read_openclaw_file(raw)
        files[name] = info
        if text is not None:
            texts[name] = text
    return texts, files


def _replay_text_import_refusal(engram: "Engram", texts: dict) -> dict | None:
    from .isolated_store import PRODUCTION, REPLAY_EXPORT_MARKER, root_mode

    mode = root_mode(engram.root, engram._store_mode)
    if mode == PRODUCTION and any(REPLAY_EXPORT_MARKER in text for text in texts.values()):
        return {"error": "replay_experience_import_refused", "changed": False}
    return None


def preview_openclaw(engram: "Engram", soul_path: str = "", memory_path: str = "", user_path: str = "") -> dict:
    """Metadata-only look at OpenClaw files: which exist and how many bullet lines each holds.

    Reads nothing when the import switch is off (ENGRAM_RECONCILE=0). Writes nothing.
    """
    from .memory_import import refusal

    refused = refusal(engram.root)
    if refused is not None:
        return {**refused, "bridge_level": OPENCLAW_BRIDGE_LEVEL}
    if not (soul_path or memory_path or user_path):
        return {"error": "give at least one OpenClaw file (soul, memory or user)",
                "bridge_level": OPENCLAW_BRIDGE_LEVEL}
    _texts, files = read_openclaw_files(soul_path, memory_path, user_path)
    mode_refusal = _replay_text_import_refusal(engram, _texts)
    if mode_refusal:
        return mode_refusal
    return {
        "status": "preview",
        "format": "openclaw",
        "dry_run": True,
        "files": files,
        "note": "metadata only; MEMORY.md lessons and USER.md / SOUL.md identity changes "
                "go to the local review queue",
    }


@overflow_batch
def import_from_openclaw(
    engram: "Engram",
    soul_path: str = "",
    memory_path: str = "",
    user_path: str = "",
) -> dict:
    """Import OpenClaw SOUL.md/MEMORY.md/USER.md into Engram.

    Parses Markdown bullet points into structured Engram data.
    Safe merge: doesn't overwrite existing Engram data, only adds new entries.

    Args:
        engram: Engram instance.
        soul_path: Path to SOUL.md (optional).
        memory_path: Path to MEMORY.md (optional).
        user_path: Path to USER.md (optional).

    Lessons from MEMORY.md wait in the review queue (staging) and a confirmed
    batch leaves an import receipt plus an audit line. USER.md / SOUL.md create
    pending identity proposals; only the Owner's local review applies them.

    Returns:
        Dict with import summary.
    """
    from .memory_import import note_outcome, recording, refusal
    from .reconcile import _display_path

    refused = refusal(engram.root)  # ENGRAM_RECONCILE=0 / reconcile_authorized=false
    if refused is not None:
        return {**refused, "bridge_level": OPENCLAW_BRIDGE_LEVEL, "receipt": ""}

    # Everything is read and checked before anything is written.
    texts, files = read_openclaw_files(soul_path, memory_path, user_path)
    mode_refusal = _replay_text_import_refusal(engram, texts)
    if mode_refusal:
        return mode_refusal
    unreadable = {name: info for name, info in files.items() if info.get("error")}
    if unreadable:
        return {"error": "unreadable_file", "files": files, "imported": [],
                "bridge_level": OPENCLAW_BRIDGE_LEVEL, "receipt": "",
                "message": "An OpenClaw file could not be read; nothing was imported."}

    imported = []
    receipt = ""

    def _parse_md_bullets(text: str) -> list[str]:
        """Extract bullet point content from markdown."""
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- "):
                lines.append(stripped[2:].strip())
        return lines

    # --- Import USER.md → profile ---
    if "user" in texts:
        content = texts["user"]
        bullets = _parse_md_bullets(content)
        updates = {}
        for b in bullets:
            if b.lower().startswith("role:"):
                updates["role"] = b.split(":", 1)[1].strip()
            elif b.lower().startswith("language:"):
                updates["language"] = b.split(":", 1)[1].strip()
            elif b.lower().startswith("technical level:"):
                updates["technical_level"] = b.split(":", 1)[1].strip()
        if updates:
            proposal = engram.propose_identity("profile", updates, source_tool="openclaw")
            imported.append(f"USER.md → profile proposal ({proposal.get('status', 'error')})")

    # --- Import SOUL.md → preferences + quality_standards ---
    if "soul" in texts:
        content = texts["soul"]
        # Simple section-based parsing
        current_section = ""
        prefs = {}
        rules = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                current_section = stripped[3:].strip().lower()
            elif stripped.startswith("- ") and current_section:
                value = stripped[2:].strip()
                if current_section in ("work preferences", "工作偏好"):
                    if ":" in value:
                        k, v = value.split(":", 1)
                        prefs[k.strip()] = v.strip()
                elif current_section in ("quality standards", "质量标准"):
                    rules.append(value)
        if prefs:
            proposal = engram.propose_identity("preferences", {"work_patterns": prefs}, source_tool="openclaw")
            imported.append(f"SOUL.md → preferences proposal ({proposal.get('status', 'error')})")
        if rules:
            existing = engram.get_quality_standards()
            existing_rules = set(existing.get("rules", []))
            new_rules = [r for r in rules if r not in existing_rules]
            if new_rules:
                all_rules = list(existing_rules) + new_rules
                proposal = engram.propose_identity("quality_standards", {"rules": all_rules[-15:]}, source_tool="openclaw")
                imported.append(f"SOUL.md → quality_standards proposal ({proposal.get('status', 'error')})")

    # --- Import MEMORY.md → lessons ---
    if "memory" in texts:
        p = Path(memory_path).expanduser()
        content = texts["memory"]
        existing_summaries = {
            l.get("summary", "") for l in engram.get_lessons(limit=None, _update_access=False)
        }
        # Lessons already moved to the overflow archive are not imported again.
        archived_texts = getattr(engram, "_overflow_archive_texts", None)
        if callable(archived_texts):
            existing_summaries |= archived_texts()
        new_count = 0
        lesson_lines: list[tuple[str, str]] = []
        current_section = ""
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                current_section = stripped[3:].strip().lower()
            elif stripped.startswith("- ") and current_section in (
                "lessons learned", "经验教训"
            ):
                text = stripped[2:].strip()
                # Remove domain prefix like [python]
                domain = ""
                if text.startswith("[") and "]" in text:
                    domain = text[1:text.index("]")]
                    text = text[text.index("]") + 1:].strip()
                if text and text not in existing_summaries:
                    lesson_lines.append((text, domain))
                    existing_summaries.add(text)
        if lesson_lines:
            with recording(
                engram, sources=["openclaw"], command="engram import --format openclaw",
                resource="knowledge/import_openclaw", source_tool="openclaw_import",
            ) as record:
                for text, domain in lesson_lines:
                    result = engram.add_lesson({
                        "summary": text,
                        "domain": domain,
                        "source_tool": "openclaw_import",
                        "tier": "staging",  # imports wait for review
                    }, _audit_metadata_only=True)
                    if note_outcome(record, result, source="openclaw",
                                    file=_display_path(p), summary=text):
                        new_count += 1
            receipt = record.receipt
        if new_count:
            imported.append(f"MEMORY.md → lessons (+{new_count}, review queue)")

    return {
        "status": "success" if imported else "no_new_data",
        "bridge_level": OPENCLAW_BRIDGE_LEVEL,
        "imported": imported,
        "receipt": receipt,
    }
