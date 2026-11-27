"""Daily anonymous usage counts (usage_stats.py) and their wiring.

Every request in this file goes to a stub server on 127.0.0.1; a socket guard
fails the test if anything tries to reach another host.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram import usage_ping as up
from piia_engram import usage_stats as us

SRC = Path(__file__).resolve().parents[1] / "src" / "piia_engram"
TODAY = "2026-11-20"
YESTERDAY = "2026-11-19"
_OPT_OUT_ENV = ("DO_NOT_TRACK", "NO_TELEMETRY", "ENGRAM_TELEMETRY", "ENGRAM_TELEMETRY_REMOTE",
                "ENGRAM_TELEMETRY_URL", "ENGRAM_TEST", "ENGRAM_EPHEMERAL", "ENGRAM_TOOLS",
                "ENGRAM_DIR", "KUBERNETES_SERVICE_HOST", *up._CI_VARS)
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    """Any connection to a host other than this machine fails the test."""
    real_connect = socket.socket.connect
    refused: list[object] = []

    def guarded(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            refused.append(address)
            raise AssertionError(f"test tried to reach {address!r}")
        return real_connect(sock, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield refused
    assert refused == []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        stub = self.server.stub
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        stub.requests.append((self.path, body.decode("utf-8"), dict(self.headers.items())))
        if stub.delay:
            stub.release.wait(stub.delay)
        if stub.status == 302:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(stub.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def stub():
    """A local HTTP server that records every POST and answers with stub.status."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    ns = SimpleNamespace(requests=[], status=204, delay=0.0, release=threading.Event(),
                         url=f"http://127.0.0.1:{server.server_port}/v1/counts")
    server.stub = ns
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        yield ns
    finally:
        ns.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def stats(tmp_path, monkeypatch, stub):
    """usage_stats with a clean env, state in tmp, and the endpoint on the stub."""
    for var in _OPT_OUT_ENV:
        monkeypatch.delenv(var, raising=False)
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(up, "state_dir", lambda: state)
    monkeypatch.setattr(up, "_in_test", lambda: False)
    monkeypatch.setattr(up, "_in_container", lambda: False)
    monkeypatch.setattr(up, "_today", lambda: TODAY)
    monkeypatch.setattr(us, "_today", lambda: TODAY)
    monkeypatch.setattr(us, "_started", False)
    monkeypatch.setattr(us, "_pending", {})
    monkeypatch.setattr(us, "_unflushed", 0)
    monkeypatch.setattr(us, "TIMEOUT_SECONDS", 2)
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", stub.url)
    return SimpleNamespace(mod=us, dir=state, home=home, stub=stub, spool=state / "stats-spool")


def _spool(stats, day=YESTERDAY, tools=None, **extra):
    """Write one spool file the way a finished process would have left it."""
    stats.spool.mkdir(parents=True, exist_ok=True)
    n = len(list(stats.spool.iterdir()))
    body = {"day": day, "version": "4.29.0", "tier": "core",
            "tools": tools if tools is not None else {"get_resume_brief": [3, 0]},
            "errors": {}, **extra}
    path = stats.spool / f"{day}-{1000 + n}-{n:08x}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _not_bucketed(body):
    """Every value in a row that is not a fixed label or a bucket token.

    The daily ID, the date, the version and the Python version are random or
    numeric by nature, so they are checked by format and never searched as text:
    a random ID may contain any digits.
    """
    problems = []
    for key in ("schema", "os", "tier", "install_age", "adoption"):
        allowed = {"schema": {us.SCHEMA}, "os": up._OS_VALUES, "tier": us.TIERS,
                   "install_age": us.INSTALL_AGES, "adoption": us.ADOPTIONS}[key]
        if body.get(key) not in allowed:
            problems.append((key, body.get(key)))
    for key, pattern in (("daily_id", us._DAILY_ID_RE), ("date", up._DATE_RE),
                         ("version", up._VERSION_RE), ("python", up._PYTHON_RE)):
        value = body.get(key)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            problems.append((key, value))
    tools = body.get("tools")
    for name, pair in (tools.items() if isinstance(tools, dict) else [("tools", tools)]):
        if not isinstance(pair, dict) or set(pair) != {"ok", "err"}:
            problems.append((name, pair))
            continue
        problems += [(f"{name}.{k}", v) for k, v in pair.items() if v not in us.COUNT_BUCKETS]
    for field, buckets in (("errors", us.COUNT_BUCKETS), ("knowledge", us.SIZE_BUCKETS)):
        mapping = body.get(field)
        if not isinstance(mapping, dict):
            problems.append((field, mapping))
            continue
        problems += [(f"{field}.{k}", v) for k, v in mapping.items() if v not in buckets]
    problems += [(key, body[key]) for key in body if key not in us.FIELDS]
    return problems


def _bodies(stats):
    return [json.loads(body) for _path, body, _headers in stats.stub.requests]


def _store_config(stats, cfg):
    root = stats.home / ".engram"
    root.mkdir(exist_ok=True)
    (root / "telemetry_config.json").write_text(json.dumps(cfg), encoding="utf-8")


# --- decision ---------------------------------------------------------------------


def test_default_is_on(stats):
    assert us.decision() == (True, "default")


def test_an_explicit_false_turns_it_off(stats):
    _store_config(stats, {"enabled": False})
    assert us.decision() == (False, "earlier opt-out")


_REFUSALS = [
    {"enabled": False},
    {"remote_enabled": False},
    {"enabled": False, "opted_out_at": "2026-01-01T00:00:00+00:00"},
    {"enabled": True, "remote_enabled": False, "remote_opted_out_at": "2026-01-01T00:00:00+00:00"},
    {"enabled": False, "remote_enabled": False},
    {"enabled": True, "remote_enabled": False},
]


@pytest.mark.parametrize("cfg", _REFUSALS)
def test_earlier_opt_outs_stay_off_until_an_explicit_on(stats, cfg):
    _spool(stats)
    _store_config(stats, cfg)
    assert us.decision()[0] is False
    assert us.drain() == 0 and stats.stub.requests == []
    # 4.23 / 4.24 wrote {"enabled": true} for `engram telemetry on`, which then meant
    # the ping and the local log only. That is not a consent to the counts.
    _spool(stats)
    (stats.dir / "usage_ping.json").write_text(
        json.dumps({"enabled": True, "changed_at": "2026-10-10"}), encoding="utf-8")
    assert up.decision() == (True, "default")  # the ping keeps its own rule
    assert us.decision() == (False, "earlier opt-out")
    assert us.drain() == 0 and stats.stub.requests == []
    # The same call without the marker, as an older client would make it.
    up.set_enabled(True)
    assert us.decision() == (False, "earlier opt-out")
    # Only this version's `on` / `remote on` records a consent to the counts.
    up.set_enabled(True, counts_consent=True)
    assert json.loads((stats.dir / "usage_ping.json").read_text(encoding="utf-8"))[
        "counts_consent"] == "stats/2"
    assert us.decision() == (True, "default")
    up.set_enabled(False)
    assert "counts_consent" not in (stats.dir / "usage_ping.json").read_text(encoding="utf-8")


def test_a_consent_marker_without_enabled_does_not_count(stats):
    _store_config(stats, {"remote_enabled": False})
    (stats.dir).mkdir(parents=True, exist_ok=True)
    (stats.dir / "usage_ping.json").write_text(
        json.dumps({"counts_consent": "stats/2"}), encoding="utf-8")
    assert us.decision() == (False, "earlier opt-out")
    (stats.dir / "usage_ping.json").write_text(
        json.dumps({"enabled": True, "counts_consent": "stats/1"}), encoding="utf-8")
    assert us.decision() == (False, "earlier opt-out")


def test_a_statistics_opt_in_after_an_old_ping_opt_out_is_not_a_refusal(stats):
    """`legacy_ping_opted_out_at` is the ping's history; the same config has the
    statistics and their remote sending turned on by the user."""
    _store_config(stats, {"enabled": True, "remote_enabled": True,
                          "legacy_ping_opted_out_at": "2026-01-01T00:00:00+00:00"})
    assert us.decision() == (False, "earlier opt-out")  # the ping's own layer
    up.set_enabled(True)  # old-format on: the ping is back, and nothing here refused the counts
    assert us.decision() == (True, "default")


def test_a_config_without_a_choice_is_not_an_opt_out(stats):
    _store_config(stats, {"local_uuid": "x", "enabled": True})
    assert us.decision() == (True, "default")


def test_the_store_named_by_engram_dir_is_read_too(stats, tmp_path, monkeypatch):
    other = tmp_path / "other-store"
    other.mkdir()
    (other / "telemetry_config.json").write_text(json.dumps({"remote_enabled": False}),
                                                 encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", str(other))
    assert us.decision() == (False, "earlier opt-out")


_ENV_OFF = [("DO_NOT_TRACK", "1"), ("NO_TELEMETRY", "1"), ("ENGRAM_TELEMETRY", "0"),
            ("ENGRAM_TELEMETRY_REMOTE", "0"), ("ENGRAM_EPHEMERAL", "1"),
            *[(var, "true") for var in up._CI_VARS]]


@pytest.mark.parametrize("var,value", _ENV_OFF)
def test_an_off_switch_sends_nothing_and_removing_it_sends_one_row(stats, monkeypatch, var, value):
    _spool(stats)
    monkeypatch.setenv(var, value)
    assert us.decision()[0] is False
    assert us.drain() == 0
    assert stats.stub.requests == []
    monkeypatch.delenv(var)
    _spool(stats)  # a user-level "no" removes what was waiting
    assert us.drain() == 1
    assert len(stats.stub.requests) == 1


def test_do_not_track_wins_over_an_explicit_on(stats, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setenv("ENGRAM_TELEMETRY", "1")
    assert us.decision() == (False, "DO_NOT_TRACK")


@pytest.mark.parametrize("marker", ["container", "test"])
def test_containers_and_tests_send_nothing(stats, monkeypatch, marker):
    _spool(stats)
    monkeypatch.setattr(up, "_in_container" if marker == "container" else "_in_test", lambda: True)
    assert us.decision() == (False, marker)
    assert us.drain() == 0 and stats.stub.requests == []


def test_the_command_off_sends_nothing(stats):
    _spool(stats)
    up.set_enabled(False)
    assert us.decision() == (False, "settings")
    assert us.drain() == 0 and stats.stub.requests == []
    assert list(stats.spool.iterdir()) == []  # waiting counts are removed, not kept


def test_ci_keeps_the_waiting_files(stats, monkeypatch):
    path = _spool(stats)
    monkeypatch.setenv("CI", "true")
    assert us.drain() == 0
    assert path.exists()


# --- what is sent -------------------------------------------------------------------


def test_only_an_ended_day_is_sent(stats):
    _spool(stats, day=TODAY)
    assert us.drain() == 0 and stats.stub.requests == []
    _spool(stats, day=YESTERDAY)
    assert us.drain() == 1
    [body] = _bodies(stats)
    assert body["date"] == YESTERDAY
    assert [p.name[:10] for p in stats.spool.iterdir()] == [TODAY]


def test_the_row_has_exactly_the_documented_fields_and_only_buckets(stats):
    _spool(stats, tools={"get_resume_brief": [3, 0], "search_knowledge": [120, 7]},
           errors={"timeout": 7}, knowledge={"lessons": "10_49", "decisions": "1_9"})
    assert us.drain() == 1
    [(path, raw, headers)] = stats.stub.requests
    body = json.loads(raw)
    assert path == "/v1/counts"
    assert set(body) == us.FIELDS
    assert body["schema"] == "stats/2" and body["version"] == "4.29.0"
    assert body["tools"] == {"get_resume_brief": {"ok": "1_4", "err": "0"},
                             "search_knowledge": {"ok": "100_plus", "err": "5_9"}}
    assert body["errors"] == {"timeout": "5_9"}
    assert body["knowledge"] == {"decisions": "1_9", "lessons": "10_49"}
    assert body["tier"] == "core" and body["os"] in up._OS_VALUES
    # No exact number anywhere in the row (checked value by value: the random
    # daily ID may contain any digits), and no install ID field.
    assert _not_bucketed(body) == []
    assert "install_id" not in body
    assert headers.get("User-Agent") == "piia-engram"
    assert us.valid_payload(body)


def test_the_exact_number_check_ignores_the_random_id_but_not_a_real_count(stats, monkeypatch):
    # A daily ID that happens to contain the digits of a count used in this row.
    monkeypatch.setattr(us, "daily_id", lambda secret, day: "b912097d33539f01")
    _spool(stats, tools={"search_knowledge": [120, 7]}, errors={"timeout": 7})
    assert us.drain() == 1
    [(_, raw, _headers)] = stats.stub.requests
    body = json.loads(raw)
    assert body["daily_id"] == "b912097d33539f01" and "120" in raw  # a text search would trip here
    assert _not_bucketed(body) == []
    # A row that really carried an exact count is still caught, wherever it sits.
    assert _not_bucketed({**body, "tools": {"search_knowledge": {"ok": 120, "err": "5_9"}}}) == [
        ("search_knowledge.ok", 120)]
    assert _not_bucketed({**body, "tools": {"search_knowledge": {"ok": "120", "err": "5_9"}}}) == [
        ("search_knowledge.ok", "120")]
    assert _not_bucketed({**body, "errors": {"timeout": 7}}) == [("errors.timeout", 7)]
    assert _not_bucketed({**body, "knowledge": {"lessons": 47}}) == [("knowledge.lessons", 47)]
    assert _not_bucketed({**body, "total_calls": 120}) == [("total_calls", 120)]
    assert _not_bucketed({**body, "tier": "120"}) == [("tier", "120")]


def test_content_and_paths_cannot_get_into_the_row(stats, monkeypatch):
    canary = "ZZ_CANARY_7f3a"
    monkeypatch.setenv("ENGRAM_TOOLS", f"C:\\Users\\{canary}\\tools")
    us.record(f"/home/{canary}/secret notes.md", True)
    us.record("search_knowledge", False, error_category=f"Traceback {canary}")
    us.note_knowledge({"lessons": 47, "decisions": canary, canary: 3})
    us.flush()
    [path] = list(stats.spool.iterdir())
    assert canary not in path.read_text(encoding="utf-8")
    path.rename(path.with_name(path.name.replace(TODAY, YESTERDAY)))
    assert us.drain() == 1
    [(_, raw, _headers)] = stats.stub.requests
    body = json.loads(raw)
    assert canary not in raw and str(stats.home) not in raw and "Users" not in raw
    assert body["tools"] == {"other": {"ok": "1_4", "err": "0"},
                             "search_knowledge": {"ok": "0", "err": "1_4"}}
    assert body["errors"] == {"unknown": "1_4"} and body["tier"] == "other"
    assert body["knowledge"] == {"lessons": "10_49"}


def test_a_tampered_spool_file_cannot_add_free_text(stats):
    canary = "ZZ_CANARY_7f3a"
    _spool(stats, tools={canary: [1, 0], "get_recall": ["many", canary]},
           errors={canary: 2}, knowledge={"lessons": canary, canary: "0"}, version=canary,
           tier=canary)
    assert us.drain() == 0  # the version is not a version: the row is not sendable
    assert stats.stub.requests == [] and list(stats.spool.iterdir()) == []


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra="x"),
    lambda p: p.update(version="see /home/me/notes"),
    lambda p: p["tools"].update({"my private tool": {"ok": "1_4", "err": "0"}}),
    lambda p: p["tools"].update({"get_recall": {"ok": 3, "err": "0"}}),
    lambda p: p["tools"].update({"get_recall": {"ok": "1_4", "err": "0", "note": "x"}}),
    lambda p: p.update(tools={}),
    lambda p: p["errors"].update({"Traceback (most recent call last)": "1_4"}),
    lambda p: p["knowledge"].update({"lessons": 47}),
    lambda p: p.update(daily_id="a" * 32),
    lambda p: p.update(tier="C:\\tools"),
    lambda p: p.update(os="win32"),
    lambda p: p.pop("knowledge"),
])
def test_the_row_check_rejects_anything_outside_the_closed_sets(stats, change):
    payload = us.build_payload(YESTERDAY, us._merge([_spool(stats)]), secret="0" * 64, state={})
    assert us.valid_payload(payload)
    change(payload)
    assert not us.valid_payload(payload)


def test_unknown_tool_names_are_folded_into_other(stats):
    assert "get_resume_brief" in us.tool_names() and len(us.tool_names()) > 30
    _spool(stats, tools={"get_resume_brief": [1, 0], "not_a_tool": [2, 0], "x y": [3, 0]})
    assert us.drain() == 1
    assert _bodies(stats)[0]["tools"] == {"get_resume_brief": {"ok": "1_4", "err": "0"},
                                          "other": {"ok": "5_9", "err": "0"}}


def test_the_daily_id_changes_every_day_and_is_not_the_install_id(stats):
    _spool(stats, day="2026-11-18")
    _spool(stats, day=YESTERDAY)
    install = up.install_id()
    assert us.drain() == 2
    first, second = _bodies(stats)
    assert first["date"] == "2026-11-18" and second["date"] == YESTERDAY
    assert first["daily_id"] != second["daily_id"]
    secret = (stats.dir / "stats_secret").read_text(encoding="utf-8")
    raw = "".join(body for _p, body, _h in stats.stub.requests)
    assert install not in raw and secret not in raw and install[:16] not in raw
    assert us.daily_id(install, YESTERDAY) != second["daily_id"]


def test_at_most_two_days_per_run_and_once_per_day(stats):
    for day in ("2026-11-16", "2026-11-17", "2026-11-18"):
        _spool(stats, day=day)
    assert us.drain() == 2
    assert [b["date"] for b in _bodies(stats)] == ["2026-11-16", "2026-11-17"]
    assert us.drain() == 0  # already sent today: the third day waits
    assert len(stats.stub.requests) == 2


def test_days_older_than_a_week_are_dropped_unsent(stats):
    old = _spool(stats, day="2026-11-12")
    edge = _spool(stats, day="2026-11-13")
    assert us.drain() == 1
    assert [b["date"] for b in _bodies(stats)] == ["2026-11-13"]
    assert not old.exists() and not edge.exists()


def test_the_file_cap_removes_the_oldest_files(stats, monkeypatch):
    monkeypatch.setattr(us, "MAX_SPOOL_FILES", 3)
    paths = [_spool(stats, day=TODAY) for _ in range(5)]
    us.drain()
    assert [p.exists() for p in paths] == [False, False, True, True, True]


def test_install_age_and_adoption_are_coarse(stats):
    _spool(stats, day="2026-11-18")
    assert us.drain() == 1
    assert _bodies(stats)[0]["install_age"] == "first_day"
    assert _bodies(stats)[0]["adoption"] == "first"
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    assert state == {"first_seen": "2026-11-18", "last_version": "4.29.0", "last_sent": TODAY,
                     "last_attempt": TODAY}
    assert us._install_age("2026-11-01", "2026-11-05") == "2_7_days"
    assert us._install_age("2026-10-01", "2026-11-05") == "31_plus_days"
    assert us._install_age("garbage", "2026-11-05") == "unknown"


def test_without_an_endpoint_nothing_is_sent_and_the_counts_wait(stats, monkeypatch):
    monkeypatch.delenv("ENGRAM_TELEMETRY_URL")
    monkeypatch.setattr("piia_engram.telemetry._DEFAULT_ENDPOINT", "")
    path = _spool(stats)
    assert us.drain() == 0
    assert stats.stub.requests == [] and path.exists()


def test_buckets():
    assert [us.count_bucket(n) for n in (0, 1, 4, 5, 9, 10, 24, 25, 99, 100, 10**6)] == [
        "0", "1_4", "1_4", "5_9", "5_9", "10_24", "10_24", "25_99", "25_99", "100_plus", "100_plus"]
    assert [us.size_bucket(n) for n in (0, 9, 10, 49, 50, 199, 200, 999, 1000)] == [
        "0", "1_9", "10_49", "10_49", "50_199", "50_199", "200_999", "200_999", "1000_plus"]
    assert us.count_bucket(-3) == "0" and us.count_bucket("12") == "0" and us.size_bucket(True) == "0"


# --- failures never reach the tool ------------------------------------------------


@pytest.mark.parametrize("status", [500, 404, 302, 422])
def test_a_refused_send_keeps_the_counts_and_waits_for_the_next_day(stats, monkeypatch, status):
    path = _spool(stats)
    stats.stub.status = status
    assert us.drain() == 0
    assert path.exists() and len(stats.stub.requests) == 1
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    assert state["last_attempt"] == TODAY and "last_sent" not in state
    stats.stub.status = 204
    assert us.drain() == 0  # same UTC day: a new process does not send it again
    assert len(stats.stub.requests) == 1 and path.exists()
    monkeypatch.setattr(us, "_today", lambda: "2026-11-21")  # control: the next day it goes out
    monkeypatch.setattr(up, "_today", lambda: "2026-11-21")
    assert us.drain() == 1
    assert not path.exists() and len(stats.stub.requests) == 2


def test_an_unreachable_server_is_silent(stats, monkeypatch, capsys):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", f"http://127.0.0.1:{port}/v1/counts")
    path = _spool(stats)
    assert us.drain() == 0
    assert path.exists()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_410_drops_the_counts_and_stops_sending_for_thirty_days(stats, monkeypatch):
    path = _spool(stats)
    stats.stub.status = 410
    assert us.drain() == 0
    assert not path.exists() and len(stats.stub.requests) == 1
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    assert state["counts_endpoint_gone_at"] == TODAY
    assert us.status()["endpoint_gone_since"] == TODAY

    def on(day):
        monkeypatch.setattr(us, "_today", lambda: day)
        monkeypatch.setattr(up, "_today", lambda: day)
        _spool(stats, day=us._day_offset(day, -1))
        return us.drain()

    stats.stub.status = 204  # even if the server accepts again, the client stays quiet
    assert on("2026-11-21") == 0 and on("2026-12-19") == 0
    assert len(stats.stub.requests) == 1
    assert on("2026-12-20") == 2  # thirty days later it tries again (two waiting days)
    assert len(stats.stub.requests) == 3
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    assert "counts_endpoint_gone_at" not in state


def test_a_second_410_after_thirty_days_restarts_the_wait(stats, monkeypatch):
    _spool(stats)
    stats.stub.status = 410
    assert us.drain() == 0
    monkeypatch.setattr(us, "_today", lambda: "2026-12-20")
    monkeypatch.setattr(up, "_today", lambda: "2026-12-20")
    _spool(stats, day="2026-12-19")
    assert us.drain() == 0 and len(stats.stub.requests) == 2
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    assert state["counts_endpoint_gone_at"] == "2026-12-20"


def test_an_oversized_row_is_dropped_unsent(stats, monkeypatch):
    monkeypatch.setattr(us, "MAX_BODY_BYTES", 50)
    path = _spool(stats)
    assert us.drain() == 0
    assert stats.stub.requests == [] and not path.exists()


def test_a_slow_server_does_not_hold_up_the_caller(stats, monkeypatch):
    monkeypatch.setattr(us, "TIMEOUT_SECONDS", 0.5)
    stats.stub.delay = 10.0
    path = _spool(stats)
    start = time.monotonic()
    thread = us.maybe_send()
    for _ in range(12):
        us.record("get_resume_brief", True)  # tool calls go on meanwhile
    elapsed = time.monotonic() - start
    assert thread is not None and thread.daemon
    assert elapsed < 0.4, elapsed
    thread.join(10)
    assert not thread.is_alive()
    assert path.exists()  # timed out: kept for the next run
    stats.stub.release.set()


def test_one_attempt_per_process(stats):
    _spool(stats)
    thread = us.maybe_send()
    assert thread is not None
    thread.join(10)
    assert us.maybe_send() is None
    assert len(stats.stub.requests) == 1


def test_the_send_waits_for_the_ping_thread(stats):
    _spool(stats)
    order = []
    gate = threading.Event()

    def ping_thread():
        gate.wait(5)
        order.append("ping")

    first = threading.Thread(target=ping_thread, daemon=True)
    first.start()
    thread = us.maybe_send(after=first)
    time.sleep(0.2)
    assert stats.stub.requests == []
    gate.set()
    thread.join(10)
    first.join(5)
    assert order == ["ping"] and len(stats.stub.requests) == 1


def test_nothing_is_printed_on_success_or_failure(stats, capsys):
    us.record("get_resume_brief", True)
    us.note_knowledge({"lessons": 3})
    us.flush()
    _spool(stats)
    assert us.drain() == 1
    stats.stub.status = 500
    _spool(stats, day="2026-11-18")
    state = json.loads((stats.dir / "usage_stats.json").read_text(encoding="utf-8"))
    state.pop("last_sent")
    state.pop("last_attempt", None)
    (stats.dir / "usage_stats.json").write_text(json.dumps(state), encoding="utf-8")
    assert us.drain() == 0
    us.status()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_the_module_never_prints():
    source = (SRC / "usage_stats.py").read_text(encoding="utf-8")
    assert "print(" not in source and "sys.stdout" not in source


# --- recording ------------------------------------------------------------------------


def test_recording_writes_one_file_per_process_after_ten_calls(stats):
    for _ in range(9):
        us.record("get_resume_brief", True)
    assert not stats.spool.exists()  # memory only so far
    us.record("add_lesson", False, error_category="io")
    [path] = list(stats.spool.iterdir())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.name.startswith(TODAY)
    assert data["tools"] == {"get_resume_brief": [9, 0], "add_lesson": [0, 1]}
    assert data["errors"] == {"io": 1} and data["day"] == TODAY
    us.record("get_resume_brief", True)
    us.flush()
    assert len(list(stats.spool.iterdir())) == 1  # same file, rewritten
    assert json.loads(path.read_text(encoding="utf-8"))["tools"]["get_resume_brief"] == [10, 0]


def test_while_off_nothing_is_written(stats, monkeypatch):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    for _ in range(25):
        us.record("get_resume_brief", True)
    us.flush()
    assert not stats.spool.exists() and us._pending == {}
    monkeypatch.delenv("DO_NOT_TRACK")  # control
    us.record("get_resume_brief", True)
    us.flush()
    assert len(list(stats.spool.iterdir())) == 1


def test_an_unwritable_state_dir_is_silent(stats, monkeypatch, capsys):
    blocker = stats.dir.parent / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(up, "state_dir", lambda: blocker / "nested")
    us.record("get_resume_brief", True)
    us.flush()
    assert us.drain() == 0 and stats.stub.requests == []
    assert capsys.readouterr().out == ""


def test_a_day_rollover_closes_the_old_day(stats, monkeypatch):
    us.record("get_resume_brief", True)
    monkeypatch.setattr(us, "_today", lambda: "2026-11-21")
    monkeypatch.setattr(up, "_today", lambda: "2026-11-21")
    us.record("search_knowledge", True)
    us.flush()
    names = sorted(p.name[:10] for p in stats.spool.iterdir())
    assert names == [TODAY, "2026-11-21"] and list(us._pending) == ["2026-11-21"]
    assert us.drain() == 1
    assert _bodies(stats)[0]["date"] == TODAY


# --- status / preview --------------------------------------------------------------------


def test_status_says_why(stats, monkeypatch):
    _spool(stats)
    assert us.status() == {"will_send": True, "decided_by": "default", "endpoint": stats.stub.url,
                           "last_sent": "", "pending_days": 1, "endpoint_gone_since": ""}
    monkeypatch.setenv("NO_TELEMETRY", "1")
    assert us.status()["will_send"] is False and us.status()["decided_by"] == "NO_TELEMETRY"


def test_preview_shows_the_waiting_row_and_creates_nothing(stats):
    sample = json.loads(us.preview())
    assert set(sample) == us.FIELDS
    assert not stats.dir.exists()
    _spool(stats, tools={"get_recall": [30, 0]})
    before = sorted(p.name for p in stats.dir.rglob("*"))
    shown = json.loads(us.preview())
    assert shown["tools"] == {"get_recall": {"ok": "25_99", "err": "0"}}
    assert shown["daily_id"] == "<derived on the first send>"
    assert sorted(p.name for p in stats.dir.rglob("*")) == before
    assert stats.stub.requests == []


# --- wiring ----------------------------------------------------------------------------


def _no_context():
    raise LookupError("no request context")


@pytest.fixture
def track(stats, monkeypatch, tmp_path):
    from piia_engram import mcp_server as ms

    started = []
    monkeypatch.setattr(up, "maybe_send", lambda client="cli": None)
    monkeypatch.setattr(us, "maybe_send", lambda after=None: started.append(after))
    monkeypatch.setattr(ms._session, "client_info", {})
    monkeypatch.setattr(ms._session, "record", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_tracker", None)
    monkeypatch.setattr(ms, "_engram", SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(ms.mcp, "get_context", _no_context)
    read_tool = next(name for name, cls in sorted(ms.TOOL_GOVERNANCE_CLASS.items()) if cls == "read")
    write_tool = next(name for name, cls in sorted(ms.TOOL_GOVERNANCE_CLASS.items())
                      if cls in ms.WRITE_GATE_CLASSES_MUTATING)
    return SimpleNamespace(ms=ms, started=started, read_tool=read_tool, write_tool=write_tool)


def test_a_tool_call_is_counted_and_starts_the_send(track):
    track.ms._track(track.read_tool)
    try:
        raise TimeoutError("slow")
    except TimeoutError:
        track.ms._track(track.write_tool, success=False)
    assert track.started  # the background send was asked for
    assert us._pending[TODAY]["tools"] == {track.read_tool: [1, 0], track.write_tool: [0, 1]}
    assert us._pending[TODAY]["errors"] == {"timeout": 1}


def test_a_non_owner_read_is_not_counted(track, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setattr(track.ms._gov_rt, "caller_is_owner", lambda root, **kw: False)
    track.ms._track(track.read_tool)
    track.ms._track(track.write_tool, success=False)
    assert track.started == [] and us._pending == {}


def test_a_clean_shutdown_writes_the_counts(track, monkeypatch):
    monkeypatch.setattr(track.ms._session, "auto_save", lambda: None)
    monkeypatch.setattr(track.ms, "_engram", None)
    track.ms._track(track.read_tool)
    track.ms._engram_clean_shutdown()
    assert len(list((us._spool_dir()).iterdir())) == 1


def test_the_tool_path_has_no_synchronous_send():
    """The count path only touches memory and a local file; the network call
    lives in the daemon thread."""
    source = (SRC / "usage_stats.py").read_text(encoding="utf-8")
    record_body = source.split("def record(", 1)[1].split("\ndef ", 1)[0]
    flush_body = source.split("def flush(", 1)[1].split("\ndef ", 1)[0]
    assert "urlopen" not in record_body and "_post" not in record_body
    assert "urlopen" not in flush_body and "_post" not in flush_body and "drain" not in flush_body


# --- default endpoint, removed synchronous send, CLI ----------------------------------

COUNTS_URL = "https://telemetry.piia-engram.com/v1/counts"


def test_the_built_in_endpoint_is_the_counts_route_and_feedback_has_none(stats, monkeypatch):
    from piia_engram import telemetry

    monkeypatch.delenv("ENGRAM_TELEMETRY_URL")
    assert telemetry._DEFAULT_ENDPOINT == COUNTS_URL == us.endpoint()
    assert telemetry._DEFAULT_FEEDBACK_ENDPOINT == ""
    assert us.status()["endpoint"] == COUNTS_URL  # shown, never contacted here
    monkeypatch.setenv("ENGRAM_TELEMETRY_URL", stats.stub.url)
    assert us.endpoint() == stats.stub.url


def test_the_local_log_flush_no_longer_sends_anything(stats, monkeypatch, tmp_path):
    from piia_engram import telemetry

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    calls = []
    monkeypatch.setattr(telemetry, "urlopen", lambda *a, **k: calls.append(a) or 1 / 0)
    telemetry.set_enabled(True)
    telemetry.set_remote_enabled(True)
    tracker = telemetry.ToolCallTracker()
    tracker.record("add_lesson", success=True)
    assert tracker.flush(force=True, engram_version="9.9.9") is not None  # the local log line
    assert calls == [] and stats.stub.requests == []
    assert not hasattr(telemetry, "_send_remote")
    source = (SRC / "telemetry.py").read_text(encoding="utf-8")
    flush_body = source.split("    def flush(", 1)[1].split("\n# ----", 1)[0]
    assert "urlopen" not in flush_body and "send" not in flush_body.replace("_mark_payload_sent", "")


def test_status_reports_the_counts_not_the_old_flag(stats, monkeypatch, tmp_path):
    from piia_engram import telemetry

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    status = telemetry.get_status()
    assert status["enabled"] is False and status["remote_enabled"] is True
    assert status["endpoint"] == stats.stub.url
    assert status["phase"] == "local log off; remote counts on"
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    status = telemetry.get_status()
    assert status["remote_enabled"] is False and status["endpoint"] == "(disabled)"
    assert status["phase"] == "local log off; remote counts off"


def _cli(args, capsys):
    from piia_engram.cli_commands import _run_telemetry_cli

    _run_telemetry_cli(args)
    return capsys.readouterr().out


def test_cli_status_shows_the_counts_with_the_reason(stats, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    _spool(stats)
    out = _cli(["status"], capsys)
    assert "Daily usage counts: ON (decided by: default)" in out
    assert f"Endpoint: {stats.stub.url}" in out and "Waiting days: 1" in out
    assert "opt-in" not in out and "需显式开启" not in out
    monkeypatch.setenv("NO_TELEMETRY", "1")
    out = _cli(["status"], capsys)
    assert "Daily usage counts: OFF (decided by: NO_TELEMETRY)" in out
    assert "Daily usage ping: OFF (decided by: NO_TELEMETRY)" in out


def test_cli_preview_shows_the_real_row_and_sends_nothing(stats, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    _spool(stats, tools={"get_recall": [30, 0]})
    out = _cli(["preview"], capsys)
    assert '"schema": "stats/2"' in out and '"ok": "25_99"' in out
    assert "opt-in" not in out and "需显式开启" not in out
    assert stats.stub.requests == [] and not (stats.dir / "stats_secret").exists()


def test_cli_off_and_on_switch_the_counts(stats, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    assert us.decision()[0] is True
    out = _cli(["off"], capsys)
    assert us.decision() == (False, "settings") and "usage counts" in out
    _cli(["on"], capsys)
    assert us.decision() == (True, "default")
    _cli(["remote", "off"], capsys)
    assert us.decision()[0] is False
    _cli(["remote", "on"], capsys)
    assert us.decision() == (True, "default")


def test_the_notice_names_the_counts_in_both_languages():
    assert "usage counts" in up.NOTICE_EN and "使用计数" in up.NOTICE_ZH
    for text in (up.NOTICE_EN, up.NOTICE_ZH):
        assert "engram telemetry off" in text and "DO_NOT_TRACK=1" in text
        assert "\n" not in text


# --- turning it off removes what was waiting, at once ----------------------------------


@pytest.mark.parametrize("args", [["off"], ["disable"], ["remote", "off"], ["remote", "disable"]])
def test_cli_off_removes_the_waiting_counts_at_once(stats, monkeypatch, tmp_path, capsys, args):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    paths = [_spool(stats), _spool(stats, day=TODAY)]
    other = stats.spool / "notes.txt"  # not a spool file: left alone
    other.write_text("x", encoding="utf-8")
    us.record("get_resume_brief", True)
    _cli(args, capsys)
    assert [p.exists() for p in paths] == [False, False] and other.exists()
    assert us._pending == {}
    assert stats.stub.requests == []


def test_cli_status_and_preview_leave_the_waiting_counts_alone(stats, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    path = _spool(stats)
    _cli(["status"], capsys)
    _cli(["preview"], capsys)
    assert path.exists()


def test_cli_on_records_the_consent_and_remote_on_does_too(stats, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    _store_config(stats, {"enabled": True, "remote_enabled": False,
                          "remote_opted_out_at": "2026-01-01T00:00:00+00:00"})
    (stats.dir).mkdir(parents=True, exist_ok=True)
    (stats.dir / "usage_ping.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    assert us.decision() == (False, "earlier opt-out")
    out = _cli(["status"], capsys)
    assert "Daily usage counts: OFF (decided by: earlier opt-out)" in out
    for args in (["on"], ["remote", "on"]):
        (stats.dir / "usage_ping.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
        _cli(args, capsys)
        assert us.decision() == (True, "default"), args


# --- the local key ---------------------------------------------------------------------


def test_the_key_file_is_private_and_created_once(stats):
    import os
    import stat
    import sys

    first = us._secret()
    path = stats.dir / "stats_secret"
    assert first and path.read_text(encoding="utf-8") == first and us._secret() == first
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_key_created_by_another_process_at_the_same_moment_is_the_one_used(stats, monkeypatch):
    other = "ab" * 32
    real_open = us.os.open

    def racing_open(path, flags, mode=0o777):
        if str(path).endswith("stats_secret"):
            # Another process wins the race between our read and our create.
            stats.dir.mkdir(parents=True, exist_ok=True)
            (stats.dir / "stats_secret").write_text(other, encoding="utf-8")
        return real_open(path, flags, mode)

    monkeypatch.setattr(us.os, "open", racing_open)
    assert us._secret() == other
    assert (stats.dir / "stats_secret").read_text(encoding="utf-8") == other
