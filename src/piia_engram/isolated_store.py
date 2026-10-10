"""Isolated, admission-gated memory store for an automated decision process.

A second, separate Engram store that one automated caller uses. It is not the
Owner's own store and never touches it:

- The process runs with a cleaned environment built by ``isolated_store_launch`` before
  this package is imported (``ENGRAM_*`` allow-list, a fake home), and :meth:`open`
  checks that again, plus a pinned deny list of the Owner's paths, the root marker
  and the pinned capacity limits.
- The caller's admission verdict is the only way in (:meth:`IsolatedStore.admit`);
  the library's own gates (dedup, tombstones, capacity) still apply. The store is
  not in strict approval mode: the admission verdict is the valve, by the Owner's
  choice for this store. The Owner keeps a veto (:meth:`owner_retire`,
  :meth:`owner_reject`).
- Every operation appends a hash-chained receipt outside the root; recall replays
  receipts to the decision's own time and never returns a card from the decision's
  family for replay and test items.

Recall only guarantees that the right cards come back; whether recall improves a
decision is measured by the caller's comparison arms, not assumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

CONFIG_ENV = "PIIA_ISOLATED_STORE_CONFIG"
MARKER = "isolated_store_root.json"
REPLAY_MARKER_FILE = "replay_experience.marker"
LIMITS_FILE = "isolated_store_limits.json"
RECEIPTS_FILE = "receipts.jsonl"
REFUSALS_FILE = "refusals.jsonl"  # guard refusals: kept out of the hash chain
STRICT_MARKER = "approval_mode.json"
ENGRAM_ALLOWED_FIXED = {
    "ENGRAM_RECONCILE": "0",
    "ENGRAM_AUDIT": "1",
    "ENGRAM_NO_UPDATE_CHECK": "1",
}
ENGRAM_ALLOWED_FREE = {"ENGRAM_DIR", "ENGRAM_CACHE_DIR"}
EVIDENCE_KEYS = ("evidence_as_of", "source_family", "source_decision_point", "subject_id")
HASHED_KEYS = ("summary", "detail") + EVIDENCE_KEYS
DEFAULT_LIMITS = {
    "soft_cap": 1000,
    "hard_cap": 1000,
    "review_queue_max": 1,
    "review_queue_ceiling": 1,
    "review_min_stay_days": 7,
    "retired_grace_days": 3650,
    "r_max": 1000,
}
MODES_EXCLUDING_OWN_FAMILY = {"replay", "test"}
# Must point inside the fake home when set (the launcher sets them there).
HOME_LIKE_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
                  "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
# Other tools' locations the library may read; the launcher drops them.
FOREIGN_PATH_VARS = ("FASTEMBED_CACHE_PATH", "CODEX_HOME", "HF_HOME", "TRANSFORMERS_CACHE")
MODES = {"live", "test", "replay"}
PRODUCTION = "production"
REPLAY_EXPERIENCE = "replay_experience"
REPLAY_EXPORT_MARKER = "<!-- store_mode: replay_experience -->"
ROOT_MODES = {PRODUCTION, REPLAY_EXPERIENCE}
REPLAY_HARD_CAP_MAX = 10000
ADMISSION_FUTURE_SKEW_SECONDS = 30
_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_PROCESS_STARTED_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GuardRefused(Exception):
    """The root or the process environment failed a guard check; nothing was written."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class RecallRefused(Exception):
    """Recall cannot run safely (missing decision-point file or field, bad time)."""


class ReceiptsUnreadable(Exception):
    """The receipt file has a line that is not JSON (e.g. a torn last write)."""


FAMILY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")


def _family_key(value: Any) -> str:
    """Canonical family code for comparison: upper case, "-" and "." as "_".

    So Q2-IRV, q2_irv and Q2.IRV are one family; two spellings can never split it.
    """
    return str(value or "").strip().upper().replace("-", "_").replace(".", "_")


class IoRetryExhausted(Exception):
    """A library write kept failing with an OS error (e.g. a Windows file in use)."""


IO_RETRIES = 3
IO_RETRY_SLEEP = 0.2


def _with_retry(fn):
    """Run a library write; retry OS errors a few times (Windows replace while a reader
    holds the file), then raise IoRetryExhausted so the caller writes a receipt."""
    last: OSError | None = None
    for attempt in range(IO_RETRIES):
        try:
            return fn()
        except OSError as exc:
            last = exc
            time.sleep(IO_RETRY_SLEEP * (attempt + 1))
    raise IoRetryExhausted(type(last).__name__ if last else "OSError")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc_z(value: Any, field: str) -> datetime:
    text = str(value or "")
    if not _UTC_Z.match(text):
        raise ValueError(f"{field} must be UTC ISO time ending in Z, got {text!r}")
    return datetime.fromisoformat(text[:-1] + "+00:00")


def _parse_clock(value: Any, field: str) -> datetime:
    """An explicit ISO-8601 instant with a timezone, normalized to UTC."""
    code = "clock" if field == "now" else field
    if value is None or value == "":
        raise GuardRefused(f"{code}_required")
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("expected ISO-8601 datetime")
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("timezone required")
        return instant.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise GuardRefused(f"{code}_invalid") from exc


def _mode_context(root: Path, receipts_dir: Path | None = None) -> tuple[dict | None, Path, bool]:
    """Find the external ledger without treating missing metadata as production."""
    marker = None
    marker_path = root / MARKER
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            marker = {}  # root_mode refuses it, using the configured audit path
        if not isinstance(marker, dict):
            marker = {}
    configured = receipts_dir is not None
    config_path = os.environ.get(CONFIG_ENV, "")
    if receipts_dir is None and config_path:
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if _norm(os.path.abspath(data.get("root", ""))) == _norm(os.path.abspath(root)):
                configured = True
                receipts_dir = Path(data["receipts_dir"])
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            pass
    if receipts_dir is None and marker and isinstance(marker.get("receipts_dir"), str):
        receipts_dir = Path(marker["receipts_dir"])
    audit_dir = receipts_dir or root.with_name(root.name + "_guard")
    isolated = configured or marker is not None or (root / LIMITS_FILE).exists()
    return marker, audit_dir, isolated


def _initial_receipt(receipts_dir: Path, *, required: bool = True) -> dict | None:
    """Read the initialization header; an existing unreadable ledger is unsafe."""
    try:
        with (receipts_dir / RECEIPTS_FILE).open(encoding="utf-8") as ledger:
            initial = json.loads(next((line for line in ledger if line.strip()), "null"))
    except FileNotFoundError as exc:
        if not required:
            return None
        raise GuardRefused("guard_mode_immutable", "initialization ledger missing") from exc
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise GuardRefused("guard_mode_immutable", "initialization ledger unreadable") from exc
    if (not isinstance(initial, dict) or initial.get("op") != "init"
            or initial.get("result") != "initialised"
            or initial.get("seq") != 1 or initial.get("prev_sha256") != ""):
        raise GuardRefused("guard_mode_immutable", "initialization ledger mismatch")
    return initial


def _replay_root_signal(root: Path) -> str:
    """Discover replay provenance in the root without metadata or a launcher."""
    def marked(text: str) -> bool:
        try:
            return carries_replay_marker(json.loads(text))
        except ValueError:
            # Preserve the existing handling of unmarked corrupt production
            # data, while still recognizing intact markers in torn records.
            return (REPLAY_EXPORT_MARKER in text
                    or re.search(r'"store_mode"\s*:\s*"replay_experience"', text) is not None)

    # Presence is sufficient, including an accidentally emptied marker file.
    if (root / REPLAY_MARKER_FILE).exists():
        return REPLAY_MARKER_FILE
    for relative in ("knowledge/lessons.json", "knowledge/decisions.json",
                     "knowledge/overflow_archive/lessons.jsonl",
                     "knowledge/overflow_archive/decisions.jsonl"):
        path = root / relative
        try:
            with path.open(encoding="utf-8-sig") as source:
                if path.suffix == ".jsonl":
                    for line in source:
                        if line.strip() and marked(line):
                            return relative
                elif marked(source.read()):
                    return relative
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as exc:
            raise GuardRefused("guard_mode_immutable", "stored provenance unreadable") from exc
    return ""


def _check_legacy_root(root: Path, marker: dict, audit_dir: Path, *, allow_rebind: bool = False) -> None:
    """Accept old production metadata only with confirmed directory provenance."""
    ledger_dirs = [audit_dir]
    configured_mode = PRODUCTION
    config_path = os.environ.get(CONFIG_ENV, "")
    if config_path:
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            if _norm(os.path.abspath(data.get("root", ""))) == _norm(os.path.abspath(root)):
                # A supplied ledger must not hide the launcher's initial ledger.
                ledger_dirs.append(Path(data["receipts_dir"]))
                configured_mode = data.get("mode", PRODUCTION)
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            raise GuardRefused("guard_mode_immutable", "legacy launcher configuration unreadable") from exc
    seen = set()
    for directory in ledger_dirs:
        location = _norm(str(directory))
        if location in seen:
            continue
        seen.add(location)
        if not os.path.isabs(str(directory)) or _within(str(directory), str(root)):
            raise GuardRefused("guard_mode_immutable", "external ledger required")
        initial = _initial_receipt(directory, required=False)
        if initial is not None:
            if carries_replay_marker(initial):
                raise GuardRefused("guard_mode_immutable", "replay initialization receipt contradicts legacy metadata")
            if "store_mode" in initial or "root_id" in initial:
                raise GuardRefused("guard_mode_immutable", "modern initialization receipt requires bound metadata")
    if configured_mode != PRODUCTION:
        raise GuardRefused("guard_mode_immutable", "replay configuration contradicts legacy metadata")
    signal = _replay_root_signal(root)
    if signal:
        raise GuardRefused("guard_mode_immutable", f"replay root signal contradicts legacy metadata: {signal}")
    try:
        current = _identity(root)
    except OSError as exc:
        raise GuardRefused("guard_root_binding", "legacy directory identity unavailable") from exc
    realpath = marker.get("realpath")
    if (not isinstance(realpath, str) or not os.path.isabs(realpath)
            or (not allow_rebind and _norm(realpath) != _norm(current["realpath"]))
            or type(marker.get("volume")) is not int or marker["volume"] != current["volume"]
            or type(marker.get("file_id")) is not int or marker["file_id"] <= 0
            or marker["file_id"] != current["file_id"]):
        raise GuardRefused("guard_root_binding", "legacy directory identity missing or mismatched")


def root_mode(root: Path, expected: str, *, receipts_dir: Path | None = None,
              allow_rebind: bool = False) -> str:
    """Validate metadata AND the initialization receipt for every attachment."""
    root = Path(root)
    marker, audit_dir, isolated = _mode_context(root, receipts_dir)
    try:
        if not isinstance(expected, str) or expected not in ROOT_MODES:
            raise GuardRefused("guard_mode_invalid")
        if marker is None and _replay_root_signal(root):
            raise GuardRefused("guard_mode_immutable", "replay root signal requires initialized metadata")
        if not isolated:
            if expected != PRODUCTION:
                raise GuardRefused("guard_mode_immutable", "initialized replay root required")
            return PRODUCTION
        if marker is None:
            raise GuardRefused("guard_mode_immutable", "root metadata missing")
        actual = marker.get("mode", PRODUCTION)
        if (marker.get("purpose") != "isolated-store" or not isinstance(actual, str)
                or actual not in ROOT_MODES):
            raise GuardRefused("guard_mode_invalid")
        # Pre-replay production metadata has no ledger locator or mode fields.
        # Preserve genuine old roots without migration, but absence of the new
        # fields alone cannot establish production provenance.
        legacy = not any(key in marker for key in ("mode", "receipts_dir", "root_id"))
        if legacy:
            if expected != PRODUCTION:
                raise GuardRefused("guard_mode_immutable", "initialized replay root required")
            _check_legacy_root(root, marker, audit_dir, allow_rebind=allow_rebind)
            return PRODUCTION
        pinned = marker.get("receipts_dir")
        if (not isinstance(pinned, str) or not os.path.isabs(pinned)
                or _norm(pinned) != _norm(str(audit_dir))):
            raise GuardRefused("guard_mode_immutable", "ledger location mismatch")
        if not os.path.isabs(str(audit_dir)) or _within(str(audit_dir), str(root)):
            raise GuardRefused("guard_mode_immutable", "external ledger required")
        # Full-chain diagnostics remain with the operation and reconcile guards.
        initial = _initial_receipt(audit_dir)
        if initial.get("store_mode", PRODUCTION) != actual:
            raise GuardRefused("guard_mode_immutable", "initialization ledger mismatch")
        # Compare with the attached directory itself, independently of editable
        # metadata. This catches accidental ledger substitution; it is not a
        # tamper-proof boundary against writers controlling both directories.
        actual_root_id = _root_id(root)
        if marker.get("root_id") != actual_root_id or initial.get("root_id") != actual_root_id:
            raise GuardRefused("guard_root_binding", "initialization ledger belongs to another root")
        if expected == PRODUCTION and actual == REPLAY_EXPERIENCE:
            raise GuardRefused("guard_replay_experience_root")
        if actual != expected:
            raise GuardRefused("guard_mode_immutable")
        return actual
    except GuardRefused as exc:
        _append_refusal(audit_dir, exc)
        raise


def carries_replay_marker(value: Any) -> bool:
    """Inspect bundles and nested entries without trusting their envelope."""
    pending, seen = [value], set()
    while pending:
        part = pending.pop()
        if isinstance(part, (dict, list, tuple)):
            if id(part) in seen:
                continue
            seen.add(id(part))
            if isinstance(part, dict):
                if part.get("store_mode") == REPLAY_EXPERIENCE or part.get("mode") == REPLAY_EXPERIENCE:
                    return True
                pending.extend(part.values())
            else:
                pending.extend(part)
        elif isinstance(part, str) and REPLAY_EXPORT_MARKER in part:
            return True
    return False


def refuse_replay_import(eng, original: Any) -> dict | None:
    """Refuse original marked input before any normalization; audit metadata only."""
    if eng._store_mode == PRODUCTION and carries_replay_marker(original):
        _marker, audit_dir, _isolated = _mode_context(eng.root)
        _append_refusal(audit_dir, GuardRefused("replay_experience_import_refused"), op="ingest")
        return {"error": "replay_experience_import_refused",
                "status": "replay_experience_import_refused", "changed": False}
    return None


def mark_replay_export(value: Any, *, _envelope: bool = True) -> Any:
    """Mark exported envelopes and nested entries, on a copy of the payload."""
    if isinstance(value, dict):
        result = {key: mark_replay_export(part, _envelope=key in {"snapshot", "identity_summary"})
                  for key, part in value.items()}
        if _envelope:
            result["store_mode"] = REPLAY_EXPERIENCE
        return result
    if isinstance(value, list):
        return [mark_replay_export(part, _envelope=_envelope or isinstance(part, dict)) for part in value]
    if isinstance(value, tuple):
        return tuple(mark_replay_export(part, _envelope=_envelope or isinstance(part, dict)) for part in value)
    if isinstance(value, str) and _envelope:
        return value if value.startswith(REPLAY_EXPORT_MARKER) else REPLAY_EXPORT_MARKER + "\n" + value
    return value


def export_mode_prefix(eng) -> str:
    """Keep the mode visible in text exports as well as native JSON backups."""
    mode = root_mode(eng.root, eng._store_mode)
    return REPLAY_EXPORT_MARKER + "\n" if mode == REPLAY_EXPERIENCE else ""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(obj: Any) -> str:
    return _sha256_bytes(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def content_hash(row: dict) -> str:
    """Hash of the card's immutable content as stored (labels, links and status excluded)."""
    return _sha256_json({k: row.get(k) for k in HASHED_KEYS})


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _within(a: str, b: str) -> bool:
    """True when normalized path a equals b or lies inside it."""
    a, b = _norm(a), _norm(b)
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _is_unc_or_device(path: str) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def _drive_problem(path: str) -> str:
    """'' for a path on a local fixed disk, else why not (Windows only)."""
    if sys.platform != "win32":
        return ""
    import ctypes

    drive = os.path.splitdrive(path)[0]
    if not re.fullmatch(r"[A-Za-z]:", drive or ""):
        return "no drive letter"
    kind = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
    if kind != 3:
        return f"drive {drive} is not a local fixed disk (type {kind})"
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.QueryDosDeviceW(drive, buf, 1024) and buf.value.startswith("\\??\\"):
        return f"drive {drive} is a subst mapping"
    return ""


def _full_admission(admission: Any, verdict: str) -> bool:
    """The same three fields admit needs: verdict, judge version, decision record."""
    return (isinstance(admission, dict) and admission.get("verdict") == verdict
            and bool(admission.get("judge_version")) and bool(admission.get("decision_record_id")))


def _same_as_root(env_dir: str, configured_root: Path) -> bool:
    """ENGRAM_DIR equals the configured root, compared as strings: the value comes from
    the environment, so it is never resolved (it could name the Owner's store)."""
    return bool(env_dir) and _norm(env_dir) == _norm(str(configured_root))


def _identity(path: Path) -> dict:
    st = os.stat(path)
    return {"realpath": os.path.realpath(path), "volume": st.st_dev, "file_id": st.st_ino}


def _root_id(path: Path) -> str:
    """Cheap directory identity, stable across a rename on the same volume."""
    st = os.stat(path)
    return f"{st.st_dev}:{st.st_ino}"


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class Config:
    """The pinned launcher configuration (a JSON file the Owner approves)."""

    def __init__(self, data: dict, path: Path | None = None):
        missing = [k for k in ("root", "receipts_dir", "fake_home", "cache_dir", "decision_points_dir",
                               "deny_list_file", "deny_list_sha256") if not data.get(k)]
        if missing:
            raise GuardRefused("config_incomplete", ",".join(missing))
        self.path = path
        self.root = Path(data["root"])
        self.receipts_dir = Path(data["receipts_dir"])
        self.fake_home = Path(data["fake_home"])
        self.cache_dir = Path(data["cache_dir"])
        self.decision_points_dir = Path(data["decision_points_dir"])
        self.deny_list_file = Path(data["deny_list_file"])
        self.deny_list_sha256 = str(data["deny_list_sha256"])
        self.mode = data.get("mode", PRODUCTION)
        if not isinstance(self.mode, str) or self.mode not in ROOT_MODES:
            raise GuardRefused("guard_mode_invalid")
        self.limits = {**DEFAULT_LIMITS, **(data.get("limits") or {})}

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Config":
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")), p)

    @classmethod
    def from_env(cls) -> "Config":
        value = os.environ.get(CONFIG_ENV, "").strip()
        if not value:
            raise GuardRefused("config_missing", f"{CONFIG_ENV} is not set; start through the launcher")
        return cls.load(value)

    def deny_list(self) -> list[str]:
        raw = self.deny_list_file.read_bytes()
        if _sha256_bytes(raw) != self.deny_list_sha256:
            raise GuardRefused("deny_list_hash_mismatch", "the pinned deny list was changed")
        entries = json.loads(raw.decode("utf-8")).get("deny")
        if not isinstance(entries, list) or not entries or not all(isinstance(e, str) and e for e in entries):
            raise GuardRefused("deny_list_invalid")
        return entries


def limits_env(limits: dict) -> dict[str, str]:
    from . import capacity as _capacity

    return {var: str(int(limits[name])) for name, var in _capacity._ENV_LIMITS.items()}


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------


def check_candidate(path: Path, deny: Iterable[str], *, label: str) -> str:
    """Resolved real path of a candidate dir, or GuardRefused. Never stats the deny list."""
    text = str(path)
    if _is_unc_or_device(text):
        raise GuardRefused("guard_unc_or_device", f"{label}: UNC, \\\\?\\ and \\\\.\\ paths are refused")
    if not os.path.isabs(text):
        raise GuardRefused("guard_relative", label)
    real = os.path.realpath(text)
    if _is_unc_or_device(real):
        raise GuardRefused("guard_unc_or_device", f"{label} resolves to a network or device path")
    problem = _drive_problem(real)
    if problem:
        raise GuardRefused("guard_drive", f"{label}: {problem}")
    for entry in deny:
        if _within(real, entry) or _within(entry, real):
            raise GuardRefused("guard_owner_store", f"{label} overlaps a denied path")
    return real


def check_environment(cfg: Config) -> None:
    from . import capacity as _capacity

    ceiling = REPLAY_HARD_CAP_MAX if cfg.mode == REPLAY_EXPERIENCE else DEFAULT_LIMITS["hard_cap"]
    try:
        valid_limits = (all(type(value) is int for value in cfg.limits.values())
                        and _capacity.limits_are_valid(_capacity.Limits(**cfg.limits))
                        and cfg.limits["hard_cap"] <= ceiling)
    except TypeError:
        valid_limits = False
    if not valid_limits:
        raise GuardRefused("guard_limits_invalid")
    allowed = set(ENGRAM_ALLOWED_FIXED) | ENGRAM_ALLOWED_FREE | set(limits_env(cfg.limits))
    present = {k.upper(): v for k, v in os.environ.items() if k.upper().startswith("ENGRAM_")}
    extra = sorted(set(present) - allowed)
    if extra:
        raise GuardRefused("guard_env_not_allowed", ",".join(extra))
    for key, value in ENGRAM_ALLOWED_FIXED.items():
        if present.get(key) != value:
            raise GuardRefused("guard_env_value", f"{key} must be {value}")
    for key, value in limits_env(cfg.limits).items():
        if present.get(key) != value:
            raise GuardRefused("guard_limits_mismatch", key)
    if (cfg.mode == REPLAY_EXPERIENCE
            and not _same_as_root(os.environ.get("ENGRAM_CACHE_DIR", ""), cfg.cache_dir)):
        raise GuardRefused("guard_cache_dir_mismatch", "ENGRAM_CACHE_DIR is not the configured cache")
    from . import capacity as _capacity

    problem = _capacity.limits_env_problem()
    if problem:
        raise GuardRefused("guard_limits_invalid", str(problem))
    # String comparison only: a path taken from the environment is never resolved.
    if not _within(str(Path.home()), str(cfg.fake_home)):
        raise GuardRefused("guard_home_not_redirected", "Path.home() is not the fake home")
    for var in HOME_LIKE_VARS:
        value = os.environ.get(var, "")
        if value and not _within(value, str(cfg.fake_home)):
            raise GuardRefused("guard_home_not_redirected", f"{var} is outside the fake home")
    if os.environ.get("HOMEDRIVE") or os.environ.get("HOMEPATH"):
        combined = os.environ.get("HOMEDRIVE", "") + os.environ.get("HOMEPATH", "")
        if not _within(combined, str(cfg.fake_home)):
            raise GuardRefused("guard_home_not_redirected", "HOMEDRIVE+HOMEPATH is outside the fake home")
    present = sorted(v for v in FOREIGN_PATH_VARS if os.environ.get(v))
    if present:
        raise GuardRefused("guard_env_foreign_path", ",".join(present))


# ---------------------------------------------------------------------------
# the root
# ---------------------------------------------------------------------------


class IsolatedStore:
    """An opened isolated store. Build with :meth:`open` (or :func:`init_root`)."""

    def __init__(self, cfg: Config, root_real: str, receipts_real: str, deny: list[str] | None = None,
                 *, allow_rebind: bool = False):
        root_mode(Path(root_real), cfg.mode, receipts_dir=Path(receipts_real), allow_rebind=allow_rebind)
        self._configure(cfg, root_real, receipts_real, deny)

    def _configure(self, cfg: Config, root_real: str, receipts_real: str, deny: list[str] | None) -> None:
        self.cfg = cfg
        self.deny = list(deny or [])
        self.root = Path(root_real)
        self.receipts_dir = Path(receipts_real)
        self.receipts_path = self.receipts_dir / RECEIPTS_FILE
        self._mode = cfg.mode
        from . import __version__

        self.lib_version = __version__

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        exc = GuardRefused("guard_mode_immutable")
        _append_refusal(self.receipts_dir, exc)
        raise exc

    def _check_mode(self, *, allow_rebind: bool = False) -> None:
        """Match the pinned init receipt, metadata and handle on every operation."""
        try:
            if self.cfg.mode != self.mode:
                raise GuardRefused("guard_mode_immutable")
        except GuardRefused as exc:
            _append_refusal(self.receipts_dir, exc)
            raise
        root_mode(self.root, self.mode, receipts_dir=self.receipts_dir, allow_rebind=allow_rebind)

    # -- opening -----------------------------------------------------------

    @classmethod
    def open(cls, cfg: Config | None = None, *, allow_strict_latch: bool = False,
             allow_rebind: bool = False) -> "IsolatedStore":
        cfg = cfg or Config.from_env()
        deny = cfg.deny_list()
        receipts_real = check_candidate(cfg.receipts_dir, deny, label="receipts_dir")
        try:
            root_real = check_candidate(cfg.root, deny, label="root")
            if _within(receipts_real, root_real) or _within(root_real, receipts_real):
                raise GuardRefused("guard_receipts_in_root")
            check_environment(cfg)
            if not _same_as_root(os.environ.get("ENGRAM_DIR", ""), cfg.root):
                raise GuardRefused("guard_engram_dir_mismatch", "ENGRAM_DIR is not the root")
            if (Path(root_real) / STRICT_MARKER).exists() and not allow_strict_latch:
                raise GuardRefused("ISOLATED_STORE_STRICT_LATCHED", "clear it with the owner veto 'clear-latch'")
            marker_path = Path(root_real) / MARKER
            if not marker_path.is_file():
                raise GuardRefused("guard_marker_missing")
            root_mode(Path(root_real), cfg.mode, receipts_dir=Path(receipts_real), allow_rebind=allow_rebind)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            current = _identity(Path(root_real))
            bound = {k: marker.get(k) for k in ("realpath", "volume", "file_id")}
            needs_rebind = (marker.get("purpose") != "isolated-store"
                            or _norm(str(bound["realpath"])) != _norm(current["realpath"])
                            or bound["volume"] != current["volume"] or bound["file_id"] != current["file_id"])
            if needs_rebind:
                if not allow_rebind:
                    raise GuardRefused("guard_marker_binding", "use the owner 'rebind' after a legitimate move")
            pinned = json.loads((Path(root_real) / LIMITS_FILE).read_text(encoding="utf-8"))
            if pinned != cfg.limits:
                raise GuardRefused("guard_limits_file_mismatch")
        except GuardRefused as exc:
            _append_refusal(Path(receipts_real), exc)
            raise
        pr = cls(cfg, root_real, receipts_real, deny, allow_rebind=allow_rebind)
        if not pr._receipts_problem():
            pr._check_mode(allow_rebind=allow_rebind)
        if not needs_rebind:
            pr._check_version()
        return pr

    # -- locks, receipts ----------------------------------------------------

    @contextmanager
    def serial(self) -> Iterator[None]:
        """The one lock admit, retire, restore, recall and the owner veto share."""
        from .storage import hold_directory_lock

        self.receipts_dir.mkdir(parents=True, exist_ok=True)
        with hold_directory_lock(self.receipts_dir, timeout=60):
            yield

    def receipts(self) -> list[dict]:
        if not self.receipts_path.is_file():
            return []
        out = []
        for number, line in enumerate(self.receipts_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                raise ReceiptsUnreadable(f"line {number}") from exc
        return out

    def _receipts_problem(self) -> str:
        try:
            self.receipts()
        except ReceiptsUnreadable as exc:
            return str(exc)
        return ""

    def _refuse_unreadable(self, op: str, detail: str) -> dict:
        """Refuse an operation without touching the broken chain; note it next to it."""
        record = {"op": op, "result": "receipts_unreadable", "detail": detail, "ts": utc_now_z(),
                  "pid": os.getpid()}
        with (self.receipts_dir / REFUSALS_FILE).open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def _append(self, record: dict) -> dict:
        existing = self.receipts()
        prev = existing[-1] if existing else None
        base = {
            "seq": (prev["seq"] + 1) if prev else 1,
            "prev_sha256": _sha256_json(prev) if prev else "",
            "ts": utc_now_z(),
            "pid": os.getpid(),
            "proc_started_utc": _PROCESS_STARTED_UTC,
            "lib_version": self.lib_version,
            "limits": self.cfg.limits,
            **({"store_mode": self.mode} if self.mode == REPLAY_EXPERIENCE else {}),
            "root_state_sha256": self.state_hash(),
        }
        full = {**base, **record}
        with self.receipts_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(full, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())  # the chain is the audit trail
        return full

    def state_hash(self) -> str:
        knowledge = self.root / "knowledge"
        parts = []
        for rel in ("lessons.json", "decisions.json", "tombstones.jsonl"):
            p = knowledge / rel
            parts.append((rel, _sha256_bytes(p.read_bytes()) if p.is_file() else ""))
        archive = knowledge / "overflow_archive"
        if archive.is_dir():
            for p in sorted(archive.iterdir()):
                if p.is_file():
                    parts.append((f"overflow_archive/{p.name}", _sha256_bytes(p.read_bytes())))
        return _sha256_json(parts)

    # -- engram handles -------------------------------------------------------

    def _engram(self, *, read_only: bool):
        from .core import Engram

        self._check_mode()
        return Engram(root=self.root, read_only=read_only, store_mode=self.mode)

    def _recheck_before_write(self) -> None:
        self._check_mode()
        if not _same_as_root(os.environ.get("ENGRAM_DIR", ""), self.cfg.root):
            raise GuardRefused("guard_engram_dir_mismatch", "ENGRAM_DIR changed after open")
        # The configured root (a config path, never an environment path) is resolved
        # again: a root swapped for a junction after open is caught here.
        if _norm(check_candidate(self.cfg.root, self.deny, label="root")) != _norm(str(self.root)):
            raise GuardRefused("guard_root_changed", "the root resolves elsewhere than at open")
        if (self.root / STRICT_MARKER).exists():
            raise GuardRefused("ISOLATED_STORE_STRICT_LATCHED")

    def _rows(self, eng) -> list[dict]:
        return eng._read_entries(eng._knowledge_dir / "lessons.json", "lesson", migrate=False)

    def _archived_raw(self, eng, item_id: str) -> dict | None:
        for row in reversed(eng._read_overflow_archive("lesson")):
            if row.get("id") == item_id:
                return row
        return None

    # -- version receipt (design s9) --------------------------------------------

    def _check_version(self, *, previous_version: str | None = None) -> None:
        if self._receipts_problem():
            return  # every operation refuses with receipts_unreadable; reconcile reports it
        last = ({"lib_version": previous_version} if previous_version is not None else
                next((r for r in reversed(self.receipts()) if r.get("lib_version")), None))
        if last is None or last.get("lib_version") == self.lib_version:
            return
        problems = self.verify_content_hashes()
        if problems:
            raise GuardRefused("upgrade_blocked_hash_mismatch", ",".join(sorted(problems)))
        with self.serial():
            self._append({"op": "version", "result": "upgraded", "from_version": last.get("lib_version"),
                          "to_version": self.lib_version})

    def verify_content_hashes(self) -> list[str]:
        """Ids whose stored content no longer matches their admit receipt."""
        admitted = {r["item_id"]: r["content_sha256"] for r in self.receipts()
                    if r.get("op") == "admit" and r.get("result") == "admitted"}
        eng = self._engram(read_only=True)
        rows = {r.get("id"): r for r in self._rows(eng)}
        bad = []
        for item_id, digest in admitted.items():
            row = rows.get(item_id) or self._archived_raw(eng, item_id)
            if row is None or content_hash(row) != digest:
                bad.append(item_id)
        return bad

    # -- the valve: admit, retire, restore --------------------------------------

    def admit(self, card: dict, round_id: str, admission: dict, *,
              admitted_before: str | None = None, now: str | None = None) -> dict:
        """Write one admitted card as a verified lesson. Returns the receipt."""
        from .storage import NOT_ADDED_STATUSES, hold_directory_lock

        base = {"op": "admit", "round_id": str(round_id)}
        problem = self._receipts_problem()
        if problem:
            return self._refuse_unreadable("admit", problem)
        if not isinstance(admission, dict) or admission.get("verdict") != "admit" or not admission.get(
                "judge_version") or not admission.get("decision_record_id"):
            with self.serial():
                return self._append({**base, "result": "admission_missing"})
        base["admission_sha256"] = _sha256_json(admission)
        try:
            self._check_mode()
            if self.mode == PRODUCTION and carries_replay_marker(card):
                with self.serial():
                    return self._append({**base, "result": "replay_experience_import_refused"})
            if self.mode == REPLAY_EXPERIENCE:
                logical_admission, clock = self._replay_times(admitted_before, now)
                base["admitted_before"] = logical_admission.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                base["clock"] = clock.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            elif admitted_before is not None or now is not None:
                raise GuardRefused("replay_parameters_not_supported")
        except GuardRefused as exc:
            with self.serial():
                return self._append({**base, "result": exc.code})
        try:
            entry = self._entry_from_card(card, round_id)
        except ValueError as exc:
            with self.serial():
                return self._append({**base, "result": "card_invalid", "detail": str(exc)[:200]})
        if self.mode == REPLAY_EXPERIENCE:
            if parse_utc_z(entry["evidence_as_of"], "evidence_as_of") > clock:
                with self.serial():
                    return self._append({**base, "result": "evidence_after_clock"})
            entry["store_mode"] = self.mode
        base.update({k: entry[k] for k in ("subject_id", "evidence_as_of", "source_family")})
        with self.serial():
            self._recheck_before_write()
            eng = self._engram(read_only=False)
            if eng._assess_memory_risk(dict(entry)).get("risk_level") == "high":
                return self._append({**base, "result": "risk_refused"})
            # The keyword risk check misses raw secret values and private paths; the
            # unsupervised-capture privacy guard catches their shapes.
            from .hook_digest import output_guard_item

            guard_ok, _reason = output_guard_item({k: entry[k] for k in ("summary", "detail") + EVIDENCE_KEYS})
            if not guard_ok:
                return self._append({**base, "result": "risk_refused", "detail": "secret_or_path_shape"})
            with hold_directory_lock(eng._knowledge_dir, timeout=60):
                if self._verified_budget_full(eng):
                    return self._append({**base, "result": "capacity_full"})
                # Template similarity is only a relation in replay admission;
                # exact, retired and archived twin guards still run in core.
                write_options = {"_replay_admission": True} if self.mode == REPLAY_EXPERIENCE else {}
                try:
                    result = _with_retry(lambda: eng.add_lesson(dict(entry), **write_options))
                except IoRetryExhausted as exc:
                    return self._append({**base, "result": "io_retry_exhausted", "detail": str(exc)})
            status = str(result.get("status") or "")
            if result.get("error") or status in NOT_ADDED_STATUSES:
                code = status or ("queue_full" if "queue" in str(result.get("error", "")) else "write_error")
                return self._append({**base, "result": code, "item_id": result.get("existing_id") or ""})
            item_id = result.get("id")
            for archived_id in result.get("overflow_archived_ids") or []:
                self._append({**base, "op": "archived", "result": "archived", "item_id": archived_id})
            _kind, row = eng._find_item_by_id(item_id)
            if row is None or row.get("tier") != "verified":
                return self._append({**base, "result": "not_verified_after_write", "item_id": item_id})
            if any(row.get(k) != entry[k] for k in EVIDENCE_KEYS):
                return self._append({**base, "result": "evidence_fields_missing", "item_id": item_id})
            receipt = {**base, "result": "admitted", "item_id": item_id, "content_sha256": content_hash(row)}
            if result.get("related_ids"):
                receipt["related_ids"] = list(result["related_ids"])
            return self._append(receipt)

    def _verified_budget_full(self, eng) -> bool:
        """At the hard cap the library would quietly park a new verified row in staging;
        refuse before writing instead (call under the knowledge lock)."""
        from . import capacity as _capacity

        limits = _capacity.limits_from_env()
        budget = sum(1 for r in self._rows(eng) if _capacity.pool_of(r) in (_capacity.POOL_V, _capacity.POOL_QD))
        return budget >= limits.hard_cap

    def _entry_from_card(self, card: dict, round_id: str) -> dict:
        if not isinstance(card, dict):
            raise ValueError("card must be a dict")
        summary, detail = card.get("summary"), card.get("detail")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary is required")
        if not isinstance(detail, str):
            raise ValueError("detail must be text")
        parse_utc_z(card.get("evidence_as_of"), "evidence_as_of")
        for key in ("source_family", "source_decision_point", "subject_id"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                raise ValueError(f"{key} is required")
        if not FAMILY_RE.match(card["source_family"].strip()):
            raise ValueError("source_family must match [A-Za-z0-9_.-]{1,48}")
        subject = card["subject_id"].strip()
        return {
            "summary": summary,
            "detail": detail,
            "domain": f"type:lesson,subject:{subject};",
            "tier": "verified",
            "evidence_as_of": card["evidence_as_of"],
            "source_family": card["source_family"].strip(),
            "source_decision_point": card["source_decision_point"].strip(),
            "subject_id": subject,
            "admitted_round": str(round_id),
            "source_tool": "admission_gate",
        }

    def retire(self, item_id: str, round_id: str, admission: dict, *, _op: str = "retire") -> dict:
        base = {"op": _op, "round_id": str(round_id), "item_id": item_id}
        problem = self._receipts_problem()
        if problem:
            return self._refuse_unreadable(_op, problem)
        if _op == "veto_retire" and isinstance(admission, dict):
            base["operator"] = str(admission.get("operator") or "")
        if _op == "retire" and not _full_admission(admission, "retire"):
            with self.serial():
                return self._append({**base, "result": "admission_missing"})
        base["admission_sha256"] = _sha256_json(admission)
        with self.serial():
            self._recheck_before_write()
            eng = self._engram(read_only=False)
            if not any(r.get("id") == item_id for r in self._rows(eng)):
                if _op == "veto_retire" and self._archived_raw(eng, item_id) is not None:
                    # already out of the active file: the veto is recorded all the same
                    return self._append({**base, "result": "retired", "detail": "already_archived"})
                return self._append({**base, "result": "not_found"})
            try:
                result = _with_retry(lambda: eng.archive_lesson(item_id))
            except IoRetryExhausted as exc:
                return self._append({**base, "result": "io_retry_exhausted", "detail": str(exc)})
            if result.get("error"):
                return self._append({**base, "result": "write_error", "detail": str(result["error"])[:200]})
            for archived_id in result.get("overflow_archived_ids") or []:
                self._append({"op": "archived", "round_id": str(round_id), "result": "archived",
                              "item_id": archived_id})
            return self._append({**base, "result": "retired"})

    def restore(self, item_id: str, round_id: str, admission: dict) -> dict:
        base = {"op": "restore", "round_id": str(round_id), "item_id": item_id}
        problem = self._receipts_problem()
        if problem:
            return self._refuse_unreadable("restore", problem)
        if not _full_admission(admission, "restore"):
            with self.serial():
                return self._append({**base, "result": "admission_missing"})
        base["admission_sha256"] = _sha256_json(admission)
        from . import tombstones as _tombstones

        with self.serial():
            self._recheck_before_write()
            if _tombstones.by_id(self.root, item_id) is not None:
                return self._append({**base, "result": "rejected_before"})
            if any(r.get("item_id") == item_id and r.get("op") in ("veto_retire", "veto_reject")
                   and r.get("result") in ("retired", "tombstoned") for r in self.receipts()):
                return self._append({**base, "result": "owner_vetoed"})  # only the Owner lifts a veto
            eng = self._engram(read_only=False)
            if not any(r.get("id") == item_id for r in self._rows(eng)):
                if self._archived_raw(eng, item_id) is None:
                    return self._append({**base, "result": "not_found"})
                try:
                    back = _with_retry(lambda: eng.restore_lifecycle_archive(item_id))
                except IoRetryExhausted as exc:
                    return self._append({**base, "result": "io_retry_exhausted", "detail": str(exc)})
                if back.get("error"):
                    return self._append({**base, "result": "write_error", "detail": str(back["error"])[:200]})
            try:
                result = _with_retry(lambda: eng.update_lesson(item_id, {"status": "active"}))
            except IoRetryExhausted as exc:
                return self._append({**base, "result": "io_retry_exhausted", "detail": str(exc)})
            if isinstance(result, dict) and result.get("error"):
                return self._append({**base, "result": "write_error", "detail": str(result["error"])[:200]})
            return self._append({**base, "result": "restored"})

    # -- the Owner's veto (runs only through the launcher's 'veto' command) ------

    def owner_retire(self, item_id: str, operator: str) -> dict:
        receipt = self.retire(item_id, "owner", {"verdict": "owner_veto", "operator": operator}, _op="veto_retire")
        return receipt

    def owner_reject(self, item_id: str, operator: str) -> dict:
        """Retire first (a tombstone refuses an active row), then tombstone."""
        from . import tombstones as _tombstones

        first = self.owner_retire(item_id, operator)
        if first.get("result") not in ("retired",):
            return first
        with self.serial():
            eng = self._engram(read_only=True)
            row = next((r for r in self._rows(eng) if r.get("id") == item_id), None) or self._archived_raw(eng, item_id)
            if row is None:
                return self._append({"op": "veto_reject", "item_id": item_id, "result": "not_found"})
            _tombstones.append(self.root, "lesson", row, via=f"owner-veto:{operator}")
            return self._append({"op": "veto_reject", "item_id": item_id, "result": "tombstoned",
                                 "operator": operator})

    def owner_clear_latch(self, operator: str) -> dict:
        from . import strict_mode as _strict_mode

        problem = self._receipts_problem()
        if problem:  # refuse before acting: never clear the latch without a receipt
            return self._refuse_unreadable("veto_clear_latch", problem)
        self._check_mode()
        with self.serial():
            cleared = _strict_mode.clear_marker(self.root)
            return self._append({"op": "veto_clear_latch", "result": "cleared" if cleared else "no_latch",
                                 "operator": operator})

    def owner_rebind(self, operator: str) -> dict:
        from .atomic_replace import replace_with_retry

        problem = self._receipts_problem()
        if problem:  # refuse before acting: never rebind without a receipt
            return self._refuse_unreadable("rebind", problem)
        self._check_mode(allow_rebind=True)
        marker_path = self.root / MARKER
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        with self.serial():
            marker.update(_identity(self.root))
            marker["rebound_at"] = utc_now_z()
            tmp = marker_path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                replace_with_retry(tmp, marker_path)
            except BaseException:
                tmp.unlink(missing_ok=True)  # leave no half-done marker behind
                raise
            previous_version = next((r["lib_version"] for r in reversed(self.receipts()) if r.get("lib_version")), None)
            # Rebinding does not verify content or complete a version upgrade.
            # Keep the earlier version persistent until validation succeeds.
            receipt = self._append({"op": "rebind", "result": "rebound", "operator": operator,
                                    "lib_version": previous_version or self.lib_version})
        # The binding and its audit receipt precede any ordinary content read.
        # Keep the earlier version visible despite the new rebind receipt.
        self._check_version(previous_version=previous_version)
        return receipt

    # -- recall (design s8) ---------------------------------------------------------

    @staticmethod
    def _replay_times(admitted_before: str | None, now: str | None) -> tuple[datetime, datetime]:
        admission = _parse_clock(admitted_before, "admitted_before")
        if admission > datetime.now(timezone.utc) + timedelta(seconds=ADMISSION_FUTURE_SKEW_SECONDS):
            raise GuardRefused("admitted_before_future")
        clock = _parse_clock(now, "now")
        if admission > clock:
            raise GuardRefused("admitted_before_after_clock")
        return admission, clock

    def _decision_point(self, decision_point_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", str(decision_point_id or "")):
            raise RecallRefused("decision_point_id is invalid")
        path = self.cfg.decision_points_dir / f"{decision_point_id}.json"
        try:
            dp = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RecallRefused(f"decision-point file unreadable: {type(exc).__name__}") from exc
        mode, family, as_of = dp.get("mode"), dp.get("family_code"), dp.get("as_of_utc")
        if mode not in MODES or not isinstance(family, str) or not FAMILY_RE.match(family.strip()):
            raise RecallRefused("decision-point file lacks mode or a valid family_code")
        try:
            parse_utc_z(as_of, "as_of_utc")
        except ValueError as exc:
            raise RecallRefused(str(exc)) from exc
        return dp

    @staticmethod
    def _replay(receipts: list[dict], until: datetime | None, *,
                experience: bool = False) -> tuple[dict, set]:
        """(state by id, vetoed ids) from receipts up to ``until`` (all when None)."""
        state: dict[str, dict] = {}
        vetoed: set[str] = set()
        for r in sorted(receipts, key=lambda x: x.get("seq", 0)):
            op, result, item_id = r.get("op"), r.get("result"), r.get("item_id")
            if op in ("veto_retire", "veto_reject") and result in ("retired", "tombstoned"):
                vetoed.add(item_id)  # the Owner's veto hides a card at every time
            event_time = (r.get("admitted_before", r["ts"])
                          if experience and op == "admit" and result == "admitted" else r["ts"])
            if until is not None and parse_utc_z(event_time, "event_time") >= until:
                continue
            if op == "admit" and result == "admitted":
                state[item_id] = {"active": True, "receipt": r}
            elif op in ("retire", "veto_retire") and result == "retired" and item_id in state:
                state[item_id]["active"] = False
            elif op == "restore" and result == "restored" and item_id in state:
                state[item_id]["active"] = True
        return state, vetoed

    def _library_disagrees(self, item_id: str, row: dict, rows: dict, latest: dict) -> bool:
        """The latest receipts say active but the library says retired or rejected.

        Catches a lost tail of the receipt file and a retire made outside the valve.
        Compared with the latest receipt state, not the state at admitted_before, so a
        card the caller retired later stays visible to replays of earlier decisions.
        """
        from . import tombstones as _tombstones

        lib_active = (row.get("status") or "active") == "active" and item_id in rows
        return bool(latest.get(item_id, {}).get("active")) and (
            not lib_active or _tombstones.by_id(self.root, item_id) is not None)

    @staticmethod
    def _effective_cuts(mode: str, as_of: datetime, evidence_before: datetime,
                        admitted_before: datetime) -> tuple[datetime, datetime]:
        """Never recall past the decision point's own as_of_utc, whatever the caller passes.

        Evidence is cut at the earlier of evidence_before and as_of_utc in every mode. A
        live decision also cuts admission there; replay and test keep the run-time
        admission clock (design s8), their history comes from evidence_before.
        """
        evidence_cut = min(evidence_before, as_of)
        admission_cut = min(admitted_before, as_of) if mode == "live" else admitted_before
        return evidence_cut, admission_cut

    @staticmethod
    def _hash_ok(row: dict, admit_receipt: dict) -> bool:
        return content_hash(row) == admit_receipt.get("content_sha256")

    def recall(self, decision_point_id: str, round_id: str, *, evidence_before: str, admitted_before: str,
               extra_exclude_families: Iterable[str] = (), subject_ids: Iterable[str] | None = None,
               query: str | None = None, limit: int = 8, now: str | None = None) -> dict:
        from . import tombstones as _tombstones

        problem = self._receipts_problem()
        if problem:
            self._refuse_unreadable("recall", problem)
            raise RecallRefused(f"receipts_unreadable: {problem}")
        dp = self._decision_point(decision_point_id)
        self._check_mode()
        experience = self.mode == REPLAY_EXPERIENCE
        if experience:
            try:
                logical_admission, clock = self._replay_times(admitted_before, now)
            except GuardRefused as exc:
                _append_refusal(self.receipts_dir, exc)
                raise
        elif now is not None:
            raise GuardRefused("replay_parameters_not_supported")
        ev_before, adm_before = self._effective_cuts(
            dp["mode"], parse_utc_z(dp["as_of_utc"], "as_of_utc"),
            parse_utc_z(evidence_before, "evidence_before"),
            logical_admission if experience else parse_utc_z(admitted_before, "admitted_before"))
        if experience:
            adm_before = logical_admission
            if ev_before > clock:
                exc = GuardRefused("evidence_after_clock")
                _append_refusal(self.receipts_dir, exc)
                raise exc
        exclude = {_family_key(f) for f in extra_exclude_families}
        if not experience and dp["mode"] in MODES_EXCLUDING_OWN_FAMILY:
            exclude.add(_family_key(dp["family_code"]))
        wanted_subjects = {str(m) for m in subject_ids} if subject_ids is not None else None
        terms = [t for t in str(query or "").casefold().split() if t]
        empty_query = query is not None and not terms  # design s8: an empty query returns nothing
        excluded: dict[str, int] = {}

        def _skip(reason: str) -> None:
            excluded[reason] = excluded.get(reason, 0) + 1

        with self.serial():
            receipts = self.receipts()
            at_time, vetoed = self._replay(receipts, adm_before, experience=experience)
            latest, _ = self._replay(receipts, None)
            eng = self._engram(read_only=True)
            rows = {r.get("id"): r for r in self._rows(eng)}
            picked = []
            for item_id, st in at_time.items():
                if not st["active"]:
                    continue
                if item_id in vetoed:
                    _skip("owner_veto")
                    continue
                admit_r = st["receipt"]
                if parse_utc_z(admit_r["evidence_as_of"], "evidence_as_of") >= ev_before:
                    _skip("evidence_after")
                    continue
                if _family_key(admit_r.get("source_family")) in exclude:
                    _skip("family_excluded")
                    continue
                if wanted_subjects is not None and admit_r.get("subject_id") not in wanted_subjects:
                    continue
                row = rows.get(item_id) or self._archived_raw(eng, item_id)
                if row is None:
                    _skip("missing_in_library")
                    continue
                if self._library_disagrees(item_id, row, rows, latest):
                    _skip("receipt_library_mismatch")
                    continue
                if not self._hash_ok(row, admit_r):
                    _skip("hash_mismatch")
                    continue
                if empty_query:
                    continue
                if terms:
                    text = f"{row.get('summary', '')} {row.get('detail', '')}".casefold()
                    if not all(t in text for t in terms):
                        continue
                picked.append((row.get("evidence_as_of", ""), item_id, row))
            picked.sort(key=lambda x: (x[0], x[1]), reverse=True)
            items = [
                {"id": item_id, "subject_id": row.get("subject_id"), "summary": row.get("summary"),
                 "detail": row.get("detail"), "evidence_as_of": row.get("evidence_as_of"),
                 "source_family": row.get("source_family"),
                 **({"store_mode": self.mode} if experience else {}),
                 "admitted_round": row.get("admitted_round")}
                for _ev, item_id, row in picked[:max(0, int(limit))]
            ]
            self._append({
                "op": "recall", "round_id": str(round_id), "result": "recalled",
                "decision_point_id": decision_point_id, "decision_point_kind": dp.get("kind"),
                "decision_point_mode": dp["mode"], "query_sha256": _sha256_bytes(str(query or "").encode("utf-8")),
                "evidence_before": evidence_before, "admitted_before": admitted_before,
                "effective_evidence_before": ev_before.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "effective_admitted_before": adm_before.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                **({"clock": clock.strftime("%Y-%m-%dT%H:%M:%S.%fZ")} if experience else {}),
                "returned_ids": [i["id"] for i in items], "excluded": excluded,
            })
        return {"items": items, "excluded": excluded, "mode": dp["mode"],
                **({"store_mode": self.mode} if experience else {}),
                "excluded_families": sorted(exclude),
                "effective_evidence_before": ev_before.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "effective_admitted_before": adm_before.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}

    # -- per-round reconciliation (design s6) --------------------------------------------

    def reconcile(self) -> dict:
        """Problems between the library and the receipts (empty list = clean)."""
        problem = self._receipts_problem()
        if problem:
            return {"problems": [f"receipts_unreadable:{problem}"], "receipts": None, "rows": None,
                    "state_sha256": self.state_hash()}
        receipts = self.receipts()
        problems: list[str] = []
        prev = None
        for r in receipts:
            if prev is not None and (r.get("seq") != prev["seq"] + 1 or r.get("prev_sha256") != _sha256_json(prev)):
                problems.append(f"receipt_chain_break:{r.get('seq')}")
            prev = r
        admitted = {r["item_id"]: r["content_sha256"] for r in receipts
                    if r.get("op") == "admit" and r.get("result") == "admitted"}
        moved = {r["item_id"] for r in receipts if r.get("result") in ("retired", "archived")}
        vetoed = {r["item_id"] for r in receipts if r.get("op") == "veto_reject" and r.get("result") == "tombstoned"}
        eng = self._engram(read_only=True)
        for row in self._rows(eng):
            item_id = row.get("id")
            if item_id not in admitted:
                problems.append(f"row_without_receipt:{item_id}")
            elif content_hash(row) != admitted[item_id]:
                problems.append(f"content_changed:{item_id}")
        decisions = eng._read_entries(eng._knowledge_dir / "decisions.json", "decision", migrate=False)
        problems += [f"decision_present:{d.get('id')}" for d in decisions]
        for row in eng._read_overflow_archive("lesson"):
            if eng._is_snapshot_record(row):
                continue
            if row.get("id") not in moved:
                problems.append(f"archived_without_receipt:{row.get('id')}")
        from . import tombstones as _tombstones

        for stone in _tombstones.load(self.root):
            stone_id = stone.get("id")
            if not isinstance(stone_id, str) or stone_id not in vetoed:
                problems.append(f"tombstone_without_veto:{stone_id}")
        for sub in ("playbooks", "projects"):
            d = self.root / sub
            if d.is_dir() and any(p.is_file() and not p.name.startswith(".") for p in d.rglob("*")):
                problems.append(f"{sub}_not_empty")
        identity = self.root / "identity"
        if identity.is_dir():
            extra = [p.name for p in identity.rglob("*") if p.is_file()
                     and p.name not in ("trust_boundaries.json",) and not p.name.startswith(".")]
            problems += [f"identity_unexpected:{n}" for n in extra]
        receipt_pids = {r.get("pid") for r in receipts}
        audit = self.root / "audit.log"
        if audit.is_file():
            for line in audit.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("action") in ("write", "archive", "delete", "import") and \
                        entry.get("pid") not in receipt_pids and entry.get("action") != "owner_cli":
                    problems.append(f"write_by_unknown_pid:{entry.get('pid')}")
        return {"problems": sorted(set(problems)), "receipts": len(receipts),
                "rows": len(self._rows(eng)), "state_sha256": self.state_hash()}


# ---------------------------------------------------------------------------
# init and refusal receipts
# ---------------------------------------------------------------------------


def _append_refusal(receipts_dir: Path, exc: GuardRefused, *, op: str = "open") -> None:
    """Record a guard refusal next to (not inside) the receipt chain."""
    if getattr(exc, "_audited", False):
        return
    receipts_dir.mkdir(parents=True, exist_ok=True)
    with (receipts_dir / REFUSALS_FILE).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"op": op, "result": "guard_refused", "code": exc.code,
                             "ts": utc_now_z(), "pid": os.getpid()}, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    exc._audited = True


def init_root(cfg: Config | None = None) -> IsolatedStore:
    """Create a new root in an EMPTY directory: marker, pinned limits, reconcile off."""
    cfg = cfg or Config.from_env()
    deny = cfg.deny_list()
    receipts_real = check_candidate(cfg.receipts_dir, deny, label="receipts_dir")
    root = cfg.root
    if root.name.lower() in (".engram", ".piia"):
        raise GuardRefused("guard_init_legacy_name", "never initialise a .engram or .piia directory")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise GuardRefused("guard_init_not_empty", "initialise only an empty directory")
    root_real = check_candidate(root, deny, label="root")
    if _within(receipts_real, root_real) or _within(root_real, receipts_real):
        raise GuardRefused("guard_receipts_in_root")
    check_environment(cfg)
    if not _same_as_root(os.environ.get("ENGRAM_DIR", ""), cfg.root):
        raise GuardRefused("guard_engram_dir_mismatch")
    root.mkdir(parents=True, exist_ok=True)
    root_id = _root_id(root)
    marker = {"purpose": "isolated-store", "mode": cfg.mode, "receipts_dir": receipts_real, "root_id": root_id,
              "created_at": utc_now_z(), **_identity(root)}
    replay_provenance = {}
    if cfg.mode == REPLAY_EXPERIENCE:
        signal = root / REPLAY_MARKER_FILE
        signal.write_text("store_mode: replay_experience\n", encoding="utf-8")
        replay_provenance = {"replay_marker": REPLAY_MARKER_FILE,
                             "replay_marker_sha256": _sha256_bytes(signal.read_bytes())}
    (root / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / LIMITS_FILE).write_text(json.dumps(cfg.limits, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "telemetry_config.json").write_text(json.dumps({"reconcile_authorized": False}), encoding="utf-8")
    # Pin the mode in the init ledger before exposing any opened handle.
    # Bootstrap only within this initializer; public constructors always verify
    # an existing initialization receipt before attaching.
    pr = object.__new__(IsolatedStore)
    pr._configure(cfg, root_real, receipts_real, deny)
    with pr.serial():
        pr._append({"op": "init", "result": "initialised", "store_mode": cfg.mode, "root_id": root_id,
                    **replay_provenance})
    return IsolatedStore.open(cfg)


# ---------------------------------------------------------------------------
# child-side commands (run by isolated_store_launch inside the cleaned process)
# ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m piia_engram.isolated_store init|reconcile|veto-retire ID OP|"
              "veto-reject ID OP|clear-latch OP|rebind OP", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "init":
            out: Any = {"result": "initialised", "root_state_sha256": init_root().state_hash()}
        elif cmd == "reconcile":
            out = IsolatedStore.open().reconcile()
        elif cmd == "veto-retire" and len(rest) == 2:
            out = IsolatedStore.open().owner_retire(rest[0], rest[1])
        elif cmd == "veto-reject" and len(rest) == 2:
            out = IsolatedStore.open().owner_reject(rest[0], rest[1])
        elif cmd == "clear-latch" and len(rest) == 1:
            out = IsolatedStore.open(allow_strict_latch=True).owner_clear_latch(rest[0])
        elif cmd == "rebind" and len(rest) == 1:
            out = IsolatedStore.open(allow_rebind=True).owner_rebind(rest[0])
        else:
            print(f"unknown or incomplete command: {cmd}", file=sys.stderr)
            return 2
    except GuardRefused as exc:
        print(json.dumps({"result": "guard_refused", "code": exc.code, "detail": exc.detail}))
        return 3
    except ReceiptsUnreadable as exc:
        print(json.dumps({"result": "receipts_unreadable", "detail": str(exc)}))
        return 4
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 4 if isinstance(out, dict) and out.get("result") == "receipts_unreadable" else 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
