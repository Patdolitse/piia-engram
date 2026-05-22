"""Engram compatibility layer — migrations from legacy formats.

- migrate_from_oca_memory: one-time import from old .oca/memory/ directory
- export_to_openclaw / import_from_openclaw: bridge to SOUL.md / MEMORY.md / USER.md

All functions take an ``Engram`` instance as the first argument.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .storage import MAX_KNOWLEDGE_ENTRIES, _now_iso

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from .core import Engram


# ---------------------------------------------------------------------------
# Migration helper: import from old oca_memory.py
# ---------------------------------------------------------------------------

def migrate_from_oca_memory(oca_memory_dir: str, engram: "Engram") -> dict:
    """Import knowledge from old .oca/memory/ into Engram.

    One-time migration for existing OCA users.
    """
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
                for nm in near_misses[-20:]:
                    engram.add_lesson({
                        "summary": nm.get("what_happened", "")[:80],
                        "detail": nm.get("what_could_have_happened", ""),
                        "domain": "safety",
                        "source_project": "migrated_from_oca_memory",
                    })
                migrated.append(f"near_misses ({len(near_misses)} entries)")
        except Exception as exc:
            logger.warning("migrate near_misses failed: %s", exc)

    return {"migrated": migrated}


# ---------------------------------------------------------------------------
# OpenClaw Compatibility — SOUL.md / MEMORY.md / USER.md
# ---------------------------------------------------------------------------


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
    out = Path(output_dir)
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
    soul_path.write_text("\n".join(soul_lines), encoding="utf-8")
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
    user_path.write_text("\n".join(user_lines), encoding="utf-8")
    exported.append(str(user_path))

    # --- MEMORY.md: Long-term memory (lessons + decisions) ---
    memory_lines = ["# MEMORY", ""]

    lessons = engram.get_lessons(limit=50)
    if lessons:
        memory_lines.append("## Lessons Learned")
        for l in lessons:
            domain = l.get("domain", "")
            prefix = f"[{domain}] " if domain else ""
            memory_lines.append(f"- {prefix}{l.get('summary', '')}")
        memory_lines.append("")

    decisions = engram.get_decisions(limit=30)
    if decisions:
        memory_lines.append("## Key Decisions")
        for d in decisions:
            memory_lines.append(f"- **{d.get('question', '')}**: {d.get('choice', '')}")
            if d.get("reasoning"):
                memory_lines.append(f"  - Why: {d['reasoning'][:100]}")
        memory_lines.append("")

    memory_lines.append(f"_Source: Engram ({_now_iso()})_")
    memory_path = out / "MEMORY.md"
    memory_path.write_text("\n".join(memory_lines), encoding="utf-8")
    exported.append(str(memory_path))

    return {"status": "success", "files": exported}


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

    Returns:
        Dict with import summary.
    """
    imported = []

    def _parse_md_bullets(text: str) -> list[str]:
        """Extract bullet point content from markdown."""
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- "):
                lines.append(stripped[2:].strip())
        return lines

    # --- Import USER.md → profile ---
    if user_path:
        p = Path(user_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
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
                engram.update_profile(updates)
                imported.append(f"USER.md → profile ({', '.join(updates.keys())})")

    # --- Import SOUL.md → preferences + quality_standards ---
    if soul_path:
        p = Path(soul_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
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
                engram.update_preferences({"work_patterns": prefs})
                imported.append(f"SOUL.md → preferences ({len(prefs)} items)")
            if rules:
                existing = engram.get_quality_standards()
                existing_rules = set(existing.get("rules", []))
                new_rules = [r for r in rules if r not in existing_rules]
                if new_rules:
                    all_rules = list(existing_rules) + new_rules
                    engram.update_quality_standards({"rules": all_rules[-15:]})
                    imported.append(f"SOUL.md → quality_standards (+{len(new_rules)} rules)")

    # --- Import MEMORY.md → lessons ---
    if memory_path:
        p = Path(memory_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            existing_summaries = {
                l.get("summary", "") for l in engram.get_lessons(limit=MAX_KNOWLEDGE_ENTRIES)
            }
            new_count = 0
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
                        engram.add_lesson({
                            "summary": text,
                            "domain": domain,
                            "source_tool": "openclaw_import",
                        })
                        existing_summaries.add(text)
                        new_count += 1
            if new_count:
                imported.append(f"MEMORY.md → lessons (+{new_count})")

    return {
        "status": "success" if imported else "no_new_data",
        "imported": imported,
    }
