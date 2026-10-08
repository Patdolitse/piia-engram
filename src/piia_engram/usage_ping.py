"""Daily anonymous usage ping -- on by default, one line of notice, easy to turn off.

At most once per UTC day per install it sends: a random install id (made on this
machine, reset with ``engram telemetry reset-id``), the Engram version, the OS
family, the Python major.minor, a bounded MCP client name and the date. Nothing
else. It never raises, never writes to stdout and never blocks a tool.

Off when (first match wins): DO_NOT_TRACK / NO_TELEMETRY set; ENGRAM_TELEMETRY=0;
``engram telemetry off``; an earlier opt-out of the detailed statistics; CI;
containers (``/.dockerenv``, ``/run/.containerenv``, KUBERNETES_SERVICE_HOST or
ENGRAM_EPHEMERAL); tests.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.request import Request, urlopen

from .atomic_replace import replace_with_retry

ENDPOINT = "https://telemetry.piia-engram.com/v1/ping"
SCHEMA = "ping/1"
TIMEOUT_SECONDS = 3

_ID_FILE = "install_id"
_DAY_FILE = "last_ping_utc"
_NOTICE_FILE = "notice_shown"
_SETTINGS_FILE = "usage_ping.json"
_OFF_VALUES = ("0", "false", "off", "no")
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD",
            "JENKINS_URL", "CIRCLECI", "TEAMCITY_VERSION")

_lock = threading.Lock()
_started = False  # one attempt per process


def state_dir() -> Path:
    """Per-user config dir, separate from any memory store (one install = one id)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "piia-engram"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "piia-engram"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".config") / "piia-engram"


def _set(var: str) -> bool:
    value = os.environ.get(var, "").strip().lower()
    return bool(value) and value not in ("0", "false")


def _flag_on(var: str) -> bool:
    """Same rule as the MCP server's env flags: only 1 / true / yes count as on."""
    return os.environ.get(var, "").strip().lower() in ("1", "true", "yes")


_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")  # Docker, Podman


def _in_container() -> bool:
    if os.environ.get("KUBERNETES_SERVICE_HOST", "").strip():  # set in every Kubernetes pod
        return True
    for marker in _CONTAINER_MARKERS:
        try:
            if os.path.isfile(marker):
                return True
        except Exception:
            pass
    return False


def _in_test() -> bool:
    return bool(os.environ.get("ENGRAM_TEST", "").strip()) or "pytest" in sys.modules


def _legacy_config_paths() -> list[Path]:
    """The detailed-statistics configs: the ENGRAM_DIR store and the default home store."""
    paths: list[Path] = []
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        try:
            paths.append(Path(custom).expanduser() / "telemetry_config.json")
        except Exception:
            pass
    try:
        paths.append(Path.home() / ".engram" / "telemetry_config.json")
    except Exception:
        pass
    return paths


def _legacy_opted_out() -> bool:
    """True when the user explicitly turned the detailed statistics off before, in any store."""
    for path in _legacy_config_paths():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue  # missing or corrupt: not an opt-out
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False and cfg.get("opted_out_at"):
            return True
        if cfg.get("remote_enabled") is False and cfg.get("remote_opted_out_at"):
            return True
    return False


def _load_settings() -> dict[str, Any]:
    try:
        data = json.loads((state_dir() / _SETTINGS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_enabled(enabled: bool) -> None:
    """Persist ``engram telemetry on/off`` for the ping (user level, not per store).

    Written atomically (temp file + replace); may raise OSError for the caller to report.
    """
    path = state_dir() / _SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps({"enabled": bool(enabled), "changed_at": _today()}) + "\n",
                       encoding="utf-8")
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def decision() -> tuple[bool, str]:
    """(on, deciding layer). The first matching layer wins."""
    for var in ("DO_NOT_TRACK", "NO_TELEMETRY"):
        if _set(var):
            return False, var
    if os.environ.get("ENGRAM_TELEMETRY", "").strip().lower() in _OFF_VALUES:
        return False, "ENGRAM_TELEMETRY"
    settings = _load_settings()
    if settings.get("enabled") is False:
        return False, "settings"
    # An explicit `engram telemetry on` wins over an earlier opt-out of the detailed
    # statistics (whose `off` also records a remote opt-out that `on` does not clear).
    if settings.get("enabled") is not True and _legacy_opted_out():
        return False, "earlier opt-out"
    if any(_set(var) for var in _CI_VARS):
        return False, "ci"
    if _in_container() or _flag_on("ENGRAM_EPHEMERAL"):
        return False, "container"
    if _in_test():
        return False, "test"
    return True, "default"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Always used with fullmatch: a trailing newline must not pass.
_ID_RE = re.compile(r"[0-9a-f]{32}")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+[0-9A-Za-z.+-]{0,16}")
_PYTHON_RE = re.compile(r"3\.\d{1,2}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FIELDS = frozenset({"schema", "install_id", "version", "os", "python", "client", "date"})
_OS_VALUES = frozenset({"windows", "macos", "linux", "other"})
CLIENTS = frozenset({"claude_code", "claude_desktop", "codex", "cursor", "windsurf", "vscode",
                     "cline", "zed", "gemini", "opencode", "cli", "other", "unknown"})
# Order matters: "cursor-vscode" must map to cursor before the vscode rule.
_CLIENT_RULES = (
    ("claude-code", "claude_code"), ("claude code", "claude_code"),
    ("claude-cli", "claude_code"),
    ("claude-ai", "claude_desktop"), ("claude-desktop", "claude_desktop"),
    ("claude desktop", "claude_desktop"), ("codex", "codex"), ("cursor", "cursor"),
    ("windsurf", "windsurf"), ("visual studio code", "vscode"), ("vscode", "vscode"),
    ("cline", "cline"), ("zed", "zed"), ("gemini", "gemini"), ("opencode", "opencode"),
)


def _read_id(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return value if _ID_RE.fullmatch(value) else None


def install_id(create: bool = True) -> str | None:
    """The random install id; created on first use. None if it cannot be stored."""
    try:
        path = state_dir() / _ID_FILE
    except Exception:
        return None
    value = _read_id(path)
    if value or not create:
        return value
    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Another process is creating it: give it a moment to finish writing.
            for _ in range(5):
                existing = _read_id(path)
                if existing:
                    return existing
                time.sleep(0.02)
            path.write_text(value, encoding="utf-8")  # still empty or invalid: replace it
            return value
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
    except OSError:
        return None
    return value


def delete_install_id() -> bool:
    """Remove the stored install id; True when none is left."""
    try:
        (state_dir() / _ID_FILE).unlink()
    except FileNotFoundError:
        pass
    except Exception:
        return False
    return True


def reset_install_id() -> str | None:
    if not delete_install_id():
        return None
    return install_id()


def _has_needle(raw: str, needle: str) -> bool:
    """Single-word needles match on word-like boundaries; multi-word ones as substrings."""
    if needle.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", raw) is not None
    return needle in raw


def normalize_client(name: Any) -> str:
    """Map an MCP clientInfo.name to a closed label; the raw string is never sent."""
    raw = str(name or "").strip().lower().replace("_", "-")
    if not raw or raw == "unknown":
        return "unknown"
    if raw == "cli":
        return "cli"
    for needle, label in _CLIENT_RULES:
        if _has_needle(raw, needle):
            return label
    if raw == "claude":  # older Claude Desktop builds
        return "claude_desktop"
    return "other"


def _os_family() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def valid_payload(payload: Any) -> bool:
    """Whole-payload check: exactly the documented fields, each in its format."""
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return False
    if not all(isinstance(v, str) for v in payload.values()):
        return False
    return bool(
        payload["schema"] == SCHEMA
        and _ID_RE.fullmatch(payload["install_id"])
        and _VERSION_RE.fullmatch(payload["version"])
        and payload["os"] in _OS_VALUES
        and _PYTHON_RE.fullmatch(payload["python"])
        and payload["client"] in CLIENTS
        and _DATE_RE.fullmatch(payload["date"])
    )


def build_payload(client: Any, *, today: str | None = None) -> dict[str, str] | None:
    """The ping body, or None when no install id can be stored or a field is invalid."""
    from piia_engram import __version__

    iid = install_id()
    if not iid:
        return None
    payload = {
        "schema": SCHEMA,
        "install_id": iid,
        "version": __version__,
        "os": _os_family(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "client": normalize_client(client),
        "date": today or _today(),
    }
    return payload if valid_payload(payload) else None


def _last_sent() -> str:
    try:
        return (state_dir() / _DAY_FILE).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _mark_sent(day: str) -> None:
    try:
        path = state_dir() / _DAY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(day, encoding="utf-8")
    except OSError:
        pass


def _send(payload: dict[str, str]) -> bool:
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = Request(ENDPOINT, data=body, method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": "piia-engram"})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            final_url = getattr(resp, "url", None)
            if final_url is None:  # older responses only offer the deprecated geturl()
                geturl = getattr(resp, "geturl", None)
                final_url = geturl() if callable(geturl) else ENDPOINT
            # Only our own endpoint's answer counts; a redirect elsewhere is not "sent".
            if final_url == ENDPOINT and 200 <= int(getattr(resp, "status", 0)) < 300:
                _mark_sent(payload["date"])
                return True
    except Exception:
        pass
    return False


def maybe_send(client: Any = "cli") -> threading.Thread | None:
    """Send today's ping in a daemon thread if it is on and due. Never raises."""
    global _started
    try:
        with _lock:
            if _started:
                return None
            _started = True
        on, _layer = decision()
        if not on:
            return None
        today = _today()
        if _last_sent() == today:
            return None
        payload = build_payload(client, today=today)
        if payload is None:
            return None
        thread = threading.Thread(target=_send, args=(payload,), name="engram-usage-ping", daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


NOTICE = (
    "[engram] Engram sends one anonymous usage ping a day (random install ID, version, OS, "
    "Python version, AI client name, date). See it: engram telemetry preview. "
    "Turn it off: engram telemetry off\n"
    "[engram] Engram 每天发送一次匿名使用信号（随机安装 ID、版本、系统、Python 版本、"
    "AI 客户端名称、日期）。查看：engram telemetry preview。关闭：engram telemetry off"
)


def maybe_show_notice(stream: TextIO | None, *, mark: bool = True) -> bool:
    """Print the notice while the ping is on. Never raises.

    ``mark`` True shows it once per install (a marker file remembers it);
    ``mark`` False prints it every time and writes nothing (MCP server logs).
    ``stream`` None (e.g. a GUI client gave the process no stderr) shows nothing:
    print(file=None) would fall back to stdout, which carries the MCP protocol.
    """
    if stream is None:
        return False
    try:
        on, _layer = decision()
        if not on:
            return False
        if not mark:
            print(NOTICE, file=stream)
            return True
        marker = state_dir() / _NOTICE_FILE
        if marker.exists():
            return False
        if not install_id():  # the ping could never be sent from here
            return False
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(_today(), encoding="utf-8")
        except OSError:
            return False  # without the marker it would repeat on every start
        print(NOTICE, file=stream)
        return True
    except Exception:
        return False


def status() -> dict[str, Any]:
    on, layer = decision()
    iid = install_id(create=False) or ""
    return {"will_send": on, "decided_by": layer, "install_id_prefix": iid[:8],
            "last_sent": _last_sent(), "endpoint": ENDPOINT}


def preview(client: Any = "cli") -> str:
    """The body the next ping would carry (the id is not created by previewing)."""
    from piia_engram import __version__

    body = {
        "schema": SCHEMA,
        "install_id": install_id(create=False) or "<created on the first ping>",
        "version": __version__,
        "os": _os_family(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "client": normalize_client(client),
        "date": _today(),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)
