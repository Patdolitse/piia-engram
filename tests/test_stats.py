"""engram_core.stats 单元测试。"""

import json
import subprocess
from unittest.mock import MagicMock, patch

from engram_core.stats import _gh, _pypi_recent, run_stats


# ── _gh tests ────────────────────────────────────────────────────────


def test_gh_success():
    """_gh 返回 JSON 响应。"""
    payload = {"stargazers_count": 42}
    result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload).encode()
    )
    with patch("engram_core.stats.subprocess.run", return_value=result):
        assert _gh("") == payload


def test_gh_nonzero_returncode():
    """非零退出码时返回 None。"""
    result = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")
    with patch("engram_core.stats.subprocess.run", return_value=result):
        assert _gh("") is None


def test_gh_exception():
    """subprocess 异常时返回 None，不崩溃。"""
    with patch(
        "engram_core.stats.subprocess.run",
        side_effect=FileNotFoundError("gh not found"),
    ):
        assert _gh("") is None


def test_gh_timeout():
    """超时时返回 None。"""
    with patch(
        "engram_core.stats.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=15),
    ):
        assert _gh("traffic/views") is None


def test_gh_invalid_json():
    """无法解析 JSON 时返回 None。"""
    result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"not valid json"
    )
    with patch("engram_core.stats.subprocess.run", return_value=result):
        assert _gh("") is None


def test_gh_endpoint_formatting():
    """endpoint 参数应正确拼接到 URL。"""
    calls = []

    def capture_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"")

    with patch("engram_core.stats.subprocess.run", side_effect=capture_run):
        _gh("traffic/views")

    assert len(calls) == 1
    assert "repos/Patdolitse/engram/traffic/views" in calls[0][2]


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
        patch("engram_core.stats._gh", side_effect=mock_gh),
        patch("engram_core.stats._pypi_recent", return_value=pypi_data),
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
        patch("engram_core.stats._gh", return_value=None),
        patch("engram_core.stats._pypi_recent", return_value=None),
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
        patch("engram_core.stats._gh", side_effect=mock_gh),
        patch("engram_core.stats._pypi_recent", return_value=None),
    ):
        run_stats()

    out = capsys.readouterr().out
    assert "2026-05-20" in out
    assert "2026-05-21" in out
