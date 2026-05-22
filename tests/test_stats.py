"""piia_engram.stats 单元测试。"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from piia_engram.stats import _gh, _pypi_recent, log_stats, main, run_stats


# ── _gh tests ────────────────────────────────────────────────────────


def test_gh_success():
    """_gh 返回 JSON 响应。"""
    payload = {"stargazers_count": 42}
    result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload).encode()
    )
    with patch("piia_engram.stats.subprocess.run", return_value=result):
        assert _gh("") == payload


def test_gh_nonzero_returncode():
    """非零退出码时返回 None。"""
    result = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")
    with patch("piia_engram.stats.subprocess.run", return_value=result):
        assert _gh("") is None


def test_gh_exception():
    """subprocess 异常时返回 None，不崩溃。"""
    with patch(
        "piia_engram.stats.subprocess.run",
        side_effect=FileNotFoundError("gh not found"),
    ):
        assert _gh("") is None


def test_gh_timeout():
    """超时时返回 None。"""
    with patch(
        "piia_engram.stats.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
    ):
        assert _gh("traffic/views") is None


def test_gh_invalid_json():
    """无法解析 JSON 时返回 None。"""
    result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"not valid json"
    )
    with patch("piia_engram.stats.subprocess.run", return_value=result):
        assert _gh("") is None


def test_gh_endpoint_formatting():
    """endpoint 参数应正确拼接到 URL。"""
    calls = []

    def capture_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")

    with patch("piia_engram.stats.subprocess.run", side_effect=capture_run):
        _gh("traffic/views")

    assert len(calls) == 1
    assert "repos/Patdolitse/piia-engram/traffic/views" in calls[0][2]


# ── _pypi_recent tests ───────────────────────────────────────────────


def test_pypi_recent_success():
    """_pypi_recent 返回下载量数据。"""
    payload = {"data": {"last_day": 10, "last_week": 50, "last_month": 200}}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = _pypi_recent()
        assert result == payload


def test_pypi_recent_network_error():
    """网络异常时返回 None。"""
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        assert _pypi_recent() is None


# ── run_stats tests ──────────────────────────────────────────────────


def test_run_stats_all_available(capsys):
    """所有 API 可用时正常打印。"""
    repo_data = {
        "stargazers_count": 100,
        "forks_count": 10,
        "open_issues_count": 5,
        "subscribers_count": 3,
    }
    views_data = {"count": 200, "uniques": 50, "views": []}
    clones_data = {"count": 30, "uniques": 10, "clones": []}
    refs_data = [{"referrer": "google.com", "count": 20, "uniques": 15}]
    pypi_data = {"data": {"last_day": 10, "last_week": 50, "last_month": 200}}

    def mock_gh(endpoint=""):
        mapping = {
            "": repo_data,
            "traffic/views": views_data,
            "traffic/clones": clones_data,
            "traffic/popular/referrers": refs_data,
        }
        return mapping.get(endpoint.rstrip("/"))

    with (
        patch("piia_engram.stats._gh", side_effect=mock_gh),
        patch("piia_engram.stats._pypi_recent", return_value=pypi_data),
    ):
        run_stats()

    out = capsys.readouterr().out
    assert "Stars:" in out
    assert "100" in out
    assert "Page Views" in out
    assert "Git Clones" in out
    assert "Top Referrers" in out
    assert "google.com" in out
    assert "PyPI Downloads" in out
    assert "50" in out  # last_week


def test_run_stats_no_apis(capsys):
    """所有 API 不可用时应打印警告而不崩溃。"""
    with (
        patch("piia_engram.stats._gh", return_value=None),
        patch("piia_engram.stats._pypi_recent", return_value=None),
    ):
        run_stats()

    out = capsys.readouterr().out
    assert "GitHub API unavailable" in out
    assert "PyPI stats unavailable" in out


def test_run_stats_with_daily_views(capsys):
    """有每日数据时应打印最近条目。"""
    views_data = {
        "count": 100,
        "uniques": 30,
        "views": [
            {"timestamp": "2026-05-20T00:00:00Z", "count": 25, "uniques": 10},
            {"timestamp": "2026-05-21T00:00:00Z", "count": 30, "uniques": 12},
        ],
    }

    def mock_gh(endpoint=""):
        if "views" in endpoint:
            return views_data
        return None

    with (
        patch("piia_engram.stats._gh", side_effect=mock_gh),
        patch("piia_engram.stats._pypi_recent", return_value=None),
    ):
        run_stats()

    out = capsys.readouterr().out
    assert "2026-05-20" in out
    assert "2026-05-21" in out


def test_run_stats_with_daily_clones(capsys):
    """有每日 clone 数据时应打印最近条目。"""
    clones_data = {
        "count": 50,
        "uniques": 20,
        "clones": [
            {"timestamp": "2026-05-19T00:00:00Z", "count": 15, "uniques": 8},
            {"timestamp": "2026-05-20T00:00:00Z", "count": 20, "uniques": 10},
        ],
    }

    def mock_gh(endpoint=""):
        if "clones" in endpoint:
            return clones_data
        return None

    with (
        patch("piia_engram.stats._gh", side_effect=mock_gh),
        patch("piia_engram.stats._pypi_recent", return_value=None),
    ):
        run_stats()

    out = capsys.readouterr().out
    assert "2026-05-19" in out
    assert "2026-05-20" in out
    assert "Git Clones" in out


# ── log_stats tests ─────────────────────────────────────────────────


def test_log_stats_creates_file(tmp_path, monkeypatch, capsys):
    """log_stats should append JSON snapshot to stats.log."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    repo_data = {"stargazers_count": 42, "forks_count": 5}
    views_data = {"count": 100, "uniques": 30}
    clones_data = {"count": 20, "uniques": 10}
    pypi_data = {"data": {"last_day": 5, "last_week": 25, "last_month": 100}}

    def mock_gh(endpoint=""):
        mapping = {
            "": repo_data,
            "traffic/views": views_data,
            "traffic/clones": clones_data,
        }
        return mapping.get(endpoint.rstrip("/"))

    with (
        patch("piia_engram.stats._gh", side_effect=mock_gh),
        patch("piia_engram.stats._pypi_recent", return_value=pypi_data),
    ):
        log_stats()

    log_path = tmp_path / "stats.log"
    assert log_path.is_file()
    line = log_path.read_text(encoding="utf-8").strip()
    snapshot = json.loads(line)
    assert snapshot["github"]["stars"] == 42
    assert snapshot["views_14d"]["total"] == 100
    assert snapshot["clones_14d"]["total"] == 20
    assert snapshot["pypi"]["last_day"] == 5
    assert "timestamp" in snapshot

    out = capsys.readouterr().out
    assert "stats.log" in out


def test_log_stats_no_apis(tmp_path, monkeypatch, capsys):
    """log_stats with no APIs should still write timestamp."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    with (
        patch("piia_engram.stats._gh", return_value=None),
        patch("piia_engram.stats._pypi_recent", return_value=None),
    ):
        log_stats()

    log_path = tmp_path / "stats.log"
    snapshot = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert "timestamp" in snapshot
    assert "github" not in snapshot


def test_log_stats_appends(tmp_path, monkeypatch, capsys):
    """Multiple log_stats calls should append, not overwrite."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    with (
        patch("piia_engram.stats._gh", return_value=None),
        patch("piia_engram.stats._pypi_recent", return_value=None),
    ):
        log_stats()
        log_stats()

    log_path = tmp_path / "stats.log"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


# ── main() tests ────────────────────────────────────────────────────


def test_main_default_runs_stats(capsys):
    """main() without --log should call run_stats."""
    with (
        patch("piia_engram.stats._gh", return_value=None),
        patch("piia_engram.stats._pypi_recent", return_value=None),
        patch("sys.argv", ["engram", "stats"]),
    ):
        main()

    out = capsys.readouterr().out
    assert "Engram Stats" in out


def test_main_log_runs_log_stats(tmp_path, monkeypatch, capsys):
    """main() with --log should call log_stats."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    with (
        patch("piia_engram.stats._gh", return_value=None),
        patch("piia_engram.stats._pypi_recent", return_value=None),
        patch("sys.argv", ["engram", "stats", "--log"]),
    ):
        main()

    assert (tmp_path / "stats.log").is_file()
