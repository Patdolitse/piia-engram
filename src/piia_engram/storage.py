"""Engram storage layer — constants, I/O helpers, and shared utilities."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import portalocker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "2.0"
_ENGRAM_DIR_NAME = ".engram"
_LEGACY_DIR_NAME = ".piia"
SIMILARITY_THRESHOLD = 0.55          # below this: pass; above: related or duplicate
SIMILARITY_DUPLICATE_THRESHOLD = 0.95  # at or above: exact duplicate, reject
# Non-destructive semantic near-duplicate surfacing on write (Round-3): when the
# lexical tier PASSES (bigram < SIMILARITY_THRESHOLD) but an embedding neighbor's
# cosine similarity is >= this, the new item is still ADDED and merely cross-linked
# (related_ids + _dedup_note). This NEVER rejects — it only governs cross-linking,
# so a slightly-off value over/under-links but can never lose knowledge.
#
# Calibrated leaning precision: with the default CJK model (bge-small-zh-v1.5) the
# cosine bands for "same insight, different words" and "same topic, different
# insight" overlap, so this sits at the conservative zero-false-link point — it
# surfaces only unambiguous near-duplicates and abstains otherwise (a missed link
# costs nothing; a wrong link erodes related_ids). Override with ENGRAM_SEMANTIC_THRESHOLD.
SEMANTIC_NEIGHBOR_THRESHOLD = float(os.environ.get("ENGRAM_SEMANTIC_THRESHOLD", "0.72"))
# Keywords that signal supplement/extension, NOT duplication — demote to "related"
_SUPPLEMENT_MARKERS = frozenset({
    "补充", "案例", "更新", "反例", "边界", "延伸", "扩展", "修正",
    "补充说明", "特殊情况", "例外", "进阶", "深入", "实战",
    "supplement", "update", "addendum", "edge case", "exception",
    "extension", "advanced", "follow-up", "correction", "counterexample",
})
SEARCH_RELEVANCE_THRESHOLD = 0.3   # minimum score for keyword search results
# v4.0 hybrid search: minimum RRF score for a fused result. 0.0 = keep any
# item that matched at least one signal (keyword/fts/vector) within the
# already active+filtered candidate pool, so hybrid recall >= keyword recall.
# Calibrated against the Round-10 retrieval benchmark (Phase E).
HYBRID_RELEVANCE_THRESHOLD = 0.0
STALE_KNOWLEDGE_DAYS = 30          # days without access before knowledge is "stale"
# Type-aware stale decay multipliers (applied to STALE_KNOWLEDGE_DAYS)
STALE_DECAY_MULTIPLIERS: dict[str, float] = {
    "user_preference": 3.0,    # 90 days — user preferences decay slowly
    "architecture": 2.0,       # 60 days — architecture decisions are long-lived
    "strategy": 2.0,           # 60 days — strategic decisions
    "product": 1.5,            # 45 days — product decisions
    "workflow": 1.0,           # 30 days — workflow/process (default)
    "debug": 0.5,              # 15 days — debugging workarounds decay fast
    "config": 0.5,             # 15 days — config/setup issues
    "default": 1.0,            # 30 days — everything else
}
MAX_KNOWLEDGE_ENTRIES = 200        # cap per knowledge type (lessons / decisions)
MEMORY_STATES = frozenset({"staging", "verified", "rejected", "deprecated"})
MEMORY_RISK_LEVELS = frozenset({"low", "medium", "high"})
# Trust/approval fields that are the *output* of the owner's risk-based write
# gate, never an untrusted caller's *input*. An agent-facing MCP payload that
# tries to set these (e.g. tier="verified" smuggled through content_json /
# items_json) must have them stripped at the boundary so the gate stays the
# sole authority over tier — otherwise high-risk content could self-certify
# past the staging gate. Internal callers (seeds / imports / fixtures) still
# legitimately pin a tier; that escape hatch lives in core, not at the MCP
# entry, so stripping here does not touch it.
UNTRUSTED_TRUST_FIELDS: tuple[str, ...] = (
    "tier",
    "memory_state",
    "approval_status",
    "approval_required",
    "labeling",
    "provenance.confirmation_source",
    "provenance.anchor_status",
    "provenance.anchor_project_id",
)
# Decision-conflict governance thresholds: post-hoc noise reduction for
# doctor/context/engram conflicts. These favor precision.
# Retrieval uses token-F1 (_bigram_similarity); reconcile uses token-Jaccard.
# The score distributions are not identical across those two algorithms.
CONFLICT_Q_THRESHOLD = 0.6     # question similarity for potential decision conflict
CONFLICT_C_CEILING = 0.5       # choice similarity ceiling; above means same choice, not conflict

# Admission-time conflict thresholds for scripts/check_admission.py.
# These favor recall: review more at write/admission time and avoid missing
# possible conflicts. Keep separate from the governance thresholds above; the
# two groups have opposite goals and must not be collapsed back together.
ADMISSION_CONFLICT_Q_THRESHOLD = 0.25
ADMISSION_CONFLICT_C_CEILING = 0.80

# Sentiment markers for lesson conflict detection
_NEGATION_MARKERS = frozenset({
    "不", "不要", "避免", "别", "禁止", "不能", "不推荐",
    "don't", "avoid", "never", "shouldn't", "not recommended",
    "without approval", "without confirmation", "without user confirmation",
    "without explicit confirmation",
})
_AFFIRMATION_MARKERS = frozenset({
    "推荐", "应该", "建议", "优先", "必须",
    "recommend", "should", "prefer", "always", "must",
})

# Sensitive profile fields eligible for encryption
ENCRYPTED_PROFILE_FIELDS: set[str] = {
    "email", "phone", "location", "company",
    "real_name", "address", "id_number",
}
# Field whitelists — reject unknown keys to prevent injection of arbitrary data
_ALLOWED_PROFILE_FIELDS: frozenset = frozenset({
    "name", "role", "language", "technical_level", "description",
    "email", "phone", "location", "company", "real_name",
    "address", "id_number", "tech_stack", "years_experience",
    "specialties", "updated_at",
})
_ALLOWED_PREFERENCES_FIELDS: frozenset = frozenset({
    "work_patterns", "communication", "tool_preferences",
    "playbook_auto_extract",
    "updated_at", "migrated_from",
})
_ALLOWED_TRUST_FIELDS: frozenset = frozenset({
    "default_sharing", "tool_access", "private_fields",
    "allowed_tools", "data_sharing", "restricted_fields",
    "notes", "updated_at",
})
_ALLOWED_QUALITY_FIELDS: frozenset = frozenset({
    "acceptance_threshold", "rules", "evidence_requirements",
    "review_checklist", "updated_at",
})
DEFAULT_TRUST_BOUNDARIES = {
    "default_sharing": "full",
    "tool_access": {},
    "private_fields": [],
    "allowed_tools": [],
    "data_sharing": "local_only",
    "restricted_fields": [],
    "notes": "默认所有工具可访问全部Engram数据。可按工具或字段限制。",
}
DECISION_TRIGGERS = [
    "决定", "选择", "采用", "放弃", "改用", "决策",
    "decided", "chose", "selected", "switched to", "dropped", "rejected", "went with",
]
LESSON_TRIGGERS = [
    "发现", "注意", "学到", "坑", "问题", "记住", "经验", "教训",
    "learned", "noted", "discovered", "remember", "gotcha", "caveat", "pitfall", "tip",
]
PLAYBOOK_TRIGGERS = [
    "流程", "步骤", "怎么做", "操作", "发布", "部署", "上架",
    "playbook", "procedure", "how to", "steps", "workflow", "runbook",
]
_ALLOWED_PLAYBOOK_UPDATE_FIELDS: frozenset = frozenset({
    "title", "description", "triggers", "domain", "steps",
    "preconditions", "pitfalls", "outcome", "source_tool",
    "source_url", "status", "parameters", "required_tools", "tool_refs",
    "scope", "scope_type", "project_id", "project_folder",
    "project_ids", "project_folders",
})
_ALLOWED_TOOL_UPDATE_FIELDS: frozenset = frozenset({
    "name", "category", "path", "version", "purpose",
    "install_method", "os_platform", "status", "notes",
})
TOOL_CATEGORIES = frozenset({
    "runtime", "cli", "library", "credential", "config", "service", "other",
})
DOMAIN_KEYWORDS = {
    "python": ["python", "pip", "pytest", "django", "fastapi", "pydantic"],
    "javascript": ["js", "javascript", "node", "npm", "react", "vue", "typescript"],
    "git": ["git", "commit", "branch", "merge", "rebase", "push", "pull"],
    "docker": ["docker", "container", "image", "dockerfile", "compose"],
    "mcp": ["mcp", "tool", "server", "stdio", "model context"],
    "architecture": ["架构", "设计", "模式", "pattern", "architecture", "design"],
    "database": ["sql", "database", "query", "index", "migration", "schema"],
}
FIELD_WEIGHTS: dict[str, float] = {
    "triggers": 4.0,
    "name": 3.0,
    "summary": 3.0,
    "title": 3.0,
    "question": 2.5,
    "purpose": 2.0,
    "description": 2.0,
    "detail": 1.5,
    "choice": 1.0,
    "reasoning": 1.0,
    "category": 0.8,
    "domain": 0.5,
}
_TERM_ALIASES: dict[str, list[str]] = {
    "mcp": ["mcp", "model context protocol"],
    "python": ["python", "py"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "database": ["database", "db", "数据库"],
    "api": ["api", "接口"],
    "frontend": ["frontend", "前端"],
    "backend": ["backend", "后端"],
    "deploy": ["deploy", "部署"],
    "debug": ["debug", "调试"],
    "refactor": ["refactor", "重构"],
    "performance": ["performance", "性能", "优化"],
    "security": ["security", "安全"],
    "docker": ["docker", "容器"],
    "tool": ["tool", "工具"],
    "memory": ["memory", "记忆", "内存"],
    "lesson": ["lesson", "教训", "经验"],
    "decision": ["decision", "决策", "决定"],
    "search": ["search", "搜索", "查询"],
    "merge": ["merge", "合并"],
    "archive": ["archive", "归档"],
    "project": ["project", "项目"],
    "knowledge": ["knowledge", "知识"],
    "config": ["config", "配置"],
    "test": ["test", "测试"],
    "error": ["error", "错误", "报错"],
    "install": ["install", "安装"],
    "document": ["document", "文档", "doc"],
    "framework": ["framework", "框架"],
    "dependency": ["dependency", "依赖", "dep"],
    "playbook": ["playbook", "流程", "操作手册", "runbook"],
    "publish": ["publish", "发布", "上架"],
}
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in _TERM_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_LOOKUP[_alias] = _canonical


def strip_untrusted_trust_fields(payload: Any) -> Any:
    """Strip caller-supplied trust/approval fields from an agent payload.

    The risk-based write gate is the sole authority over ``tier`` and the
    approval state. An untrusted agent must not be able to pre-set those
    fields (e.g. ``tier="verified"``) and thereby bypass the high-risk
    staging gate. Call this at the agent-facing MCP boundary on any
    JSON-decoded payload before handing it to ``add_lesson`` /
    ``add_decision`` / ``bulk_add_knowledge``.

    Mutates ``payload`` in place when it is a dict (popping every field in
    :data:`UNTRUSTED_TRUST_FIELDS`) and returns it; leaves non-dict payloads
    untouched. Internal callers (seeds / imports / fixtures) bypass this and
    keep their legitimate ``tier`` escape hatch, which lives in core.
    """
    if isinstance(payload, dict):
        for _field in UNTRUSTED_TRUST_FIELDS:
            if "." not in _field:
                payload.pop(_field, None)
                continue
            parent, child = _field.split(".", 1)
            nested = payload.get(parent)
            if isinstance(nested, dict):
                nested.pop(child, None)
    return payload


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _has_engram_data(root: Path) -> bool:
    """Return True if *root* looks like an active Engram data directory."""
    return (
        (root / "knowledge" / "lessons.json").is_file()
        or (root / "identity" / "profile.json").is_file()
    )


def _engram_root() -> Path:
    """Global Engram root directory. ENGRAM_DIR env var overrides default."""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    home = Path.home()
    engram_root = home / _ENGRAM_DIR_NAME
    legacy_root = home / _LEGACY_DIR_NAME
    if not engram_root.exists() and legacy_root.exists():
        return legacy_root
    return engram_root


def detect_data_fragmentation(active_root: Path) -> list[str]:
    """Check all known paths for Engram data outside *active_root*.

    Returns a list of directory paths that contain data but are NOT the
    active root.  An empty list means no fragmentation detected.
    """
    candidates = [
        Path.home() / _ENGRAM_DIR_NAME,
        Path.home() / _LEGACY_DIR_NAME,
    ]
    env_dir = os.environ.get("ENGRAM_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser().resolve())

    active_resolved = active_root.resolve()
    orphans: list[str] = []
    for cand in candidates:
        try:
            if cand.resolve() == active_resolved:
                continue
            if _has_engram_data(cand):
                orphans.append(str(cand))
        except OSError:
            continue
    return orphans


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

class DataCorruptionError(Exception):
    """Raised when a JSON data file exists but cannot be parsed."""


# Standalone reads run OUTSIDE the per-directory write lock, so a concurrent
# atomic ``os.replace`` (Engram writer) or an external truncate-then-write
# (e.g. PowerShell ``Out-File``) can make a read transiently fail on a file
# that is valid milliseconds later. Retry a few times before treating it as
# real corruption — this prevents quarantining perfectly good files (the
# source of repeated ``.corrupt.*`` spam on lessons.json/domains.json).
_READ_RETRIES = 4
_READ_RETRY_BACKOFF = 0.05  # seconds; multiplied by the attempt number


def _corrupt_copy_exists(path: Path, raw: bytes) -> bool:
    """True if a prior .corrupt copy of this file already holds identical bytes."""
    try:
        for sibling in path.parent.glob(f"{path.stem}.corrupt.*{path.suffix}"):
            try:
                if sibling.read_bytes() == raw:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _read_json(path: Path, *, allow_corrupt: bool = False) -> Any:
    if not path.is_file():
        return {}
    last_exc: Exception | None = None
    for attempt in range(_READ_RETRIES):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001 — retry, then quarantine on final failure
            last_exc = exc
            if attempt + 1 < _READ_RETRIES:
                if not path.is_file():
                    # File vanished mid-replace; the replacement appears shortly.
                    return {}
                time.sleep(_READ_RETRY_BACKOFF * (attempt + 1))

    exc = last_exc
    logger.warning(
        "failed to read %s after %d attempts: %s", path.name, _READ_RETRIES, exc
    )
    # Genuinely unreadable after retries: back it up so it can be recovered
    # manually, skipping duplicate quarantine copies of identical content.
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(f".corrupt.{ts}.json")
        raw = path.read_bytes()
        if not _corrupt_copy_exists(path, raw):
            shutil.copy2(path, backup)
            logger.warning("corrupted file backed up to %s", backup.name)
    except OSError:
        pass
    if allow_corrupt:
        return {}
    raise DataCorruptionError(
        f"{path.name} is corrupted and cannot be read. "
        f"A backup has been saved. Please check or delete the file."
    ) from exc


def _maybe_backup_before_engram_json_replace(path: Path, candidate_text: str) -> None:
    """Back up an existing Engram-owned JSON file before replacing it."""
    try:
        from piia_engram.file_safety import (
            backup_existing_file,
            classify_path,
            record_file_write,
        )

        root = _engram_root()
        if classify_path(root, path) != "engram_root":
            return
        if not path.is_file():
            return
        try:
            existing = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            existing = None
        if existing == candidate_text:
            return
        backup_path = backup_existing_file(
            root,
            path,
            scope="engram_root",
            tool="storage",
        )
        record_file_write(
            root,
            path,
            scope="engram_root",
            tool="storage",
            backup_path=backup_path,
        )
    except Exception:
        logger.exception("failed to back up %s before Engram JSON write", path.name)
        raise


def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON with a file lock for concurrent writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    lock_path = path.parent / ".engram-write.lock"

    try:
        with portalocker.Lock(lock_path, "a", timeout=5):
            try:
                existing = path.read_text(encoding="utf-8") if path.is_file() else None
            except UnicodeDecodeError:
                existing = None
            if existing == candidate_text:
                os.close(fd)
                fd = -1
                if tmp_path.exists():
                    tmp_path.unlink()
                return
            _maybe_backup_before_engram_json_replace(path, candidate_text)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                f.write(candidate_text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
    except portalocker.LockException as exc:
        if fd != -1:
            os.close(fd)
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"无法获取文件锁（超时 5s）：{path.name}") from exc
    except Exception:
        if fd != -1:
            os.close(fd)
        if tmp_path.exists():
            tmp_path.unlink()
        raise


class SkipWrite(Exception):
    """Raised by an ``_update_json`` mutator to abort the write with no side effect.

    A mutator that determines, under the write lock, that no change should be
    persisted raises this instead of returning data. ``_update_json`` then
    returns the current state without serializing or replacing the file — a true
    zero-write abort that holds even when content-equality cannot short-circuit
    the write (e.g. corpus encryption re-serializes unchanged data with a fresh
    nonce). Existing mutators that always return data are unaffected.
    """


def _update_json(path: Path, mutator, *, default: Any = None) -> Any:
    """Atomic read-modify-write under ONE lock.

    Plain ``_read_json`` + ``_atomic_write_json`` is NOT safe for concurrent
    updaters: the read happens outside the write lock, so two processes can
    read the same state and clobber each other (lost updates). This holds the
    per-directory write lock across read → ``mutator(current)`` → atomic
    replace, so updates serialize correctly. ``mutator`` returns the new data,
    or raises :class:`SkipWrite` to abort with no write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".engram-write.lock"
    _default = {} if default is None else default
    try:
        with portalocker.Lock(lock_path, "a", timeout=5):
            # Read current state INSIDE the lock. Fail CLOSED on corruption:
            # _read_json backs up the bad file and raises DataCorruptionError.
            # We must NOT silently fall back to the default and then overwrite
            # — that would wipe real governance state (grants/revoked/edges).
            current = _read_json(path) if path.is_file() else _default
            try:
                new_data = mutator(current)
            except SkipWrite:
                # Mutator aborted under the lock: return current, write nothing.
                return current
            candidate_text = json.dumps(new_data, ensure_ascii=False, indent=2) + "\n"
            try:
                existing = path.read_text(encoding="utf-8") if path.is_file() else None
            except UnicodeDecodeError:
                existing = None
            if existing == candidate_text:
                return new_data
            _maybe_backup_before_engram_json_replace(path, candidate_text)
            # atomic replace INSIDE the same lock
            fd, tmp_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    fd = -1  # os.fdopen owns the fd now
                    f.write(candidate_text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except Exception:
                if fd != -1:
                    os.close(fd)
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
            return new_data
    except portalocker.LockException as exc:
        raise RuntimeError(f"无法获取文件锁（超时 5s）：{path.name}") from exc


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_json(path, data)


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


_NO_PROJECT_LITERAL = "(no-project)"


def _project_id(folder: str) -> str:
    """Stable short hash for a project folder path.

    v3.30 M3 fix: empty / whitespace-only folder maps to a fixed
    ``(no-project)`` literal *before* hashing. The old behaviour passed
    an empty string through ``Path("").resolve()``, which returns the
    current working directory — meaning two tools (Claude Code in
    project A's cwd, Codex in project B's cwd) ended up with different
    "no-project" hashes and couldn't see each other's spillover logs.
    """
    raw = (folder or "").strip()
    if not raw:
        normalized = _NO_PROJECT_LITERAL
    else:
        normalized = str(Path(raw).resolve()).replace("\\", "/").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]
