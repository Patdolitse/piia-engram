"""Engram — AI 记忆印记，核心读写库。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from . import provenance as _provenance

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
    OWNER_ONLY_PROVENANCE_FIELDS,
    PLAYBOOK_TRIGGERS,
    SCHEMA_VERSION,
    SEARCH_RELEVANCE_THRESHOLD,
    SIMILARITY_DUPLICATE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    _SUPPLEMENT_MARKERS,
    STALE_KNOWLEDGE_DAYS,
    TOOL_CATEGORIES,
    UNTRUSTED_TRUST_FIELDS,
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
    _project_id_aliases,
    _read_json,
    _update_json,
    _write_json,
    DataCorruptionError,
    SkipWrite,
    strip_untrusted_trust_fields,
)
from .retrieval import RetrievalMixin
from .context import ContextMixin
from .context import EXTRACTION_PROMPT, extract_knowledge, ingest_extraction  # noqa: F401
from .reconcile import ReconcileMixin
from .reports import ReportsMixin
from .contexts import ContextStoreMixin
from .import_export import ImportExportMixin
from .knowledge_ops import KnowledgeOpsMixin
from .playbooks import PlaybookMixin
from .tools_registry import ToolRegistryMixin
from .encoding_repair import normalize_entry_text
# Compat helpers re-exported for backward compatibility (tests import these
# from piia_engram.core directly).
from .compat import (  # noqa: F401
    export_to_openclaw,
    hermes_handoff_payload,
    import_from_openclaw,
    migrate_from_oca_memory,
)


def _strip_untrusted_freshness_provenance(entry: dict[str, Any]) -> None:
    """Remove trust/evidence claims from ordinary core dict writes."""
    evidence = entry.get("evidence")
    if (
        isinstance(evidence, dict)
        and str(evidence.get("source_type") or "").strip() == "session_digest"
    ):
        entry.pop("evidence", None)

    for field in OWNER_ONLY_PROVENANCE_FIELDS:
        entry.pop(f"provenance.{field}", None)

    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        return
    clean = dict(provenance)
    for field in OWNER_ONLY_PROVENANCE_FIELDS:
        clean.pop(field, None)
    entry["provenance"] = clean



# ---------------------------------------------------------------------------
# Engram Core Class
# ---------------------------------------------------------------------------

class BackupFailedError(RuntimeError):
    """Raised when a pre-upgrade backup fails AND a schema migration is pending,
    so init is halted rather than migrating the irreplaceable store unprotected.
    """


class Engram(
    ImportExportMixin,
    RetrievalMixin,
    ContextMixin,
    ReconcileMixin,
    ReportsMixin,
    ContextStoreMixin,
    PlaybookMixin,
    ToolRegistryMixin,
    KnowledgeOpsMixin,
):
    """Read/write interface to the user's global Engram."""

    def __init__(self, root: Path | None = None, *, read_only: bool = False):
        # read_only: open the store for a guaranteed zero-write read — e.g. a
        # local desktop client that only needs a resume brief and must never
        # mutate the store. Skips the session-state stamp, structure
        # creation (mkdir / schema_version / migration / trust boundaries), the
        # audit log, and the encryption index purge — so the store root is never
        # mutated. Reads still work; they tolerate missing optional dirs.
        self._read_only = read_only
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
            salt = b""
            if salt_path.is_file():
                salt = salt_path.read_bytes()
                if len(salt) != 16:
                    if self._has_existing_ciphertext():
                        raise RuntimeError(
                            f".corpus_salt in {self.root} is corrupted "
                            f"({len(salt)} bytes, expected 16). Encrypted "
                            "corpus data (enc:v2c:) exists — restore the "
                            "original .corpus_salt from backup."
                        )
                    if not read_only:
                        salt = os.urandom(16)
                        self._atomic_write_bytes(salt_path, salt)
                    else:
                        salt = b""
            else:
                if self._has_existing_ciphertext():
                    raise RuntimeError(
                        f".corpus_salt is missing from {self.root} but encrypted "
                        "corpus data (enc:v2c:) exists. Restore the original "
                        ".corpus_salt file to recover your data. Creating a new "
                        "salt would make existing data permanently unreadable."
                    )
                if not read_only:
                    salt = os.urandom(16)
                    self.root.mkdir(parents=True, exist_ok=True)
                    self._atomic_write_bytes(salt_path, salt)
            if salt:
                self._corpus_key = self._crypto.derive_corpus_key(salt)
            # A plaintext hybrid search index left over from a pre-encryption
            # run would keep the decrypted bodies readable on disk even though
            # all new writes are encrypted. Purge it on init so enabling
            # encryption can't be silently undermined by a stale index
            # (Codex a5 round-2 P1-2). purge_search_index is provided by the
            # RetrievalMixin.
            try:
                if not read_only:
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

        # Audit logger: a local, tamper-evident audit.log is ON BY DEFAULT so
        # every identity/knowledge write leaves a trail the owner can inspect.
        # This is a *local file only* — never network/telemetry; telemetry
        # remains opt-in / off by default and is unrelated to this trail.
        # Opt out explicitly with ENGRAM_AUDIT=0/false/no/off. Under
        # ENGRAM_TEST=1 audit defaults OFF (suite isolation, same carve-out as
        # data-fragmentation detection below) so the test suite doesn't litter
        # every temp root with an audit.log; tests that need it set
        # ENGRAM_AUDIT=1 explicitly.
        from piia_engram.audit import AuditLogger
        _audit_env = os.environ.get("ENGRAM_AUDIT", "").strip().lower()
        if read_only:
            audit_enabled = False  # a zero-write open never appends audit.log
        elif _audit_env in ("1", "true", "yes", "on"):
            audit_enabled = True
        elif _audit_env in ("0", "false", "no", "off"):
            audit_enabled = False
        else:
            _audit_in_test = os.environ.get("ENGRAM_TEST", "").strip().lower() in (
                "1", "true", "yes",
            )
            audit_enabled = not _audit_in_test
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

        # Auto-backup before any migration: snapshot the user's data the FIRST time
        # the store is opened under a NEWER Engram version, so an upgrade migration
        # can never silently lose or corrupt the irreplaceable memory. Best-effort (a
        # failure warns, never blocks); opt out with ENGRAM_NO_AUTO_BACKUP=1. Skipped
        # under ENGRAM_TEST=1 for suite isolation (same carve-out as the audit above);
        # the dedicated test drives _maybe_backup_on_upgrade() directly.
        if not read_only and os.environ.get("ENGRAM_TEST", "").strip().lower() not in (
            "1", "true", "yes",
        ):
            self._maybe_backup_on_upgrade()

        # Directory/file creation (mkdir, schema_version, migration, trust
        # boundaries) is a write — skip it for a read-only open.
        if not read_only:
            self._ensure_structure()

        # v3.30 mechanism (1): unclean-exit detection.
        # Each Engram() init stamps session_state.json with current pid +
        # last_clean_exit=False. Normal shutdown rewrites it with True
        # via _mark_clean_exit. If a later instance sees the previous
        # state was last_clean_exit=False, doctor (mechanism 1) surfaces
        # "previous session may have ended unexpectedly". This is the
        # crash-recovery user-visible signal — the data itself is already
        # safe thanks to _atomic_write_json + portalocker.
        if not read_only:
            try:
                self._mark_session_start()
            except Exception:
                pass  # Best-effort; never block init.

    def _maybe_backup_on_upgrade(self) -> None:
        """Snapshot the user's data store once when first opened under a NEWER Engram
        version — BEFORE any schema/field migration can touch it — so an upgrade can
        never silently lose or corrupt the irreplaceable memory.

        Best-effort by default (a failure warns but never blocks). The one exception:
        if a backup fails *and a schema migration is actually pending*, init is halted
        rather than migrating the store unprotected. Opt out with ENGRAM_NO_AUTO_BACKUP=1.
        """
        if self._read_only:
            return
        if os.environ.get("ENGRAM_NO_AUTO_BACKUP", "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            return
        try:
            from piia_engram import __version__ as current_version
        except Exception:
            return
        state_path = self.root / ".backup_state.json"
        meta = _read_json(state_path, allow_corrupt=True)  # {} if missing/corrupt; never raises
        last_version = (
            str(meta.get("last_backed_up_version", "") or "")
            if isinstance(meta, dict)
            else ""
        )
        if not self._is_upgrade(last_version, current_version):
            return  # unchanged or a downgrade — no newer migration to protect against

        # Only back up if there is real memory to protect — skip a brand-new/empty
        # store (nothing to lose; just record the version so we don't back up later).
        if not self._store_has_user_data():
            self._record_backup_version(current_version)
            return

        try:
            self._backup_store(current_version)
        except Exception as exc:
            # A backup failed. If a real schema migration is pending, refuse to migrate
            # the irreplaceable store unprotected — halt so the user can free disk space
            # / investigate (or set ENGRAM_NO_AUTO_BACKUP=1). With no migration pending
            # the open won't mutate data, so stay best-effort and proceed.
            if self._schema_migration_pending():
                raise BackupFailedError(
                    "Engram could not back up your data before a pending schema "
                    "migration, so the upgrade was halted to avoid touching the store "
                    f"unprotected ({exc}). Free disk space and retry, or set "
                    "ENGRAM_NO_AUTO_BACKUP=1 to proceed without a backup."
                ) from exc
            logger.warning(
                "Engram auto-backup before upgrade failed (%s); no schema migration is "
                "pending, so proceeding without a fresh backup. Set "
                "ENGRAM_NO_AUTO_BACKUP=1 to silence this.", exc,
            )
            return
        # Record + prune only after a complete backup so a failed attempt retries next open.
        self._record_backup_version(current_version)
        self._prune_backups(keep=5)

    @staticmethod
    def _is_upgrade(last_version: str, current_version: str) -> bool:
        """True when ``current_version`` is newer than ``last_version`` (a backup is
        due). An empty or unparseable version falls back to 'back up on any change' —
        we never silently skip protecting data because a version string looked odd.
        """
        if not last_version:
            return True  # never recorded → first open under this feature → protect

        def _tuple(v: str) -> tuple[int, ...] | None:
            parts = str(v).split(".")
            out: list[int] = []
            for p in parts:
                if not p.isdigit():
                    return None
                out.append(int(p))
            return tuple(out) if out else None

        lt, ct = _tuple(last_version), _tuple(current_version)
        if lt is None or ct is None:
            return last_version != current_version  # can't order → conservative
        return ct > lt

    def _store_has_user_data(self) -> bool:
        """True if the store holds any irreplaceable user data — knowledge, identity
        facts, playbooks, or project snapshots. Excludes default-seeded files (e.g.
        trust_boundaries) so a freshly-initialised empty store reads as nothing to
        protect.
        """
        def _has_content(p: Path) -> bool:
            try:
                return p.is_file() and p.stat().st_size > 2  # bigger than "[]"/"{}"
            except Exception:
                return False

        if _has_content(self._knowledge_dir / "lessons.json"):
            return True
        if _has_content(self._knowledge_dir / "decisions.json"):
            return True
        for name in ("profile.json", "preferences.json", "work_style.json", "quality_standards.json"):
            if _has_content(self._identity_dir / name):
                return True
        if self._playbooks_dir.is_dir() and any(self._playbooks_dir.glob("*.json")):
            return True
        if self._projects_dir.is_dir() and any(self._projects_dir.glob("*.json")):
            return True
        return False

    def _schema_migration_pending(self) -> bool:
        """True if the on-disk schema is older than the code's SCHEMA_VERSION, so a
        data-touching migration will run in _ensure_structure."""
        ver_data = _read_json(self.root / "schema_version.json", allow_corrupt=True)
        current = (
            ver_data.get("schema_version", "1.0") if isinstance(ver_data, dict) else "1.0"
        )
        return self._parse_schema_version(current) < self._parse_schema_version(SCHEMA_VERSION)

    def _backup_store(self, version: str) -> Path:
        """Copy the store's data into backups/engram-<version>-<timestamp>/, skipping
        transient logs (telemetry/audit/session/update markers) and the backups dir
        itself (never recurse). Keeps .corpus_salt so an encrypted store stays
        restorable. The copy is staged to a hidden .partial dir and atomically renamed
        into place only on full success, so a failed/partial copy never survives as a
        seemingly-valid backup.
        """
        import shutil
        from datetime import datetime

        # Sub-second + pid uniqueness so two near-simultaneous opens can't collide.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        bdir = self.root / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        final = bdir / f"engram-{version}-{ts}-{os.getpid()}"
        staging = bdir / f".{final.name}.partial"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        skip_exact = {"backups", "audit.log", "beta_events.jsonl", ".update_check.json"}

        def _ignore(_directory: str, names: list[str]) -> set[str]:
            out: set[str] = set()
            for n in names:
                if n in skip_exact or n.endswith(".tmp") or "heartbeat" in n:
                    out.add(n)
            return out

        try:
            shutil.copytree(self.root, staging, ignore=_ignore)
            os.replace(staging, final)  # atomic rename within the same dir/filesystem
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)  # leave no partial behind
            raise
        return final

    def _record_backup_version(self, version: str) -> None:
        try:
            _write_json(self.root / ".backup_state.json", {"last_backed_up_version": version})
        except Exception:
            pass  # best-effort metadata; never block init

    def _prune_backups(self, keep: int = 5) -> None:
        """Keep only the most-recent ``keep`` upgrade backups; delete older ones so
        the backups dir can't grow without bound."""
        import shutil

        try:
            bdir = self.root / "backups"
            if not bdir.is_dir():
                return
            backups = sorted(
                (p for p in bdir.iterdir() if p.is_dir() and p.name.startswith("engram-")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in backups[keep:]:
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass  # pruning is best-effort

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

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        """Write bytes atomically via temp file + fsync + rename."""
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            Path(tmp).replace(path)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

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
        """Atomically write JSON through the shared Engram file lock.

        Under ``read_only`` this is a no-op: a read-only open is a guaranteed
        zero-write, so any lazy backfill (trust-boundary defaults, etc.) that
        routes through here keeps its in-memory value but never persists. This is
        the central guard that makes read_only bulletproof against read-time
        lazy writes (companion to the _read_entries migration guard).
        """
        if getattr(self, "_read_only", False):
            return
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
        ver_data = _read_json(ver_path, allow_corrupt=True)
        current = ver_data.get("schema_version", "1.0") if isinstance(ver_data, dict) else "1.0"
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
        if not isinstance(ver_data, dict):
            ver_data = {}
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
            # The safe projection must NEVER surface the inherently-sensitive
            # encrypted PII fields (email/phone/real_name/id_number/...), even
            # when the user has not configured restricted_fields (which defaults
            # to []). These fields are encrypted at rest precisely because they
            # are private, so a "safe" read — used by resource_profile, shareable
            # reports, and the non-owner facet path — strips them unconditionally,
            # unioned with any user-configured restricted_fields. (Code review
            # 2026-06-23 S2-1/A1-1: empty restricted_fields let safe=True leak PII.)
            tb = self.get_trust_boundaries()
            restricted = set(tb.get("restricted_fields", [])) | set(ENCRYPTED_PROFILE_FIELDS)
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
        path = self._identity_dir / "profile.json"

        def _mutate(profile):
            if not isinstance(profile, dict):
                profile = {}
            profile = self._crypto.decrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)

            if "description" in updates and profile.get("description"):
                old_desc = profile["description"]
                new_desc = updates["description"]
                if not new_desc:
                    updates["description"] = old_desc
                else:
                    existing_parts = set(old_desc.split())
                    new_parts = [p for p in new_desc.split() if p not in existing_parts]
                    if new_parts:
                        updates["description"] = old_desc + " " + " ".join(new_parts)
                    else:
                        updates["description"] = old_desc

            now = _now_iso()

            provenance = profile.get("_provenance", {})
            for key in updates:
                if key not in ("updated_at",):
                    provenance[key] = {"by": source_tool or "unknown", "at": now}

            profile.update(updates)
            profile["updated_at"] = now
            profile["_provenance"] = provenance
            if source_tool:
                profile["_last_updated_by"] = source_tool
            return self._crypto.encrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)

        _update_json(path, _mutate, default={})
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
        path = self._identity_dir / "preferences.json"

        def _mutate(prefs):
            if not isinstance(prefs, dict):
                prefs = self.get_preferences()
            prefs.update(updates)
            prefs["updated_at"] = _now_iso()
            return prefs

        _update_json(path, _mutate, default={})

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
        path = self._identity_dir / "trust_boundaries.json"

        def _mutate(tb):
            if not isinstance(tb, dict):
                tb = {}
            tb.update(updates)
            tb["updated_at"] = _now_iso()
            return tb

        _update_json(path, _mutate, default={})

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
        path = self._identity_dir / "quality_standards.json"

        def _mutate(standards):
            if not isinstance(standards, dict):
                standards = {}
            standards.update(updates)
            standards["updated_at"] = _now_iso()
            return standards

        _update_json(path, _mutate, default={})

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

    def _normalize_project_scope_for_entry(self, entry: dict) -> dict:
        """Bind project-specific knowledge to a stable project id.

        ``project_folder`` is accepted as a local write hint but not kept as a
        knowledge field: the durable lookup key is the hashed ``project_id`` and
        the human-facing label is the sanitized folder name. Entries without a
        project marker remain global reusable memory.
        """
        if not isinstance(entry, dict):
            return entry
        provenance = entry.get("provenance")
        if isinstance(provenance, dict):
            provenance = dict(provenance)
            provenance.pop("project_folder", None)
            provenance.pop("source_project_folder", None)
            raw_source_project = provenance.get("source_project")
            if (
                isinstance(raw_source_project, str)
                and raw_source_project.strip()
                and self._looks_like_project_path(raw_source_project)
            ):
                provenance.pop("source_project", None)
            raw_project = provenance.get("project")
            if (
                isinstance(raw_project, str)
                and raw_project.strip()
                and self._looks_like_project_path(raw_project)
            ):
                provenance["project"] = self._sanitize_project(raw_project)
            entry["provenance"] = provenance
        project_folder = entry.pop("project_folder", "") or entry.pop("source_project_folder", "")
        source_project = entry.get("source_project")
        if (
            not project_folder
            and isinstance(source_project, str)
            and source_project.strip()
            and self._looks_like_project_path(source_project)
        ):
            project_folder = source_project
        if isinstance(project_folder, str) and project_folder.strip():
            project_label = self._sanitize_project(project_folder)
            entry["project_id"] = _project_id(project_folder)
            entry["project"] = project_label
            entry.pop("source_project", None)
            provenance = entry.get("provenance")
            if isinstance(provenance, dict):
                provenance["project"] = project_label
                provenance["project_id"] = entry["project_id"]
                provenance.pop("source_project", None)
            return entry
        value = entry.get("project")
        if isinstance(value, str) and value.strip():
            entry["project"] = self._sanitize_project(value)
        return entry

    @staticmethod
    def _looks_like_project_path(value: str) -> bool:
        return bool(value) and (
            "/" in value or "\\" in value or (len(value) > 2 and value[1] == ":")
        )

    def _entry_project_id(self, entry: dict) -> str:
        value = entry.get("project_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        for key in ("project_folder", "source_project_folder", "source_project"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip() and self._looks_like_project_path(raw):
                return _project_id(raw)
        return ""

    def _entry_project_label(self, entry: dict) -> str:
        for key in ("source_project", "project"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                return self._sanitize_project(raw).strip().lower()
        return ""

    def _entry_visible_for_project(self, entry: dict, project_folder: str | None) -> bool:
        project_folder = (project_folder or "").strip()
        entry_pid = self._entry_project_id(entry)
        entry_label = self._entry_project_label(entry)
        if not project_folder:
            return not entry_pid and not entry_label
        target_pids = set(_project_id_aliases(project_folder))
        if entry_pid:
            return entry_pid in target_pids
        if entry_label:
            return entry_label == self._sanitize_project(project_folder).strip().lower()
        return True

    def _entries_share_project_scope(self, left: dict, right: dict) -> bool:
        left_pid = self._entry_project_id(left)
        right_pid = self._entry_project_id(right)
        if left_pid and right_pid:
            return left_pid == right_pid

        left_label = self._entry_project_label(left)
        right_label = self._entry_project_label(right)
        if left_pid or right_pid:
            return bool(left_label and right_label and left_label == right_label)
        if left_label or right_label:
            return bool(left_label and right_label and left_label == right_label)
        return True

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
        """Classify an entry's security risk with *value-match priority*.

        Only value-bearing credentials and destructive / publish actions are
        escalated to ``high`` (the tier the write gate holds in staging for
        owner approval). A bare prose mention of a sensitive topic — the words
        ``secret`` / ``approval`` / ``permission`` / ``bypass`` / ``git push``,
        an ``mcp_server`` filename, ... — is a *weak* signal that maps to
        ``medium``: it is flagged for the audit trail but still auto-absorbs to
        verified, so ordinary dev notes do not flood the review queue.

        Empirically (N3 dogfood) the old "any keyword -> high" rule mis-routed
        ~100% of keyword-bearing benign dev notes to staging; splitting strong
        vs weak markers keeps the recall on truly sensitive content (0% missed
        in the dogfood corpus) while removing that review-fatigue tax.
        """
        text = self._entry_risk_text(entry)
        lowered = text.lower()
        flags: list[str] = []
        strong = False

        def _scan(flag: str, markers: tuple[str, ...], *, is_strong: bool) -> None:
            nonlocal strong
            if flag in flags:
                return
            if any(marker in lowered for marker in markers):
                flags.append(flag)
                if is_strong:
                    strong = True

        # External URLs are a weak signal (medium): note-taking links abound.
        if re.search(r"https?://", text):
            flags.append("external_url")

        # credential: actual secret *values* are strong; the bare word "secret"
        # (as in "the secret to fast tests") is a weak prose signal.
        _scan("credential", (
            "api_key", "apikey", "token=", "password", "bearer ", "ssh-rsa",
            "-----begin", "private key", "client_secret", "secret_key", "server_key",
        ), is_strong=True)
        _scan("credential", ("secret",), is_strong=False)

        # command: destructive / shell execution is strong; benign vcs / publish
        # mentions ("after git push, check CI") are weak.
        _scan("command", (
            "run command", "powershell", "cmd.exe", "bash ", "curl ", "wget ",
            "rm -rf",
        ), is_strong=True)
        _scan("command", ("git push", "twine upload", "delete all"), is_strong=False)

        # mcp_config: a key *value* is already caught as a credential
        # (server_key); a plain mcp_server / mcp config mention is weak.
        _scan("mcp_config", (
            "mcp config", "mcp_server", "mcp_servers", "mcpservers",
        ), is_strong=False)

        # permission_rule: an explicit publish-without-approval intent is strong;
        # prose words (permission / approval / allowlist / bypass) are weak.
        _scan("permission_rule", (
            "publish without approval", "push/tag/publish",
        ), is_strong=True)
        _scan("permission_rule", (
            "permission", "allowlist", "denylist", "bypass", "approval",
        ), is_strong=False)

        if strong:
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

    @staticmethod
    def _labeling_source_kind(entry: dict) -> str:
        return _provenance.derive_labeling_source_kind(entry)

    def _derive_labeling(self, entry: dict) -> dict[str, Any]:
        """Derive non-authoritative data-label maturity metadata."""
        return _provenance.derive_labeling(entry)

    def _refresh_labeling(self, entry: dict) -> dict:
        entry["labeling"] = self._derive_labeling(entry)
        return entry

    def _apply_write_risk_gate(self, entry: dict, *, tier_explicit: bool) -> str:
        """Risk-tiered write gate for a NEW entry (call once, after _ensure_fields).

        Realizes the cold-start -> approve -> resume policy: low/medium-risk
        knowledge is auto-absorbed straight to ``verified`` by default (with a
        post-hoc audit entry), while high-risk knowledge (credentials / shell
        commands / MCP config / permission rules, i.e. risk_level == "high") is
        held in ``staging`` for explicit owner approval. Opt-in
        ``ENGRAM_APPROVAL=strict`` sends otherwise auto-absorbed new entries to
        ``staging`` too. The auditable moat is preserved: the returned note is
        logged for every write.

        If the caller explicitly pinned a ``tier`` (a deliberate seed or a test
        fixture), that intent is honored and the gate is skipped — *except*
        under ``ENGRAM_APPROVAL=strict``, which gates every new write
        regardless of any caller-supplied tier, so a strict owner cannot be
        bypassed by a tier smuggled through ``content_json``.

        Returns a short note describing the decision, for the audit log.
        """
        # Never auto-promote an entry that is already rejected / deprecated /
        # outdated — those states were set deliberately and must survive the
        # gate (otherwise a rejected draft would silently become verified).
        # This runs before everything else, including strict mode, so a
        # deliberate negative state is never bumped into staging either.
        if entry.get("status") == "rejected" or entry.get("memory_state") in {
            "rejected",
            "deprecated",
        }:
            self._refresh_labeling(entry)
            return f"preserved (state={entry.get('memory_state', 'rejected')})"
        # Strict mode gates EVERY new write — including a caller-pinned tier.
        # This must run *before* honoring an explicit tier: otherwise a caller
        # (or content injected into ``content_json``) could smuggle
        # tier="verified" and silently bypass the owner's strict gate.
        if os.environ.get("ENGRAM_APPROVAL", "").strip().lower() == "strict":
            entry["tier"] = "staging"
            entry["memory_state"] = "staging"
            entry["approval_status"] = "pending"
            entry["approval_required"] = True
            self._refresh_labeling(entry)
            return "strict-mode->staging (ENGRAM_APPROVAL=strict)"
        # Outside strict mode, a deliberately caller-pinned tier (a seed, an
        # import, or a test fixture) is honored and the risk gate is skipped.
        if tier_explicit:
            self._refresh_labeling(entry)
            return f"explicit tier={entry.get('tier', 'verified')}"
        if entry.get("risk_level") == "high":
            entry["tier"] = "staging"
            entry["memory_state"] = "staging"
            entry["approval_status"] = "pending"
            entry["approval_required"] = True
            flags = ",".join(entry.get("risk_flags", [])) or "none"
            self._refresh_labeling(entry)
            return f"gated->staging (risk=high, flags={flags})"
        # low / medium risk -> auto-absorbed to verified
        entry["tier"] = "verified"
        entry["memory_state"] = "verified"
        entry["approval_status"] = "approved"
        entry["approval_required"] = False
        self._refresh_labeling(entry)
        return f"auto-absorbed->verified (risk={entry.get('risk_level', 'low')})"

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
        if entry.get("source_project"):
            provenance.setdefault("source_project", entry.get("source_project"))
        if entry.get("project_id"):
            provenance.setdefault("project_id", entry.get("project_id"))
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
        self._refresh_labeling(entry)

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

        # A read_only open is a guaranteed zero-write: still backfill fields in
        # memory for this read, but never persist the migration to disk. Without
        # this guard a legacy entry needing backfill would be rewritten even under
        # read_only=True (dock-resume / dock-search / preview --read-only).
        if changed and migrate and not self._read_only:
            # Re-read and migrate under the write lock to avoid overwriting
            # concurrent additions (a bare _write_json here would use a stale
            # snapshot and silently drop entries added since our read above).
            _entry_type = entry_type

            def _migrate_locked(current):
                if not isinstance(current, list):
                    raise SkipWrite()
                migrated = [self._ensure_fields(e, _entry_type) for e in current]
                if all(a == b for a, b in zip(current, migrated)):
                    raise SkipWrite()
                return migrated

            _update_json(path, _migrate_locked, default=[])

        # Decrypt content fields for in-memory use
        if self._corpus_key:
            return [self._crypto.decrypt_entry(e, self._corpus_key, entry_type)
                    for e in ensured]
        return ensured

    def _display_sanitize(self, entries: list[dict], entry_type: str) -> list[dict]:
        """Return display-safe copies of decrypted entries.

        Replaces any content field that still carries an ``enc:`` prefix (i.e.
        decryption silently failed) with a clear placeholder, so raw ciphertext
        is never handed to the model as if it were plaintext. No-op when corpus
        encryption is disabled.

        MUST only be applied to results returned for display/query — never to
        values headed for at-rest storage. Write-back paths keep using the raw
        decrypt output so recoverable ciphertext is preserved on failure.
        """
        if not self._corpus_key:
            return entries
        return [self._crypto.sanitize_failed_decryption(e, entry_type) for e in entries]

    def _display_sanitize_one(self, entry: dict, entry_type: str) -> dict:
        """Single-entry variant of :meth:`_display_sanitize`."""
        if not self._corpus_key:
            return entry
        return self._crypto.sanitize_failed_decryption(entry, entry_type)

    def _entries_for_locked_mutation(self, entries: Any, entry_type: str) -> list[dict]:
        """Normalize raw stored entries for a locked read-modify-write mutation."""
        if not isinstance(entries, list):
            return []

        normalized: list[dict] = []
        for entry in entries:
            item = self._ensure_fields(entry, entry_type)
            if self._corpus_key:
                item = self._crypto.decrypt_entry(item, self._corpus_key, entry_type)
                item = self._ensure_fields(item, entry_type)
            normalized.append(item)
        return normalized

    def _entries_for_storage(self, entries: list[dict], entry_type: str) -> list[dict]:
        """Prepare normalized in-memory entries for at-rest JSON storage."""
        if not self._corpus_key:
            return entries
        return [
            self._crypto.encrypt_entry(entry, self._corpus_key, entry_type)
            for entry in entries
        ]

    def _update_entries(self, path: Path, entry_type: str, mutator) -> None:
        """Apply ``mutator`` to a knowledge list under the storage write lock."""
        def _locked(current: Any) -> list[dict]:
            entries = self._entries_for_locked_mutation(current, entry_type)
            updated = mutator(entries)
            if updated is None:
                updated = entries
            return self._entries_for_storage(updated, entry_type)

        _update_json(path, _locked, default=[])

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

        allow_internal_provenance = extra.pop("_allow_internal_provenance", False) is True
        tier_explicit = ("tier" in lesson) if isinstance(lesson, dict) else False
        tier_explicit = tier_explicit or ("tier" in extra)

        if isinstance(lesson, dict):
            new_lesson = dict(lesson)
            for k in ("summary", "detail", "domain"):
                if k in new_lesson and not isinstance(new_lesson[k], str):
                    new_lesson[k] = str(new_lesson[k])
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

        new_lesson = self._normalize_project_scope_for_entry(new_lesson)

        if not allow_internal_provenance:
            _strip_untrusted_freshness_provenance(new_lesson)

        new_lesson = self._repair_incoming_text(new_lesson)
        new_lesson["timestamp"] = new_lesson.get("timestamp") or _now_iso()
        new_lesson = self._ensure_fields(new_lesson, "lesson")
        _gate_note = self._apply_write_risk_gate(new_lesson, tier_explicit=tier_explicit)

        # Round-3: embedding neighbors of the identity text, computed BEFORE the
        # write lock (gated; [] unless hybrid+backend+not-encrypted). Used only
        # to cross-link when the lexical tier passes — never to reject.
        semantic_neighbors = self._semantic_neighbors_for_write(
            new_lesson.get("summary", ""), exclude_id=new_lesson.get("id", "")
        )

        result_box: dict[str, dict] = {}

        def _mutate_lessons(lessons: list[dict]) -> list[dict]:
            # Three-tier dedup: exact duplicate / semantically related / pass
            same_scope_lessons = [
                self._ensure_fields(existing, "lesson")
                for existing in lessons
                if self._entries_share_project_scope(new_lesson, existing)
            ]
            best_sim = 0.0
            best_match = None
            for existing in same_scope_lessons:
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
                    result_box["result"] = {
                        "status": "duplicate",
                        "similarity": round(best_sim, 2),
                        "existing_id": best_match.get("id"),
                        "existing_summary": best_match.get("summary"),
                        "message": f"与现有教训相似度 {best_sim:.0%}，未重复添加",
                    }
                    return lessons
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

            # Round-3 additive hook: when lexical passed (best_sim < threshold),
            # surface a close embedding neighbor by cross-linking only. Re-verifies
            # the neighbor against this fresh active list inside the lock.
            self._semantic_crosslink_in_lock(
                new_lesson, same_scope_lessons, semantic_neighbors, best_sim
            )

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
            result_box["result"] = new_lesson
            return lessons

        self._update_entries(path, "lesson", _mutate_lessons)
        result = result_box["result"]
        if result.get("status") == "duplicate":
            return result

        summary = new_lesson.get("summary", "")
        self._audit.log(
            "write", "knowledge/lessons",
            detail=f"[{_gate_note}] {summary[:100]}",
            source_tool=new_lesson.get("source_tool", ""),
        )
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
        project_folder: str | None = None,
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
            if not self._entry_visible_for_project(lesson, project_folder):
                continue
            result.append(lesson)
        result = result[-limit:] if limit is not None else result
        if _update_access and result:
            now = _now_iso()
            selected_ids = {lesson.get("id") for lesson in result if lesson.get("id")}
            for lesson in result:
                lesson["last_reviewed"] = now
                lesson["access_count"] = lesson.get("access_count", 0) + 1

            def _bump_access(entries: list[dict]) -> list[dict]:
                for entry in entries:
                    if entry.get("id") in selected_ids:
                        entry["last_reviewed"] = now
                        entry["access_count"] = entry.get("access_count", 0) + 1
                return entries

            self._update_entries(path, "lesson", _bump_access)
        self._audit.log("read", "knowledge/lessons", detail=f"returned {len(result)} items")
        if _update_access:
            # Model-facing read: never surface raw ciphertext as content.
            # (Export/internal callers pass _update_access=False and keep the
            # recoverable ciphertext untouched.)
            result = self._display_sanitize(result, "lesson")
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
        updates = self._repair_incoming_text(dict(updates))
        allowed_fields = {"summary", "detail", "domain", "status", "tier"}
        valid_tiers = {"staging", "verified", "archived"}
        result_box: dict[str, Any] = {}

        def _mutate_lessons(lessons: list[dict]) -> list[dict]:
            existing_ids = {str(item.get("id")) for item in lessons if item.get("id")}
            for lesson in lessons:
                if lesson.get("id") != lesson_id:
                    continue
                before = dict(lesson)
                content_changed = False
                old_tier = lesson.get("tier")
                tier_changed = False
                new_tier = None
                now = _now_iso()
                for key, value in updates.items():
                    if key not in allowed_fields:
                        continue
                    if key == "tier":
                        if value not in valid_tiers:
                            result_box["result"] = {
                                "error": (
                                    f"Invalid tier {value!r}; "
                                    f"must be one of {sorted(valid_tiers)}"
                                )
                            }
                            return lessons
                        if value != old_tier:
                            tier_changed = True
                            new_tier = value
                    elif key in {"summary", "detail", "domain"} and value != lesson.get(key):
                        content_changed = True
                    lesson[key] = value
                lesson["last_updated"] = now
                lesson = self._ensure_fields(lesson, "lesson")
                if content_changed:
                    suffix = re.sub(r"[^0-9A-Za-z]+", "", now) or "snapshot"
                    base_id = f"{lesson_id}-prev-{suffix}"
                    snapshot_id = base_id
                    counter = 2
                    while snapshot_id in existing_ids:
                        snapshot_id = f"{base_id}-{counter}"
                        counter += 1
                    snapshot = dict(before)
                    snapshot["id"] = snapshot_id
                    snapshot["status"] = "superseded"
                    snapshot["tier"] = "archived"
                    snapshot["snapshot_of"] = lesson_id
                    snapshot["superseded_by"] = lesson_id
                    snapshot["superseded_at"] = now
                    snapshot["last_updated"] = now
                    snapshot = self._ensure_fields(snapshot, "lesson")
                    lessons.append(snapshot)
                    result_box["snapshot_id"] = snapshot_id
                result_box["result"] = lesson
                result_box["tier_changed"] = tier_changed
                result_box["old_tier"] = old_tier
                result_box["new_tier"] = new_tier
                return lessons
            result_box["result"] = {"error": f"Lesson not found: {lesson_id}"}
            return lessons

        self._update_entries(path, "lesson", _mutate_lessons)
        result = result_box["result"]
        if result_box.get("tier_changed"):
            self._audit.log(
                "write",
                "knowledge/tier_change",
                detail=f"lesson {lesson_id}: {result_box.get('old_tier')} -> {result_box.get('new_tier')}",
            )
        snapshot_id = result_box.get("snapshot_id")
        if snapshot_id:
            from .governance_store import RelationStore

            RelationStore(self.root).add_relation(lesson_id, "supersedes", snapshot_id)
            self._audit.log(
                "write",
                "knowledge/version_snapshot",
                detail=f"lesson {lesson_id} supersedes {snapshot_id}",
            )
        return result

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

        allow_internal_provenance = extra.pop("_allow_internal_provenance", False) is True
        tier_explicit = ("tier" in decision) if isinstance(decision, dict) else False
        tier_explicit = tier_explicit or ("tier" in extra)

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

        new_decision = self._normalize_project_scope_for_entry(new_decision)

        if not allow_internal_provenance:
            _strip_untrusted_freshness_provenance(new_decision)

        new_decision = self._repair_incoming_text(new_decision)
        # Sanitize project field regardless of input path (dict or kwargs)
        if new_decision.get("project"):
            new_decision["project"] = self._sanitize_project(new_decision["project"])

        new_decision["timestamp"] = new_decision.get("timestamp") or _now_iso()
        new_decision = self._ensure_fields(new_decision, "decision")
        _gate_note = self._apply_write_risk_gate(new_decision, tier_explicit=tier_explicit)

        new_title = self._entry_identity_text(new_decision, "decision")

        # Round-3: embedding neighbors of the identity text, computed BEFORE the
        # write lock (gated; [] unless hybrid+backend+not-encrypted). Used only
        # to cross-link when the lexical tier passes — never to reject.
        semantic_neighbors = self._semantic_neighbors_for_write(
            new_title, exclude_id=new_decision.get("id", "")
        )

        result_box: dict[str, dict] = {}
        supersedes_box: dict[str, str | None] = {}

        def _mutate_decisions(decisions: list[dict]) -> list[dict]:
            # Three-tier dedup for decisions.
            # >= (not strict >) so that when multiple entries share the same
            # similarity (e.g. same question text, sim=1.0), the LAST entry
            # (most recent by list position) wins. This is correct for both
            # dedup (compare against the latest) and auto-supersedes (chain
            # should target the most recent predecessor, not an older one).
            same_scope_decisions = [
                self._ensure_fields(existing, "decision")
                for existing in decisions
                if self._entries_share_project_scope(new_decision, existing)
            ]
            best_sim = 0.0
            best_match = None
            for existing in same_scope_decisions:
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
            auto_supersedes_target: str | None = None

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
                    result_box["result"] = {
                        "status": "duplicate",
                        "similarity": round(best_sim, 2),
                        "existing_id": best_match.get("id"),
                        "existing_title": self._entry_identity_text(best_match, "decision"),
                        "message": f"与现有决策相似度 {best_sim:.0%}，未重复添加",
                    }
                    return decisions
                # Different choice or supplement — fall through to related tier.
                # Same question + different choice → the new decision supersedes the old.
                if choices_differ:
                    auto_supersedes_target = best_match.get("id")

            if best_sim >= SIMILARITY_THRESHOLD and best_match:
                new_id = new_decision.get("id", "")
                existing_id = best_match.get("id", "")
                # Guard against self-reference
                if existing_id and existing_id != new_id and existing_id not in new_decision.get("related_ids", []):
                    new_decision.setdefault("related_ids", []).append(existing_id)
                if new_id and new_id != existing_id and new_id not in best_match.get("related_ids", []):
                    best_match.setdefault("related_ids", []).append(new_id)
                new_decision["_dedup_note"] = f"related to {existing_id} (sim={best_sim:.0%})"

            # Round-3 additive hook: when lexical passed (best_sim < threshold),
            # surface a close embedding neighbor by cross-linking only. Re-verifies
            # the neighbor against this fresh active list inside the lock.
            self._semantic_crosslink_in_lock(
                new_decision, same_scope_decisions, semantic_neighbors, best_sim
            )

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
            result_box["result"] = new_decision
            supersedes_box["target"] = auto_supersedes_target
            return decisions

        self._update_entries(path, "decision", _mutate_decisions)
        result = result_box["result"]
        if result.get("status") == "duplicate":
            return result
        title = new_decision.get("question", "") or new_decision.get("title", "")
        self._audit.log(
            "write", "knowledge/decisions",
            detail=f"[{_gate_note}] {title[:100]}",
            source_tool=new_decision.get("source_tool", ""),
        )

        # Auto-supersedes: build a directed edge in the decision thread.
        # Priority: (1) explicit ``supersedes`` field in the input,
        #           (2) auto-detected same-question conflict (different choice).
        # Best-effort: a failed edge write must NEVER block the decision write.
        supersedes_id = new_decision.get("supersedes") or supersedes_box.get("target")
        if supersedes_id:
            try:
                target_decision = next(
                    (
                        self._ensure_fields(existing, "decision")
                        for existing in self._read_entries(
                            self._knowledge_dir / "decisions.json",
                            "decision",
                            migrate=False,
                        )
                        if str(existing.get("id") or "") == str(supersedes_id)
                    ),
                    None,
                )
                if target_decision and self._entries_share_project_scope(
                    new_decision, target_decision
                ):
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
        project_folder: str | None = None,
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
            if not project and not self._entry_visible_for_project(decision, project_folder):
                continue
            if project and project_folder and not self._entry_visible_for_project(decision, project_folder):
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
            selected_ids = {decision.get("id") for decision in result if decision.get("id")}
            for decision in result:
                decision["last_reviewed"] = now
                decision["access_count"] = decision.get("access_count", 0) + 1

            def _bump_access(entries: list[dict]) -> list[dict]:
                for entry in entries:
                    if entry.get("id") in selected_ids:
                        entry["last_reviewed"] = now
                        entry["access_count"] = entry.get("access_count", 0) + 1
                return entries

            self._update_entries(path, "decision", _bump_access)
        self._audit.log("read", "knowledge/decisions", detail=f"returned {len(result)} items")
        if _update_access:
            # Model-facing read: never surface raw ciphertext as content.
            result = self._display_sanitize(result, "decision")
        return result

    def update_decision(self, decision_id: str, updates: dict) -> dict:
        """Update fields on a decision entry.

        v3.31 P0-1: ``tier`` is updatable; same validation + audit semantics
        as :meth:`update_lesson`.
        """
        path = self._knowledge_dir / "decisions.json"
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
        result_box: dict[str, Any] = {}

        def _mutate_decisions(decisions: list[dict]) -> list[dict]:
            for decision in decisions:
                if decision.get("id") != decision_id:
                    continue
                old_tier = decision.get("tier")
                tier_changed = False
                new_tier = None
                for key, value in updates.items():
                    if key not in allowed_fields:
                        continue
                    if key == "tier":
                        if value not in valid_tiers:
                            result_box["result"] = {
                                "error": (
                                    f"Invalid tier {value!r}; "
                                    f"must be one of {sorted(valid_tiers)}"
                                )
                            }
                            return decisions
                        if value != old_tier:
                            tier_changed = True
                            new_tier = value
                    decision[key] = value
                decision["last_updated"] = _now_iso()
                decision = self._ensure_fields(decision, "decision")
                result_box["result"] = decision
                result_box["tier_changed"] = tier_changed
                result_box["old_tier"] = old_tier
                result_box["new_tier"] = new_tier
                return decisions
            result_box["result"] = {"error": f"Decision not found: {decision_id}"}
            return decisions

        self._update_entries(path, "decision", _mutate_decisions)
        result = result_box["result"]
        if result_box.get("tier_changed"):
            self._audit.log(
                "write",
                "knowledge/tier_change",
                detail=f"decision {decision_id}: {result_box.get('old_tier')} -> {result_box.get('new_tier')}",
            )
        return result

    def archive_decision(self, decision_id: str) -> dict:
        """Mark a decision as outdated without deleting it."""
        return self.update_decision(decision_id, {"status": "outdated"})

    def update_domain(self, domain: str, updates: dict) -> None:
        """Update skill/experience data for a domain (e.g. "python", "frontend")."""
        path = self._knowledge_dir / "domains.json"

        def _mutate(domains):
            if not isinstance(domains, dict):
                domains = {}
            if domain not in domains:
                domains[domain] = {"first_seen": _now_iso(), "project_count": 0}
            domains[domain].update(updates)
            domains[domain]["updated_at"] = _now_iso()
            return domains

        _update_json(path, _mutate, default={})

    def get_domains(self) -> dict:
        path = self._knowledge_dir / "domains.json"
        stored = _read_json(path)
        if not isinstance(stored, dict):
            stored = {}

        active_counts: dict[str, int] = {}
        # Read-aggregation only — never trigger a field-migration write (a read
        # path must not persist; keeps build_user_portrait truly all-reads).
        for lesson in self.get_lessons(limit=None, _update_access=False, _migrate_fields=False):
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

        def _mutate(domains):
            if not isinstance(domains, dict):
                domains = {}
            entry = domains.get(domain, {"first_seen": _now_iso(), "project_count": 0})
            entry["project_count"] = entry.get("project_count", 0) + 1
            entry["last_used"] = _now_iso()
            domains[domain] = entry
            return domains

        _update_json(path, _mutate, default={})

    # =====================================================================
    # Projects — per-project knowledge
    # =====================================================================

    def save_project_snapshot(
        self,
        project_folder: str,
        data: dict,
        *,
        source_tool: str = "",
        source_session: str = "",
    ) -> None:
        """Save project metadata and advance its canonical checkpoint.

        Legacy top-level fields remain merge-compatible. When ``current_state``
        is supplied, it is replaced as one structured value and the previous
        state moves to bounded history; omitted state fields therefore cannot
        survive through a shallow merge.
        """
        pid = _project_id(project_folder)
        path = self._projects_dir / f"{pid}.json"
        legacy_default: dict = {}
        if not path.exists():
            for alias in _project_id_aliases(project_folder):
                if alias == pid:
                    continue
                candidate = _read_json(self._projects_dir / f"{alias}.json")
                if isinstance(candidate, dict) and candidate:
                    legacy_default = candidate
                    break
        data = self._repair_incoming_text(dict(data))
        has_current_state = "current_state" in data
        incoming_current_state = data.get("current_state")
        if has_current_state and not isinstance(incoming_current_state, dict):
            raise ValueError("current_state must be an object")

        def _mutate(existing):
            if not isinstance(existing, dict):
                existing = {}
            now = _now_iso()
            old_checkpoint = existing.get("checkpoint")
            if not isinstance(old_checkpoint, dict):
                old_checkpoint = {}
            try:
                old_revision = max(0, int(old_checkpoint.get("revision") or 0))
            except (TypeError, ValueError):
                old_revision = 0

            if has_current_state:
                previous_state = existing.get("current_state")
                if isinstance(previous_state, dict) and previous_state != incoming_current_state:
                    history = existing.get("checkpoint_history")
                    if not isinstance(history, list):
                        history = []
                    history.append({
                        "revision": old_revision,
                        "generated_at": str(old_checkpoint.get("generated_at") or ""),
                        "source_scope": deepcopy(old_checkpoint.get("source_scope") or {}),
                        "source_session": deepcopy(old_checkpoint.get("source_session") or {}),
                        "current_state": deepcopy(previous_state),
                    })
                    existing["checkpoint_history"] = history[-5:]

            existing.update(data)
            if has_current_state:
                existing["current_state"] = deepcopy(incoming_current_state)

            existing["schema"] = "project_snapshot.v2"
            if has_current_state:
                session_meta: dict[str, str] = {}
                if source_tool:
                    session_meta["tool"] = str(source_tool)[:80]
                if source_session:
                    session_meta["session_ref"] = str(source_session)[:120]
                existing["checkpoint"] = {
                    "revision": old_revision + 1,
                    "generated_at": now,
                    "source_scope": {
                        "mode": "project_exact",
                        "project_id": pid,
                    },
                    "source_session": session_meta,
                }
            elif old_checkpoint:
                existing["checkpoint"] = old_checkpoint
            existing["project_folder"] = project_folder
            existing["updated_at"] = now
            if "created_at" not in existing:
                existing["created_at"] = now
            return existing

        _update_json(path, _mutate, default=legacy_default)

    def get_project_snapshot(self, project_folder: str) -> dict:
        for pid in _project_id_aliases(project_folder):
            data = _read_json(self._projects_dir / f"{pid}.json")
            if data:
                return data
        return {}

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
