"""Which AI clients are connected to Engram, and do they actually call it?

Read-only report for ``engram doctor``:

* **configured** -- does the client's MCP config (the files ``engram setup``
  knows) hold an ``engram`` server entry? Only the client name, the status
  and the config file path (``~``-shortened) are reported; nothing from the
  entry itself (commands, env values, keys) is shown.
* **calls** -- local traces of MCP use, per client, over the last N days:

  - session checkpoints the MCP server writes itself (``contexts/<client>/auto-*.md``):
    one file per server session (``-cpN`` files are checkpoints of the same
    session). Only the file name, its modification time and the
    ``工具调用次数: N`` header counter are read -- never the recorded actions.
  - knowledge rows written over MCP (``provenance.origin == "mcp"``): only
    ``provenance.client`` and the creation time are read.

  Client names are the client's own claim (MCP ``clientInfo``), normalized
  to the closed labels of ``write_provenance``. Counts are a lower bound: a
  short session that never reached a checkpoint and wrote nothing leaves no
  trace.
* **strict approval** and **startup writes** -- one line each.

Nothing here writes: no store, no config file, no cache. A file that cannot
be read is skipped.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DAYS = 14

_AUTO_SESSION = re.compile(r"^(auto-.+?)(?:-cp\d+)?\.md$")
_CALL_COUNTER = re.compile(r"^工具调用次数:\s*(\d+)\s*$")
# A checkpoint header is short; never read past this many bytes of a file.
_HEADER_READ_LIMIT = 64 * 1024

SELF_REPORTED_NOTE = (
    "Client names are self-reported by each client (MCP clientInfo), not verified. "
    "Counts are a lower bound: a short session that never reached a checkpoint and "
    "wrote nothing leaves no trace."
)


def client_label(name: str) -> str:
    """The write_provenance label for a client or tool name (``claude_code`` -> ``claude_code``)."""
    from .write_provenance import client_label as _label

    raw = str(name or "").strip()
    if not raw:
        return "unknown"
    if raw.lower() in ("mcp_auto", "mcp-auto"):  # the server never learned the client's name
        return "unknown"
    label = _label(raw.replace("_", "-"))
    if label == "other":
        label = _label(raw)
    return label


def _short_path(path: Path, home: Path) -> str:
    """``~/...`` under the home directory; otherwise only the file name."""
    try:
        rel = Path(path).resolve().relative_to(home.resolve())
        return "~/" + rel.as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _servers(config: dict, server_key: str) -> dict:
    if not isinstance(config, dict):
        return {}
    servers = config.get(server_key, {})
    if isinstance(servers, dict) and servers:
        return servers
    other = {"mcpServers": "mcp_servers", "mcp_servers": "mcpServers"}.get(server_key)
    fallback = config.get(other, {}) if other else {}
    return fallback if isinstance(fallback, dict) else {}


def client_configs(home: Path | None = None) -> list[dict[str, Any]]:
    """Every client ``engram setup`` knows: not_installed | not_configured | configured."""
    from . import setup_wizard as W

    home = Path.home() if home is None else home
    rows: list[dict[str, Any]] = []
    for tool_id, cfg in W._tool_configs().items():
        fmt = cfg.get("format", "json")
        server_key = cfg.get("server_key", "mcpServers")
        installed = False
        configured_path: Path | None = None
        first_path: Path | None = None
        for raw_path in cfg.get("config_paths", []):
            path = Path(raw_path)
            if not path.parent.exists():
                continue
            installed = True
            first_path = first_path or path
            if path.is_file() and "engram" in _servers(W._read_mcp_config(path, fmt=fmt), server_key):
                configured_path = path
                break
        status = "configured" if configured_path else ("not_configured" if installed else "not_installed")
        shown = configured_path or first_path
        rows.append({
            "tool_id": tool_id,
            "name": cfg.get("name", tool_id),
            "client": client_label(tool_id),
            "config_status": status,
            "config_path": _short_path(shown, home) if shown else "",
        })
    return rows


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # naive: local time
    return moment.timestamp()


def _read_json_quietly(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _rows_of(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("items", "lessons", "decisions"):
            if isinstance(data.get(key), list):
                return [row for row in data[key] if isinstance(row, dict)]
    return []


def _session_calls(path: Path) -> int:
    """The largest ``工具调用次数: N`` counter in a checkpoint file (0 when none)."""
    best = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(_HEADER_READ_LIMIT)
    except OSError:
        return 0
    for line in text.splitlines():
        match = _CALL_COUNTER.match(line.strip())
        if match:
            best = max(best, int(match.group(1)))
    return best


def call_activity(root: Path, *, days: int = DEFAULT_DAYS, now: float | None = None) -> dict[str, dict]:
    """Per client label: sessions, calls (lower bound), writes and the last time seen."""
    root = Path(root)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    since = now - timedelta(days=max(int(days), 0)).total_seconds()
    activity: dict[str, dict] = {}

    def bucket(label: str) -> dict:
        return activity.setdefault(label, {"sessions": 0, "calls": 0, "writes": 0, "last_seen": None})

    def seen(entry: dict, moment: float) -> None:
        if entry["last_seen"] is None or moment > entry["last_seen"]:
            entry["last_seen"] = moment

    contexts = root / "contexts"
    if contexts.is_dir():
        for tool_dir in sorted(contexts.iterdir()):
            if not tool_dir.is_dir():
                continue
            sessions: dict[str, tuple[int, float]] = {}
            for path in tool_dir.iterdir():
                match = _AUTO_SESSION.match(path.name)
                if not match or not path.is_file():
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime < since:
                    continue
                calls, last = sessions.get(match.group(1), (0, 0.0))
                sessions[match.group(1)] = (max(calls, _session_calls(path)), max(last, mtime))
            if not sessions:
                continue
            entry = bucket(client_label(tool_dir.name))
            entry["sessions"] += len(sessions)
            for calls, last in sessions.values():
                entry["calls"] += calls
                seen(entry, last)

    knowledge_files = [root / "knowledge" / "lessons.json", root / "knowledge" / "decisions.json"]
    playbook_dir = root / "playbooks"
    if playbook_dir.is_dir():
        knowledge_files += [p for p in sorted(playbook_dir.glob("*.json")) if not p.name.startswith("_")]
    for path in knowledge_files:
        if not path.is_file():
            continue
        data = _read_json_quietly(path)
        rows = [data] if isinstance(data, dict) and "provenance" in data else _rows_of(data)
        for row in rows:
            prov = row.get("provenance")
            if not isinstance(prov, dict) or prov.get("origin") != "mcp":
                continue
            moment = _parse_time(prov.get("created_at")) or _parse_time(row.get("created_at"))
            if moment is None or moment < since:
                continue
            entry = bucket(str(prov.get("client") or "unknown"))
            entry["writes"] += 1
            seen(entry, moment)
    return activity


def strict_line(root: Path) -> dict[str, str]:
    from . import strict_mode

    env_strict = os.environ.get("ENGRAM_APPROVAL", "").strip().lower() == "strict"
    latched = (Path(root) / strict_mode.MARKER).is_file()
    if latched:
        return {"state": "on", "detail": (
            "on for every client: this store is latched by approval_mode.json "
            "(engram review strict-marker --clear leaves strict mode)")}
    if env_strict:
        return {"state": "on_here", "detail": (
            "ENGRAM_APPROVAL=strict in this shell; a client is strict only when its own "
            "MCP env block sets it (see MCP Client Env)")}
    return {"state": "off", "detail": "off (writes follow the default risk-based review)"}


def startup_line(root: Path) -> dict[str, Any]:
    from .reconcile import _reconcile_config_value

    def env(name: str) -> str:
        return str(os.environ.get(name, "") or "").strip()

    configured = _reconcile_config_value(root)
    reconcile = env("ENGRAM_RECONCILE")
    if not reconcile:
        reconcile_meaning = "unset"
    elif reconcile.lower() in ("0", "false", "off", "no"):
        reconcile_meaning = "other AI tools' files are never read"
    elif reconcile.lower() in ("1", "true", "on", "yes"):
        reconcile_meaning = "no effect at start; only engram import-memories imports"
    else:
        reconcile_meaning = "not a recognised value; ignored"
    if configured is True:
        authorized_meaning = "allows engram import-memories only; nothing at start"
    elif configured is False:
        authorized_meaning = "other AI tools' files are never read"
    else:
        authorized_meaning = "unset"
    sync = env("ENGRAM_MCP_STARTUP_SYNC")
    return {
        "state": "zero_write",
        "detail": "the MCP server imports nothing from other AI tools at start",
        "variables": {
            "ENGRAM_MCP_STARTUP_SYNC": {"value": sync, "meaning": "no effect" if sync else "unset"},
            "ENGRAM_RECONCILE": {"value": reconcile, "meaning": reconcile_meaning},
            "reconcile_authorized": {
                "value": "" if configured is None else str(bool(configured)).lower(),
                "meaning": authorized_meaning,
            },
        },
    }


def _verdict(config_status: str, active: dict | None, shared_label: bool) -> str:
    has_calls = bool(active) and not shared_label
    if config_status == "configured":
        if shared_label:
            return "configured_unattributed"
        return "connected" if has_calls else "configured_no_calls"
    if has_calls:
        return "calls_without_config"
    return config_status  # not_configured | not_installed


def build_report(root: Path, *, days: int = DEFAULT_DAYS, home: Path | None = None,
                 now: float | None = None) -> dict[str, Any]:
    """The whole connection report (JSON-ready)."""
    root = Path(root)
    configs = client_configs(home)
    activity = call_activity(root, days=days, now=now)
    label_counts: dict[str, int] = {}
    for row in configs:
        label_counts[row["client"]] = label_counts.get(row["client"], 0) + 1
    clients = []
    for row in configs:
        label = row["client"]
        shared = label in ("other", "unknown") or label_counts.get(label, 0) > 1
        active = activity.get(label)
        entry = dict(row)
        entry["verdict"] = _verdict(row["config_status"], active, shared)
        if active and not shared:
            entry.update({
                "sessions": active["sessions"], "calls": active["calls"], "writes": active["writes"],
                "last_seen": _iso(active["last_seen"]),
            })
        clients.append(entry)
    known = {row["client"] for row in configs if row["client"] not in ("other", "unknown")}
    unlisted = {
        label: {**info, "last_seen": _iso(info["last_seen"])}
        for label, info in sorted(activity.items()) if label not in known
    }
    return {
        "schema_version": 1,
        "days": int(days),
        "strict_approval": strict_line(root),
        "startup_writes": startup_line(root),
        "clients": clients,
        "other_activity": unlisted,
        "note": SELF_REPORTED_NOTE,
        "read_only": True,
    }


def _iso(moment: float | None) -> str:
    if moment is None:
        return ""
    return datetime.fromtimestamp(moment).astimezone().replace(microsecond=0).isoformat()


def _when(iso: str) -> str:
    return iso[:16].replace("T", " ") if iso else "?"


def render_text(report: dict[str, Any]) -> list[str]:
    """Doctor lines for the report (no leading indentation)."""
    days = report["days"]
    lines = [f"Strict approval: {report['strict_approval']['detail']}"]
    start = report["startup_writes"]
    parts = []
    for name, info in start["variables"].items():
        shown = f"{name}={info['value']}" if info["value"] else f"{name} unset"
        parts.append(shown if info["meaning"] == "unset" else f"{shown} ({info['meaning']})")
    lines.append(f"Startup writes: none -- {start['detail']}; " + "; ".join(parts))
    not_installed = []
    for row in report["clients"]:
        verdict = row["verdict"]
        label = f"{row['name']} ({row['client']})"
        if verdict == "not_installed":
            not_installed.append(row["name"])
        elif verdict == "connected":
            lines.append(
                f"[ok] {label}: connected, called in the last {days} days -- "
                f"{row['sessions']} session(s), {row['calls']}+ call(s), {row['writes']} write(s); "
                f"last {_when(row['last_seen'])}")
        elif verdict == "configured_no_calls":
            lines.append(
                f"[--] {label}: configured, no Engram calls in the last {days} days -- restart "
                f"{row['name']}, then check the engram entry in {row['config_path']}")
        elif verdict == "configured_unattributed":
            lines.append(
                f"[ok] {label}: configured; its calls cannot be told apart from other clients "
                "(no label of its own)")
        elif verdict == "calls_without_config":
            lines.append(
                f"[ok] {label}: called in the last {days} days (last {_when(row['last_seen'])}), but "
                "no engram entry in the config file engram setup knows; it may be configured elsewhere")
        else:
            lines.append(f"[--] {label}: not configured -- run 'engram setup' to connect it")
    for label, info in report.get("other_activity", {}).items():
        lines.append(
            f"[ok] {label}: {info['sessions']} session(s), {info['calls']}+ call(s), "
            f"{info['writes']} write(s) in the last {days} days; last {_when(info['last_seen'])}")
    if not_installed:
        lines.append("Not installed: " + ", ".join(not_installed))
    lines.append(report["note"])
    return lines
