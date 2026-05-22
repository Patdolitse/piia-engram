"""engram stats — 一键查看项目增长数据。"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime

logger = logging.getLogger(__name__)

REPO = "Patdolitse/engram"
PYPI_PACKAGE = "piia-engram"


def _gh(endpoint: str = "") -> dict | list | None:
    """调用 GitHub API，返回 JSON 或 None。"""
    try:
        url = f"repos/{REPO}/{endpoint}".rstrip("/")
        result = subprocess.run(
            ["gh", "api", url],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("gh api call failed: %s", exc)
    return None


def _pypi_recent() -> dict | None:
    """从 pypistats API 获取近期下载量。"""
    try:
        import urllib.request
        url = f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/recent"
        req = urllib.request.Request(url, headers={"User-Agent": "engram-stats/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("PyPI stats fetch failed: %s", exc)
        return None


def run_stats() -> None:
    """打印项目增长数据。"""
    print(f"\n  Engram Stats — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("  " + "=" * 44)

    # --- GitHub 基础 ---
    repo = _gh("")
    if repo:
        print(f"\n  GitHub")
        print(f"    Stars:        {repo.get('stargazers_count', '?')}")
        print(f"    Forks:        {repo.get('forks_count', '?')}")
        print(f"    Open Issues:  {repo.get('open_issues_count', '?')}")
        print(f"    Watchers:     {repo.get('subscribers_count', '?')}")
    else:
        print("\n  [!] GitHub API unavailable (need `gh` CLI)")

    # --- Traffic ---
    views = _gh("traffic/views")
    clones = _gh("traffic/clones")
    if views:
        print(f"\n  Page Views (14d)")
        print(f"    Total:        {views.get('count', '?')}")
        print(f"    Unique:       {views.get('uniques', '?')}")
        daily = views.get("views", [])
        recent = [d for d in daily if d.get("count", 0) > 0]
        for d in recent[-5:]:
            date = d["timestamp"][:10]
            print(f"      {date}  {d['count']:>5} views  ({d['uniques']} unique)")

    if clones:
        print(f"\n  Git Clones (14d)")
        print(f"    Total:        {clones.get('count', '?')}")
        print(f"    Unique:       {clones.get('uniques', '?')}")
        daily = clones.get("clones", [])
        recent = [d for d in daily if d.get("count", 0) > 0]
        for d in recent[-5:]:
            date = d["timestamp"][:10]
            print(f"      {date}  {d['count']:>5} clones ({d['uniques']} unique)")

    # --- Referrers ---
    refs = _gh("traffic/popular/referrers")
    if refs:
        print(f"\n  Top Referrers")
        for r in refs[:5]:
            print(f"    {r['referrer']:<25} {r['count']:>4} ({r['uniques']} unique)")

    # --- PyPI ---
    pypi = _pypi_recent()
    if pypi and "data" in pypi:
        data = pypi["data"]
        print(f"\n  PyPI Downloads ({PYPI_PACKAGE})")
        print(f"    Last day:     {data.get('last_day', '?')}")
        print(f"    Last week:    {data.get('last_week', '?')}")
        print(f"    Last month:   {data.get('last_month', '?')}")
    else:
        print(f"\n  [!] PyPI stats unavailable")

    print("\n  " + "=" * 44 + "\n")


def main() -> None:
    run_stats()


if __name__ == "__main__":
    main()
