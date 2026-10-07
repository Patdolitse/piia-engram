"""Claude Code: where its MCP servers live and how Engram is registered there.

Claude Code reads user-scope MCP servers from the top-level ``mcpServers`` of
its user config (``~/.claude.json``, or ``$CLAUDE_CONFIG_DIR/.claude.json``
when that variable is set) and local-scope servers from
``projects.<dir>.mcpServers`` in the same file. It does not read
``~/.claude/.mcp.json``; older Engram versions wrote their entry there.

Engram never writes the user config itself: it registers through the
``claude`` command (``claude mcp add --scope user ...``) and only reads the
file to tell whether an entry is already there. When the command is not
available, the caller prints the same command for the user to run.

``cli_path`` and ``run_cli`` are the only places that touch the ``claude``
executable; tests replace them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Callable

SERVER_NAME = "engram"
# Names an Engram entry may have: setup's name and the one the README used.
KNOWN_NAMES = ("engram", "piia-engram")
_MCP_SCRIPT = "piia-engram-mcp"
_MCP_MODULE = "piia_engram.mcp_server"
# ~/.claude.json grows with Claude Code's history; past this size it is not parsed.
MAX_USER_CONFIG_BYTES = 32 * 1024 * 1024
CLI_TIMEOUT_SECONDS = 60
# Env keys setup itself manages; other keys in an entry are the owner's own.
_SHOWN_ENV_KEYS = frozenset({
    "PYTHONIOENCODING", "ENGRAM_TOOLS", "PYTHONPATH", "ENGRAM_DIR", "ENGRAM_SEARCH",
    "ENGRAM_APPROVAL",
})


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------

def config_dir_override() -> Path | None:
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def config_dir() -> Path:
    """Claude Code's config directory (``~/.claude`` or ``$CLAUDE_CONFIG_DIR``)."""
    return config_dir_override() or Path.home() / ".claude"


def user_config_path() -> Path:
    """The file holding user- and local-scope MCP servers."""
    override = config_dir_override()
    return (override if override else Path.home()) / ".claude.json"


def user_config_label() -> str:
    """How reports name the user config: ``~/.claude.json`` or ``$CLAUDE_CONFIG_DIR/.claude.json``."""
    return "$CLAUDE_CONFIG_DIR/.claude.json" if config_dir_override() else "~/.claude.json"


def legacy_path() -> Path:
    """Where Engram setup used to write; Claude Code does not read this file."""
    return Path.home() / ".claude" / ".mcp.json"


LEGACY_LABEL = "~/.claude/.mcp.json"


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def is_installed() -> bool:
    """Claude Code leaves a config directory or user config behind (files only, no PATH lookup)."""
    return (
        _is_dir(config_dir())
        or _is_file(user_config_path())
        or _is_file(legacy_path())
    )


# ---------------------------------------------------------------------------
# reading (detection only)
# ---------------------------------------------------------------------------

def _stem(value: object) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    name = PurePath(text.replace("\\", "/")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def is_engram_entry(name: str, entry: object) -> bool:
    """An ``engram`` / ``piia-engram`` key, or a server that launches Engram's MCP server."""
    if name in KNOWN_NAMES:
        return True
    if not isinstance(entry, dict):
        return False
    if _stem(entry.get("command")) == _MCP_SCRIPT:
        return True
    args = entry.get("args")
    if isinstance(args, list):
        for arg in args:
            if _stem(arg) == _MCP_SCRIPT or str(arg).strip() == _MCP_MODULE:
                return True
    return False


def engram_names(servers: object) -> list[str]:
    """The keys of every Engram entry in one ``mcpServers`` object."""
    if not isinstance(servers, dict):
        return []
    return [str(name) for name, entry in servers.items() if is_engram_entry(str(name), entry)]


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


@dataclass
class UserConfigState:
    """What the user config says about Engram.

    status: ``missing`` (no file) | ``absent`` (no Engram entry) |
    ``configured`` | ``undetermined`` (too large or not readable as JSON).
    ``user_names``: Engram keys in the top-level (user scope) ``mcpServers``;
    ``local``: an Engram entry under some ``projects.<dir>``.
    ``user_entry``: the top-level ``engram`` entry, kept only for comparison.
    """

    status: str
    user_names: list[str] = field(default_factory=list)
    local: bool = False
    user_entry: dict | None = None


def read_user_config(path: Path | None = None) -> UserConfigState:
    path = user_config_path() if path is None else Path(path)
    if not _is_file(path):
        return UserConfigState("missing")
    try:
        if path.stat().st_size > MAX_USER_CONFIG_BYTES:
            return UserConfigState("undetermined")
    except OSError:
        return UserConfigState("undetermined")
    data = _read_json(path)
    if not isinstance(data, dict):
        return UserConfigState("undetermined")
    top = data.get("mcpServers")
    user_names = engram_names(top)
    local = False
    projects = data.get("projects")
    if isinstance(projects, dict):
        for project in projects.values():
            if isinstance(project, dict) and engram_names(project.get("mcpServers")):
                local = True
                break
    entry = top.get(SERVER_NAME) if isinstance(top, dict) else None
    return UserConfigState(
        "configured" if user_names or local else "absent",
        user_names=user_names,
        local=local,
        user_entry=entry if isinstance(entry, dict) else None,
    )


@dataclass
class LegacyState:
    """Engram entries left in ``~/.claude/.mcp.json`` (a file Claude Code does not read)."""

    names: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)

    @property
    def has_engram(self) -> bool:
        return bool(self.names)


def read_legacy(path: Path | None = None) -> LegacyState:
    path = legacy_path() if path is None else Path(path)
    if not _is_file(path):
        return LegacyState()
    data = _read_json(path)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    names = engram_names(servers)
    env: dict = {}
    for name in [SERVER_NAME, *names]:
        entry = servers.get(name) if isinstance(servers, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("env"), dict):
            env = dict(entry["env"])
            break
    return LegacyState(names=names, env=env)


def detection_status() -> str:
    """One status for doctor and the connection report.

    ``configured`` | ``undetermined`` | ``legacy_only`` | ``not_configured`` |
    ``not_installed``. Only the user config counts as configured; an entry in
    ``~/.claude/.mcp.json`` alone is ``legacy_only``.
    """
    state = read_user_config()
    if state.status == "configured":
        return "configured"
    if state.status == "undetermined":
        return "undetermined"
    if read_legacy().has_engram:
        return "legacy_only"
    return "not_configured" if is_installed() else "not_installed"


# ---------------------------------------------------------------------------
# the claude command
# ---------------------------------------------------------------------------

def _find_cli() -> str | None:
    return shutil.which("claude")


def _run(argv: list[str], timeout: float = CLI_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


# Seams: tests replace these so no real `claude` ever runs.
cli_path: Callable[[], str | None] = _find_cli
run_cli: Callable[..., subprocess.CompletedProcess] = _run


def add_args(entry: dict) -> list[str]:
    """``claude`` arguments that register ``entry`` as the user-scope ``engram`` server."""
    args = ["mcp", "add", "--scope", "user", SERVER_NAME]
    for key, value in (entry.get("env") or {}).items():
        args += ["-e", f"{key}={value}"]
    args += ["--", str(entry["command"]), *[str(a) for a in entry.get("args") or []]]
    return args


def remove_args() -> list[str]:
    return ["mcp", "remove", "--scope", "user", SERVER_NAME]


def get_args() -> list[str]:
    return ["mcp", "get", SERVER_NAME]


def _quote(value: str) -> str:
    if value and not any(c in value for c in ' \t"&|<>()^!%\'$`;*?'):
        return value
    return '"' + value.replace('"', '\\"') + '"'


def manual_command(entry: dict) -> tuple[str, list[str]]:
    """The add command to print, and the owner env keys left out of it.

    Only the env keys setup manages are written out; any other key of the
    old entry (it may hold a secret) is named, not shown.
    """
    env = entry.get("env") or {}
    shown = {k: v for k, v in env.items() if k in _SHOWN_ENV_KEYS}
    hidden = sorted(k for k in env if k not in _SHOWN_ENV_KEYS)
    args = add_args({**entry, "env": shown})
    return "claude " + " ".join(_quote(a) for a in args), hidden


def _comparable(entry: dict | None) -> tuple | None:
    if not isinstance(entry, dict):
        return None
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    return (
        str(entry.get("command") or ""),
        [str(a) for a in entry.get("args") or []],
        {str(k): str(v) for k, v in env.items()},
    )


def same_entry(existing: dict | None, wanted: dict) -> bool:
    return _comparable(existing) == _comparable(wanted)


def _first_line(text: str, limit: int = 200) -> str:
    for line in (text or "").splitlines():
        line = "".join(ch for ch in line if ch.isprintable()).strip()
        if line:
            return line[:limit]
    return ""


@dataclass
class Registration:
    """What happened to Claude Code's ``engram`` server.

    status:
      ``added`` / ``replaced`` -- registered through the claude command;
      ``unchanged`` -- the same entry was already there;
      ``present`` -- Engram is already registered (other name, or the file
      could not be checked and ``claude mcp get engram`` found it);
      ``kept`` -- a different ``engram`` entry exists and was left alone;
      ``manual`` -- the user has to run ``command`` (no claude command, or
      a different entry the caller would not replace);
      ``failed`` -- the claude command returned an error (``detail``).
    """

    status: str
    command: str = ""
    hidden_env: list[str] = field(default_factory=list)
    detail: str = ""
    name: str = SERVER_NAME

    @property
    def registered(self) -> bool:
        return self.status in ("added", "replaced", "unchanged", "present", "kept")


def register(
    build_entry: Callable[[dict], dict],
    *,
    on_differ: str = "keep",
    confirm_replace: Callable[[], bool] | None = None,
) -> Registration:
    """Register Engram as Claude Code's user-scope ``engram`` server.

    ``build_entry(existing_env)`` returns the wanted entry; ``existing_env`` is
    the env of the current user-scope ``engram`` entry, else of the old entry
    in ``~/.claude/.mcp.json``, so the owner's settings carry over.

    A different ``engram`` entry is never replaced silently: ``on_differ`` is
    ``"ask"`` (``confirm_replace()`` decides) or ``"keep"`` (report the
    commands instead). The user config is only read here, never written.
    """
    state = read_user_config()
    legacy = read_legacy()
    existing_env = {}
    if state.user_entry and isinstance(state.user_entry.get("env"), dict):
        existing_env = dict(state.user_entry["env"])
    elif legacy.env:
        existing_env = dict(legacy.env)
    wanted = build_entry(existing_env)
    command, hidden = manual_command(wanted)

    def manual(detail: str = "") -> Registration:
        return Registration("manual", command=command, hidden_env=hidden, detail=detail)

    other_names = [n for n in state.user_names if n != SERVER_NAME]
    if state.user_entry is None and other_names:
        return Registration("present", name=other_names[0])

    replacing = False
    if state.user_entry is not None:
        if same_entry(state.user_entry, wanted):
            return Registration("unchanged")
        if on_differ == "ask" and confirm_replace is not None and confirm_replace():
            replacing = True
        else:
            result = manual("differs")
            result.status = "kept" if on_differ == "ask" else "manual"
            return result

    exe = cli_path()
    if not exe:
        return manual("no_cli")

    if state.status == "undetermined" and not replacing:
        try:
            probe = run_cli([exe, *get_args()])
        except (OSError, subprocess.SubprocessError) as exc:
            return Registration("failed", command=command, hidden_env=hidden,
                                detail=type(exc).__name__)
        if probe.returncode == 0:
            return Registration("present")

    try:
        if replacing:
            removed = run_cli([exe, *remove_args()])
            if removed.returncode != 0:
                return Registration("failed", command=command, hidden_env=hidden,
                                    detail=_first_line(removed.stderr or removed.stdout))
        done = run_cli([exe, *add_args(wanted)])
    except (OSError, subprocess.SubprocessError) as exc:
        return Registration("failed", command=command, hidden_env=hidden, detail=type(exc).__name__)
    if done.returncode != 0:
        return Registration("failed", command=command, hidden_env=hidden,
                            detail=_first_line(done.stderr or done.stdout))
    return Registration("replaced" if replacing else "added")


# ---------------------------------------------------------------------------
# the old location
# ---------------------------------------------------------------------------

def remove_legacy_entries(
    *,
    write_text: Callable[[Path, str], None],
    path: Path | None = None,
) -> list[str]:
    """Drop Engram entries from ``~/.claude/.mcp.json``; every other key stays.

    ``write_text(path, text)`` does the (backed-up) write. Returns the removed
    names; an unreadable file is left untouched.
    """
    path = legacy_path() if path is None else Path(path)
    if not _is_file(path):
        return []
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        return []
    servers = data["mcpServers"]
    names = engram_names(servers)
    if not names:
        return []
    for name in names:
        del servers[name]
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return names
