"""Daily anonymous usage counts -- on by default, same switches as the daily ping.

Once a UTC day has ended, the next MCP session sends one row for it: which tools
were used and roughly how often (buckets, never exact numbers), error categories
(buckets), rough memory-size buckets, the Engram version, the OS family, the
Python major.minor, the tool tier and a coarse install-age bucket. The row
carries a daily rotating ID, not the install ID of the ping. Nothing else is
sent: no content, prompts, arguments, file paths or error text.

Off whenever the daily ping is off (``usage_ping.decision``: DO_NOT_TRACK /
NO_TELEMETRY, ENGRAM_TELEMETRY=0, ``engram telemetry off``, an earlier opt-out,
CI, containers, ENGRAM_EPHEMERAL, tests), and also with ENGRAM_TELEMETRY_REMOTE=0
or any explicit ``false`` in a store's ``telemetry_config.json``. Only an
``engram telemetry on`` / ``remote on`` from a version that has these counts
(it records a consent marker) overrides such a refusal.

Counts wait in ``stats-spool/`` under the per-user config directory (never in a
memory store), one small file per process and day. Nothing here raises, writes
to stdout or blocks a tool: the send runs in a daemon thread.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from . import usage_ping
from .atomic_replace import replace_with_retry

SCHEMA = "stats/2"
TIMEOUT_SECONDS = 3
MAX_BODY_BYTES = 8192
MAX_SPOOL_FILES = 200
MAX_AGE_DAYS = 7
MAX_DAYS_PER_RUN = 2
GONE_QUIET_DAYS = 30  # after a 410 from the endpoint
FLUSH_EVERY = 10
OTHER_TOOL = "other"

_SPOOL_DIR = "stats-spool"
_SECRET_FILE = "stats_secret"
_STATE_FILE = "usage_stats.json"
_OFF_VALUES = ("0", "false", "off", "no")

COUNT_BUCKETS = ("0", "1_4", "5_9", "10_24", "25_99", "100_plus")
SIZE_BUCKETS = ("0", "1_9", "10_49", "50_199", "200_999", "1000_plus")
TIERS = frozenset({"core", "all", "other"})
INSTALL_AGES = frozenset({"first_day", "2_7_days", "8_30_days", "31_plus_days", "unknown"})
ADOPTIONS = frozenset({"first", "same", "upgrade", "downgrade", "changed"})
ERROR_CATEGORIES = frozenset({"timeout", "validation", "io", "permission", "network", "unknown"})
KNOWLEDGE_KEYS = frozenset({"lessons", "decisions", "domains"})
FIELDS = frozenset({"schema", "daily_id", "date", "version", "os", "python", "tier",
                    "install_age", "adoption", "tools", "errors", "knowledge"})

_DAILY_ID_RE = re.compile(r"[0-9a-f]{16}")
_SECRET_RE = re.compile(r"[0-9a-f]{64}")
_SPOOL_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-\d+-[0-9a-f]{8}\.json")

_lock = threading.Lock()
_started = False  # one send attempt per process
_suffix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_pending: dict[str, dict[str, Any]] = {}  # day -> this process's counts
_unflushed = 0


_tool_names: frozenset[str] | None = None


def tool_names() -> frozenset[str]:
    """The registered MCP tool names; any other name is counted as ``other``."""
    global _tool_names
    if _tool_names is None:
        try:
            from .tool_annotations import TOOL_ANNOTATIONS

            _tool_names = frozenset(TOOL_ANNOTATIONS)
        except Exception:
            return frozenset()
    return _tool_names


def count_bucket(n: Any) -> str:
    n = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0
    for limit, label in ((0, "0"), (4, "1_4"), (9, "5_9"), (24, "10_24"), (99, "25_99")):
        if n <= limit:
            return label
    return "100_plus"


def size_bucket(n: Any) -> str:
    n = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0
    for limit, label in ((0, "0"), (9, "1_9"), (49, "10_49"), (199, "50_199"), (999, "200_999")):
        if n <= limit:
            return label
    return "1000_plus"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _day_offset(day: str, delta: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


def endpoint() -> str:
    """ENGRAM_TELEMETRY_URL, else the built-in default; empty means never send."""
    try:
        from . import telemetry

        return telemetry.get_endpoint()
    except Exception:
        return ""


def _explicit_false_in_a_store() -> bool:
    """Any explicit ``false`` for the statistics or their remote sending, with or
    without a timestamp: it is read as a refusal."""
    for path in usage_ping._legacy_config_paths():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(cfg, dict) and (cfg.get("enabled") is False
                                      or cfg.get("remote_enabled") is False):
            return True
    return False


def _consented() -> bool:
    """True after an ``engram telemetry on`` / ``remote on`` that covered the counts.

    ``enabled: true`` alone is not enough: versions before the counts wrote it
    for an ``on`` that turned on the ping and the local log only.
    """
    settings = usage_ping._load_settings()
    return (settings.get("enabled") is True
            and settings.get("counts_consent") == usage_ping.COUNTS_CONSENT)


def decision() -> tuple[bool, str]:
    """(on, deciding layer). Every layer that turns the daily ping off wins first."""
    on, layer = usage_ping.decision()
    if not on:
        return False, layer
    if os.environ.get("ENGRAM_TELEMETRY_REMOTE", "").strip().lower() in _OFF_VALUES:
        return False, "ENGRAM_TELEMETRY_REMOTE"
    if _explicit_false_in_a_store() and not _consented():
        return False, "earlier opt-out"
    return True, "default"


# --- recording (in memory, then one spool file per process and day) -----------


def _tier() -> str:
    raw = os.environ.get("ENGRAM_TOOLS", "").strip().lower()
    if raw in ("", "core"):
        return "core"
    return "all" if raw == "all" else "other"


def _day_counts(day: str) -> dict[str, Any]:
    entry = _pending.get(day)
    if entry is None:
        entry = _pending[day] = {"tools": {}, "errors": {}}
    return entry


def record(tool_name: Any, success: bool = True, error_category: Any = None) -> None:
    """Count one tool call in memory. Never raises, never touches the disk
    except through the periodic :func:`flush`."""
    global _unflushed
    try:
        name = tool_name if isinstance(tool_name, str) and tool_name in tool_names() else OTHER_TOOL
        category = error_category if error_category in ERROR_CATEGORIES else "unknown"
        with _lock:
            entry = _day_counts(_today())
            pair = entry["tools"].setdefault(name, [0, 0])
            pair[0 if success else 1] += 1
            if not success:
                entry["errors"][category] = entry["errors"].get(category, 0) + 1
            _unflushed += 1
            due = _unflushed >= FLUSH_EVERY
        if due:
            flush()
    except Exception:
        pass


def note_knowledge(counts: Any) -> None:
    """Remember today's memory-size buckets (the exact numbers are not kept)."""
    try:
        if not isinstance(counts, dict):
            return
        buckets = {key: size_bucket(counts[key]) for key in sorted(KNOWLEDGE_KEYS)
                   if isinstance(counts.get(key), int) and not isinstance(counts.get(key), bool)}
        if buckets:
            with _lock:
                _day_counts(_today())["knowledge"] = buckets
    except Exception:
        pass


def _spool_dir() -> Path:
    return usage_ping.state_dir() / _SPOOL_DIR


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def flush() -> None:
    """Write this process's counts to its spool files. While the counts are off
    nothing is written and the in-memory counts are dropped. Never raises."""
    global _unflushed
    try:
        from piia_engram import __version__

        with _lock:
            _unflushed = 0
            if not _pending:
                return
            if not decision()[0]:
                _pending.clear()
                return
            today = _today()
            snapshot = {day: json.loads(json.dumps(entry)) for day, entry in _pending.items()}
            for day in [d for d in _pending if d != today]:
                del _pending[day]  # an ended day gets its last write below
        tier = _tier()
        for day, entry in snapshot.items():
            body = {"day": day, "version": __version__, "tier": tier,
                    "tools": entry["tools"], "errors": entry["errors"]}
            if entry.get("knowledge"):
                body["knowledge"] = entry["knowledge"]
            _write_json(_spool_dir() / f"{day}-{_suffix}.json", body)
    except Exception:
        pass


# --- building the row ------------------------------------------------------------


def _spool_files() -> dict[str, list[Path]]:
    """Spool files by day (names that do not match the pattern are ignored)."""
    out: dict[str, list[Path]] = {}
    try:
        for path in sorted(_spool_dir().iterdir()):
            match = _SPOOL_NAME_RE.fullmatch(path.name)
            if match:
                out.setdefault(match.group(1), []).append(path)
    except Exception:
        pass
    return out


def _delete(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def purge_spool() -> int:
    """Remove every waiting count (files and this process's memory). Used when
    telemetry is turned off. Returns the number of files removed; never raises."""
    removed = 0
    try:
        with _lock:
            _pending.clear()
        for paths in _spool_files().values():
            for path in paths:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def _nonneg(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _merge(paths: list[Path]) -> dict[str, Any]:
    """Sum one day's spool files. Unknown tool names fold into ``other``."""
    names = tool_names()
    tools: dict[str, list[int]] = {}
    errors: dict[str, int] = {}
    knowledge: dict[str, str] = {}
    version = tier = ""
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for name, pair in (data.get("tools") or {}).items() if isinstance(data.get("tools"), dict) else ():
            if not (isinstance(pair, list) and len(pair) == 2):
                continue
            key = name if name in names else OTHER_TOOL
            slot = tools.setdefault(key, [0, 0])
            slot[0] += _nonneg(pair[0])
            slot[1] += _nonneg(pair[1])
        for cat, n in (data.get("errors") or {}).items() if isinstance(data.get("errors"), dict) else ():
            key = cat if cat in ERROR_CATEGORIES else "unknown"
            errors[key] = errors.get(key, 0) + _nonneg(n)
        if isinstance(data.get("knowledge"), dict):
            for key, bucket in data["knowledge"].items():
                if key in KNOWLEDGE_KEYS and bucket in SIZE_BUCKETS:
                    knowledge[key] = bucket
        if isinstance(data.get("version"), str):
            version = data["version"]
        if isinstance(data.get("tier"), str):
            tier = data["tier"]
    return {"tools": tools, "errors": errors, "knowledge": knowledge,
            "version": version, "tier": tier}


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads((usage_ping.state_dir() / _STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        _write_json(usage_ping.state_dir() / _STATE_FILE, state)
    except Exception:
        pass


def _secret(create: bool = True) -> str | None:
    """A random key that stays on this machine; the daily ID is derived from it."""
    try:
        path = usage_ping.state_dir() / _SECRET_FILE
        try:
            value = path.read_text(encoding="utf-8").strip()
        except Exception:
            value = ""
        if _SECRET_RE.fullmatch(value):
            return value
        if not create:
            return None
        value = uuid.uuid4().hex + uuid.uuid4().hex
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():  # present but not a key: replace it, owner-only
            try:
                path.unlink()
            except OSError:
                return None
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Another process is creating it: use that key, not a second one.
            for _ in range(5):
                try:
                    existing = path.read_text(encoding="utf-8").strip()
                except Exception:
                    existing = ""
                if _SECRET_RE.fullmatch(existing):
                    return existing
                time.sleep(0.02)
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        return value
    except Exception:
        return None


def daily_id(secret: str, day: str) -> str:
    return hmac.new(secret.encode(), f"{SCHEMA}:{day}".encode(), hashlib.sha256).hexdigest()[:16]


def _install_age(first_seen: Any, day: str) -> str:
    try:
        age = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(first_seen, "%Y-%m-%d")).days
    except Exception:
        return "unknown"
    if age < 1:
        return "first_day"
    if age < 8:
        return "2_7_days"
    return "8_30_days" if age < 31 else "31_plus_days"


def _adoption(version: str, previous: Any) -> str:
    try:
        from .telemetry import _version_adoption

        value = _version_adoption(version, previous if isinstance(previous, str) else None)
        return value if value in ADOPTIONS else "changed"
    except Exception:
        return "changed"


def _bucket_map(value: Any, keys: frozenset[str], buckets: tuple[str, ...]) -> bool:
    return (isinstance(value, dict)
            and all(k in keys and isinstance(v, str) and v in buckets for k, v in value.items()))


def valid_payload(payload: Any) -> bool:
    """Whole-row check: exactly the documented fields, every value from a closed
    set or a fixed format. A row that fails is not sent."""
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        return False
    names = tool_names() | {OTHER_TOOL}
    tools = payload["tools"]
    if not isinstance(tools, dict) or not tools:
        return False
    for name, pair in tools.items():
        if name not in names or not isinstance(pair, dict) or set(pair) != {"ok", "err"}:
            return False
        if pair["ok"] not in COUNT_BUCKETS or pair["err"] not in COUNT_BUCKETS:
            return False
    scalars = ("schema", "daily_id", "date", "version", "os", "python", "tier",
               "install_age", "adoption")
    if not all(isinstance(payload[key], str) for key in scalars):
        return False
    return bool(
        payload["schema"] == SCHEMA
        and _DAILY_ID_RE.fullmatch(payload["daily_id"])
        and usage_ping._DATE_RE.fullmatch(payload["date"])
        and usage_ping._VERSION_RE.fullmatch(payload["version"])
        and payload["os"] in usage_ping._OS_VALUES
        and usage_ping._PYTHON_RE.fullmatch(payload["python"])
        and payload["tier"] in TIERS
        and payload["install_age"] in INSTALL_AGES
        and payload["adoption"] in ADOPTIONS
        and _bucket_map(payload["errors"], ERROR_CATEGORIES, COUNT_BUCKETS)
        and _bucket_map(payload["knowledge"], KNOWLEDGE_KEYS, SIZE_BUCKETS)
    )


def build_payload(day: str, merged: dict[str, Any], *, secret: str,
                  state: dict[str, Any]) -> dict[str, Any] | None:
    """The row for one ended day, or None when it would not pass the check."""
    payload = {
        "schema": SCHEMA,
        "daily_id": daily_id(secret, day),
        "date": day,
        "version": merged.get("version") or "",
        "os": usage_ping._os_family(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "tier": merged.get("tier") if merged.get("tier") in TIERS else "other",
        "install_age": _install_age(state.get("first_seen"), day),
        "adoption": _adoption(merged.get("version") or "", state.get("last_version")),
        "tools": {name: {"ok": count_bucket(pair[0]), "err": count_bucket(pair[1])}
                  for name, pair in sorted(merged.get("tools", {}).items())
                  if pair[0] or pair[1]},
        "errors": {cat: count_bucket(n) for cat, n in sorted(merged.get("errors", {}).items()) if n},
        "knowledge": dict(sorted(merged.get("knowledge", {}).items())),
    }
    return payload if valid_payload(payload) else None


# --- sending -------------------------------------------------------------------


def _post(url: str, payload: dict[str, Any]) -> str:
    """'sent', 'gone' (the server answered 410), 'too_large' (not sent) or 'failed'."""
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            return "too_large"
        req = Request(url, data=body, method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": "piia-engram"})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            final_url = getattr(resp, "url", None)
            if final_url is None:
                geturl = getattr(resp, "geturl", None)
                final_url = geturl() if callable(geturl) else url
            # Only our own endpoint's answer counts; a redirect elsewhere is not "sent".
            if final_url == url and 200 <= int(getattr(resp, "status", 0)) < 300:
                return "sent"
    except Exception as exc:
        if getattr(exc, "code", None) == 410:
            return "gone"
    return "failed"


def _prune(files: dict[str, list[Path]], today: str) -> dict[str, list[Path]]:
    """Drop days older than a week and, past the file cap, the oldest files."""
    oldest = _day_offset(today, -MAX_AGE_DAYS)
    for day in [d for d in files if d < oldest]:
        _delete(files.pop(day))
    ordered = [path for day in sorted(files) for path in files[day]]
    extra = len(ordered) - MAX_SPOOL_FILES
    if extra > 0:
        doomed = set(ordered[:extra])
        _delete(list(doomed))
        files = {day: [p for p in paths if p not in doomed] for day, paths in files.items()}
        files = {day: paths for day, paths in files.items() if paths}
    return files


def _gone_quiet(state: dict[str, Any], today: str) -> bool:
    """True while a 410 from the endpoint is less than thirty days old."""
    gone = state.get("counts_endpoint_gone_at")
    if not isinstance(gone, str) or not usage_ping._DATE_RE.fullmatch(gone):
        return False
    try:
        return today < _day_offset(gone, GONE_QUIET_DAYS)
    except Exception:
        return False


def drain(after: threading.Thread | None = None) -> int:
    """Send the oldest ended days that have counts (at most two). Returns the
    number of rows sent. Runs in the background thread; never raises."""
    sent = 0
    try:
        if after is not None:
            after.join(usage_ping.TIMEOUT_SECONDS + 1)  # the ping goes first
        on, layer = decision()
        if not on:
            if layer not in ("ci", "container", "test"):
                # The user said no: counts that were waiting are removed, not kept.
                _delete([p for paths in _spool_files().values() for p in paths])
            return 0
        today = _today()
        files = _prune(_spool_files(), today)
        url = endpoint()
        state = _load_state()
        if not state.get("first_seen"):
            state["first_seen"] = min([*files, today])
            _save_state(state)
        if not url or today in (state.get("last_sent"), state.get("last_attempt")):
            return 0  # one try per UTC day, whatever its outcome
        if _gone_quiet(state, today):
            return 0
        secret = _secret()
        if not secret:
            return 0
        ended = [d for d in sorted(files) if d < today]
        tried = False
        for day in ended[:MAX_DAYS_PER_RUN]:
            merged = _merge(files[day])
            payload = build_payload(day, merged, secret=secret, state=state)
            if payload is None:
                _delete(files[day])  # nothing sendable: do not keep it around
                continue
            result = _post(url, payload)
            if result == "too_large":
                _delete(files[day])  # dropped unsent, no request was made
                continue
            tried = True
            if result == "failed":
                break
            if result == "gone":
                # The endpoint was switched off: drop what was waiting and stay quiet.
                for waiting in ended:
                    _delete(files[waiting])
                state["counts_endpoint_gone_at"] = today
                break
            _delete(files[day])
            sent += 1
            state["last_version"] = payload["version"]
            state.pop("counts_endpoint_gone_at", None)
        if sent:
            state["last_sent"] = today
        if tried:
            state["last_attempt"] = today
            _save_state(state)
    except Exception:
        pass
    return sent


def maybe_send(after: threading.Thread | None = None) -> threading.Thread | None:
    """Start the background send once per process. Never raises."""
    global _started
    try:
        with _lock:
            if _started:
                return None
            _started = True
        if not decision()[0] and not _spool_files():
            return None
        thread = threading.Thread(target=drain, args=(after,), name="engram-usage-stats",
                                  daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


# --- status / preview ------------------------------------------------------------


def status() -> dict[str, Any]:
    on, layer = decision()
    files = _spool_files()
    today = _today()
    state = _load_state()
    return {"will_send": on, "decided_by": layer, "endpoint": endpoint(),
            "last_sent": str(state.get("last_sent") or ""),
            "pending_days": len([d for d in files if d < today]),
            "endpoint_gone_since": (str(state.get("counts_endpoint_gone_at") or "")
                                    if _gone_quiet(state, today) else "")}


def preview() -> str:
    """The next row as it would be sent (a sample when nothing is waiting).
    Creates neither the key nor any file."""
    today = _today()
    files = _spool_files()
    days = sorted(files)
    state = _load_state()
    payload = None
    if days:
        merged = _merge(files[days[0]])
        payload = build_payload(days[0], merged, secret=_secret(create=False) or "0" * 64,
                                state=state)
        if payload is not None and _secret(create=False) is None:
            payload["daily_id"] = "<derived on the first send>"
    if payload is None:
        from piia_engram import __version__

        payload = {
            "schema": SCHEMA, "daily_id": "<changes every day>", "date": today,
            "version": __version__, "os": usage_ping._os_family(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "tier": _tier(), "install_age": _install_age(state.get("first_seen"), today),
            "adoption": "same",
            "tools": {"get_resume_brief": {"ok": "1_4", "err": "0"},
                      "search_knowledge": {"ok": "5_9", "err": "0"}},
            "errors": {}, "knowledge": {"lessons": "10_49", "decisions": "1_9", "domains": "1_9"},
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)
