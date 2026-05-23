"""Engram — AI 记忆印记，核心读写库。"""

from __future__ import annotations

import hashlib
import logging
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# All constants and I/O utilities live in storage.py — re-exported here
# for backward compatibility (tests import from piia_engram.core).
from .storage import (  # noqa: F401 — re-exports
    CONFLICT_C_CEILING,
    CONFLICT_Q_THRESHOLD,
    DEFAULT_TRUST_BOUNDARIES,
    DECISION_TRIGGERS,
    DOMAIN_KEYWORDS,
    ENCRYPTED_PROFILE_FIELDS,
    FIELD_WEIGHTS,
    LESSON_TRIGGERS,
    MAX_KNOWLEDGE_ENTRIES,
    PLAYBOOK_TRIGGERS,
    SCHEMA_VERSION,
    SEARCH_RELEVANCE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    STALE_KNOWLEDGE_DAYS,
    TOOL_CATEGORIES,
    _AFFIRMATION_MARKERS,
    _ALIAS_LOOKUP,
    _ALLOWED_PLAYBOOK_UPDATE_FIELDS,
    _ALLOWED_PREFERENCES_FIELDS,
    _ALLOWED_PROFILE_FIELDS,
    _ALLOWED_QUALITY_FIELDS,
    _ALLOWED_TOOL_UPDATE_FIELDS,
    _ALLOWED_TRUST_FIELDS,
    _ENGRAM_DIR_NAME,
    _LEGACY_DIR_NAME,
    _NEGATION_MARKERS,
    _TERM_ALIASES,
    _atomic_write_json,
    _engram_root,
    detect_data_fragmentation,
    _now_iso,
    _parse_iso,
    _project_id,
    _read_json,
    _write_json,
)
from .retrieval import RetrievalMixin
from .context import ContextMixin
from .context import EXTRACTION_PROMPT, extract_knowledge, ingest_extraction  # noqa: F401
from .reconcile import ReconcileMixin
from .reports import ReportsMixin
from .contexts import ContextStoreMixin
# Compat helpers re-exported for backward compatibility (tests import these
# from piia_engram.core directly).
from .compat import (  # noqa: F401
    export_to_openclaw,
    import_from_openclaw,
    migrate_from_oca_memory,
)


# ---------------------------------------------------------------------------
# Engram Core Class
# ---------------------------------------------------------------------------

class Engram(RetrievalMixin, ContextMixin, ReconcileMixin, ReportsMixin, ContextStoreMixin):
    """Read/write interface to the user's global Engram."""

    def __init__(self, root: Path | None = None):
        self.root = root or _engram_root()
        self._identity_dir = self.root / "identity"
        self._knowledge_dir = self.root / "knowledge"
        self._playbooks_dir = self.root / "playbooks"
        self._projects_dir = self.root / "projects"
        self._exports_dir = self.root / "exports"
        self._environment_dir = self.root / "environment"

        # Encryption engine (transparent when ENGRAM_SECRET is not set)
        from piia_engram.crypto import EncryptionEngine
        secret = os.environ.get("ENGRAM_SECRET", "").strip()
        self._crypto = EncryptionEngine(secret if secret else None)

        # Audit logger (disabled unless ENGRAM_AUDIT=1/true/yes)
        from piia_engram.audit import AuditLogger
        audit_enabled = os.environ.get("ENGRAM_AUDIT", "").strip().lower() in ("1", "true", "yes")
        self._audit = AuditLogger(
            log_path=self.root / "audit.log" if audit_enabled else None,
            enabled=audit_enabled,
        )

        # Data fragmentation detection — warn, don't silently split.
        self.data_orphans = detect_data_fragmentation(self.root)
        if self.data_orphans:
            logger.warning(
                "DATA FRAGMENTATION: active root is %s but data also "
                "exists at: %s — knowledge may be incomplete!",
                self.root, ", ".join(self.data_orphans),
            )

        self._ensure_structure()

    def _ensure_structure(self) -> None:
        """Create directory structure if it doesn't exist."""
        for sub in ["identity", "knowledge", "playbooks", "projects", "exports", "compat", "contexts", "environment"]:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # Write schema version
        ver_path = self.root / "schema_version.json"
        if not ver_path.exists():
            _write_json(ver_path, {
                "schema_version": SCHEMA_VERSION,
                "created_at": _now_iso(),
            })
        # Auto-migrate from v1 to v2
        self._migrate_v1_to_v2()
        self._ensure_trust_boundaries()

    def _atomic_write(self, path: Path, data: Any) -> None:
        """Atomically write JSON through the shared Engram file lock."""
        _write_json(path, data)

    def _ensure_trust_boundaries(self) -> dict:
        """Backfill trust boundary defaults, including v2.2 restricted fields."""
        path = self._identity_dir / "trust_boundaries.json"
        existing = _read_json(path)
        if not isinstance(existing, dict):
            existing = {}

        changed = False
        for key, value in DEFAULT_TRUST_BOUNDARIES.items():
            if key not in existing:
                existing[key] = deepcopy(value)
                changed = True

        if changed:
            existing["updated_at"] = existing.get("updated_at") or _now_iso()
            self._atomic_write(path, existing)
        return existing

    # =====================================================================
    # Schema Migration
    # =====================================================================

    @staticmethod
    def _parse_schema_version(value: str) -> tuple[int, ...]:
        """Parse a dotted schema version into a tuple for numeric comparison.

        String comparison fails once any component reaches double digits
        (e.g. "10.0" < "2.0" lexicographically).  Tuple comparison is safe.
        """
        try:
            return tuple(int(part) for part in str(value).split("."))
        except (ValueError, AttributeError):
            return (0, 0)

    def _migrate_v1_to_v2(self) -> None:
        """Migrate from schema v1.0 to v2.0 (idempotent)."""
        ver_path = self.root / "schema_version.json"
        ver_data = _read_json(ver_path)
        current = ver_data.get("schema_version", "1.0")
        if self._parse_schema_version(current) >= (2, 0):
            return

        # 1) work_style.json → preferences.json (keep old file for compat)
        old_style = self.root / "identity" / "work_style.json"
        new_prefs = self.root / "identity" / "preferences.json"
        if old_style.is_file() and not new_prefs.is_file():
            data = _read_json(old_style)
            prefs = {
                "work_patterns": data.get("preferences", {}),
                "communication": data.get("communication", ""),
                "tool_preferences": {},
                "updated_at": _now_iso(),
                "migrated_from": "work_style.json",
            }
            _write_json(new_prefs, prefs)

        # 2) Initialize trust_boundaries.json if missing
        tb_path = self.root / "identity" / "trust_boundaries.json"
        if not tb_path.is_file():
            _write_json(tb_path, {
                "default_sharing": "full",
                "tool_access": {},
                "private_fields": [],
                "notes": "默认所有工具可访问全部Engram数据。可按工具或字段限制。",
                "updated_at": _now_iso(),
            })

        # 3) Bump schema version
        ver_data["schema_version"] = "2.0"
        ver_data["migrated_at"] = _now_iso()
        _write_json(ver_path, ver_data)

    # =====================================================================
    # Identity — who the user is
    # =====================================================================

    def get_profile(self, safe: bool = False) -> dict:
        profile = _read_json(self._identity_dir / "profile.json")
        profile = self._crypto.decrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
        if safe:
            tb = self.get_trust_boundaries()
            restricted = set(tb.get("restricted_fields", []))
            if restricted:
                profile = {key: value for key, value in profile.items() if key not in restricted}
        self._audit.log("read", "identity/profile")
        return profile

    def get_safe_profile(self) -> dict:
        """Return profile with trust_boundaries.restricted_fields filtered out."""
        return self.get_profile(safe=True)

    @staticmethod
    def _filter_allowed(updates: dict, allowed: frozenset) -> tuple[dict, list[str]]:
        """Return (filtered_updates, rejected_keys)."""
        rejected = [k for k in updates if k not in allowed]
        filtered = {k: v for k, v in updates.items() if k in allowed}
        return filtered, rejected

    def update_profile(self, updates: dict) -> None:
        """Merge updates into the user profile."""
        updates, rejected = self._filter_allowed(updates, _ALLOWED_PROFILE_FIELDS)
        if rejected:
            self._audit.log("warn", "identity/profile",
                            detail=f"rejected unknown fields: {rejected}")
        if not updates:
            return
        profile = self.get_profile()
        profile.update(updates)
        profile["updated_at"] = _now_iso()
        encrypted = self._crypto.encrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
        _write_json(self._identity_dir / "profile.json", encrypted)
        self._audit.log("write", "identity/profile", detail=str(list(updates.keys())))

    def get_work_style(self) -> dict:
        return _read_json(self._identity_dir / "work_style.json")

    def update_work_style(self, updates: dict) -> None:
        style = self.get_work_style()
        style.update(updates)
        style["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "work_style.json", style)

    # -- Preferences (v2.0, replaces work_style) --

    def get_preferences(self) -> dict:
        """Get user preferences (v2.0). Falls back to work_style.json if needed."""
        prefs = _read_json(self._identity_dir / "preferences.json")
        if prefs:
            return prefs
        # Fallback: read old work_style.json
        old = self.get_work_style()
        if old:
            return {
                "work_patterns": old.get("preferences", {}),
                "communication": old.get("communication", ""),
                "tool_preferences": {},
            }
        return {}

    def update_preferences(self, updates: dict) -> None:
        updates, rejected = self._filter_allowed(updates, _ALLOWED_PREFERENCES_FIELDS)
        if rejected:
            self._audit.log("warn", "identity/preferences",
                            detail=f"rejected unknown fields: {rejected}")
        if not updates:
            return
        prefs = self.get_preferences()
        prefs.update(updates)
        prefs["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "preferences.json", prefs)

    # -- Trust Boundaries (v2.0, new) --

    def get_trust_boundaries(self) -> dict:
        return self._ensure_trust_boundaries()

    def update_trust_boundaries(self, updates: dict) -> None:
        updates, rejected = self._filter_allowed(updates, _ALLOWED_TRUST_FIELDS)
        if rejected:
            self._audit.log("warn", "identity/trust_boundaries",
                            detail=f"rejected unknown fields: {rejected}")
        if not updates:
            return
        tb = self.get_trust_boundaries()
        tb.update(updates)
        tb["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "trust_boundaries.json", tb)

    def get_quality_standards(self) -> dict:
        return _read_json(self._identity_dir / "quality_standards.json")

    def update_quality_standards(self, updates: dict) -> None:
        updates, rejected = self._filter_allowed(updates, _ALLOWED_QUALITY_FIELDS)
        if rejected:
            self._audit.log("warn", "identity/quality_standards",
                            detail=f"rejected unknown fields: {rejected}")
        if not updates:
            return
        standards = self.get_quality_standards()
        standards.update(updates)
        standards["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "quality_standards.json", standards)

    # =====================================================================
    # Knowledge — what you've learned
    # =====================================================================

    @staticmethod
    def _sanitize_project(project: str) -> str:
        """Extract a short project name from a value that may be a file path."""
        if not project:
            return project
        # Detect file paths (contains slash/backslash or drive letter)
        if "/" in project or "\\" in project or (len(project) > 2 and project[1] == ":"):
            # Use PureWindowsPath to handle both / and \ on any OS
            from pathlib import PureWindowsPath
            name = PureWindowsPath(project).name
            return name if name else project
        return project

    def _entry_identity_text(self, entry: dict, entry_type: str) -> str:
        if entry_type == "decision":
            return str(entry.get("title") or entry.get("question") or "")
        if entry_type == "playbook":
            return str(entry.get("title") or "")
        return str(entry.get("summary") or "")

    def _ensure_fields(self, entry: dict, entry_type: str) -> dict:
        """Backfill v2.1 fields on old lesson/decision entries."""
        if not isinstance(entry, dict):
            entry = {}

        if not entry.get("timestamp"):
            entry["timestamp"] = _now_iso()
        entry.setdefault("created_at", entry.get("timestamp", _now_iso()))
        entry.setdefault("last_reviewed", entry.get("created_at", _now_iso()))

        if not entry.get("id"):
            identity = self._entry_identity_text(entry, entry_type)
            if entry_type == "lesson":
                seed = f"{identity}{entry.get('domain', '')}{entry.get('timestamp', '')}"
            else:
                seed = f"{identity}{entry.get('timestamp', '')}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]

        entry.setdefault("status", "active")
        entry.setdefault("access_count", 0)
        # Knowledge tier: "staging" (unverified) or "verified" (confirmed valuable)
        entry.setdefault("tier", "verified")  # Legacy items default to verified
        if not isinstance(entry.get("related_ids"), list):
            entry["related_ids"] = []

        return entry

    def _read_entries(self, path: Path, entry_type: str) -> list[dict]:
        entries = _read_json(path)
        if not isinstance(entries, list):
            return []

        changed = False
        ensured: list[dict] = []
        for entry in entries:
            before = dict(entry) if isinstance(entry, dict) else {}
            item = self._ensure_fields(entry, entry_type)
            if item != before:
                changed = True
            ensured.append(item)

        if changed:
            _write_json(path, ensured)
        return ensured

    def add_lesson(
        self,
        lesson: dict | str,
        domain: str = "",
        detail: str = "",
        source_tool: str = "",
        source_url: str = "",
        **extra: Any,
    ) -> dict:
        """Add a lesson learned.

        Accepts either the original dict form or a convenience form:
        add_lesson("summary", "domain", source_tool="codex").
        """
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")

        if isinstance(lesson, dict):
            new_lesson = dict(lesson)
        else:
            new_lesson = {"summary": str(lesson)}
            if domain:
                new_lesson["domain"] = domain
            if detail:
                new_lesson["detail"] = detail
            if source_tool:
                new_lesson["source_tool"] = source_tool
            if source_url:
                new_lesson["source_url"] = source_url

        for key, value in extra.items():
            if value is not None:
                new_lesson[key] = value

        new_lesson["timestamp"] = new_lesson.get("timestamp") or _now_iso()
        new_lesson = self._ensure_fields(new_lesson, "lesson")

        for existing in lessons:
            existing = self._ensure_fields(existing, "lesson")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_lesson.get("summary", ""),
                existing.get("summary", ""),
            )
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": existing.get("id"),
                    "existing_summary": existing.get("summary"),
                    "message": f"与现有教训相似度 {sim:.0%}，未重复添加",
                }

        lessons.append(new_lesson)
        if len(lessons) > MAX_KNOWLEDGE_ENTRIES:
            # Evict staging items first, then oldest; never drop verified
            staging = [l for l in lessons if l.get("tier") == "staging"]
            verified = [l for l in lessons if l.get("tier") != "staging"]
            overflow = len(lessons) - MAX_KNOWLEDGE_ENTRIES
            if len(staging) >= overflow:
                staging = staging[overflow:]  # drop oldest staging
            else:
                remaining = overflow - len(staging)
                staging = []
                verified = verified[remaining:]  # drop oldest verified as last resort
            lessons = verified + staging
        _write_json(path, lessons)

        summary = new_lesson.get("summary", "")
        self._audit.log("write", "knowledge/lessons", detail=summary[:100])
        if new_lesson.get("domain"):
            for _d in new_lesson["domain"].split(","):
                _d = _d.strip()
                if _d:
                    self.increment_domain_usage(_d)
        return new_lesson

    def get_lessons(
        self,
        domain: str | None = None,
        source_tool: str | None = None,
        limit: int | None = 50,
        _update_access: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")
        result = []
        for lesson in lessons:
            if lesson.get("status") != "active":
                continue
            if domain:
                lesson_domains = {d.strip() for d in (lesson.get("domain") or "").split(",") if d.strip()}
                if domain not in lesson_domains:
                    continue
            if source_tool and lesson.get("source_tool") != source_tool:
                continue
            result.append(lesson)
        result = result[-limit:] if limit is not None else result
        if _update_access and result:
            now = _now_iso()
            for lesson in result:
                lesson["last_reviewed"] = now
                lesson["access_count"] = lesson.get("access_count", 0) + 1
            _write_json(path, lessons)
        self._audit.log("read", "knowledge/lessons", detail=f"returned {len(result)} items")
        return result

    def update_lesson(self, lesson_id: str, updates: dict) -> dict:
        """Update fields on a lesson entry."""
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")
        allowed_fields = {"summary", "detail", "domain", "status"}
        for lesson in lessons:
            if lesson.get("id") == lesson_id:
                for key, value in updates.items():
                    if key in allowed_fields:
                        lesson[key] = value
                lesson["last_updated"] = _now_iso()
                _write_json(path, lessons)
                return lesson
        return {"error": f"Lesson not found: {lesson_id}"}

    def archive_lesson(self, lesson_id: str) -> dict:
        """Mark a lesson as outdated without deleting it."""
        return self.update_lesson(lesson_id, {"status": "outdated"})

    def add_decision(
        self,
        decision: dict | str,
        choice: str = "",
        reasoning: str = "",
        alternatives: list[str] | None = None,
        source_tool: str = "",
        project: str = "",
        **extra: Any,
    ) -> dict:
        """Record a key decision.

        Accepts either the original dict form or:
        add_decision("question", "choice", "reasoning").
        """
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")

        if isinstance(decision, dict):
            new_decision = dict(decision)
        else:
            new_decision = {"question": str(decision), "choice": choice}
            if reasoning:
                new_decision["reasoning"] = reasoning
            if alternatives:
                new_decision["alternatives"] = alternatives
            if source_tool:
                new_decision["source_tool"] = source_tool
            if project:
                new_decision["project"] = self._sanitize_project(project)

        for key, value in extra.items():
            if value is not None:
                new_decision[key] = value

        # Sanitize project field regardless of input path (dict or kwargs)
        if new_decision.get("project"):
            new_decision["project"] = self._sanitize_project(new_decision["project"])

        new_decision["timestamp"] = new_decision.get("timestamp") or _now_iso()
        new_decision = self._ensure_fields(new_decision, "decision")

        new_title = self._entry_identity_text(new_decision, "decision")
        for existing in decisions:
            existing = self._ensure_fields(existing, "decision")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_title,
                self._entry_identity_text(existing, "decision"),
            )
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": existing.get("id"),
                    "existing_title": self._entry_identity_text(existing, "decision"),
                    "message": f"与现有决策相似度 {sim:.0%}，未重复添加",
                }

        decisions.append(new_decision)
        if len(decisions) > MAX_KNOWLEDGE_ENTRIES:
            staging = [d for d in decisions if d.get("tier") == "staging"]
            verified = [d for d in decisions if d.get("tier") != "staging"]
            overflow = len(decisions) - MAX_KNOWLEDGE_ENTRIES
            if len(staging) >= overflow:
                staging = staging[overflow:]
            else:
                remaining = overflow - len(staging)
                staging = []
                verified = verified[remaining:]
            decisions = verified + staging
        _write_json(path, decisions)
        title = new_decision.get("question", "") or new_decision.get("title", "")
        self._audit.log("write", "knowledge/decisions", detail=title[:100])
        return new_decision

    def get_decisions(
        self,
        limit: int | None = 30,
        source_tool: str | None = None,
        project: str | None = None,
        domain: str | None = None,
        _update_access: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")
        result = []
        for decision in decisions:
            if decision.get("status") != "active":
                continue
            if source_tool and decision.get("source_tool") != source_tool:
                continue
            if project:
                decision_project = decision.get("project") or decision.get("source_project")
                if decision_project != project:
                    continue
            if domain:
                decision_domains = {d.strip() for d in (decision.get("domain") or "").split(",") if d.strip()}
                if domain not in decision_domains:
                    continue
            result.append(decision)
        result = result[-limit:] if limit is not None else result
        if _update_access and result:
            now = _now_iso()
            for decision in result:
                decision["last_reviewed"] = now
                decision["access_count"] = decision.get("access_count", 0) + 1
            _write_json(path, decisions)
        self._audit.log("read", "knowledge/decisions", detail=f"returned {len(result)} items")
        return result

    def update_decision(self, decision_id: str, updates: dict) -> dict:
        """Update fields on a decision entry."""
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")
        allowed_fields = {
            "title",
            "question",
            "choice",
            "reasoning",
            "alternatives",
            "status",
            "project",
            "source_tool",
        }
        for decision in decisions:
            if decision.get("id") == decision_id:
                for key, value in updates.items():
                    if key in allowed_fields:
                        decision[key] = value
                decision["last_updated"] = _now_iso()
                _write_json(path, decisions)
                return decision
        return {"error": f"Decision not found: {decision_id}"}

    def archive_decision(self, decision_id: str) -> dict:
        """Mark a decision as outdated without deleting it."""
        return self.update_decision(decision_id, {"status": "outdated"})

    # ------------------------------------------------------------------
    # Playbook CRUD — independent file-per-playbook storage
    # ------------------------------------------------------------------

    def _read_playbook_index(self) -> list[dict]:
        """Read the lightweight playbook index."""
        data = _read_json(self._playbooks_dir / "_index.json")
        if isinstance(data, list):
            return data
        return []

    def _write_playbook_index(self, entries: list[dict]) -> None:
        _write_json(self._playbooks_dir / "_index.json", entries)

    def _read_playbook_by_id(self, playbook_id: str) -> dict | None:
        """Read a single playbook file by ID."""
        path = self._playbooks_dir / f"{playbook_id}.json"
        if not path.exists():
            return None
        data = _read_json(path)
        return data if isinstance(data, dict) else None

    def _ensure_playbook_fields(self, entry: dict) -> dict:
        """Backfill metadata fields on a playbook entry."""
        if not isinstance(entry, dict):
            entry = {}
        if not entry.get("timestamp"):
            entry["timestamp"] = _now_iso()
        entry.setdefault("created_at", entry.get("timestamp", _now_iso()))
        entry.setdefault("last_reviewed", entry.get("created_at", _now_iso()))
        if not entry.get("id"):
            title = str(entry.get("title") or "")
            seed = f"{title}{entry.get('timestamp', '')}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]
        entry.setdefault("status", "active")
        entry.setdefault("access_count", 0)
        entry.setdefault("tier", "verified")
        if not isinstance(entry.get("related_ids"), list):
            entry["related_ids"] = []
        if not isinstance(entry.get("triggers"), list):
            entry["triggers"] = []
        if not isinstance(entry.get("steps"), list):
            entry["steps"] = []
        if not isinstance(entry.get("preconditions"), list):
            entry["preconditions"] = []
        if not isinstance(entry.get("pitfalls"), list):
            entry["pitfalls"] = []
        entry.setdefault("version", 1)
        return entry

    def _playbook_index_entry(self, pb: dict) -> dict:
        """Extract lightweight index entry from a full playbook."""
        return {
            "id": pb.get("id", ""),
            "title": pb.get("title", ""),
            "triggers": pb.get("triggers", []),
            "domain": pb.get("domain", ""),
            "status": pb.get("status", "active"),
            "updated_at": pb.get("last_updated") or pb.get("created_at") or _now_iso(),
        }

    def add_playbook(
        self,
        playbook: dict,
        source_tool: str = "",
        **extra: Any,
    ) -> dict:
        """Add an operational playbook.

        Each playbook is stored as an individual file in ~/.engram/playbooks/.
        An index file (_index.json) is maintained for fast search.
        """
        new_pb = dict(playbook)
        if source_tool:
            new_pb["source_tool"] = source_tool
        for key, value in extra.items():
            if value is not None:
                new_pb[key] = value

        if not new_pb.get("title"):
            return {"error": "Playbook must have a title"}

        new_pb["timestamp"] = new_pb.get("timestamp") or _now_iso()
        new_pb = self._ensure_playbook_fields(new_pb)

        # Duplicate detection against existing playbooks
        index = self._read_playbook_index()
        new_title = str(new_pb.get("title", ""))
        for entry in index:
            if entry.get("status") != "active":
                continue
            sim = self._bigram_similarity(new_title, entry.get("title", ""))
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": entry.get("id"),
                    "existing_title": entry.get("title"),
                    "message": f"与现有 Playbook 相似度 {sim:.0%}，未重复添加",
                }

        # Write individual playbook file
        pb_path = self._playbooks_dir / f"{new_pb['id']}.json"
        _write_json(pb_path, new_pb)

        # Update index
        index.append(self._playbook_index_entry(new_pb))
        self._write_playbook_index(index)

        self._audit.log("write", "playbooks", detail=new_title[:100])
        if new_pb.get("domain"):
            for _d in new_pb["domain"].split(","):
                _d = _d.strip()
                if _d:
                    self.increment_domain_usage(_d)
        return new_pb

    def get_playbooks(
        self,
        domain: str | None = None,
        limit: int | None = 20,
        _update_access: bool = True,
    ) -> list[dict]:
        """List active playbooks, optionally filtered by domain."""
        index = self._read_playbook_index()
        result = []
        for entry in index:
            if entry.get("status") != "active":
                continue
            if domain:
                pb_domains = {d.strip() for d in (entry.get("domain") or "").split(",") if d.strip()}
                if domain not in pb_domains:
                    continue
            pb = self._read_playbook_by_id(entry.get("id", ""))
            if pb:
                result.append(pb)

        result = result[-limit:] if limit is not None else result

        if _update_access and result:
            now = _now_iso()
            for pb in result:
                pb["last_reviewed"] = now
                pb["access_count"] = pb.get("access_count", 0) + 1
                _write_json(self._playbooks_dir / f"{pb['id']}.json", pb)

        self._audit.log("read", "playbooks", detail=f"returned {len(result)} items")
        return result

    def get_playbook(self, playbook_id: str, _update_access: bool = True) -> dict:
        """Get a single playbook by ID."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        if _update_access:
            pb["last_reviewed"] = _now_iso()
            pb["access_count"] = pb.get("access_count", 0) + 1
            _write_json(self._playbooks_dir / f"{playbook_id}.json", pb)

        return pb

    def update_playbook(self, playbook_id: str, updates: dict) -> dict:
        """Update fields on a playbook entry."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        for key, value in updates.items():
            if key in _ALLOWED_PLAYBOOK_UPDATE_FIELDS:
                pb[key] = value
        pb["last_updated"] = _now_iso()
        pb["version"] = pb.get("version", 1) + 1
        _write_json(self._playbooks_dir / f"{playbook_id}.json", pb)

        # Update index entry
        index = self._read_playbook_index()
        idx_entry = self._playbook_index_entry(pb)
        updated = False
        for i, entry in enumerate(index):
            if entry.get("id") == playbook_id:
                index[i] = idx_entry
                updated = True
                break
        if not updated:
            index.append(idx_entry)
        self._write_playbook_index(index)

        self._audit.log("write", "playbooks", detail=f"updated {playbook_id}")
        return pb

    def archive_playbook(self, playbook_id: str) -> dict:
        """Mark a playbook as outdated without deleting it."""
        return self.update_playbook(playbook_id, {"status": "outdated"})

    def _export_playbooks(self) -> list[dict]:
        """Export all playbooks as a list for backup."""
        index = self._read_playbook_index()
        result = []
        for entry in index:
            pb = self._read_playbook_by_id(entry.get("id", ""))
            if pb:
                result.append(pb)
        return result

    # ------------------------------------------------------------------
    # Tools Registry — local environment tool/program tracking
    # ------------------------------------------------------------------

    def _read_tools(self) -> list[dict]:
        """Read the tools registry."""
        path = self._environment_dir / "tools.json"
        data = _read_json(path)
        if isinstance(data, list):
            return data
        return []

    def _write_tools(self, tools: list[dict]) -> None:
        _write_json(self._environment_dir / "tools.json", tools)

    def _ensure_tool_fields(self, entry: dict) -> dict:
        """Backfill metadata fields on a tool entry."""
        if not isinstance(entry, dict):
            entry = {}
        now = _now_iso()
        if not entry.get("id"):
            name = str(entry.get("name") or "")
            path = str(entry.get("path") or "")
            seed = f"{name}{path}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]
        entry.setdefault("category", "other")
        entry.setdefault("status", "active")
        entry.setdefault("created_at", now)
        entry.setdefault("updated_at", now)
        entry.setdefault("registered_by", "")
        entry.setdefault("os_platform", "")
        entry.setdefault("version", "")
        entry.setdefault("install_method", "")
        entry.setdefault("notes", "")
        return entry

    def register_tool(
        self,
        tool: dict,
        registered_by: str = "",
    ) -> dict:
        """Register a local tool/program in the environment registry.

        If a tool with the same name already exists, update it instead.
        """
        new_tool = dict(tool)
        if not new_tool.get("name"):
            return {"error": "Tool must have a name"}

        if registered_by:
            new_tool["registered_by"] = registered_by

        new_tool = self._ensure_tool_fields(new_tool)
        tools = self._read_tools()

        # Check for existing tool with same name (case-insensitive) — update it
        new_name = str(new_tool.get("name", "")).lower()
        for i, existing in enumerate(tools):
            if str(existing.get("name", "")).lower() == new_name:
                # Update existing entry
                for key in ("path", "version", "purpose", "install_method",
                            "os_platform", "category", "notes", "status"):
                    if new_tool.get(key):
                        existing[key] = new_tool[key]
                existing["updated_at"] = _now_iso()
                if registered_by:
                    existing["registered_by"] = registered_by
                tools[i] = existing
                self._write_tools(tools)
                self._audit.log("write", "environment/tools", detail=f"updated {new_name}")
                return {**existing, "_action": "updated"}

        # New tool
        tools.append(new_tool)
        self._write_tools(tools)
        self._audit.log("write", "environment/tools", detail=f"registered {new_name}")
        return {**new_tool, "_action": "registered"}

    def find_tool(self, query: str) -> list[dict]:
        """Search tools by name, category, purpose, or path keyword."""
        tools = self._read_tools()
        if not query or not query.strip():
            return [t for t in tools if t.get("status") == "active"]

        terms = query.lower().split()
        results = []
        for tool in tools:
            if tool.get("status") != "active":
                continue
            searchable = " ".join([
                str(tool.get("name", "")),
                str(tool.get("category", "")),
                str(tool.get("purpose", "")),
                str(tool.get("path", "")),
                str(tool.get("notes", "")),
                str(tool.get("install_method", "")),
            ]).lower()
            if all(term in searchable for term in terms):
                results.append(tool)
        self._audit.log("read", "environment/tools", detail=f"found {len(results)} for '{query}'")
        return results

    def list_tools(self, category: str | None = None) -> list[dict]:
        """List all registered tools, optionally filtered by category."""
        tools = self._read_tools()
        result = []
        for tool in tools:
            if tool.get("status") != "active":
                continue
            if category and tool.get("category") != category:
                continue
            result.append(tool)
        self._audit.log("read", "environment/tools", detail=f"listed {len(result)} tools")
        return result

    def update_tool(self, tool_id: str, updates: dict) -> dict:
        """Update fields on a registered tool."""
        tools = self._read_tools()
        for tool in tools:
            if tool.get("id") == tool_id:
                for key, value in updates.items():
                    if key in _ALLOWED_TOOL_UPDATE_FIELDS:
                        tool[key] = value
                tool["updated_at"] = _now_iso()
                self._write_tools(tools)
                return tool
        return {"error": f"Tool not found: {tool_id}"}

    def remove_tool(self, tool_id: str) -> dict:
        """Mark a tool as removed (soft delete)."""
        return self.update_tool(tool_id, {"status": "removed"})

    def _export_tools(self) -> list[dict]:
        """Export all tools for backup."""
        return self._read_tools()

    def update_knowledge(self, item_id: str, updates: dict) -> dict:
        """Update a lesson, decision, or playbook by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.update_lesson(item_id, updates)
        if item_type == "playbook":
            return self.update_playbook(item_id, updates)
        return self.update_decision(item_id, updates)

    def archive_knowledge(self, item_id: str) -> dict:
        """Archive a lesson, decision, or playbook by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.archive_lesson(item_id)
        if item_type == "playbook":
            return self.archive_playbook(item_id)
        return self.archive_decision(item_id)

    def review_knowledge(self, knowledge_id: str) -> dict:
        """Mark a lesson or decision as reviewed without changing its content."""
        lessons, decisions = self._read_link_collections()
        item_type, item = self._find_item_in_collections(knowledge_id, lessons, decisions)
        if item is None or item_type is None:
            return {"error": f"Item not found: {knowledge_id}"}

        item["last_reviewed"] = _now_iso()
        item["access_count"] = item.get("access_count", 0) + 1
        self._write_link_collections(lessons, decisions)
        self._audit.log("write", "knowledge/review", detail=knowledge_id)
        return item

    def merge_knowledge(self, primary_id: str, secondary_id: str) -> dict:
        """Merge secondary into primary, then archive the secondary item."""
        if primary_id == secondary_id:
            return {"error": "Cannot merge an item with itself"}

        lessons, decisions = self._read_link_collections()
        primary_type, primary = self._find_item_in_collections(primary_id, lessons, decisions)
        secondary_type, secondary = self._find_item_in_collections(secondary_id, lessons, decisions)

        if primary is None:
            return {"error": f"Primary item not found: {primary_id}"}
        if secondary is None:
            return {"error": f"Secondary item not found: {secondary_id}"}
        if primary.get("status") != "active":
            return {"error": f"Primary item is not active (status={primary.get('status')})"}
        if secondary.get("status") != "active":
            return {"error": f"Secondary item is not active (status={secondary.get('status')})"}

        primary_related = set(primary.get("related_ids", []))
        transferred = []
        secondary_related = list(secondary.get("related_ids", []))

        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            if related_id not in primary_related:
                primary_related.add(related_id)
                transferred.append(related_id)

        primary_related.discard(primary_id)
        primary_related.discard(secondary_id)
        primary["related_ids"] = sorted(primary_related)

        # Preserve bidirectional link semantics for migrated related items.
        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            _, related_item = self._find_item_in_collections(related_id, lessons, decisions)
            if related_item is None:
                continue
            related_ids = set(related_item.get("related_ids", []))
            related_ids.discard(secondary_id)
            related_ids.discard(related_id)
            related_ids.add(primary_id)
            related_item["related_ids"] = sorted(related_ids)

        secondary["status"] = "outdated"
        secondary["merged_into"] = primary_id
        secondary["last_updated"] = _now_iso()
        self._write_link_collections(lessons, decisions)

        return {
            "success": True,
            "primary_id": primary_id,
            "secondary_id": secondary_id,
            "secondary_archived": True,
            "related_ids_transferred": len(transferred),
            "primary_title": self._knowledge_title(primary_type, primary),
            "secondary_title": self._knowledge_title(secondary_type, secondary),
        }

    def _read_link_collections(self) -> tuple[list[dict], list[dict]]:
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        return lessons, decisions

    def _write_link_collections(self, lessons: list[dict], decisions: list[dict]) -> None:
        # Each write is individually atomic, but the two writes together are not.
        # A crash between them could leave a one-sided link. Acceptable for local single-user use.
        _write_json(self._knowledge_dir / "lessons.json", lessons)
        _write_json(self._knowledge_dir / "decisions.json", decisions)

    def _find_item_in_collections(
        self,
        item_id: str,
        lessons: list[dict],
        decisions: list[dict],
    ) -> tuple[str | None, dict | None]:
        for item in lessons:
            if item.get("id") == item_id:
                return "lesson", item
        for item in decisions:
            if item.get("id") == item_id:
                return "decision", item
        return None, None

    def _find_item_by_id(self, item_id: str) -> tuple[str | None, dict | None]:
        """Find a lesson, decision, or playbook by id without updating access metadata."""
        lessons, decisions = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions)
        if item_type is not None:
            return item_type, item
        # Fall through to playbook file-based lookup
        pb = self._read_playbook_by_id(item_id)
        if pb is not None:
            return "playbook", pb
        # Fall through to tools registry
        for tool in self._read_tools():
            if tool.get("id") == item_id:
                return "tool", tool
        return None, None

    def _knowledge_title(self, item_type: str | None, item: dict | None) -> str:
        if not item:
            return ""
        if item_type == "decision":
            return self._entry_identity_text(item, "decision")
        if item_type == "playbook":
            return item.get("title", "")
        return item.get("summary", "")

    def _knowledge_view(self, item_type: str, item: dict) -> dict:
        if item_type == "decision":
            return {
                "id": item.get("id", ""),
                "type": "decision",
                "title": self._entry_identity_text(item, "decision"),
                "choice": item.get("choice", ""),
                "rationale": item.get("reasoning", ""),
            }
        if item_type == "playbook":
            return {
                "id": item.get("id", ""),
                "type": "playbook",
                "title": item.get("title", ""),
                "triggers": item.get("triggers", []),
                "description": item.get("description", ""),
                "domain": item.get("domain", ""),
            }
        return {
            "id": item.get("id", ""),
            "type": "lesson",
            "title": item.get("summary", ""),
            "content": item.get("detail") or item.get("summary", ""),
            "domain": item.get("domain", ""),
        }

    def link_knowledge(self, id_a: str, id_b: str) -> dict:
        """Create a bidirectional link between two knowledge items."""
        lessons, decisions = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        if id_b not in item_a["related_ids"]:
            item_a["related_ids"].append(id_b)
        if id_a not in item_b["related_ids"]:
            item_b["related_ids"].append(id_a)
        self._write_link_collections(lessons, decisions)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Linked: {title_a} ↔ {title_b}"}

    def unlink_knowledge(self, id_a: str, id_b: str) -> dict:
        """Remove the bidirectional link between two knowledge items."""
        lessons, decisions = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        item_a["related_ids"] = [item_id for item_id in item_a["related_ids"] if item_id != id_b]
        item_b["related_ids"] = [item_id for item_id in item_b["related_ids"] if item_id != id_a]
        self._write_link_collections(lessons, decisions)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Unlinked: {title_a} ↔ {title_b}"}

    def get_related_knowledge(self, item_id: str) -> dict:
        """Return all knowledge items linked to a lesson or decision id."""
        lessons, decisions = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        related = []
        for related_id in item.get("related_ids", []):
            related_type, related_item = self._find_item_in_collections(
                related_id,
                lessons,
                decisions,
            )
            if related_item is not None and related_type is not None:
                related.append(self._knowledge_view(related_type, related_item))

        return {
            "source": self._knowledge_view(item_type, item),
            "related": related,
            "total": len(related),
        }

    def update_domain(self, domain: str, updates: dict) -> None:
        """Update skill/experience data for a domain (e.g. "python", "frontend")."""
        path = self._knowledge_dir / "domains.json"
        domains = _read_json(path)
        if not isinstance(domains, dict):
            domains = {}
        if domain not in domains:
            domains[domain] = {"first_seen": _now_iso(), "project_count": 0}
        domains[domain].update(updates)
        domains[domain]["updated_at"] = _now_iso()
        _write_json(path, domains)

    def get_domains(self) -> dict:
        path = self._knowledge_dir / "domains.json"
        stored = _read_json(path)
        if not isinstance(stored, dict):
            stored = {}

        active_counts: dict[str, int] = {}
        for lesson in self.get_lessons(limit=None, _update_access=False):
            raw = lesson.get("domain") or ""
            for _d in raw.split(","):
                _d = _d.strip()
                if _d:
                    active_counts[_d] = active_counts.get(_d, 0) + 1

        result: dict[str, dict] = {}
        for domain, count in active_counts.items():
            entry = stored.get(domain, {})
            if not isinstance(entry, dict):
                entry = {}
            merged = dict(entry)
            merged["project_count"] = count
            result[domain] = merged
        return result

    def increment_domain_usage(self, domain: str) -> None:
        """Increment project count for a domain."""
        path = self._knowledge_dir / "domains.json"
        domains = _read_json(path)
        if not isinstance(domains, dict):
            domains = {}
        entry = domains.get(domain, {"first_seen": _now_iso(), "project_count": 0})
        entry["project_count"] = entry.get("project_count", 0) + 1
        entry["last_used"] = _now_iso()
        domains[domain] = entry
        _write_json(path, domains)

    # =====================================================================
    # Projects — per-project knowledge
    # =====================================================================

    def save_project_snapshot(self, project_folder: str, data: dict) -> None:
        """Save/update knowledge for a specific project."""
        pid = _project_id(project_folder)
        path = self._projects_dir / f"{pid}.json"
        existing = _read_json(path)
        existing.update(data)
        existing["project_folder"] = project_folder
        existing["updated_at"] = _now_iso()
        if "created_at" not in existing:
            existing["created_at"] = _now_iso()
        _write_json(path, existing)

    def get_project_snapshot(self, project_folder: str) -> dict:
        pid = _project_id(project_folder)
        return _read_json(self._projects_dir / f"{pid}.json")

    def list_projects(self) -> list[dict]:
        """List all known projects with basic info."""
        result = []
        for f in sorted(self._projects_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                result.append({
                    "id": f.stem,
                    "folder": data.get("project_folder", ""),
                    "title": data.get("title", ""),
                    "updated_at": data.get("updated_at", ""),
                    "session_count": data.get("session_count", 0),
                })
        return result

    # =====================================================================
    # Import / Export — 备份、迁移、跨机器同步
    # =====================================================================

    def export_all(self, output_path: str | None = None) -> str:
        """导出整个 Engram 为单一 JSON 文件。

        包含：identity、knowledge、projects 所有数据。
        用于备份或迁移到另一台机器。

        Args:
            output_path: 导出文件路径。默认存到 ~/.engram/exports/engram_backup_<date>.json

        Returns:
            导出文件的完整路径。
        """
        export_data = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "identity": {
                "profile": self.get_profile(),
                "preferences": self.get_preferences(),
                "work_style": self.get_work_style(),  # backward compat
                "quality_standards": self.get_quality_standards(),
                "trust_boundaries": self.get_trust_boundaries(),
            },
            "knowledge": {
                "lessons": _read_json(self._knowledge_dir / "lessons.json") or [],
                "decisions": _read_json(self._knowledge_dir / "decisions.json") or [],
                "domains": self.get_domains(),
                "playbooks": self._export_playbooks(),
            },
            "environment": {
                "tools": self._export_tools(),
            },
            "projects": {},
        }

        # 导出所有项目快照
        for f in sorted(self._projects_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                export_data["projects"][f.stem] = data

        # 确定输出路径
        if output_path:
            out = Path(output_path)
        else:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = self._exports_dir / f"engram_backup_{date_str}.json"

        out.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out, export_data)
        self._audit.log("export", "all", detail=f"exported to {out}")
        return str(out)

    def import_all(self, input_path: str, merge: bool = True) -> dict:
        """从备份文件导入 Engram 数据。

        Args:
            input_path: 备份文件路径（export_all 生成的 JSON）。
            merge: True=合并（已有数据保留，新数据追加），False=覆盖。

        Returns:
            导入结果摘要。
        """
        path = Path(input_path)
        if not path.is_file():
            return {"error": f"文件不存在: {input_path}"}

        data = _read_json(path)
        if not data or "schema_version" not in data:
            return {"error": "不是有效的 Engram 备份文件"}

        imported = []

        # Identity
        identity = data.get("identity", {})
        if identity.get("profile"):
            if merge:
                existing = self.get_profile()
                # 合并：新字段补充，不覆盖已有值
                for k, v in identity["profile"].items():
                    if k not in existing or not existing[k]:
                        existing[k] = v
                self.update_profile(existing)
            else:
                _write_json(self._identity_dir / "profile.json", identity["profile"])
            imported.append("profile")

        if identity.get("work_style"):
            if merge:
                existing = self.get_work_style()
                existing.update(identity["work_style"])
                self.update_work_style(existing)
            else:
                _write_json(self._identity_dir / "work_style.json", identity["work_style"])
            imported.append("work_style")

        if identity.get("quality_standards"):
            if merge:
                existing = self.get_quality_standards()
                new_rules = identity["quality_standards"].get("rules", [])
                old_rules = set(existing.get("rules", []))
                merged_rules = list(old_rules | set(new_rules))
                existing["rules"] = merged_rules[-15:]
                if identity["quality_standards"].get("acceptance_threshold"):
                    existing["acceptance_threshold"] = identity["quality_standards"]["acceptance_threshold"]
                self.update_quality_standards(existing)
            else:
                _write_json(self._identity_dir / "quality_standards.json", identity["quality_standards"])
            imported.append("quality_standards")

        # Knowledge
        knowledge = data.get("knowledge", {})

        if knowledge.get("lessons"):
            if merge:
                existing = _read_json(self._knowledge_dir / "lessons.json") or []
                existing_summaries = {l.get("summary", "") for l in existing}
                new_count = 0
                for lesson in knowledge["lessons"]:
                    if lesson.get("summary") not in existing_summaries:
                        existing.append(lesson)
                        existing_summaries.add(lesson.get("summary", ""))
                        new_count += 1
                # Keep last MAX_KNOWLEDGE_ENTRIES
                _write_json(self._knowledge_dir / "lessons.json", existing[-MAX_KNOWLEDGE_ENTRIES:])
                imported.append(f"lessons(+{new_count})")
            else:
                _write_json(self._knowledge_dir / "lessons.json", knowledge["lessons"][-MAX_KNOWLEDGE_ENTRIES:])
                imported.append(f"lessons({len(knowledge['lessons'])})")

        if knowledge.get("decisions"):
            if merge:
                existing = _read_json(self._knowledge_dir / "decisions.json") or []
                existing_questions = {d.get("question", "") for d in existing}
                new_count = 0
                for decision in knowledge["decisions"]:
                    if decision.get("question") not in existing_questions:
                        existing.append(decision)
                        existing_questions.add(decision.get("question", ""))
                        new_count += 1
                _write_json(self._knowledge_dir / "decisions.json", existing[-MAX_KNOWLEDGE_ENTRIES:])
                imported.append(f"decisions(+{new_count})")
            else:
                _write_json(self._knowledge_dir / "decisions.json", knowledge["decisions"][-MAX_KNOWLEDGE_ENTRIES:])
                imported.append(f"decisions({len(knowledge['decisions'])})")

        if knowledge.get("domains"):
            if merge:
                existing = self.get_domains()
                for name, info in knowledge["domains"].items():
                    if name not in existing:
                        existing[name] = info
                    else:
                        # 取更大的 project_count
                        existing[name]["project_count"] = max(
                            existing[name].get("project_count", 0),
                            info.get("project_count", 0),
                        )
                _write_json(self._knowledge_dir / "domains.json", existing)
            else:
                _write_json(self._knowledge_dir / "domains.json", knowledge["domains"])
            imported.append("domains")

        if knowledge.get("playbooks"):
            new_count = 0
            existing_index = self._read_playbook_index()
            existing_titles = {e.get("title", "") for e in existing_index}
            for pb in knowledge["playbooks"]:
                if pb.get("title") not in existing_titles:
                    pb = self._ensure_playbook_fields(pb)
                    _write_json(self._playbooks_dir / f"{pb['id']}.json", pb)
                    existing_index.append(self._playbook_index_entry(pb))
                    existing_titles.add(pb.get("title", ""))
                    new_count += 1
            if new_count:
                self._write_playbook_index(existing_index)
            imported.append(f"playbooks(+{new_count})" if merge else f"playbooks({len(knowledge['playbooks'])})")

        # Environment (tools registry)
        environment = data.get("environment", {})
        if environment.get("tools"):
            if merge:
                existing = self._read_tools()
                existing_names = {t.get("name", "").lower() for t in existing}
                new_count = 0
                for tool in environment["tools"]:
                    if tool.get("name", "").lower() not in existing_names:
                        tool = self._ensure_tool_fields(tool)
                        existing.append(tool)
                        existing_names.add(tool.get("name", "").lower())
                        new_count += 1
                self._write_tools(existing)
                imported.append(f"tools(+{new_count})")
            else:
                self._write_tools(environment["tools"])
                imported.append(f"tools({len(environment['tools'])})")

        # Projects
        projects = data.get("projects", {})
        if projects:
            for pid, proj_data in projects.items():
                proj_path = self._projects_dir / f"{pid}.json"
                if merge and proj_path.exists():
                    existing = _read_json(proj_path)
                    existing.update(proj_data)
                    _write_json(proj_path, existing)
                else:
                    _write_json(proj_path, proj_data)
            imported.append(f"projects({len(projects)})")

        self._audit.log("import", "all", detail=f"imported from {input_path}")
        return {
            "status": "success",
            "mode": "merge" if merge else "overwrite",
            "imported": imported,
            "source": input_path,
        }

