"""Engram — AI 记忆印记，核心读写库。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
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
    MEMORY_RISK_LEVELS,
    MEMORY_STATES,
    PLAYBOOK_TRIGGERS,
    SCHEMA_VERSION,
    SEARCH_RELEVANCE_THRESHOLD,
    SIMILARITY_DUPLICATE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    _SUPPLEMENT_MARKERS,
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
    DataCorruptionError,
)
from .retrieval import RetrievalMixin
from .context import ContextMixin
from .context import EXTRACTION_PROMPT, extract_knowledge, ingest_extraction  # noqa: F401
from .reconcile import ReconcileMixin
from .reports import ReportsMixin
from .contexts import ContextStoreMixin
from .encoding_repair import normalize_entry_text
# Compat helpers re-exported for backward compatibility (tests import these
# from piia_engram.core directly).
from .compat import (  # noqa: F401
    export_to_openclaw,
    import_from_openclaw,
    migrate_from_oca_memory,
)


_BUILTIN_PLAYBOOKS: dict[str, dict] = {}


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
        # Corpus encryption key (derived once, reused for all entry I/O)
        self._corpus_key: bytes = b""
        if self._crypto.enabled:
            salt_path = self.root / ".corpus_salt"
            if salt_path.is_file():
                salt = salt_path.read_bytes()
            else:
                # Fail-closed: if corpus files already contain enc:v2c: data
                # but the salt is missing, refuse to create a new salt — that
                # would make existing data permanently unreadable.
                if self._has_existing_ciphertext():
                    raise RuntimeError(
                        f".corpus_salt is missing from {self.root} but encrypted "
                        "corpus data (enc:v2c:) exists. Restore the original "
                        ".corpus_salt file to recover your data. Creating a new "
                        "salt would make existing data permanently unreadable."
                    )
                salt = os.urandom(16)
                self.root.mkdir(parents=True, exist_ok=True)
                salt_path.write_bytes(salt)
            self._corpus_key = self._crypto.derive_corpus_key(salt)
            # A plaintext hybrid search index left over from a pre-encryption
            # run would keep the decrypted bodies readable on disk even though
            # all new writes are encrypted. Purge it on init so enabling
            # encryption can't be silently undermined by a stale index
            # (Codex a5 round-2 P1-2). purge_search_index is provided by the
            # RetrievalMixin.
            try:
                self.purge_search_index()
            except RuntimeError:
                # Fail-closed: a plaintext index survived purge under encryption
                # (Codex a5 round-3 O2). Refuse to construct the engram rather
                # than leave decrypted bodies readable on disk.
                raise
            except Exception:
                # Tolerate MRO/availability surprises — the purge is a
                # best-effort defensive step for everything except the
                # fail-closed condition above.
                pass

        # Audit logger (disabled unless ENGRAM_AUDIT=1/true/yes)
        from piia_engram.audit import AuditLogger
        audit_enabled = os.environ.get("ENGRAM_AUDIT", "").strip().lower() in ("1", "true", "yes")
        self._audit = AuditLogger(
            log_path=self.root / "audit.log" if audit_enabled else None,
            enabled=audit_enabled,
        )

        # Data fragmentation detection — warn, don't silently split.
        # Skip in test environments (ENGRAM_TEST=1) to avoid noisy warnings
        # when a temporary ENGRAM_DIR coexists with the real ~/.engram.
        if os.environ.get("ENGRAM_TEST", "").strip().lower() in ("1", "true", "yes"):
            self.data_orphans: list[str] = []
        else:
            self.data_orphans = detect_data_fragmentation(self.root)
        if self.data_orphans:
            logger.warning(
                "DATA FRAGMENTATION: active root is %s but data also "
                "exists at: %s — knowledge may be incomplete!",
                self.root, ", ".join(self.data_orphans),
            )

        self._ensure_structure()

        # v3.30 mechanism (1): unclean-exit detection.
        # Each Engram() init stamps session_state.json with current pid +
        # last_clean_exit=False. Normal shutdown rewrites it with True
        # via _mark_clean_exit. If a later instance sees the previous
        # state was last_clean_exit=False, doctor (mechanism 1) surfaces
        # "previous session may have ended unexpectedly". This is the
        # crash-recovery user-visible signal — the data itself is already
        # safe thanks to _atomic_write_json + portalocker.
        try:
            self._mark_session_start()
        except Exception:
            pass  # Best-effort; never block init.

    @property
    def _session_state_path(self) -> Path:
        return self.root / "session_state.json"

    def get_unclean_exit_marker(self) -> dict | None:
        """Return the prior session's state iff it ended uncleanly.

        Returns None when:
        - no prior state exists (fresh install or first run after migration);
        - prior state recorded a clean exit (last_clean_exit=True);
        - the stored pid is the CURRENT process (so we never mis-report
          our own in-progress session as "unclean" — only the previous
          process's leftover state qualifies).

        Returns a dict with ``pid``, ``started_at``, ``last_seen_at``,
        ``last_session_id`` when the previous session ended without
        ``_mark_clean_exit`` firing (process killed, OS crashed, etc.).
        Doctor surfaces this so the user knows a checkpoint may have
        been lost (limited to the heartbeat interval, mechanism 2).
        """
        try:
            data = _read_json(self._session_state_path)
            if not isinstance(data, dict):
                return None
            if data.get("last_clean_exit"):
                return None
            pid = data.get("pid")
            if not pid:
                return None
            # The "in-progress current session" must not be reported as
            # unclean — that's the breadcrumb _mark_session_start just
            # wrote 1ms ago, not a kill survivor. ``_owns_session_state``
            # uses nonce + started_at so a fresh process that happens to
            # inherit a recycled pid won't accidentally suppress the
            # warning either (M1).
            if self._owns_session_state(data):
                return None
            return {
                "pid": pid,
                "started_at": data.get("started_at", ""),
                "last_seen_at": data.get("last_seen_at", ""),
                "last_session_id": data.get("last_session_id", ""),
            }
        except Exception:
            return None

    def _mark_session_start(self) -> None:
        """Stamp session_state.json with the new session's metadata.

        v3.30 H5 fix: also record a per-process ``session_nonce`` so we
        can later prove ownership of the on-disk breadcrumb when marking
        a clean exit. Without that nonce two Engram processes that share
        the same ``ENGRAM_DIR`` would step on each other's session
        markers — A's clean exit could erase B's unclean-exit signal.
        """
        # We deliberately don't preserve the previous file: get_unclean_exit_marker
        # is meant to be called once per init (before we overwrite the file).
        # Callers that need to surface the warning must do so on init.
        self._prev_unclean = self.get_unclean_exit_marker()
        # Per-process owner identifiers. ``session_nonce`` is the
        # canonical owner key — pid + started_at alone are not unique
        # enough across pid reuse (M1) or NTP clock skew.
        import secrets
        self._session_pid = os.getpid()
        self._session_started_at = _now_iso()
        self._session_nonce = secrets.token_hex(8)
        payload = {
            "pid": self._session_pid,
            "started_at": self._session_started_at,
            "last_seen_at": self._session_started_at,
            "last_clean_exit": False,
            "last_session_id": "",
            "session_nonce": self._session_nonce,
        }
        try:
            _write_json(self._session_state_path, payload)
        except Exception:
            pass

    def _owns_session_state(self, data: dict | None) -> bool:
        """Return True iff ``data`` on disk was written by this process.

        Owner is verified by ``session_nonce`` (preferred) with a
        ``pid + started_at`` fallback for older v3.30 files that don't
        yet have a nonce. Anything else means another process owns the
        breadcrumb and we must not touch ``last_clean_exit``.
        """
        if not isinstance(data, dict):
            return False
        my_nonce = getattr(self, "_session_nonce", None)
        on_disk_nonce = data.get("session_nonce")
        if on_disk_nonce:
            # Disk has a v3.30+ nonce.  We can only claim ownership if
            # we also have a nonce AND the two match.  During init
            # (before _mark_session_start sets our nonce) my_nonce is
            # None — that means the on-disk marker belongs to a
            # *previous* session, not ours. Returning False here lets
            # get_unclean_exit_marker correctly surface a crash even
            # when the OS has recycled the old process's pid (H1 fix).
            return bool(my_nonce) and on_disk_nonce == my_nonce
        # Pre-nonce fallback: disk has no nonce (pre-v3.30 marker).
        # We must have our own identifiers already set to make a
        # meaningful comparison; during init they're absent, so we
        # can't prove ownership and must return False (safe default).
        my_pid = getattr(self, "_session_pid", None)
        if my_pid is None:
            return False
        if data.get("pid") != my_pid:
            return False
        my_started = getattr(self, "_session_started_at", None)
        if my_started and data.get("started_at") != my_started:
            return False
        return True

    def _mark_clean_exit(self, last_session_id: str = "") -> None:
        """Stamp session_state.json with last_clean_exit=True on graceful shutdown.

        v3.30 H5 fix: only writes when the breadcrumb on disk is the one
        this process wrote at ``_mark_session_start``. If another
        Engram process has since taken over the file, we leave its
        ``last_clean_exit=False`` flag alone so its eventual crash is
        still detectable.
        """
        try:
            data = _read_json(self._session_state_path)
            if not self._owns_session_state(data if isinstance(data, dict) else None):
                # Another process owns the marker. Don't clobber it.
                return
            if not isinstance(data, dict):
                data = {}
            data.update({
                "last_clean_exit": True,
                "last_seen_at": _now_iso(),
                "last_session_id": last_session_id,
            })
            _write_json(self._session_state_path, data)
        except Exception:
            pass

    def _has_existing_ciphertext(self) -> bool:
        """Quick check: do any corpus files contain enc:v2c: data?

        Scans the FULL contents of knowledge/*.json and playbooks/*.json (plus
        the playbook ``_index.json`` and execution plans) for the corpus
        encryption prefix. Used during init to fail-closed when .corpus_salt is
        missing but encrypted data exists.

        A truncated (first-4KB) scan would miss ciphertext that appears after
        the prefix window — a large lessons.json whose first entry is plaintext
        metadata but whose encrypted ``content`` lands past 4KB would be wrongly
        treated as "no ciphertext", silently minting a fresh salt and making the
        real data permanently unreadable (Codex a5 round-2 P1-3).

        ``playbooks/_index.json`` is included rather than skipped: it carries
        encrypted playbook titles, so a root whose ONLY surviving ciphertext is
        the index (e.g. partial restore) must still fail-closed instead of
        minting a fresh salt (Codex a5 round-3 O1).
        """
        from .crypto import ENC_PREFIX_V2C
        marker = ENC_PREFIX_V2C.encode("ascii")
        for pattern in (
            "knowledge/*.json",
            "playbooks/*.json",
            "playbooks/executions/*.json",
        ):
            for path in self.root.glob(pattern):
                try:
                    if marker in path.read_bytes():
                        return True
                except OSError:
                    continue
        return False

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

    def update_profile(self, updates: dict, source_tool: str = "") -> None:
        """Merge updates into the user profile.

        ``description`` uses append semantics: new tokens that are not
        already present are appended so that markers from multiple tools
        coexist rather than overwriting each other.

        Field-level provenance is tracked in ``_provenance`` so callers
        can determine which tool last touched each field.
        """
        updates, rejected = self._filter_allowed(updates, _ALLOWED_PROFILE_FIELDS)
        if rejected:
            self._audit.log("warn", "identity/profile",
                            detail=f"rejected unknown fields: {rejected}")
        if not updates:
            return
        updates = self._repair_incoming_text(dict(updates))
        profile = self.get_profile()

        # Description: append-merge to preserve multi-tool markers
        if "description" in updates and profile.get("description"):
            old_desc = profile["description"]
            new_desc = updates["description"]
            if not new_desc:
                # Empty update — keep existing
                updates["description"] = old_desc
            else:
                existing_parts = set(old_desc.split())
                new_parts = [p for p in new_desc.split() if p not in existing_parts]
                if new_parts:
                    updates["description"] = old_desc + " " + " ".join(new_parts)
                else:
                    # All tokens already present — keep existing unchanged
                    updates["description"] = old_desc

        now = _now_iso()

        # Track field-level provenance
        provenance = profile.get("_provenance", {})
        for key in updates:
            if key not in ("updated_at",):
                provenance[key] = {"by": source_tool or "unknown", "at": now}

        profile.update(updates)
        profile["updated_at"] = now
        profile["_provenance"] = provenance
        if source_tool:
            profile["_last_updated_by"] = source_tool
        encrypted = self._crypto.encrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
        _write_json(self._identity_dir / "profile.json", encrypted)
        self._audit.log("write", "identity/profile", detail=str(list(updates.keys())))

    def get_work_style(self) -> dict:
        return _read_json(self._identity_dir / "work_style.json")

    def update_work_style(self, updates: dict) -> None:
        updates = self._repair_incoming_text(dict(updates))
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
        updates = self._repair_incoming_text(dict(updates))
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
        updates = self._repair_incoming_text(dict(updates))
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

    @staticmethod
    def _repair_incoming_text(payload: dict) -> dict:
        normalized, _ = normalize_entry_text(payload)
        return normalized if isinstance(normalized, dict) else payload

    @staticmethod
    def _derive_memory_state(entry: dict) -> str:
        if entry.get("tier") == "staging":
            return "staging"
        if entry.get("status") == "rejected":
            return "rejected"
        if entry.get("tier") == "archived" or entry.get("status") in {"outdated", "archived", "deprecated"}:
            return "deprecated"
        if entry.get("tier") == "verified":
            return "verified"
        explicit = entry.get("memory_state")
        if explicit in MEMORY_STATES:
            return explicit
        return "verified"

    @staticmethod
    def _entry_risk_text(entry: dict) -> str:
        fields = ("summary", "detail", "question", "choice", "reasoning", "title", "description")
        return "\n".join(str(entry.get(key) or "") for key in fields)

    def _assess_memory_risk(self, entry: dict) -> dict[str, Any]:
        text = self._entry_risk_text(entry)
        lowered = text.lower()
        flags: list[str] = []
        if re.search(r"https?://", text):
            flags.append("external_url")
        if any(marker in lowered for marker in (
            "api_key", "apikey", "private key", "secret", "token=", "password",
            "bearer ", "ssh-rsa", "-----begin",
        )):
            flags.append("credential")
        if any(marker in lowered for marker in (
            "run command", "powershell", "cmd.exe", "bash ", "curl ", "wget ",
            "rm -rf", "delete all", "git push", "twine upload",
        )):
            flags.append("command")
        if any(marker in lowered for marker in (
            "mcp config", "mcp_server", "mcp_servers", "mcpservers", "server_key",
        )):
            flags.append("mcp_config")
        if any(marker in lowered for marker in (
            "permission", "allowlist", "denylist", "bypass", "approval",
            "push/tag/publish", "publish without approval",
        )):
            flags.append("permission_rule")

        if any(flag in flags for flag in ("credential", "command", "mcp_config", "permission_rule")):
            level = "high"
        elif flags:
            level = "medium"
        else:
            level = "low"
        return {"risk_flags": sorted(set(flags)), "risk_level": level}

    @staticmethod
    def _max_risk_level(*levels: str) -> str:
        rank = {"low": 0, "medium": 1, "high": 2}
        valid = [level for level in levels if level in rank]
        if not valid:
            return "low"
        return max(valid, key=lambda level: rank[level])

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
            elif entry_type == "decision":
                seed = f"{identity}{entry.get('choice', '')}{entry.get('timestamp', '')}"
            else:
                seed = f"{identity}{entry.get('timestamp', '')}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]

        entry.setdefault("status", "active")
        entry.setdefault("access_count", 0)
        # Knowledge tier: "staging" (unverified) or "verified" (confirmed valuable)
        entry.setdefault("tier", "verified")  # Legacy items default to verified
        if not isinstance(entry.get("related_ids"), list):
            entry["related_ids"] = []

        state = self._derive_memory_state(entry)
        entry["memory_state"] = state
        if state == "staging":
            entry["approval_status"] = "pending"
        elif state == "rejected":
            entry["approval_status"] = "rejected"
        elif state == "deprecated":
            entry["approval_status"] = "deprecated"
        else:
            entry["approval_status"] = "approved"

        provenance = entry.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        provenance.setdefault("source_tool", entry.get("source_tool") or "unknown")
        provenance.setdefault("created_at", entry.get("created_at"))
        provenance.setdefault("entry_type", entry_type)
        if entry.get("domain"):
            provenance.setdefault("domain", entry.get("domain"))
        if entry.get("project"):
            provenance.setdefault("project", entry.get("project"))
        entry["provenance"] = provenance

        risk = self._assess_memory_risk(entry)
        existing_risk_level = entry.get("risk_level")
        if existing_risk_level not in MEMORY_RISK_LEVELS:
            existing_risk_level = "low"
        entry["risk_level"] = self._max_risk_level(existing_risk_level, risk["risk_level"])
        existing_flags = entry.get("risk_flags")
        if isinstance(existing_flags, list):
            flag_set = {str(flag) for flag in existing_flags if str(flag)}
        else:
            flag_set = set()
        flag_set.update(risk["risk_flags"])
        entry["risk_flags"] = sorted(flag_set)
        entry["approval_required"] = (
            entry["memory_state"] == "staging" or entry["risk_level"] == "high"
        )

        return entry

    # ------------------------------------------------------------------
    # Corpus encryption helpers (transparent when ENGRAM_SECRET not set)
    # ------------------------------------------------------------------

    def _write_entries(self, path: Path, entries: list[dict], entry_type: str):
        """Write knowledge entries with corpus encryption if enabled."""
        if self._corpus_key:
            entries = [self._crypto.encrypt_entry(e, self._corpus_key, entry_type)
                       for e in entries]
        _write_json(path, entries)

    def _write_playbook_file(self, path: Path, pb: dict):
        """Write a single playbook file with corpus encryption if enabled."""
        if self._corpus_key:
            pb = self._crypto.encrypt_entry(pb, self._corpus_key, "playbook")
        _write_json(path, pb)

    def _read_playbook_file(self, path: Path) -> dict:
        """Read a single playbook file with corpus decryption."""
        data = _read_json(path)
        if not isinstance(data, dict):
            return {}
        if self._corpus_key:
            return self._crypto.decrypt_entry(data, self._corpus_key, "playbook")
        return data

    def _read_entries(
        self,
        path: Path,
        entry_type: str,
        *,
        migrate: bool = True,
    ) -> list[dict]:
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

        if changed and migrate:
            _write_json(path, ensured)  # preserves encrypted fields as-is

        # Decrypt content fields for in-memory use
        if self._corpus_key:
            return [self._crypto.decrypt_entry(e, self._corpus_key, entry_type)
                    for e in ensured]
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

        new_lesson = self._repair_incoming_text(new_lesson)
        new_lesson["timestamp"] = new_lesson.get("timestamp") or _now_iso()
        new_lesson = self._ensure_fields(new_lesson, "lesson")

        # Three-tier dedup: exact duplicate / semantically related / pass
        best_sim = 0.0
        best_match = None
        for existing in lessons:
            existing = self._ensure_fields(existing, "lesson")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_lesson.get("summary", ""),
                existing.get("summary", ""),
            )
            if sim > best_sim:
                best_sim = sim
                best_match = existing

        if best_sim >= SIMILARITY_DUPLICATE_THRESHOLD and best_match:
            # Check for supplement markers — demote to related if new text
            # signals extension/supplement rather than true duplication
            new_summary_lower = new_lesson.get("summary", "").lower()
            existing_summary_lower = (best_match.get("summary") or "").lower()
            has_supplement_signal = any(
                marker in new_summary_lower and marker not in existing_summary_lower
                for marker in _SUPPLEMENT_MARKERS
            )
            if not has_supplement_signal:
                # Tier 1: exact duplicate — reject
                return {
                    "status": "duplicate",
                    "similarity": round(best_sim, 2),
                    "existing_id": best_match.get("id"),
                    "existing_summary": best_match.get("summary"),
                    "message": f"与现有教训相似度 {best_sim:.0%}，未重复添加",
                }
            # Supplement signal detected — fall through to related tier

        if best_sim >= SIMILARITY_THRESHOLD and best_match:
            # Tier 2: semantically related — add but link
            new_id = new_lesson.get("id", "")
            existing_id = best_match.get("id", "")
            # Guard against self-reference
            if existing_id and existing_id != new_id and existing_id not in new_lesson.get("related_ids", []):
                new_lesson.setdefault("related_ids", []).append(existing_id)
            if new_id and new_id != existing_id and new_id not in best_match.get("related_ids", []):
                best_match.setdefault("related_ids", []).append(new_id)
            new_lesson["_dedup_note"] = f"related to {existing_id} (sim={best_sim:.0%})"

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
        self._write_entries(path, lessons, "lesson")

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
        _migrate_fields: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson", migrate=_migrate_fields)
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
            self._write_entries(path, lessons, "lesson")
        self._audit.log("read", "knowledge/lessons", detail=f"returned {len(result)} items")
        return result

    def update_lesson(self, lesson_id: str, updates: dict) -> dict:
        """Update fields on a lesson entry.

        v3.31 P0-1: ``tier`` is now updatable so users can promote a staging
        lesson to ``verified`` or demote a stale ``verified`` lesson to
        ``archived`` in a single call, instead of the previous two-step
        ``archive_knowledge`` + ``add_lesson`` workaround. Tier change is
        recorded in the audit log so the transition is traceable.
        """
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")
        updates = self._repair_incoming_text(dict(updates))
        allowed_fields = {"summary", "detail", "domain", "status", "tier"}
        valid_tiers = {"staging", "verified", "archived"}
        for lesson in lessons:
            if lesson.get("id") == lesson_id:
                old_tier = lesson.get("tier")
                tier_changed = False
                new_tier = None
                for key, value in updates.items():
                    if key not in allowed_fields:
                        continue
                    if key == "tier":
                        if value not in valid_tiers:
                            return {
                                "error": (
                                    f"Invalid tier {value!r}; "
                                    f"must be one of {sorted(valid_tiers)}"
                                )
                            }
                        if value != old_tier:
                            tier_changed = True
                            new_tier = value
                    lesson[key] = value
                lesson["last_updated"] = _now_iso()
                lesson = self._ensure_fields(lesson, "lesson")
                self._write_entries(path, lessons, "lesson")
                if tier_changed:
                    self._audit.log(
                        "write",
                        "knowledge/tier_change",
                        detail=f"lesson {lesson_id}: {old_tier} -> {new_tier}",
                    )
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

        new_decision = self._repair_incoming_text(new_decision)
        # Sanitize project field regardless of input path (dict or kwargs)
        if new_decision.get("project"):
            new_decision["project"] = self._sanitize_project(new_decision["project"])

        new_decision["timestamp"] = new_decision.get("timestamp") or _now_iso()
        new_decision = self._ensure_fields(new_decision, "decision")

        new_title = self._entry_identity_text(new_decision, "decision")
        # Three-tier dedup for decisions.
        # >= (not strict >) so that when multiple entries share the same
        # similarity (e.g. same question text, sim=1.0), the LAST entry
        # (most recent by list position) wins. This is correct for both
        # dedup (compare against the latest) and auto-supersedes (chain
        # should target the most recent predecessor, not an older one).
        best_sim = 0.0
        best_match = None
        for existing in decisions:
            existing = self._ensure_fields(existing, "decision")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_title,
                self._entry_identity_text(existing, "decision"),
            )
            if sim >= best_sim:
                best_sim = sim
                best_match = existing

        # Track whether the new decision should auto-supersede the best match.
        # Set when same question + different choice (a decision revision).
        _auto_supersedes_target: str | None = None

        if best_sim >= SIMILARITY_DUPLICATE_THRESHOLD and best_match:
            # For decisions: different choice on same question = conflict, not duplicate
            new_choice = (new_decision.get("choice") or "").strip().lower()
            existing_choice = (best_match.get("choice") or "").strip().lower()
            choices_differ = new_choice and existing_choice and new_choice != existing_choice
            new_title_lower = new_title.lower()
            existing_title_lower = self._entry_identity_text(best_match, "decision").lower()
            has_supplement = any(
                m in new_title_lower and m not in existing_title_lower
                for m in _SUPPLEMENT_MARKERS
            )
            if not choices_differ and not has_supplement:
                return {
                    "status": "duplicate",
                    "similarity": round(best_sim, 2),
                    "existing_id": best_match.get("id"),
                    "existing_title": self._entry_identity_text(best_match, "decision"),
                    "message": f"与现有决策相似度 {best_sim:.0%}，未重复添加",
                }
            # Different choice or supplement — fall through to related tier.
            # Same question + different choice → the new decision supersedes the old.
            if choices_differ:
                _auto_supersedes_target = best_match.get("id")

        if best_sim >= SIMILARITY_THRESHOLD and best_match:
            new_id = new_decision.get("id", "")
            existing_id = best_match.get("id", "")
            # Guard against self-reference
            if existing_id and existing_id != new_id and existing_id not in new_decision.get("related_ids", []):
                new_decision.setdefault("related_ids", []).append(existing_id)
            if new_id and new_id != existing_id and new_id not in best_match.get("related_ids", []):
                best_match.setdefault("related_ids", []).append(new_id)
            new_decision["_dedup_note"] = f"related to {existing_id} (sim={best_sim:.0%})"

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
        self._write_entries(path, decisions, "decision")
        title = new_decision.get("question", "") or new_decision.get("title", "")
        self._audit.log("write", "knowledge/decisions", detail=title[:100])

        # Auto-supersedes: build a directed edge in the decision thread.
        # Priority: (1) explicit ``supersedes`` field in the input,
        #           (2) auto-detected same-question conflict (different choice).
        # Best-effort: a failed edge write must NEVER block the decision write.
        supersedes_id = new_decision.get("supersedes") or _auto_supersedes_target
        if supersedes_id:
            try:
                self.add_relation(
                    str(new_decision["id"]), "supersedes", str(supersedes_id)
                )
            except Exception:
                pass  # edge is advisory; the decision itself is the hard write

        return new_decision

    def get_decisions(
        self,
        limit: int | None = 30,
        source_tool: str | None = None,
        project: str | None = None,
        domain: str | None = None,
        _update_access: bool = True,
        _migrate_fields: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision", migrate=_migrate_fields)
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
            self._write_entries(path, decisions, "decision")
        self._audit.log("read", "knowledge/decisions", detail=f"returned {len(result)} items")
        return result

    def update_decision(self, decision_id: str, updates: dict) -> dict:
        """Update fields on a decision entry.

        v3.31 P0-1: ``tier`` is updatable; same validation + audit semantics
        as :meth:`update_lesson`.
        """
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")
        updates = self._repair_incoming_text(dict(updates))
        allowed_fields = {
            "title",
            "question",
            "choice",
            "reasoning",
            "alternatives",
            "status",
            "project",
            "source_tool",
            "tier",
        }
        valid_tiers = {"staging", "verified", "archived"}
        for decision in decisions:
            if decision.get("id") == decision_id:
                old_tier = decision.get("tier")
                tier_changed = False
                new_tier = None
                for key, value in updates.items():
                    if key not in allowed_fields:
                        continue
                    if key == "tier":
                        if value not in valid_tiers:
                            return {
                                "error": (
                                    f"Invalid tier {value!r}; "
                                    f"must be one of {sorted(valid_tiers)}"
                                )
                            }
                        if value != old_tier:
                            tier_changed = True
                            new_tier = value
                    decision[key] = value
                decision["last_updated"] = _now_iso()
                decision = self._ensure_fields(decision, "decision")
                self._write_entries(path, decisions, "decision")
                if tier_changed:
                    self._audit.log(
                        "write",
                        "knowledge/tier_change",
                        detail=f"decision {decision_id}: {old_tier} -> {new_tier}",
                    )
                return decision
        return {"error": f"Decision not found: {decision_id}"}

    def archive_decision(self, decision_id: str) -> dict:
        """Mark a decision as outdated without deleting it."""
        return self.update_decision(decision_id, {"status": "outdated"})

    # ------------------------------------------------------------------
    # Playbook CRUD — independent file-per-playbook storage
    # ------------------------------------------------------------------

    def _read_playbook_index(self) -> list[dict]:
        """Read the lightweight playbook index (with corpus decryption of title)."""
        data = _read_json(self._playbooks_dir / "_index.json")
        if not isinstance(data, list):
            return []
        if self._corpus_key:
            for entry in data:
                if "title" in entry and isinstance(entry["title"], str):
                    entry["title"] = self._crypto.corpus_decrypt(
                        entry["title"], self._corpus_key)
        return data

    def _write_playbook_index(self, entries: list[dict]) -> None:
        """Write the playbook index (with corpus encryption of title)."""
        if self._corpus_key:
            entries = [dict(e) for e in entries]
            for e in entries:
                if "title" in e and isinstance(e["title"], str):
                    e["title"] = self._crypto.corpus_encrypt(
                        e["title"], self._corpus_key)
        _write_json(self._playbooks_dir / "_index.json", entries)

    def _read_playbook_by_id(self, playbook_id: str) -> dict | None:
        """Read a single playbook file by ID (with corpus decryption)."""
        path = self._playbooks_dir / f"{playbook_id}.json"
        if not path.exists():
            return None
        pb = self._read_playbook_file(path) or None
        if pb:
            pb = self._ensure_playbook_fields(pb)
        return pb

    @staticmethod
    def _extract_parameters(playbook: dict) -> list[str]:
        """Extract ${variable} placeholders from playbook steps and description."""
        _PARAM_RE = re.compile(r"\$\{(\w+)\}")
        params: list[str] = []
        seen: set[str] = set()
        # Scan steps (handle both string and dict formats)
        for step in playbook.get("steps", []):
            if isinstance(step, str):
                texts = [step]
            else:
                texts = [step.get(f) or "" for f in ("action", "detail")]
            for text in texts:
                for m in _PARAM_RE.finditer(text):
                    name = m.group(1)
                    if name not in seen:
                        params.append(name)
                        seen.add(name)
        # Scan description and outcome
        for field in ("description", "outcome"):
            text = playbook.get(field) or ""
            for m in _PARAM_RE.finditer(text):
                name = m.group(1)
                if name not in seen:
                    params.append(name)
                    seen.add(name)
        return params

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        """Return a clean list of strings from a scalar or sequence."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = [value]
        return [str(item).strip() for item in values if str(item).strip()]

    def _normalize_playbook_scope(
        self,
        entry: dict,
        scope_type: str | None = None,
        project_folder: str | None = None,
        project_id: str | None = None,
        project_folders: list[str] | None = None,
        project_ids: list[str] | None = None,
    ) -> dict:
        """Return the canonical scope dict for a playbook.

        Legacy playbooks have no scope metadata; they read as global so older
        stores remain visible until the user runs a classification migration.
        """
        raw_scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}
        folder = (
            project_folder
            if project_folder is not None
            else entry.get("project_folder") or raw_scope.get("project_folder")
        )
        pid = project_id or entry.get("project_id") or raw_scope.get("project_id")
        raw_type = (
            scope_type
            or entry.get("scope_type")
            or raw_scope.get("type")
            or ("project" if folder or pid else "global")
        )
        raw_type = str(raw_type or "global").strip().lower()
        if raw_type == "shared":
            folders = (
                self._string_list(project_folders)
                or self._string_list(entry.get("project_folders"))
                or self._string_list(raw_scope.get("project_folders"))
            )
            ids = (
                self._string_list(project_ids)
                or self._string_list(entry.get("project_ids"))
                or self._string_list(raw_scope.get("project_ids"))
            )
            ordered_ids: list[str] = []
            folders_by_id: dict[str, str] = {}
            seen: set[str] = set()
            for shared_folder in folders:
                shared_id = _project_id(shared_folder)
                if not shared_id or shared_id in seen:
                    continue
                seen.add(shared_id)
                ordered_ids.append(shared_id)
                folders_by_id[shared_id] = shared_folder
            for shared_id in ids:
                if not shared_id or shared_id in seen:
                    continue
                seen.add(shared_id)
                ordered_ids.append(shared_id)
            if not ordered_ids:
                return {"type": "global"}
            scope = {"type": "shared", "project_ids": ordered_ids}
            ordered_folders = [
                folders_by_id[shared_id]
                for shared_id in ordered_ids
                if shared_id in folders_by_id
            ]
            if ordered_folders:
                scope["project_folders"] = ordered_folders
            return scope
        if raw_type != "project":
            return {"type": "global"}
        if not pid and folder:
            pid = _project_id(str(folder))
        if not pid:
            return {"type": "global"}
        scope = {"type": "project", "project_id": str(pid)}
        if folder:
            scope["project_folder"] = str(folder)
        return scope

    @staticmethod
    def _apply_playbook_scope(entry: dict, scope: dict) -> dict:
        """Mirror canonical scope fields onto a playbook or index entry."""
        entry["scope"] = dict(scope)
        entry["scope_type"] = scope.get("type", "global")
        if scope.get("type") == "project":
            entry["project_id"] = scope.get("project_id", "")
            if scope.get("project_folder"):
                entry["project_folder"] = scope["project_folder"]
            entry.pop("project_ids", None)
            entry.pop("project_folders", None)
        elif scope.get("type") == "shared":
            entry["project_id"] = ""
            entry.pop("project_folder", None)
            entry["project_ids"] = list(scope.get("project_ids") or [])
            if scope.get("project_folders"):
                entry["project_folders"] = list(scope.get("project_folders") or [])
            else:
                entry.pop("project_folders", None)
        else:
            entry["project_id"] = ""
            entry.pop("project_folder", None)
            entry.pop("project_ids", None)
            entry.pop("project_folders", None)
        return entry

    def _playbook_visible_for_project(
        self, playbook: dict, project_folder: str | None = None,
    ) -> bool:
        """Whether a playbook should be visible in a project context."""
        scope = self._normalize_playbook_scope(playbook)
        if scope.get("type") == "global":
            return True
        if not project_folder:
            return False
        if scope.get("type") == "shared":
            return _project_id(project_folder) in set(scope.get("project_ids") or [])
        return scope.get("project_id") == _project_id(project_folder)

    def _same_playbook_scope(self, left: dict, right: dict) -> bool:
        """Scope equality for duplicate detection."""
        l_scope = self._normalize_playbook_scope(left)
        r_scope = self._normalize_playbook_scope(right)
        if l_scope.get("type") != r_scope.get("type"):
            return False
        if l_scope.get("type") == "project":
            return l_scope.get("project_id") == r_scope.get("project_id")
        if l_scope.get("type") == "shared":
            return set(l_scope.get("project_ids") or []) == set(
                r_scope.get("project_ids") or []
            )
        return True

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
        scope = self._normalize_playbook_scope(entry)
        self._apply_playbook_scope(entry, scope)
        return entry

    def _playbook_index_entry(self, pb: dict) -> dict:
        """Extract lightweight index entry from a full playbook."""
        entry = {
            "id": pb.get("id", ""),
            "title": pb.get("title", ""),
            "triggers": pb.get("triggers", []),
            "domain": pb.get("domain", ""),
            "status": pb.get("status", "active"),
            "updated_at": pb.get("last_updated") or pb.get("created_at") or _now_iso(),
        }
        if pb.get("builtin_name"):
            entry["builtin_name"] = pb.get("builtin_name", "")
        return self._apply_playbook_scope(entry, self._normalize_playbook_scope(pb))

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

        new_pb = self._repair_incoming_text(new_pb)
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
            if not self._same_playbook_scope(new_pb, entry):
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
        self._write_playbook_file(pb_path, new_pb)

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

    @staticmethod
    def available_builtin_playbooks() -> list[str]:
        """Return built-in Playbook template names."""
        return sorted(_BUILTIN_PLAYBOOKS)

    def builtin_playbook_template(
        self,
        name: str,
        project_folder: str | None = None,
    ) -> dict:
        """Return a normalized built-in Playbook template without writing it."""
        key = str(name or "").strip().lower()
        if key not in _BUILTIN_PLAYBOOKS:
            return {
                "error": f"Unknown builtin playbook: {name}",
                "available": self.available_builtin_playbooks(),
            }
        template = deepcopy(_BUILTIN_PLAYBOOKS[key])
        scope_type = "project" if project_folder else "global"
        scope = self._normalize_playbook_scope(
            template, scope_type=scope_type, project_folder=project_folder,
        )
        self._apply_playbook_scope(template, scope)
        return self._ensure_playbook_fields(template)

    def _find_existing_builtin_playbook(self, template: dict) -> dict | None:
        builtin_name = str(template.get("builtin_name") or "").strip().lower()
        title = str(template.get("title") or "").strip().lower()
        for entry in self._read_playbook_index():
            if entry.get("status") != "active":
                continue
            entry_builtin_name = str(entry.get("builtin_name") or "").strip().lower()
            if builtin_name and entry_builtin_name and entry_builtin_name != builtin_name:
                continue
            if not entry_builtin_name:
                if str(entry.get("title") or "").strip().lower() != title:
                    continue
            if self._same_playbook_scope(template, entry):
                return entry
        return None

    def install_builtin_playbook(
        self,
        name: str,
        *,
        project_folder: str | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Install a built-in Playbook template with dry-run and idempotency.

        Built-ins are verified local templates, but installation still defaults
        to preview-only so setup/CLI callers never mutate stores by accident.
        """
        template = self.builtin_playbook_template(name, project_folder=project_folder)
        if "error" in template:
            return template

        existing = self._find_existing_builtin_playbook(template)
        if existing:
            return {
                "dry_run": bool(dry_run or not confirm),
                "status": "already_installed",
                "existing_id": existing.get("id", ""),
                "playbook": existing,
            }

        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "status": "would_install",
                "playbook": template,
            }

        installed = self.add_playbook(
            template,
            source_tool=str(template.get("source_tool") or "engram_builtin"),
        )
        if "error" in installed or installed.get("status") == "duplicate":
            return {
                "dry_run": False,
                "status": installed.get("status", "error"),
                "playbook": installed,
                "existing_id": installed.get("existing_id", ""),
            }
        return {
            "dry_run": False,
            "status": "installed",
            "playbook_id": installed.get("id", ""),
            "playbook": installed,
        }

    def get_playbooks(
        self,
        domain: str | None = None,
        limit: int | None = 20,
        project_folder: str | None = None,
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
            if pb and self._playbook_visible_for_project(pb, project_folder):
                result.append(pb)

        result = result[-limit:] if limit is not None else result

        if _update_access and result:
            now = _now_iso()
            for pb in result:
                pb["last_reviewed"] = now
                pb["access_count"] = pb.get("access_count", 0) + 1
                self._write_playbook_file(self._playbooks_dir / f"{pb['id']}.json", pb)

        self._audit.log("read", "playbooks", detail=f"returned {len(result)} items")
        return result

    def get_playbook(
        self,
        playbook_id: str,
        _update_access: bool = True,
        project_folder: str | None = None,
        confirm_cross_project: bool = False,
    ) -> dict:
        """Get a single playbook by ID. Includes extracted parameters list.

        When a project_folder is supplied, project-scoped playbooks from other
        projects are refused unless confirm_cross_project=True. Calls without a
        project context preserve the legacy direct-ID behavior.
        """
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}
        if project_folder is not None and not self._playbook_visible_for_project(pb, project_folder):
            if not confirm_cross_project:
                return {
                    "error": "cross_project_playbook",
                    "playbook_id": playbook_id,
                    "scope": pb.get("scope", {"type": "global"}),
                    "project_folder": project_folder,
                    "message": "Playbook belongs to another project; pass confirm_cross_project=True to use it explicitly.",
                }

        if _update_access:
            pb["last_reviewed"] = _now_iso()
            pb["access_count"] = pb.get("access_count", 0) + 1
            self._write_playbook_file(self._playbooks_dir / f"{playbook_id}.json", pb)

        # Always include dynamic parameters extraction
        pb["parameters"] = self._extract_parameters(pb)
        return pb

    def get_recent_playbooks(
        self, limit: int = 5, project_folder: str | None = None,
    ) -> list[dict]:
        """Return recently used active playbooks, sorted by last_reviewed descending."""
        all_pbs = self._export_playbooks()
        active = [
            pb for pb in all_pbs
            if pb.get("status") == "active"
            and self._playbook_visible_for_project(pb, project_folder)
        ]
        active.sort(key=lambda pb: pb.get("last_reviewed", ""), reverse=True)
        result = active[:limit]
        for pb in result:
            pb["parameters"] = self._extract_parameters(pb)
        return result

    @staticmethod
    def _playbook_text_for_classification(pb: dict) -> str:
        parts: list[str] = [
            str(pb.get("title", "")),
            str(pb.get("domain", "")),
            str(pb.get("description", "")),
            " ".join(str(t) for t in pb.get("triggers", []) if t),
            " ".join(str(p) for p in pb.get("pitfalls", []) if p),
        ]
        for step in pb.get("steps", []):
            if isinstance(step, str):
                parts.append(step)
            elif isinstance(step, dict):
                parts.append(str(step.get("action", "")))
                parts.append(str(step.get("detail", "")))
        return " ".join(parts).lower()

    def classify_legacy_playbooks(
        self,
        project_folders: list[str] | None = None,
    ) -> dict:
        """Dry-run legacy playbook scope classification.

        This intentionally does not mutate stored playbooks. It produces a
        reviewable migration plan with confidence and evidence so old users can
        batch-apply only the high-confidence items later.
        """
        projects: list[dict] = []
        if project_folders is not None:
            for folder in project_folders:
                projects.append({
                    "folder": str(folder),
                    "title": self._sanitize_project(str(folder)),
                })
        else:
            projects = self.list_projects()

        project_terms: list[dict] = []
        for project in projects:
            folder = str(project.get("folder") or project.get("project_folder") or "")
            title = str(project.get("title") or "")
            terms = {term.strip().lower() for term in [title, Path(folder).name] if term}
            terms = {term for term in terms if len(term) >= 3}
            if folder and terms:
                project_terms.append({
                    "folder": folder,
                    "project_id": _project_id(folder),
                    "terms": sorted(terms),
                })

        global_markers = {
            "global", "universal", "common", "general", "shared",
            "cross-project", "通用", "共通", "全局",
        }

        suggestions = []
        for pb in self._export_playbooks():
            if pb.get("status") != "active":
                continue
            text = self._playbook_text_for_classification(pb)
            project_matches: list[dict] = []
            for project in project_terms:
                evidence = [term for term in project["terms"] if term in text]
                if evidence:
                    project_matches.append({
                        "folder": project["folder"],
                        "project_id": project["project_id"],
                        "evidence": evidence,
                    })

            project_matches.sort(key=lambda match: len(match["evidence"]), reverse=True)
            if (
                len(project_matches) >= 2
                and len(project_matches[0]["evidence"]) == len(project_matches[1]["evidence"])
            ):
                top_count = len(project_matches[0]["evidence"])
                shared_matches = [
                    match for match in project_matches
                    if len(match["evidence"]) == top_count
                ]
                confidence = min(
                    0.95,
                    0.7 + 0.05 * len(shared_matches)
                    + 0.03 * sum(len(match["evidence"]) for match in shared_matches),
                )
                suggested_scope = {
                    "type": "shared",
                    "project_ids": [match["project_id"] for match in shared_matches],
                    "project_folders": [match["folder"] for match in shared_matches],
                }
                evidence = [
                    f"matched project term: {term}"
                    for match in shared_matches
                    for term in match["evidence"]
                ]
            elif project_matches:
                match = project_matches[0]
                confidence = min(0.95, 0.65 + 0.1 * len(match["evidence"]))
                suggested_scope = {
                    "type": "project",
                    "project_id": match["project_id"],
                    "project_folder": match["folder"],
                }
                evidence = [f"matched project term: {term}" for term in match["evidence"]]
            elif any(marker in text for marker in global_markers):
                confidence = 0.75
                suggested_scope = {"type": "global"}
                evidence = ["matched global/common marker"]
            else:
                confidence = 0.35
                suggested_scope = {"type": "needs_review"}
                evidence = ["no strong project or global evidence"]

            suggestions.append({
                "id": pb.get("id", ""),
                "title": pb.get("title", ""),
                "current_scope": pb.get("scope", {"type": "global"}),
                "suggested_scope": suggested_scope,
                "confidence": round(confidence, 2),
                "evidence": evidence,
                "apply_ready": confidence >= 0.7 and suggested_scope.get("type") != "needs_review",
            })

        return {
            "dry_run": True,
            "total": len(suggestions),
            "suggestions": suggestions,
        }

    def _write_playbook_and_index(self, pb: dict) -> None:
        """Persist a playbook and keep the lightweight index in sync."""
        playbook_id = str(pb.get("id") or "")
        if not playbook_id:
            raise ValueError("missing playbook id")
        self._write_playbook_file(self._playbooks_dir / f"{playbook_id}.json", pb)

        index = self._read_playbook_index()
        idx_entry = self._playbook_index_entry(pb)
        for i, entry in enumerate(index):
            if entry.get("id") == playbook_id:
                index[i] = idx_entry
                break
        else:
            index.append(idx_entry)
        self._write_playbook_index(index)

    @staticmethod
    def _scope_impact_summary(
        *,
        pending_key: str,
        completed_key: str,
        pending: list[dict],
        completed: list[dict],
        skipped: list[dict],
        requires_confirmation: bool,
    ) -> dict:
        """Return metadata-only batch impact counts for management surfaces."""
        target_scope_counts: dict[str, int] = {}
        for change in [*pending, *completed]:
            scope = change.get("to_scope")
            if not isinstance(scope, dict):
                continue
            scope_type = str(scope.get("type") or "unknown")
            target_scope_counts[scope_type] = target_scope_counts.get(scope_type, 0) + 1

        skipped_reason_counts: dict[str, int] = {}
        for item in skipped:
            reason = str(item.get("reason") or "unknown")
            skipped_reason_counts[reason] = skipped_reason_counts.get(reason, 0) + 1

        return {
            pending_key: len(pending),
            completed_key: len(completed),
            "skipped_count": len(skipped),
            "target_scope_counts": target_scope_counts,
            "skipped_reason_counts": skipped_reason_counts,
            "requires_confirmation": bool(requires_confirmation),
        }

    def apply_legacy_playbook_scope_suggestions(
        self,
        project_folders: list[str] | None = None,
        playbook_ids: list[str] | None = None,
        min_confidence: float = 0.7,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Apply high-confidence legacy Playbook scope suggestions.

        The default is a write-free preview. Actual migration requires
        ``dry_run=False`` and ``confirm=True`` so old-user data is never
        silently reorganized.
        """
        effective_dry_run = bool(dry_run or not confirm)
        selected_ids = set(playbook_ids or [])
        classification = self.classify_legacy_playbooks(project_folders=project_folders)
        now = _now_iso()
        would_apply: list[dict] = []
        applied: list[dict] = []
        skipped: list[dict] = []

        for suggestion in classification.get("suggestions", []):
            playbook_id = str(suggestion.get("id") or "")
            if selected_ids and playbook_id not in selected_ids:
                continue

            if (
                not suggestion.get("apply_ready")
                or float(suggestion.get("confidence") or 0) < min_confidence
            ):
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "not_apply_ready",
                    "suggested_scope": suggestion.get("suggested_scope"),
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            pb = self._read_playbook_by_id(playbook_id)
            if pb is None:
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "not_found",
                })
                continue

            current_scope = self._normalize_playbook_scope(pb)
            if current_scope.get("type") != "global":
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "already_scoped",
                    "current_scope": current_scope,
                    "suggested_scope": suggestion.get("suggested_scope"),
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            target_scope = self._normalize_playbook_scope(
                {"scope": suggestion.get("suggested_scope") or {}}
            )
            if target_scope.get("type") not in {"global", "project", "shared"}:
                skipped.append({
                    "id": playbook_id,
                    "title": suggestion.get("title", ""),
                    "reason": "invalid_scope",
                    "suggested_scope": suggestion.get("suggested_scope"),
                })
                continue

            if current_scope == target_scope:
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "unchanged",
                    "suggested_scope": target_scope,
                    "confidence": suggestion.get("confidence", 0),
                })
                continue

            change = {
                "id": playbook_id,
                "title": pb.get("title", suggestion.get("title", "")),
                "from_scope": current_scope,
                "to_scope": target_scope,
                "confidence": suggestion.get("confidence", 0),
                "evidence": suggestion.get("evidence", []),
            }
            if effective_dry_run:
                would_apply.append(change)
                continue

            history = list(pb.get("scope_migration_history") or [])
            history.append({
                "timestamp": now,
                "from_scope": current_scope,
                "to_scope": target_scope,
                "confidence": suggestion.get("confidence", 0),
                "evidence": suggestion.get("evidence", []),
                "reason": "legacy_playbook_scope_classification",
            })
            self._apply_playbook_scope(pb, target_scope)
            pb["scope_migration_history"] = history
            pb["last_updated"] = now
            pb["version"] = pb.get("version", 1) + 1
            self._write_playbook_and_index(pb)
            applied.append(change)

        if applied:
            self._audit.log(
                "write", "playbooks",
                detail=f"applied scope migration to {len(applied)} playbooks",
            )

        return {
            "dry_run": effective_dry_run,
            "requires_confirmation": not confirm,
            "total": classification.get("total", 0),
            "would_apply": would_apply,
            "applied": applied,
            "skipped": skipped,
            "impact": self._scope_impact_summary(
                pending_key="would_apply_count",
                completed_key="applied_count",
                pending=would_apply,
                completed=applied,
                skipped=skipped,
                requires_confirmation=not confirm,
            ),
        }

    def rollback_playbook_scope_migration(
        self,
        playbook_ids: list[str] | None = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Rollback the latest scope migration for selected Playbooks."""
        effective_dry_run = bool(dry_run or not confirm)
        selected_ids = set(playbook_ids or [])
        candidates = self._export_playbooks()
        if selected_ids:
            seen = {pb.get("id") for pb in candidates}
            for missing_id in sorted(selected_ids - seen):
                candidates.append({"id": missing_id, "_missing": True})

        would_rollback: list[dict] = []
        rolled_back: list[dict] = []
        skipped: list[dict] = []
        now = _now_iso()

        for pb in candidates:
            playbook_id = str(pb.get("id") or "")
            if selected_ids and playbook_id not in selected_ids:
                continue
            if pb.get("_missing"):
                skipped.append({"id": playbook_id, "reason": "not_found"})
                continue

            history = list(pb.get("scope_migration_history") or [])
            if not history:
                skipped.append({
                    "id": playbook_id,
                    "title": pb.get("title", ""),
                    "reason": "no_migration_history",
                })
                continue

            last = history[-1]
            target_scope = self._normalize_playbook_scope(
                {"scope": last.get("from_scope") or {}}
            )
            current_scope = self._normalize_playbook_scope(pb)
            change = {
                "id": playbook_id,
                "title": pb.get("title", ""),
                "from_scope": current_scope,
                "to_scope": target_scope,
                "rolled_back_migration": last,
            }
            if effective_dry_run:
                would_rollback.append(change)
                continue

            history.pop()
            self._apply_playbook_scope(pb, target_scope)
            pb["scope_migration_history"] = history
            pb["last_updated"] = now
            pb["version"] = pb.get("version", 1) + 1
            self._write_playbook_and_index(pb)
            rolled_back.append(change)

        if rolled_back:
            self._audit.log(
                "write", "playbooks",
                detail=f"rolled back scope migration for {len(rolled_back)} playbooks",
            )

        return {
            "dry_run": effective_dry_run,
            "requires_confirmation": not confirm,
            "would_rollback": would_rollback,
            "rolled_back": rolled_back,
            "skipped": skipped,
            "impact": self._scope_impact_summary(
                pending_key="would_rollback_count",
                completed_key="rolled_back_count",
                pending=would_rollback,
                completed=rolled_back,
                skipped=skipped,
                requires_confirmation=not confirm,
            ),
        }

    def get_playbook_scope_review_queue(
        self,
        project_folders: list[str] | None = None,
        include_resolved: bool = False,
        limit: int | None = None,
    ) -> dict:
        """Return unresolved legacy Playbooks that need manual scope review."""
        classification = self.classify_legacy_playbooks(project_folders=project_folders)
        items: list[dict] = []
        for suggestion in classification.get("suggestions", []):
            playbook_id = str(suggestion.get("id") or "")
            pb = self._read_playbook_by_id(playbook_id)
            if pb is None or pb.get("status") != "active":
                continue
            current_scope = self._normalize_playbook_scope(pb)
            if current_scope.get("type") != "global":
                continue
            review_status = str(pb.get("scope_review_status") or "unresolved")
            if review_status in {"resolved", "skipped"} and not include_resolved:
                continue
            if (
                suggestion.get("suggested_scope", {}).get("type") != "needs_review"
                and not include_resolved
            ):
                continue
            item = dict(suggestion)
            item["scope_review_status"] = review_status
            item["scope_review_history"] = list(pb.get("scope_review_history") or [])
            items.append(item)

        if limit is not None:
            items = items[:limit]
        return {
            "dry_run": True,
            "total": len(items),
            "items": items,
        }

    def resolve_playbook_scope_review(
        self,
        playbook_id: str,
        action: str,
        project_folder: str | None = None,
        project_folders: list[str] | None = None,
        note: str = "",
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Resolve one Playbook scope review item by keeping, assigning, or skipping."""
        action = str(action or "").strip().lower()
        if action not in {"accept_global", "accept_project", "accept_shared", "skip"}:
            return {
                "error": "invalid_action",
                "allowed_actions": [
                    "accept_global",
                    "accept_project",
                    "accept_shared",
                    "skip",
                ],
            }
        if action == "accept_project" and not project_folder:
            return {"error": "project_folder_required"}
        if action == "accept_shared" and not project_folders:
            return {"error": "project_folders_required"}

        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_scope = self._normalize_playbook_scope(pb)
        if action == "accept_project":
            target_scope = self._normalize_playbook_scope(
                {}, scope_type="project", project_folder=project_folder,
            )
        elif action == "accept_shared":
            target_scope = self._normalize_playbook_scope(
                {}, scope_type="shared", project_folders=project_folders,
            )
        elif action == "accept_global":
            target_scope = self._normalize_playbook_scope({}, scope_type="global")
        else:
            target_scope = current_scope

        change = {
            "id": playbook_id,
            "title": pb.get("title", ""),
            "action": action,
            "from_scope": current_scope,
            "to_scope": target_scope,
            "note": note,
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_update": change,
            }

        now = _now_iso()
        history = list(pb.get("scope_review_history") or [])
        history.append({
            "timestamp": now,
            "action": action,
            "from_scope": current_scope,
            "to_scope": target_scope,
            "note": note,
            "reason": "manual_playbook_scope_review",
        })
        if action != "skip":
            self._apply_playbook_scope(pb, target_scope)
        pb["scope_review_status"] = "skipped" if action == "skip" else "resolved"
        pb["scope_review_resolution"] = action
        pb["scope_review_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log(
            "write", "playbooks",
            detail=f"resolved scope review for {playbook_id}: {action}",
        )
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "updated": change,
        }

    def update_playbook(self, playbook_id: str, updates: dict) -> dict:
        """Update fields on a playbook entry."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        updates = self._repair_incoming_text(dict(updates))
        for key, value in updates.items():
            if key in _ALLOWED_PLAYBOOK_UPDATE_FIELDS:
                pb[key] = value
        pb["last_updated"] = _now_iso()
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_file(self._playbooks_dir / f"{playbook_id}.json", pb)

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

    @staticmethod
    def _normalize_playbook_status_filter(status: str | None) -> str:
        value = str(status or "all").strip().lower()
        aliases = {
            "archived": "outdated",
            "archive": "outdated",
            "hidden": "deleted",
            "trash": "deleted",
        }
        return aliases.get(value, value)

    def _playbook_management_entry(self, pb: dict, include_content: bool = False) -> dict:
        """Return a Playbook entry suitable for management views."""
        if include_content:
            return dict(pb)
        scope = self._normalize_playbook_scope(pb)
        scope_type = str(scope.get("type") or "global")
        if scope_type == "shared":
            project_count = len(scope.get("project_ids") or [])
        elif scope_type == "project":
            project_count = 1
        else:
            project_count = 0
        public_scope = dict(scope)
        public_scope.pop("project_folder", None)
        public_scope.pop("project_folders", None)
        return {
            "id": pb.get("id", ""),
            "status": pb.get("status", "active"),
            "scope": public_scope,
            "scope_type": scope_type,
            "project_count": project_count,
            "scope_review_status": pb.get("scope_review_status", ""),
            "scope_review_resolution": pb.get("scope_review_resolution", ""),
            "created_at": pb.get("created_at", ""),
            "last_updated": pb.get("last_updated", ""),
            "last_reviewed": pb.get("last_reviewed", ""),
            "version": pb.get("version", 1),
            "deleted_at": pb.get("deleted_at", ""),
        }

    def list_playbooks_for_management(
        self,
        status: str = "all",
        project_folder: str | None = None,
        scope_type: str = "all",
        include_content: bool = False,
        limit: int | None = None,
    ) -> dict:
        """List Playbooks for management UI/API surfaces, including hidden items."""
        status_filter = self._normalize_playbook_status_filter(status)
        scope_filter = str(scope_type or "all").strip().lower()
        valid_statuses = {"all", "active", "outdated", "staging", "deleted"}
        valid_scopes = {"all", "global", "project", "shared"}
        if status_filter not in valid_statuses:
            return {"error": f"Invalid status {status!r}; must be one of {sorted(valid_statuses)}"}
        if scope_filter not in valid_scopes:
            return {"error": f"Invalid scope_type {scope_type!r}; must be one of {sorted(valid_scopes)}"}
        if limit is not None and limit < 0:
            return {"error": "limit_must_be_positive"}

        items: list[dict] = []
        for pb in self._export_playbooks():
            pb_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
            if status_filter != "all" and pb_status != status_filter:
                continue
            scope = self._normalize_playbook_scope(pb)
            if scope_filter != "all" and scope.get("type") != scope_filter:
                continue
            if project_folder and not self._playbook_visible_for_project(pb, project_folder):
                continue
            items.append(self._playbook_management_entry(pb, include_content=include_content))

        items.sort(
            key=lambda item: (
                item.get("last_updated")
                or item.get("last_reviewed")
                or item.get("created_at")
                or ""
            ),
            reverse=True,
        )
        if limit is not None:
            items = items[:limit]
        self._audit.log("read", "playbooks", detail=f"management list returned {len(items)} items")
        return {
            "total": len(items),
            "status": status_filter,
            "scope_type": scope_filter,
            "items": items,
        }

    def delete_playbook(
        self,
        playbook_id: str,
        reason: str = "",
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Soft-delete a Playbook so it is hidden but recoverable."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
        if current_status == "deleted":
            return {"error": "playbook_already_deleted", "playbook_id": playbook_id}
        change = {
            "id": playbook_id,
            "from_status": current_status,
            "to_status": "deleted",
            "soft_delete": True,
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_delete": change,
            }

        now = _now_iso()
        history = list(pb.get("deletion_history") or [])
        history.append({
            "timestamp": now,
            "action": "delete",
            "from_status": current_status,
            "to_status": "deleted",
            "reason": reason,
        })
        pb["status"] = "deleted"
        pb["deleted_at"] = now
        pb["deletion_reason"] = reason
        pb["deletion_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log("write", "playbooks", detail=f"soft-deleted {playbook_id}")
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "deleted": change,
        }

    def restore_playbook(
        self,
        playbook_id: str,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        """Restore a deleted/outdated Playbook to active status."""
        pb = self._read_playbook_by_id(playbook_id)
        if pb is None:
            return {"error": f"Playbook not found: {playbook_id}"}

        current_status = self._normalize_playbook_status_filter(pb.get("status", "active"))
        if current_status == "active":
            return {"error": "playbook_already_active", "playbook_id": playbook_id}
        change = {
            "id": playbook_id,
            "from_status": current_status,
            "to_status": "active",
        }
        effective_dry_run = bool(dry_run or not confirm)
        if effective_dry_run:
            return {
                "dry_run": True,
                "requires_confirmation": not confirm,
                "would_restore": change,
            }

        now = _now_iso()
        history = list(pb.get("deletion_history") or [])
        history.append({
            "timestamp": now,
            "action": "restore",
            "from_status": current_status,
            "to_status": "active",
        })
        pb["status"] = "active"
        pb["restored_at"] = now
        pb["deletion_history"] = history
        pb["last_updated"] = now
        pb["version"] = pb.get("version", 1) + 1
        self._write_playbook_and_index(pb)
        self._audit.log("write", "playbooks", detail=f"restored {playbook_id}")
        return {
            "dry_run": False,
            "requires_confirmation": False,
            "restored": change,
        }

    def merge_playbooks(self, target_id: str, source: dict) -> dict:
        """Merge steps and pitfalls from *source* dict into existing playbook *target_id*.

        Steps are de-duplicated by action text similarity. Pitfalls and triggers
        are union-merged. The target playbook is updated in-place.
        """
        target = self._read_playbook_by_id(target_id)
        if target is None:
            return {"error": f"Playbook not found: {target_id}"}

        # Merge steps (de-dup by similarity, handle both string and dict formats)
        def _step_action(s: Any) -> str:
            return s if isinstance(s, str) else s.get("action", "")

        def _step_order(s: Any) -> int:
            return s.get("order", 0) if isinstance(s, dict) else 0

        existing_actions = {_step_action(s): True for s in target.get("steps", [])}
        next_order = max((_step_order(s) for s in target.get("steps", [])), default=0)
        merged_steps = list(target.get("steps", []))
        for s in source.get("steps", []):
            action = _step_action(s)
            # Skip if already exists (exact or highly similar)
            is_dup = False
            for existing_action in existing_actions:
                if self._bigram_similarity(action, existing_action) >= 0.6:
                    is_dup = True
                    break
            if not is_dup and action:
                next_order += 1
                merged_steps.append({"order": next_order, "action": action})
                existing_actions[action] = True

        # Merge pitfalls (union)
        existing_pitfalls = set(target.get("pitfalls", []))
        merged_pitfalls = list(target.get("pitfalls", []))
        for p in source.get("pitfalls", []):
            if p not in existing_pitfalls:
                merged_pitfalls.append(p)
                existing_pitfalls.add(p)

        # Merge triggers (union)
        existing_triggers = set(target.get("triggers", []))
        merged_triggers = list(target.get("triggers", []))
        for t in source.get("triggers", []):
            if t not in existing_triggers:
                merged_triggers.append(t)
                existing_triggers.add(t)

        updates = {
            "steps": merged_steps,
            "pitfalls": merged_pitfalls,
            "triggers": merged_triggers,
        }
        result = self.update_playbook(target_id, updates)
        result["merged"] = True
        return result

    def prepare_playbook_execution(
        self,
        playbook_id: str,
        params: dict[str, str] | None = None,
        project_folder: str | None = None,
        confirm_cross_project: bool = False,
    ) -> dict:
        """Prepare a playbook for guided execution with parameter substitution.

        Returns a copy of the playbook with ``${variable}`` placeholders
        replaced by values from *params*, plus per-step status tracking fields.
        Does NOT auto-execute — the AI tool should walk through steps one by one.

        Args:
            playbook_id: ID of the playbook to prepare.
            params: ``{variable_name: value}`` for ``${variable}`` substitution.

        Returns:
            ``{playbook_id, title, execution_plan: [{order, action, status}], parameters_used}``
        """
        pb = self.get_playbook(
            playbook_id,
            _update_access=True,
            project_folder=project_folder,
            confirm_cross_project=confirm_cross_project,
        )
        if pb.get("error"):
            return pb

        params = params or {}

        # Substitute parameters in steps (handle both string and dict formats)
        execution_plan = []
        for i, step in enumerate(pb.get("steps", []), 1):
            if isinstance(step, str):
                action, detail = step, ""
            else:
                action = step.get("action", "")
                detail = step.get("detail", "")
            for var_name, var_value in params.items():
                action = action.replace(f"${{{var_name}}}", var_value)
                detail = detail.replace(f"${{{var_name}}}", var_value)
            execution_plan.append({
                "order": step.get("order", i) if isinstance(step, dict) else i,
                "action": action,
                "detail": detail,
                "status": "pending",
            })

        result = {
            "playbook_id": playbook_id,
            "title": pb.get("title", ""),
            "execution_plan": execution_plan,
            "parameters_used": params,
            "pitfalls": pb.get("pitfalls", []),
            "preconditions": pb.get("preconditions", []),
            "scope": pb.get("scope", {"type": "global"}),
        }
        if confirm_cross_project and project_folder and not self._playbook_visible_for_project(pb, project_folder):
            result["cross_project_confirmed"] = True
            result["requested_project_folder"] = project_folder
        self.save_execution_plan(result)
        return result

    # ------------------------------------------------------------------
    # Playbook execution tracking
    # ------------------------------------------------------------------

    def _executions_dir(self) -> Path:
        d = self.root / "playbooks" / "executions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _execution_path(self, playbook_id: str) -> Path:
        return self._executions_dir() / f"{playbook_id}.json"

    def _encrypt_execution_plan(self, plan: dict) -> dict:
        """Encrypt sensitive fields in an execution plan for at-rest storage."""
        if not self._corpus_key:
            return plan
        result = dict(plan)
        # Encrypt title (derived from playbook)
        if "title" in result and isinstance(result["title"], str):
            result["title"] = self._crypto.corpus_encrypt(result["title"], self._corpus_key)
        # Encrypt step action/detail and list fields
        for list_field in ("execution_plan", "pitfalls", "preconditions"):
            if list_field not in result or not isinstance(result[list_field], list):
                continue
            encrypted_items = []
            for item in result[list_field]:
                if isinstance(item, dict):
                    d = dict(item)
                    for k in ("action", "detail", "notes"):
                        if k in d and isinstance(d[k], str) and d[k]:
                            d[k] = self._crypto.corpus_encrypt(d[k], self._corpus_key)
                    encrypted_items.append(d)
                elif isinstance(item, str):
                    encrypted_items.append(self._crypto.corpus_encrypt(item, self._corpus_key))
                else:
                    encrypted_items.append(item)
            result[list_field] = encrypted_items
        return result

    def _decrypt_execution_plan(self, plan: dict) -> dict:
        """Decrypt sensitive fields in an execution plan for in-memory use."""
        if not self._corpus_key or not isinstance(plan, dict):
            return plan
        result = dict(plan)
        if "title" in result and isinstance(result["title"], str):
            result["title"] = self._crypto.corpus_decrypt(result["title"], self._corpus_key)
        for list_field in ("execution_plan", "pitfalls", "preconditions"):
            if list_field not in result or not isinstance(result[list_field], list):
                continue
            decrypted_items = []
            for item in result[list_field]:
                if isinstance(item, dict):
                    d = dict(item)
                    for k in ("action", "detail", "notes"):
                        if k in d and isinstance(d[k], str) and d[k]:
                            d[k] = self._crypto.corpus_decrypt(d[k], self._corpus_key)
                    decrypted_items.append(d)
                elif isinstance(item, str):
                    decrypted_items.append(self._crypto.corpus_decrypt(item, self._corpus_key))
                else:
                    decrypted_items.append(item)
            result[list_field] = decrypted_items
        return result

    def save_execution_plan(self, plan: dict) -> dict:
        """Persist an execution plan returned by prepare_playbook_execution."""
        pid = plan.get("playbook_id", "")
        if not pid:
            return {"error": "missing playbook_id"}
        plan["started_at"] = _now_iso()
        plan["updated_at"] = _now_iso()
        _write_json(self._execution_path(pid), self._encrypt_execution_plan(plan))
        return {"status": "saved", "playbook_id": pid}

    def update_execution_step(
        self,
        playbook_id: str,
        step_order: int,
        status: str,
        notes: str = "",
    ) -> dict:
        """Update the status of a step in a saved execution plan.

        Args:
            playbook_id: ID of the playbook being executed.
            step_order: The ``order`` number of the step to update.
            status: One of ``"completed"``, ``"skipped"``, ``"failed"``.
            notes: Optional note (e.g. error message for failed steps).

        Returns:
            ``{status, step_order, playbook_id, completed, total}``
        """
        valid = {"completed", "skipped", "failed"}
        if status not in valid:
            return {"error": f"status must be one of {valid}"}

        path = self._execution_path(playbook_id)
        plan = _read_json(path)
        if not plan:
            return {"error": f"no execution plan found for {playbook_id}"}
        # Decrypt the at-rest plan before mutating it, then re-encrypt on
        # write-back. Operating on the raw (encrypted) plan and assigning
        # plaintext ``notes`` directly would leak the note in cleartext, since
        # the surrounding ciphertext fields are never re-encrypted on this path
        # (Codex a5 round-2 P1-4).
        plan = self._decrypt_execution_plan(plan)

        updated = False
        for step in plan.get("execution_plan", []):
            if step.get("order") == step_order:
                step["status"] = status
                if notes:
                    step["notes"] = notes
                step["updated_at"] = _now_iso()
                updated = True
                break

        if not updated:
            return {"error": f"step {step_order} not found in execution plan"}

        plan["updated_at"] = _now_iso()

        steps = plan.get("execution_plan", [])
        completed = sum(1 for s in steps if s.get("status") in ("completed", "skipped"))
        total = len(steps)
        if completed == total:
            plan["completed_at"] = _now_iso()

        _write_json(path, self._encrypt_execution_plan(plan))
        return {
            "status": "updated",
            "step_order": step_order,
            "step_status": status,
            "playbook_id": playbook_id,
            "completed": completed,
            "total": total,
        }

    def get_execution_status(self, playbook_id: str) -> dict:
        """Return the current execution state for a playbook."""
        plan = _read_json(self._execution_path(playbook_id))
        if not plan:
            return {"error": f"no execution plan found for {playbook_id}"}
        plan = self._decrypt_execution_plan(plan)
        steps = plan.get("execution_plan", [])
        return {
            "playbook_id": playbook_id,
            "title": plan.get("title", ""),
            "started_at": plan.get("started_at"),
            "completed_at": plan.get("completed_at"),
            "steps": steps,
            "completed": sum(1 for s in steps if s.get("status") in ("completed", "skipped")),
            "total": len(steps),
        }

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

    def soft_archive_knowledge_tier(
        self,
        item_id: str,
        *,
        now: str | None = None,
    ) -> dict:
        """Reversibly soft-archive a lesson/decision into the ``archived`` tier.

        This is the tier-transition primitive behind the owner-confirmed
        lifecycle archive apply path. It sets ``tier="archived"`` plus an
        ``archived_at`` timestamp and records the prior tier in
        ``archived_from_tier`` so the move can be undone. It never deletes and
        never changes ``status``.

        Fail-closed protections:
        - a ``verified`` entry is refused (``error="protected_verified"``);
        - an entry already in the ``archived`` tier is an idempotent no-op
          (``changed=False``), leaving its existing ``archived_at`` intact.

        Returns a metadata-only dict ``{id, type, changed, from_tier, to_tier,
        archived_at}`` (no bodies), or ``{"error": ...}`` if not found.
        """
        ts = now or _now_iso()
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            path = self._knowledge_dir / fname
            entries = self._read_entries(path, kind)
            for entry in entries:
                if entry.get("id") != item_id:
                    continue
                current = entry.get("tier") if isinstance(entry.get("tier"), str) else ""
                if current == "verified":
                    return {
                        "id": item_id, "type": kind, "changed": False,
                        "from_tier": current, "to_tier": current,
                        "error": "protected_verified",
                    }
                if current == "archived":
                    return {
                        "id": item_id, "type": kind, "changed": False,
                        "from_tier": "archived", "to_tier": "archived",
                        "archived_at": entry.get("archived_at"),
                    }
                entry["archived_from_tier"] = current
                entry["tier"] = "archived"
                entry["archived_at"] = ts
                entry["last_updated"] = ts
                self._ensure_fields(entry, kind)
                self._write_entries(path, entries, kind)
                self._audit.log(
                    "write",
                    "knowledge/lifecycle_archive",
                    detail=f"{kind} {item_id}: {current or 'none'} -> archived",
                )
                return {
                    "id": item_id, "type": kind, "changed": True,
                    "from_tier": current, "to_tier": "archived",
                    "archived_at": ts,
                }
        return {"error": f"Item not found: {item_id}"}

    def restore_lifecycle_archive(
        self,
        item_id: str,
        *,
        now: str | None = None,
    ) -> dict:
        """Undo a lifecycle soft archive: move an ``archived`` entry back.

        Restores the entry to its recorded prior tier (``archived_from_tier``,
        falling back to ``staging``) and clears the archive markers. A
        non-archived entry is an idempotent no-op (``changed=False``). Returns a
        metadata-only dict, or ``{"error": ...}`` if not found.
        """
        ts = now or _now_iso()
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            path = self._knowledge_dir / fname
            entries = self._read_entries(path, kind)
            for entry in entries:
                if entry.get("id") != item_id:
                    continue
                current = entry.get("tier") if isinstance(entry.get("tier"), str) else ""
                if current != "archived":
                    return {
                        "id": item_id, "type": kind, "changed": False,
                        "from_tier": current, "to_tier": current,
                    }
                prior = entry.get("archived_from_tier")
                to_tier = prior if prior in {"staging", "verified"} else "staging"
                entry["tier"] = to_tier
                entry.pop("archived_at", None)
                entry.pop("archived_from_tier", None)
                entry["last_updated"] = ts
                self._ensure_fields(entry, kind)
                self._write_entries(path, entries, kind)
                self._audit.log(
                    "write",
                    "knowledge/lifecycle_restore",
                    detail=f"{kind} {item_id}: archived -> {to_tier}",
                )
                return {
                    "id": item_id, "type": kind, "changed": True,
                    "from_tier": "archived", "to_tier": to_tier,
                }
        return {"error": f"Item not found: {item_id}"}

    def review_knowledge(self, knowledge_id: str) -> dict:
        """Mark a lesson, decision, or playbook as reviewed without changing its content."""
        lessons, decisions, playbooks = self._read_link_collections()
        item_type, item = self._find_item_in_collections(knowledge_id, lessons, decisions, playbooks)
        if item is None or item_type is None:
            return {"error": f"Item not found: {knowledge_id}"}

        item["last_reviewed"] = _now_iso()
        item["access_count"] = item.get("access_count", 0) + 1
        modified_pbs = [item] if item_type == "playbook" else None
        self._write_link_collections(lessons, decisions, modified_pbs)
        self._audit.log("write", "knowledge/review", detail=knowledge_id)
        return item

    def merge_knowledge(self, primary_id: str, secondary_id: str) -> dict:
        """Merge secondary into primary, then archive the secondary item."""
        if primary_id == secondary_id:
            return {"error": "Cannot merge an item with itself"}

        lessons, decisions, playbooks = self._read_link_collections()
        primary_type, primary = self._find_item_in_collections(primary_id, lessons, decisions, playbooks)
        secondary_type, secondary = self._find_item_in_collections(secondary_id, lessons, decisions, playbooks)

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
            _, related_item = self._find_item_in_collections(related_id, lessons, decisions, playbooks)
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
        # Write back any playbooks that were involved in the merge
        involved_ids = {primary_id, secondary_id} | set(secondary_related)
        modified_pbs = [pb for pb in playbooks if pb.get("id") in involved_ids]
        self._write_link_collections(lessons, decisions, modified_pbs or None)

        return {
            "success": True,
            "primary_id": primary_id,
            "secondary_id": secondary_id,
            "secondary_archived": True,
            "related_ids_transferred": len(transferred),
            "primary_title": self._knowledge_title(primary_type, primary),
            "secondary_title": self._knowledge_title(secondary_type, secondary),
        }

    def _read_link_collections(self) -> tuple[list[dict], list[dict], list[dict]]:
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        playbooks = self._export_playbooks()
        return lessons, decisions, playbooks

    def _write_link_collections(
        self,
        lessons: list[dict],
        decisions: list[dict],
        playbooks: list[dict] | None = None,
    ) -> None:
        # Each write is individually atomic, but the writes together are not.
        # A crash between them could leave a one-sided link. Acceptable for local single-user use.
        self._write_entries(self._knowledge_dir / "lessons.json", lessons, "lesson")
        self._write_entries(self._knowledge_dir / "decisions.json", decisions, "decision")
        if playbooks is not None:
            for pb in playbooks:
                pb_id = pb.get("id")
                if pb_id:
                    self._write_playbook_file(self._playbooks_dir / f"{pb_id}.json", pb)

    def _find_item_in_collections(
        self,
        item_id: str,
        lessons: list[dict],
        decisions: list[dict],
        playbooks: list[dict] | None = None,
    ) -> tuple[str | None, dict | None]:
        for item in lessons:
            if item.get("id") == item_id:
                return "lesson", item
        for item in decisions:
            if item.get("id") == item_id:
                return "decision", item
        if playbooks is not None:
            for item in playbooks:
                if item.get("id") == item_id:
                    return "playbook", item
        return None, None

    def _find_item_by_id(self, item_id: str) -> tuple[str | None, dict | None]:
        """Find a lesson, decision, playbook, or tool by id without updating access metadata."""
        lessons, decisions, playbooks = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions, playbooks)
        if item_type is not None:
            return item_type, item
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
        """Create a bidirectional link between two knowledge items (lessons, decisions, or playbooks)."""
        lessons, decisions, playbooks = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions, playbooks)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions, playbooks)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        if id_b not in item_a["related_ids"]:
            item_a["related_ids"].append(id_b)
        if id_a not in item_b["related_ids"]:
            item_b["related_ids"].append(id_a)

        # Only write back playbooks that were modified
        modified_pbs = [pb for pb in playbooks
                        if pb.get("id") in (id_a, id_b)]
        self._write_link_collections(lessons, decisions, modified_pbs or None)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Linked: {title_a} ↔ {title_b}"}

    def unlink_knowledge(self, id_a: str, id_b: str) -> dict:
        """Remove the bidirectional link between two knowledge items."""
        lessons, decisions, playbooks = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions, playbooks)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions, playbooks)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        item_a["related_ids"] = [item_id for item_id in item_a["related_ids"] if item_id != id_b]
        item_b["related_ids"] = [item_id for item_id in item_b["related_ids"] if item_id != id_a]

        modified_pbs = [pb for pb in playbooks
                        if pb.get("id") in (id_a, id_b)]
        self._write_link_collections(lessons, decisions, modified_pbs or None)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Unlinked: {title_a} ↔ {title_b}"}

    def get_related_knowledge(self, item_id: str) -> dict:
        """Return all knowledge items linked to a lesson, decision, or playbook id."""
        lessons, decisions, playbooks = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions, playbooks)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        related = []
        for related_id in item.get("related_ids", []):
            related_type, related_item = self._find_item_in_collections(
                related_id,
                lessons,
                decisions,
                playbooks,
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
        data = self._repair_incoming_text(dict(data))
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
                # Export decrypted plaintext so backups are portable across
                # different .corpus_salt / ENGRAM_SECRET combinations.
                # The backup file itself should be protected by the user.
                "lessons": self._read_entries(
                    self._knowledge_dir / "lessons.json", "lesson"),
                "decisions": self._read_entries(
                    self._knowledge_dir / "decisions.json", "decision"),
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
                self._write_entries(self._knowledge_dir / "lessons.json", existing[-MAX_KNOWLEDGE_ENTRIES:], "lesson")
                imported.append(f"lessons(+{new_count})")
            else:
                self._write_entries(self._knowledge_dir / "lessons.json", knowledge["lessons"][-MAX_KNOWLEDGE_ENTRIES:], "lesson")
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
                self._write_entries(self._knowledge_dir / "decisions.json", existing[-MAX_KNOWLEDGE_ENTRIES:], "decision")
                imported.append(f"decisions(+{new_count})")
            else:
                self._write_entries(self._knowledge_dir / "decisions.json", knowledge["decisions"][-MAX_KNOWLEDGE_ENTRIES:], "decision")
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
                    self._write_playbook_file(self._playbooks_dir / f"{pb['id']}.json", pb)
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

