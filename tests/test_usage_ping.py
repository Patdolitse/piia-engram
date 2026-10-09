"""Daily anonymous usage ping (usage_ping.py) and its wiring."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram import usage_ping as up

SRC = str(Path(__file__).resolve().parents[1] / "src")
_OPT_OUT_ENV = ("DO_NOT_TRACK", "NO_TELEMETRY", "ENGRAM_TELEMETRY", "ENGRAM_TEST",
                "ENGRAM_EPHEMERAL", "KUBERNETES_SERVICE_HOST", *up._CI_VARS)


class _Resp:
    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self.url = url

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def ping(tmp_path, monkeypatch):
    """usage_ping with the env cleared, state in tmp, and the network stubbed."""
    for var in _OPT_OUT_ENV:
        monkeypatch.delenv(var, raising=False)
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    # Path.home() must never reach the real profile (its ~/.engram may hold an opt-out).
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    ns = SimpleNamespace(mod=up, dir=state, home=home, sent=[], status=204, final_url=None,
                         orig_in_test=up._in_test, orig_legacy=up._legacy_opted_out,
                         orig_in_container=up._in_container)
    monkeypatch.setattr(up, "state_dir", lambda: state)
    monkeypatch.setattr(up, "_in_test", lambda: False)
    monkeypatch.setattr(up, "_in_container", lambda: False)
    monkeypatch.setattr(up, "_legacy_opted_out", lambda: False)
    monkeypatch.setattr(up, "_started", False)
    monkeypatch.setattr(up, "_today", lambda: "2026-10-06")  # no flake at UTC midnight

    def fake_urlopen(req, timeout=None):
        ns.sent.append((req.full_url, json.loads(req.data), timeout, dict(req.header_items())))
        return _Resp(ns.status, ns.final_url or req.full_url)

    ns.fake_urlopen = fake_urlopen
    monkeypatch.setattr(up, "urlopen", fake_urlopen)
    return ns


# --- decision layers ----------------------------------------------------------


def test_default_is_on(ping):
    assert ping.mod.decision() == (True, "default")


@pytest.mark.parametrize("var", ["DO_NOT_TRACK", "NO_TELEMETRY"])
def test_do_not_track_wins_over_an_explicit_on(ping, monkeypatch, var):
    monkeypatch.setenv(var, "1")
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    assert ping.mod.decision() == (False, var)


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_do_not_track_zero_or_empty_is_not_an_opt_out(ping, monkeypatch, value):
    monkeypatch.setenv("DO_NOT_TRACK", value)
    assert ping.mod.decision() == (True, "default")


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "OFF"])
def test_engram_telemetry_off_turns_it_off(ping, monkeypatch, value):
    monkeypatch.setenv("ENGRAM_TELEMETRY", value)
    assert ping.mod.decision() == (False, "ENGRAM_TELEMETRY")


def test_the_off_setting_is_remembered_and_can_be_undone(ping):
    ping.mod.set_enabled(False)
    assert ping.mod.decision() == (False, "settings")
    ping.mod.set_enabled(True)
    assert ping.mod.decision() == (True, "default")


def test_an_earlier_opt_out_of_the_detailed_statistics_is_respected(ping, monkeypatch):
    monkeypatch.setattr(ping.mod, "_legacy_opted_out", lambda: True)
    assert ping.mod.decision() == (False, "earlier opt-out")


def test_earlier_opt_out_is_read_from_the_detailed_statistics_config(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    assert ping.orig_legacy() is False
    (store / "telemetry_config.json").write_text(
        json.dumps({"enabled": False, "opted_out_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert ping.orig_legacy() is True
    (store / "telemetry_config.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
    assert ping.orig_legacy() is False  # never opted in or out: not an opt-out


@pytest.mark.parametrize("var", ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD"])
def test_ci_is_off(ping, monkeypatch, var):
    monkeypatch.setenv(var, "true")
    assert ping.mod.decision() == (False, "ci")


def test_ci_false_is_not_ci(ping, monkeypatch):
    monkeypatch.setenv("CI", "false")
    assert ping.mod.decision() == (True, "default")


def test_test_runs_are_off(ping, monkeypatch):
    assert ping.orig_in_test() is True  # pytest is loaded
    monkeypatch.setattr(ping.mod, "_in_test", ping.orig_in_test)
    assert ping.mod.decision() == (False, "test")


def test_the_suite_itself_runs_with_the_ping_off():
    # No fixture: conftest's ENGRAM_TEST=1 must keep every other test silent.
    assert up.decision()[0] is False


# --- install id, client names, payload -----------------------------------------


def test_install_id_is_random_hex_and_stable(ping):
    first = ping.mod.install_id()
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert ping.mod.install_id() == first
    assert (ping.dir / "install_id").read_text(encoding="utf-8") == first


def test_reset_gives_a_new_id(ping):
    first = ping.mod.install_id()
    second = ping.mod.reset_install_id()
    assert second and second != first and ping.mod.install_id() == second


def test_a_corrupt_id_file_is_replaced(ping):
    ping.dir.mkdir(parents=True)
    (ping.dir / "install_id").write_text("not-an-id", encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{32}", ping.mod.install_id())


def test_no_id_when_the_state_dir_cannot_be_written(ping, monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(ping.mod, "state_dir", lambda: blocker / "state")
    assert ping.mod.install_id() is None
    assert ping.mod.build_payload("cli") is None


def test_install_id_without_create_does_not_make_one(ping):
    assert ping.mod.install_id(create=False) is None
    assert not (ping.dir / "install_id").exists()


@pytest.mark.parametrize("raw,label", [
    ("claude-code", "claude_code"), ("Claude Code", "claude_code"),
    ("claude-ai", "claude_desktop"), ("claude", "claude_desktop"),
    ("codex-mcp-client", "codex"), ("cursor-vscode", "cursor"),
    ("Visual Studio Code", "vscode"), ("windsurf-client", "windsurf"),
    ("Cline", "cline"), ("Zed", "zed"), ("gemini-cli-mcp-client", "gemini"),
    ("opencode", "opencode"), ("cli", "cli"), ("", "unknown"), (None, "unknown"),
    ("my-private-tool /home/alice", "other"),
    ("authorized-agent", "other"), ("customized-x", "other"), ("zed", "zed"),
    ("Zed Industries", "zed"),
])
def test_client_names_are_mapped_to_a_closed_set(raw, label):
    assert up.normalize_client(raw) == label


def test_payload_has_exactly_the_documented_fields(ping):
    payload = ping.mod.build_payload("claude-code", today="2026-10-06")
    assert set(payload) == {"schema", "install_id", "version", "os", "python", "client", "date"}
    assert payload["schema"] == "ping/1"
    assert payload["client"] == "claude_code"
    assert payload["date"] == "2026-10-06"
    assert payload["os"] in {"windows", "macos", "linux", "other"}
    assert re.fullmatch(r"3\.\d{1,2}", payload["python"])
    assert ping.mod.valid_payload(payload)


def test_payload_carries_no_path_or_identity(ping):
    text = json.dumps(ping.mod.build_payload("cli", today="2026-10-06"))
    assert str(Path.home()) not in text
    assert "\\" not in text and "@" not in text
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) >= 3:
        assert user not in text


@pytest.mark.parametrize("field,value", [
    ("install_id", "XYZ"), ("os", "plan9"), ("client", "codex -> claude"),
    ("python", "2.7"), ("date", "06/10/2026"), ("version", "4.21.2 /home/x"),
    ("schema", "ping/2"),
])
def test_an_invalid_field_rejects_the_whole_payload(ping, field, value):
    payload = ping.mod.build_payload("cli", today="2026-10-06")
    payload[field] = value
    assert not ping.mod.valid_payload(payload)


def test_an_extra_field_rejects_the_whole_payload(ping):
    payload = ping.mod.build_payload("cli", today="2026-10-06")
    payload["path"] = "x"
    assert not ping.mod.valid_payload(payload)


@pytest.mark.parametrize("field,value", [
    ("version", "4.22.0\n"), ("install_id", "a" * 32 + "\n"),
    ("python", "3.12\n"), ("date", "2026-10-06\n"),
])
def test_a_trailing_newline_rejects_the_whole_payload(ping, field, value):
    payload = ping.mod.build_payload("cli", today="2026-10-06")
    payload[field] = value
    assert not ping.mod.valid_payload(payload)


def test_an_id_file_ending_in_a_newline_is_still_read(ping):
    ping.dir.mkdir(parents=True)
    (ping.dir / "install_id").write_text("c" * 32 + "\n", encoding="utf-8")
    assert ping.mod.install_id() == "c" * 32


# --- sending --------------------------------------------------------------------


def test_sends_once_per_day(ping):
    thread = ping.mod.maybe_send("claude-code")
    thread.join(5)
    assert len(ping.sent) == 1
    url, body, timeout, headers = ping.sent[0]
    assert url == "https://telemetry.piia-engram.com/v1/ping"
    assert timeout == 3
    assert body["client"] == "claude_code" and ping.mod.valid_payload(body)
    assert headers.get("User-agent") == "piia-engram"
    assert (ping.dir / "last_ping_utc").read_text(encoding="utf-8") == body["date"]
    ping.mod._started = False  # a new process on the same day
    assert ping.mod.maybe_send("claude-code") is None
    assert len(ping.sent) == 1


def test_one_attempt_per_process(ping):
    ping.status = 500
    ping.mod.maybe_send("cli").join(5)
    assert ping.mod.maybe_send("cli") is None
    assert len(ping.sent) == 1


def test_a_failure_is_silent_and_retried_by_the_next_process(ping, monkeypatch, capsys):
    def down(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(ping.mod, "urlopen", down)
    ping.mod.maybe_send("cli").join(5)
    assert not (ping.dir / "last_ping_utc").exists()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    monkeypatch.setattr(ping.mod, "urlopen", ping.fake_urlopen)
    monkeypatch.setattr(ping.mod, "_started", False)
    ping.mod.maybe_send("cli").join(5)
    assert len(ping.sent) == 1


def test_a_non_2xx_answer_is_not_marked_as_sent(ping):
    ping.status = 500
    ping.mod.maybe_send("cli").join(5)
    assert not (ping.dir / "last_ping_utc").exists()


def test_nothing_happens_when_off(ping, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert ping.mod.maybe_send("cli") is None
    assert ping.sent == []
    assert not ping.dir.exists()  # no id, no marker: nothing written at all


def test_maybe_send_never_raises(ping, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(ping.mod, "build_payload", boom)
    assert ping.mod.maybe_send("cli") is None


# --- notice, status, preview ----------------------------------------------------


def test_the_notice_is_shown_once(ping):
    first = io.StringIO()
    assert ping.mod.maybe_show_notice(first) is True
    assert "engram telemetry off" in first.getvalue()
    assert "每天发送一次匿名使用信号" in first.getvalue()
    second = io.StringIO()
    assert ping.mod.maybe_show_notice(second) is False
    assert second.getvalue() == ""


def test_no_notice_when_off(ping, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream) is False
    assert stream.getvalue() == "" and not ping.dir.exists()


def test_status_names_the_deciding_layer(ping, monkeypatch):
    status = ping.mod.status()
    assert status["will_send"] is True and status["decided_by"] == "default"
    assert status["endpoint"] == "https://telemetry.piia-engram.com/v1/ping"
    assert status["install_id_prefix"] == "" and status["last_sent"] == ""
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert ping.mod.status()["decided_by"] == "DO_NOT_TRACK"


def test_status_and_preview_do_not_create_an_id(ping):
    ping.mod.status()
    ping.mod.preview()
    assert not (ping.dir / "install_id").exists()


def test_preview_shows_the_documented_fields(ping):
    data = json.loads(ping.mod.preview())
    assert set(data) == {"schema", "install_id", "version", "os", "python", "client", "date"}
    assert data["client"] == "cli"
    ping.mod.install_id()
    assert re.fullmatch(r"[0-9a-f]{32}", json.loads(ping.mod.preview())["install_id"])


# --- notice edge cases, earlier opt-outs, install id races, sending details ------


def test_no_notice_without_a_stream(ping, capsys):
    # print(file=None) would fall back to stdout, which carries the MCP protocol.
    assert ping.mod.maybe_show_notice(None) is False
    assert capsys.readouterr().out == ""
    assert not (ping.dir / "notice_shown").exists()


def test_the_notice_names_every_field_and_the_preview_command(ping):
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream) is True
    text = stream.getvalue()
    assert len(text.strip().splitlines()) == 2
    for needle in ("random install ID", "version", "OS", "Python version", "AI client name",
                   "date", "engram telemetry preview", "engram telemetry off",
                   "随机安装 ID", "Python 版本", "AI 客户端名称", "日期"):
        assert needle in text


def test_no_notice_when_the_state_dir_cannot_be_written(ping, monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(ping.mod, "state_dir", lambda: blocker / "state")
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream) is False
    assert stream.getvalue() == ""


def test_the_notice_is_not_printed_when_its_marker_cannot_be_written(ping, monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(ping.mod, "state_dir", lambda: blocker / "state")
    monkeypatch.setattr(ping.mod, "install_id", lambda create=True: "a" * 32)
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream) is False
    assert stream.getvalue() == ""  # otherwise it would repeat on every start


def _write_cfg(directory: Path, cfg: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "telemetry_config.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_an_earlier_opt_out_of_remote_statistics_counts(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    _write_cfg(store, {"remote_enabled": False, "remote_opted_out_at": "2026-01-01T00:00:00+00:00"})
    assert ping.orig_legacy() is True


def test_an_opt_out_in_the_home_store_counts_when_engram_dir_points_elsewhere(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    assert ping.orig_legacy() is False
    _write_cfg(ping.home / ".engram", {"enabled": False, "opted_out_at": "2026-01-01T00:00:00+00:00"})
    assert ping.orig_legacy() is True


def test_never_opted_in_or_out_is_not_an_opt_out_in_either_store(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    _write_cfg(store, {"enabled": False})
    _write_cfg(ping.home / ".engram", {"enabled": False, "remote_enabled": False})
    assert ping.orig_legacy() is False


def test_a_corrupt_detailed_statistics_config_is_not_an_opt_out(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    (store / "telemetry_config.json").write_text("{not json", encoding="utf-8")
    assert ping.orig_legacy() is False


def test_an_id_written_by_another_process_meanwhile_is_kept(ping, monkeypatch):
    # Another process creates the id between our first read and our create.
    existing = "b" * 32
    ping.dir.mkdir(parents=True)
    (ping.dir / "install_id").write_text(existing, encoding="utf-8")
    real_read_text = Path.read_text
    missed = []

    def read_text(self, *a, **k):
        if self.name == "install_id" and not missed:
            missed.append(True)
            raise FileNotFoundError(str(self))
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert ping.mod.install_id() == existing
    assert real_read_text(ping.dir / "install_id", encoding="utf-8") == existing


def test_an_empty_id_file_left_behind_is_replaced(ping):
    ping.dir.mkdir(parents=True)
    (ping.dir / "install_id").write_text("", encoding="utf-8")
    value = ping.mod.install_id()
    assert re.fullmatch(r"[0-9a-f]{32}", value)
    assert ping.mod.install_id() == value


def test_set_enabled_is_atomic(ping, monkeypatch):
    ping.mod.set_enabled(False)

    def no_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(ping.mod.os, "replace", no_replace)
    with pytest.raises(OSError):
        ping.mod.set_enabled(True)
    assert json.loads((ping.dir / "usage_ping.json").read_text(encoding="utf-8"))["enabled"] is False
    assert sorted(p.name for p in ping.dir.iterdir()) == ["usage_ping.json"]  # temp cleaned up


def test_set_enabled_leaves_no_temp_files(ping):
    ping.mod.set_enabled(False)
    ping.mod.set_enabled(True)
    assert sorted(p.name for p in ping.dir.iterdir()) == ["usage_ping.json"]


def test_maybe_send_reads_the_date_once(ping, monkeypatch):
    calls = []

    def today():
        calls.append(1)
        return "2026-10-06" if len(calls) == 1 else "2026-10-07"

    monkeypatch.setattr(ping.mod, "_today", today)
    ping.mod.maybe_send("cli").join(5)
    assert len(calls) == 1
    assert ping.sent[0][1]["date"] == "2026-10-06"
    assert (ping.dir / "last_ping_utc").read_text(encoding="utf-8") == "2026-10-06"


def test_a_redirected_answer_is_not_marked_as_sent(ping):
    ping.final_url = "https://elsewhere.example/v1/ping"
    ping.mod.maybe_send("cli").join(5)
    assert len(ping.sent) == 1
    assert not (ping.dir / "last_ping_utc").exists()


def test_status_preview_and_decision_survive_a_missing_home(ping, monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(ping.mod, "state_dir", no_home)
    assert ping.mod.decision() == (True, "default")
    status = ping.mod.status()
    assert status["will_send"] is True and status["install_id_prefix"] == ""
    assert status["last_sent"] == ""
    assert set(json.loads(ping.mod.preview())) == {
        "schema", "install_id", "version", "os", "python", "client", "date"}
    assert ping.mod.maybe_show_notice(io.StringIO()) is False
    assert ping.mod.maybe_send("cli") is None


def test_an_explicit_on_wins_over_an_earlier_opt_out(ping, monkeypatch, tmp_path):
    # `engram telemetry off` also records a remote opt-out that `on` does not clear.
    monkeypatch.setattr(ping.mod, "_legacy_opted_out", ping.orig_legacy)
    store = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    _write_cfg(store, {"enabled": True, "remote_enabled": False,
                       "remote_opted_out_at": "2026-01-01T00:00:00+00:00"})
    assert ping.mod.decision() == (False, "earlier opt-out")
    ping.mod.set_enabled(True)
    assert ping.mod.decision() == (True, "default")
    ping.mod.set_enabled(False)
    assert ping.mod.decision() == (False, "settings")


class _UrlOnlyResp(_Resp):
    """A response exposing only ``url`` (geturl is deprecated)."""

    geturl = None


class _GeturlOnlyResp(_Resp):
    """An older response exposing only geturl()."""

    def __init__(self, status: int, url: str) -> None:
        self.status = status
        self._final = url

    def geturl(self) -> str:
        return self._final


@pytest.mark.parametrize("resp_cls", [_UrlOnlyResp, _GeturlOnlyResp])
@pytest.mark.parametrize("final,marked", [
    ("https://telemetry.piia-engram.com/v1/ping", True),
    ("https://elsewhere.example/v1/ping", False),
])
def test_the_final_url_is_read_from_url_or_geturl(ping, monkeypatch, resp_cls, final, marked):
    monkeypatch.setattr(ping.mod, "urlopen", lambda req, timeout=None: resp_cls(204, final))
    ping.mod.maybe_send("cli").join(5)
    assert (ping.dir / "last_ping_utc").exists() is marked


# --- MCP server wiring ----------------------------------------------------------


def _fake_ctx(name):
    info = SimpleNamespace(name=name, version="1.0")
    return SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(clientInfo=info)))


def _no_context():
    raise LookupError("no request context")


@pytest.fixture
def track(monkeypatch, tmp_path):
    """mcp_server._track with the ping, the stats tracker and session records stubbed."""
    from piia_engram import mcp_server as ms

    calls = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(ms._session, "client_info", {})
    monkeypatch.setattr(ms._session, "tool_name", ms._session.tool_name)
    monkeypatch.setattr(ms._session, "record", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_tracker", None)
    monkeypatch.setattr(ms, "_engram", SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(ms.mcp, "get_context", _no_context)
    read_tool = next(name for name, cls in sorted(ms.TOOL_GOVERNANCE_CLASS.items()) if cls == "read")
    write_tool = next(name for name, cls in sorted(ms.TOOL_GOVERNANCE_CLASS.items())
                      if cls in ms.WRITE_GATE_CLASSES_MUTATING)
    return SimpleNamespace(ms=ms, calls=calls, read_tool=read_tool, write_tool=write_tool)


def test_a_tool_call_starts_the_ping_with_the_client_name(track, monkeypatch):
    monkeypatch.setattr(track.ms.mcp, "get_context", lambda: _fake_ctx("claude-code"))
    track.ms._track(track.read_tool)
    track.ms._track(track.read_tool)
    # usage_ping itself allows one attempt per process; the name stays the detected one.
    assert track.calls and set(track.calls) == {"claude-code"}


def test_a_client_without_info_still_counts_as_unknown(track):
    track.ms._track(track.read_tool)
    assert track.calls == ["unknown"]


def test_client_detection_alone_does_not_start_the_ping(track, monkeypatch):
    monkeypatch.setattr(track.ms.mcp, "get_context", lambda: _fake_ctx("claude-code"))
    track.ms._detect_mcp_client_once()
    assert track.calls == []


def test_a_non_owner_read_does_not_start_the_ping(track, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setattr(track.ms._gov_rt, "caller_is_owner", lambda root, **kw: False)
    track.ms._track(track.read_tool)
    track.ms._track_read_safe(track.read_tool)
    assert track.calls == []


def test_an_owner_read_starts_the_ping(track, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setattr(track.ms._gov_rt, "caller_is_owner", lambda root, **kw: True)
    track.ms._track(track.read_tool)
    assert track.calls == ["unknown"]


def test_a_refused_non_owner_write_does_not_start_the_ping(track, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setattr(track.ms._gov_rt, "caller_is_owner", lambda root, **kw: False)
    track.ms._track(track.write_tool, success=False)
    assert track.calls == []


def test_the_server_notice_goes_to_stderr_on_every_start_without_a_marker(ping, capsys):
    from piia_engram import mcp_server as ms

    ms._show_usage_notice()
    ms._show_usage_notice()
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.count("Engram sends one anonymous usage ping") == 2
    assert not (ping.dir / "notice_shown").exists()


def test_the_server_loads_the_ping_only_from_the_package():
    text = (Path(SRC) / "piia_engram" / "mcp_server.py").read_text(encoding="utf-8")
    assert "from piia_engram import usage_ping" in text
    assert not re.search(r"^\s*import usage_ping\b", text, re.M)


# --- CLI wiring -----------------------------------------------------------------


def test_mutating_cli_commands_start_the_ping(ping, monkeypatch):
    from piia_engram import setup_wizard as sw

    calls = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(sw, "run_pin", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["engram", "pin"])
    with pytest.raises(SystemExit):
        sw.main()
    assert calls == ["cli"]


def test_mutating_cli_commands_show_the_notice_on_stderr_not_stdout(ping, monkeypatch, capsys):
    from piia_engram import setup_wizard as sw

    monkeypatch.setattr(up, "maybe_send", lambda client="cli": None)
    monkeypatch.setattr(sw, "run_pin", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["engram", "pin"])
    with pytest.raises(SystemExit):
        sw.main()
    out = capsys.readouterr()
    assert "engram telemetry off" in out.err
    assert "engram telemetry" not in out.out
    assert (ping.dir / "notice_shown").exists()  # CLI users see it once


@pytest.mark.parametrize("command,handler", [
    ("dock-status", "_run_dock_status"), ("dock-list", "_run_dock_list"),
    ("capabilities", "_run_capabilities_cli"), ("doctor", "run_doctor"),
    ("weekly", "_run_weekly"),
])
def test_zero_write_and_machine_facing_commands_never_start_the_ping(
        ping, monkeypatch, capsys, command, handler):
    from piia_engram import setup_wizard as sw

    calls = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(sw, handler, lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["engram", command])
    with pytest.raises(SystemExit):
        sw.main()
    assert calls == []
    assert "engram telemetry off" not in capsys.readouterr().err


@pytest.mark.parametrize("sub", ["once", "status", "start"])
def test_the_session_watcher_never_starts_the_ping(ping, monkeypatch, capsys, sub):
    # The watcher runs from autostart / schedulers: background runs are not use.
    from piia_engram import setup_wizard as sw
    from piia_engram.watcher import install as watcher_install

    calls = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(watcher_install, "run_watcher_cli", lambda args: 0)
    monkeypatch.setattr(sys, "argv", ["engram", "watcher", sub])
    with pytest.raises(SystemExit):
        sw.main()
    assert calls == []
    assert "engram telemetry off" not in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["dock-something-new"], ["dock-status"], ["-h"], ["--help"], ["help"],
    ["--version"], ["-V"], ["version"],
])
def test_help_version_and_dock_commands_never_start_the_ping(ping, monkeypatch, capsys, args):
    from piia_engram import setup_wizard as sw
    from piia_engram import update_check

    calls = []
    reminders = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(update_check, "maybe_print_update_notice", lambda *a, **k: reminders.append(1))
    monkeypatch.setattr(sw, "_run_dock_status", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["engram", *args])
    try:
        sw.main()
    except SystemExit:
        pass
    assert calls == []
    assert "engram telemetry off" not in capsys.readouterr().err
    assert reminders == []


def test_the_ping_only_exclusions_leave_the_update_reminder_alone():
    from piia_engram import setup_wizard as sw

    assert sw._PING_SKIP_EXTRA == ("telemetry", "watcher")
    assert not set(sw._PING_SKIP_EXTRA) & set(sw._QUIET_COMMANDS)


def test_quiet_commands_are_one_shared_list():
    from piia_engram import setup_wizard as sw

    required = {
        "doctor", "capabilities", "continuity", "dock-status", "dock-resume", "dock-quality",
        "dock-governance", "dock-review-queue", "dock-quality-action", "dock-search",
        "dock-portrait", "dock-archived", "dock-list", "dock-playbooks", "dock-get-lang",
        "dock-onboard-scan", "weekly", "migrate-project", "preview", "status",
        "review", "import", "repair-encoding", "retention"}
    assert required <= set(sw._QUIET_COMMANDS)
    assert len(sw._QUIET_COMMANDS) == len(set(sw._QUIET_COMMANDS))


def test_engram_telemetry_never_starts_the_ping(ping, monkeypatch, capsys):
    # `ping` keeps the ping state in tmp, so the status call reads nothing real.
    from piia_engram import setup_wizard as sw

    calls = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": calls.append(client))
    monkeypatch.setattr(sys, "argv", ["engram", "telemetry", "status"])
    try:
        sw.main()
    except SystemExit:
        pass
    assert calls == []


@pytest.mark.parametrize("argv", [["engram"], ["engram", "setup"]])
def test_setup_shows_the_notice_and_starts_the_ping_only_after_its_questions(monkeypatch, argv):
    from piia_engram import setup_wizard as sw

    order = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": order.append(("send", client)))
    monkeypatch.setattr(up, "maybe_show_notice",
                        lambda stream, **kw: order.append(("notice", stream)))
    monkeypatch.setattr(sw, "run_setup", lambda **kw: order.append(("setup",)))
    monkeypatch.setattr(sys, "argv", argv)
    sw.main()
    assert order == [("setup",), ("notice", sys.stdout), ("send", "cli")]


def test_a_no_to_statistics_during_setup_stops_the_first_ping(ping, monkeypatch, tmp_path):
    from piia_engram import setup_wizard as sw
    from piia_engram import telemetry

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    monkeypatch.setattr(up, "_legacy_opted_out", ping.orig_legacy)
    monkeypatch.setattr(sw, "run_setup", lambda **kw: telemetry.set_enabled(False))
    monkeypatch.setattr(sys, "argv", ["engram", "setup"])
    sw.main()
    assert up.decision() == (False, "earlier opt-out")
    assert ping.sent == []
    assert not (ping.dir / "install_id").exists()


# --- engram telemetry -----------------------------------------------------------


@pytest.fixture
def tele_cli(ping, monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    from piia_engram import cli_commands

    return cli_commands._run_telemetry_cli


def test_telemetry_off_turns_the_ping_off(tele_cli, ping, capsys):
    tele_cli(["off"])
    assert ping.mod.decision() == (False, "settings")
    assert "Daily usage ping disabled" in capsys.readouterr().out


def test_telemetry_on_turns_it_back_on(tele_cli, ping):
    tele_cli(["off"])
    tele_cli(["on"])
    assert ping.mod.decision() == (True, "default")


def test_telemetry_status_shows_the_ping(tele_cli, capsys):
    tele_cli(["status"])
    out = capsys.readouterr().out
    assert "Daily usage ping: ON (decided by: default)" in out
    assert "Anonymous usage statistics:" in out  # detailed stats line unchanged


def test_telemetry_preview_shows_the_ping_body(tele_cli, capsys):
    tele_cli(["preview"])
    out = capsys.readouterr().out
    assert '"schema": "ping/1"' in out
    assert "Next payload (if enabled):" in out


def test_telemetry_reset_id(tele_cli, ping, capsys):
    first = ping.mod.install_id()
    tele_cli(["reset-id"])
    assert ping.mod.install_id() != first
    assert "New install ID:" in capsys.readouterr().out


@pytest.mark.parametrize("had_id", [True, False])
def test_telemetry_reset_id_while_off_makes_no_new_id(tele_cli, ping, monkeypatch, capsys, had_id):
    if had_id:
        ping.mod.install_id()
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    tele_cli(["reset-id"])
    assert not (ping.dir / "install_id").exists()
    out = capsys.readouterr().out
    assert "A new install ID will be created when the ping next runs." in out
    assert "New install ID:" not in out


def test_telemetry_remote_off_also_turns_the_ping_off(tele_cli, ping, capsys):
    tele_cli(["remote", "off"])
    assert ping.mod.decision() == (False, "settings")
    out = capsys.readouterr().out
    assert "Daily usage ping disabled" in out
    assert "Remote sending disabled" in out


@pytest.mark.parametrize("args,expected", [
    (["off"], [("set_enabled", False), ("set_remote_enabled", False)]),
    (["on"], [("set_enabled", True)]),
    (["remote", "off"], [("set_remote_enabled", False)]),
])
def test_a_ping_setting_that_cannot_be_saved_is_reported_and_the_rest_still_runs(
        tele_cli, ping, monkeypatch, capsys, args, expected):
    from piia_engram import telemetry

    def cannot_save(enabled):
        raise OSError("read-only folder")

    monkeypatch.setattr(up, "set_enabled", cannot_save)
    legacy_calls = []
    for name in ("set_enabled", "set_remote_enabled"):
        real = getattr(telemetry, name)

        def record(enabled, _name=name, _real=real):
            legacy_calls.append((_name, enabled))
            return _real(enabled)

        monkeypatch.setattr(telemetry, name, record)
    tele_cli(args)
    out = capsys.readouterr()
    assert "Could not save the daily ping setting: read-only folder." in out.err
    if args != ["on"]:
        assert "Use ENGRAM_TELEMETRY=0 or DO_NOT_TRACK=1 instead." in out.err
    assert "Daily usage ping" not in out.out  # no success line for the ping
    assert legacy_calls == expected


# --- isolation ------------------------------------------------------------------

_ISOLATED_STORE_FILES = tuple(Path(SRC) / "piia_engram" / name
                              for name in ("isolated_store.py", "isolated_store_launch.py"))
# The guards below switch on by themselves once the isolated store is part of this tree.
_needs_isolated_store = pytest.mark.skipif(
    not all(path.is_file() for path in _ISOLATED_STORE_FILES),
    reason="the isolated store is not part of this tree")


def _loads_ping(code: str) -> str:
    env = {**os.environ, "PYTHONPATH": SRC}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, timeout=180)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


@_needs_isolated_store
def test_the_isolated_store_never_loads_the_ping():
    code = ("import sys, piia_engram.isolated_store, piia_engram.isolated_store_launch; "
            "print('piia_engram.usage_ping' in sys.modules)")
    assert _loads_ping(code) == "False"


def test_importing_the_package_or_the_server_loads_no_ping():
    code = ("import sys, piia_engram, piia_engram.core, piia_engram.mcp_server; "
            "print('piia_engram.usage_ping' in sys.modules)")
    assert _loads_ping(code) == "False"


@_needs_isolated_store
def test_isolated_store_sources_do_not_reference_the_ping():
    for path in _ISOLATED_STORE_FILES:
        assert "usage_ping" not in path.read_text(encoding="utf-8")


@_needs_isolated_store
def test_the_ping_is_off_in_the_isolated_store_child_environment(tmp_path):
    from piia_engram import isolated_store, isolated_store_launch

    data = {name: str(tmp_path / name) for name in (
        "root", "receipts_dir", "fake_home", "cache_dir", "decision_points_dir", "deny_list_file")}
    data["deny_list_sha256"] = "0" * 64
    cfg = isolated_store.Config(data, tmp_path / "config.json")
    env = isolated_store_launch.build_child_env(dict(os.environ), cfg)
    env["PYTHONPATH"] = SRC
    code = "from piia_engram import usage_ping; print(usage_ping.decision()[0])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, timeout=180)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "False"


def _snapshot(directory: Path) -> dict[str, int] | None:
    if not directory.exists():
        return None
    return {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}


def test_the_suite_keeps_the_ping_state_out_of_the_real_profile(
        real_ping_state_dir, tmp_path, monkeypatch, capsys):
    # No `ping` fixture: only the suite-wide isolation from conftest is active.
    # Only checks the real dir; never creates anything there.
    from piia_engram import cli_commands

    if real_ping_state_dir is None:
        pytest.skip("no resolvable home directory")
    assert up.state_dir() != real_ping_state_dir
    before = _snapshot(real_ping_state_dir)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    for sub in ("on", "off", "enable", "disable"):
        cli_commands._run_telemetry_cli([sub])
    capsys.readouterr()
    assert (up.state_dir() / "usage_ping.json").is_file()  # the writes went somewhere
    assert _snapshot(real_ping_state_dir) == before


def test_the_ping_module_reads_no_hardware_or_host_identity():
    text = (Path(SRC) / "piia_engram" / "usage_ping.py").read_text(encoding="utf-8")
    for needle in ("getnode", "gethostname", "platform.node", "machine-id", "MachineGuid", "getpass"):
        assert needle not in text


# --- notice without marker, containers ------------------------------------------


def test_a_notice_without_marking_prints_and_writes_nothing(ping):
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream, mark=False) is True
    assert "engram telemetry off" in stream.getvalue()
    assert not ping.dir.exists()  # no marker, no id: nothing written at all
    again = io.StringIO()
    assert ping.mod.maybe_show_notice(again, mark=False) is True
    marked = io.StringIO()
    assert ping.mod.maybe_show_notice(marked) is True  # the one-time notice is still due


def test_no_unmarked_notice_when_off(ping, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    stream = io.StringIO()
    assert ping.mod.maybe_show_notice(stream, mark=False) is False
    assert stream.getvalue() == ""


def test_a_container_is_off(ping, monkeypatch):
    monkeypatch.setattr(ping.mod, "_in_container", lambda: True)
    assert ping.mod.decision() == (False, "container")


# Same rule as the server's ENGRAM_EPHEMERAL check: only 1 / true / yes count as on.
@pytest.mark.parametrize("value,expected", [
    ("1", (False, "container")), ("true", (False, "container")),
    ("yes", (False, "container")), ("YES", (False, "container")),
    (" True ", (False, "container")),
    ("0", (True, "default")), ("", (True, "default")), ("false", (True, "default")),
    ("off", (True, "default")), ("no", (True, "default")), ("on", (True, "default")),
])
def test_engram_ephemeral_is_off(ping, monkeypatch, value, expected):
    monkeypatch.setenv("ENGRAM_EPHEMERAL", value)
    assert ping.mod.decision() == expected


@pytest.mark.parametrize("marker", ["/.dockerenv", "/run/.containerenv"])
def test_a_container_is_detected_by_its_marker_file(ping, monkeypatch, marker):
    monkeypatch.setattr(ping.mod.os.path, "isfile", lambda path: path == marker)
    assert ping.orig_in_container() is True
    monkeypatch.setattr(ping.mod.os.path, "isfile", lambda path: False)
    assert ping.orig_in_container() is False


def test_a_failing_marker_check_is_not_a_container(ping, monkeypatch):
    def broken(path):
        raise OSError("no access")

    monkeypatch.setattr(ping.mod.os.path, "isfile", broken)
    assert ping.orig_in_container() is False


@pytest.mark.parametrize("value,expected", [
    ("10.96.0.1", True), ("", False), ("   ", False),
])
def test_kubernetes_counts_as_a_container(ping, monkeypatch, value, expected):
    monkeypatch.setattr(ping.mod.os.path, "isfile", lambda path: False)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", value)
    assert ping.orig_in_container() is expected


def test_kubernetes_turns_the_ping_off(ping, monkeypatch):
    monkeypatch.setattr(ping.mod, "_in_container", ping.orig_in_container)
    monkeypatch.setattr(ping.mod.os.path, "isfile", lambda path: False)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    assert ping.mod.decision() == (False, "container")
