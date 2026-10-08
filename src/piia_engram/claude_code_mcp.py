"""Claude Code: where its MCP servers live and how Engram is registered there.

Claude Code reads user-scope MCP servers from the top-level ``mcpServers`` of
its user config (``~/.claude.json``, or ``$CLAUDE_CONFIG_DIR/.claude.json``
when that variable is set) and local-scope servers from
``projects.<dir>.mcpServers`` in the same file. It does not read
``~/.claude/.mcp.json``; older Engram versions wrote their entry there.

Engram never writes the user config itself: it registers through the
``claude`` command (``claude mcp add --scope user ...``) and only parses the
file to tell whether an entry is already there; nothing from it is output. When the command is not
available, the caller prints the same command for the user to run.

``cli_path`` and ``run_cli`` are the only places that touch the ``claude``
executable; tests replace them.
"""

from __future__ import annotations

import json
import os
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


def config_dir_for(home: Path) -> Path:
    """Claude Code's config directory for a given home directory."""
    return config_dir_override() or Path(home) / ".claude"


def instructions_path(home: Path | None = None) -> Path:
    """Claude Code's user instruction file (``CLAUDE.md``) in its config directory."""
    return config_dir_for(Path.home() if home is None else home) / "CLAUDE.md"


def settings_path(home: Path | None = None) -> Path:
    """Claude Code's user settings (hooks) in its config directory."""
    return config_dir_for(Path.home() if home is None else home) / "settings.json"


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
    return name in KNOWN_NAMES or launches_engram(entry)


def launches_engram(entry: object) -> bool:
    """Does this server entry start Engram's MCP server (whatever its name)?"""
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


def engram_entry_name(servers: object) -> str | None:
    """The Engram entry a reader looks at in any client's servers: ``engram`` first."""
    names = engram_names(servers)
    if not names:
        return None
    return SERVER_NAME if SERVER_NAME in names else names[0]


_TOO_LARGE = object()


def _read_capped(path: Path) -> object:
    """Parsed JSON, ``None`` when unreadable, or ``_TOO_LARGE``.

    The size is judged by the bytes actually read (at most one byte past the
    limit), not by an earlier ``stat``, so a file that grows in between is
    still caught.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_USER_CONFIG_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_USER_CONFIG_BYTES:
        return _TOO_LARGE
    try:
        return json.loads(raw.decode("utf-8-sig"))
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
    ``env_keys``: the env key names (no values) of the user-scope Engram
    entry; None when there is no such entry.
    """

    status: str
    user_names: list[str] = field(default_factory=list)
    local: bool = False
    user_entry: dict | None = None
    env_keys: list[str] | None = None


def read_user_config(path: Path | None = None) -> UserConfigState:
    path = user_config_path() if path is None else Path(path)
    if not _is_file(path):
        return UserConfigState("missing")
    try:
        if path.stat().st_size > MAX_USER_CONFIG_BYTES:
            return UserConfigState("undetermined")
    except OSError:
        return UserConfigState("undetermined")
    data = _read_capped(path)
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
    env_keys = None
    for name in ([SERVER_NAME] if SERVER_NAME in user_names else []) + user_names:
        candidate = top.get(name)
        if isinstance(candidate, dict):
            env = candidate.get("env")
            env_keys = sorted(str(k) for k in env) if isinstance(env, dict) else []
            break
    return UserConfigState(
        "configured" if user_names or local else "absent",
        user_names=user_names,
        local=local,
        user_entry=entry if isinstance(entry, dict) else None,
        env_keys=env_keys,
    )


@dataclass
class LegacyState:
    """Engram entries left in ``~/.claude/.mcp.json`` (a file Claude Code does not read)."""

    names: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    # Entries setup may remove: an Engram name AND a command that launches Engram.
    removable: list[str] = field(default_factory=list)
    too_large: bool = False

    @property
    def has_engram(self) -> bool:
        return bool(self.names)


def _removable(name: str, entry: object) -> bool:
    return name in KNOWN_NAMES and launches_engram(entry)


def read_legacy(path: Path | None = None) -> LegacyState:
    path = legacy_path() if path is None else Path(path)
    if not _is_file(path):
        return LegacyState()
    data = _read_capped(path)
    if data is _TOO_LARGE:
        return LegacyState(too_large=True)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    names = engram_names(servers)
    removable = [n for n in names if _removable(n, servers.get(n))]
    env: dict = {}
    for name in [SERVER_NAME, *names]:
        entry = servers.get(name) if isinstance(servers, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("env"), dict):
            env = dict(entry["env"])
            break
    return LegacyState(names=names, env=env, removable=removable)


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


# detection_status -> (status, style) in the pathless client summaries
# (`engram status`, `engram dock-governance`).
_SUMMARY_ROWS = {
    "configured": ("configured", "claude_cli"),
    "undetermined": ("needs attention", "unknown"),
    "legacy_only": ("needs attention", "legacy_only"),
    "not_configured": ("missing entry", "missing"),
    "not_installed": ("not configured", "missing"),
}


def summary_status(status: str | None = None) -> tuple[str, str]:
    """``(status, style)`` for a client summary row; no path, no config value."""
    return _SUMMARY_ROWS[detection_status() if status is None else status]


# ---------------------------------------------------------------------------
# the claude command
# ---------------------------------------------------------------------------

def _find_cli() -> str | None:
    """The ``claude`` command from PATH, as an absolute path.

    Walks PATH itself instead of ``shutil.which``: empty entries, ``.`` and
    relative directories are skipped, so a ``claude`` file in the current
    directory is never picked. On Windows the names follow PATHEXT.
    """
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
        names = ["claude" + ext.lower() for ext in exts]
    else:
        names = ["claude"]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or entry == "." or not os.path.isabs(entry):
            continue
        for name in names:
            candidate = os.path.join(entry, name)
            if not os.path.isfile(candidate):
                continue
            if os.name != "nt" and not os.access(candidate, os.X_OK):
                continue
            return os.path.abspath(candidate)
    return None


# A .cmd / .bat shim runs through cmd.exe, which re-parses its arguments: any
# of these in an argument could change the command or split it.
_CMD_UNSAFE = frozenset('&|<>^%!"()\n\r')


def _runs_through_cmd(exe: str) -> bool:
    return PurePath(str(exe)).suffix.lower() in (".cmd", ".bat")


def cmd_unsafe(exe: str, args: list[str]) -> bool:
    """True when ``exe`` is a batch shim and an argument holds a character cmd.exe interprets."""
    return _runs_through_cmd(exe) and any(ch in _CMD_UNSAFE for arg in args for ch in str(arg))


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


_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_PLAIN_POSIX = frozenset(_ALNUM + "-_./:=+,@")
_PLAIN_POWERSHELL = frozenset(_ALNUM + "-_./:=+\\")


def _quote_powershell(value: str) -> str:
    """PowerShell: single quotes (a quote inside is doubled). ``--`` is quoted too:
    unquoted, PowerShell may take it as its own end-of-parameters marker."""
    if value and value != "--" and all(c in _PLAIN_POWERSHELL for c in value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _quote_posix(value: str) -> str:
    if value and all(c in _PLAIN_POSIX for c in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def manual_command(entry: dict, *, windows: bool | None = None) -> tuple[str, list[str]]:
    """The add command to print, and the owner env keys left out of it.

    Quoted for PowerShell on Windows and for a POSIX shell elsewhere. Only
    the env keys setup manages are written out; any other key of the old
    entry (it may hold a secret) is named, not shown.
    """
    windows = (os.name == "nt") if windows is None else windows
    quote = _quote_powershell if windows else _quote_posix
    env = entry.get("env") or {}
    shown = {k: v for k, v in env.items() if k in _SHOWN_ENV_KEYS}
    hidden = sorted(k for k in env if k not in _SHOWN_ENV_KEYS)
    args = add_args({**entry, "env": shown})
    return "claude " + " ".join(quote(a) for a in args), hidden


def manual_shell() -> str:
    """The shell the printed commands are quoted for."""
    return "PowerShell" if os.name == "nt" else "sh"


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
      ``conflict`` -- an ``engram`` entry exists that does not launch Engram;
      it is left alone and nothing is added;
      ``manual`` -- the user has to run ``command`` (no claude command, a
      different entry the caller would not replace, or ``cmd_unsafe``: a
      .cmd/.bat shim with arguments cmd.exe would re-interpret);
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
    commands instead). The user config is parsed here, never written.
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
        if not launches_engram(state.user_entry):
            return Registration("conflict", command=command, hidden_env=hidden, detail="not_engram")
        if on_differ == "ask" and confirm_replace is not None and confirm_replace():
            replacing = True
        else:
            result = manual("differs")
            result.status = "kept" if on_differ == "ask" else "manual"
            return result

    exe = cli_path()
    if not exe:
        return manual("no_cli")

    planned = [add_args(wanted)] + ([remove_args()] if replacing else [])
    if cmd_unsafe(exe, [a for argv in planned for a in argv]):
        return manual("cmd_unsafe")

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

def _indent_of(text: str) -> str | int:
    """The indent of the first indented line (a tab or a number of spaces), default 2."""
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) < len(line):
            lead = line[: len(line) - len(stripped)]
            return "\t" if lead.startswith("\t") else len(lead)
    return 2


def remove_legacy_entries(
    *,
    write_text: Callable[[Path, str], None],
    path: Path | None = None,
) -> list[str]:
    """Drop Engram entries from ``~/.claude/.mcp.json``; every other key stays.

    Only an entry with an Engram name (``engram`` / ``piia-engram``) that also
    launches Engram is removed; an entry matching only one of them is left
    for the user. A leading BOM and the file's indent are kept.
    ``write_text(path, text)`` does the (backed-up, atomic) write. Returns
    the removed names; an unreadable or oversized file is left untouched.
    """
    path = legacy_path() if path is None else Path(path)
    if not _is_file(path):
        return []
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_USER_CONFIG_BYTES + 1)
    except OSError:
        return []
    if len(raw) > MAX_USER_CONFIG_BYTES:
        return []
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        return []
    servers = data["mcpServers"]
    names = [n for n in engram_names(servers) if _removable(n, servers.get(n))]
    if not names:
        return []
    for name in names:
        del servers[name]
    out = json.dumps(data, ensure_ascii=False, indent=_indent_of(text)) + "\n"
    write_text(path, ("\ufeff" if bom else "") + out)
    return names
