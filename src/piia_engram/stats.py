"""engram stats — 一键查看项目增长数据。"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone

from piia_engram.i18n import t

logger = logging.getLogger(__name__)

REPO = "Patdolitse/piia-engram"
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
    print(f"\n  {t('Engram 数据概览', 'Engram Stats')} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("  " + "=" * 44)

    # --- GitHub 基础 ---
    repo = _gh("")
    if repo:
        print(f"\n  GitHub")
        print(f"    {t('星标', 'Stars')}:        {repo.get('stargazers_count', '?')}")
        print(f"    {t('复刻', 'Forks')}:        {repo.get('forks_count', '?')}")
        print(f"    {t('议题', 'Open Issues')}:  {repo.get('open_issues_count', '?')}")
        print(f"    {t('关注', 'Watchers')}:     {repo.get('subscribers_count', '?')}")
    else:
        print(t("\n  [!] GitHub API 不可用（需要 `gh` CLI）",
               "\n  [!] GitHub API unavailable (need `gh` CLI)"))

    # --- Traffic ---
    views = _gh("traffic/views")
    clones = _gh("traffic/clones")
    if views:
        print(f"\n  {t('页面访问 (14天)', 'Page Views (14d)')}")
        print(f"    {t('总计', 'Total')}:        {views.get('count', '?')}")
        print(f"    {t('独立', 'Unique')}:       {views.get('uniques', '?')}")
        daily = views.get("views", [])
        recent = [d for d in daily if d.get("count", 0) > 0]
        for d in recent[-5:]:
            date = d["timestamp"][:10]
            print(f"      {date}  {d['count']:>5} {t('次访问', 'views')}  ({d['uniques']} {t('独立', 'unique')})")

    if clones:
        print(f"\n  {t('Git 克隆 (14天)', 'Git Clones (14d)')}")
        print(f"    {t('总计', 'Total')}:        {clones.get('count', '?')}")
        print(f"    {t('独立', 'Unique')}:       {clones.get('uniques', '?')}")
        daily = clones.get("clones", [])
        recent = [d for d in daily if d.get("count", 0) > 0]
        for d in recent[-5:]:
            date = d["timestamp"][:10]
            print(f"      {date}  {d['count']:>5} {t('次克隆', 'clones')} ({d['uniques']} {t('独立', 'unique')})")

    # --- Referrers ---
    refs = _gh("traffic/popular/referrers")
    if refs:
        print(f"\n  {t('来源排行', 'Top Referrers')}")
        for r in refs[:5]:
            print(f"    {r['referrer']:<25} {r['count']:>4} ({r['uniques']} {t('独立', 'unique')})")

    # --- PyPI ---
    pypi = _pypi_recent()
    if pypi and "data" in pypi:
        data = pypi["data"]
        print(f"\n  {t('PyPI 下载量', 'PyPI Downloads')} ({PYPI_PACKAGE})")
        print(f"    {t('昨日', 'Last day')}:     {data.get('last_day', '?')}")
        print(f"    {t('上周', 'Last week')}:    {data.get('last_week', '?')}")
        print(f"    {t('上月', 'Last month')}:   {data.get('last_month', '?')}")
    else:
        print(t("\n  [!] PyPI 统计不可用", "\n  [!] PyPI stats unavailable"))

    print("\n  " + "=" * 44 + "\n")


def log_stats() -> None:
    """Append a JSON snapshot of current stats to ~/.engram/stats.log."""
    import os
    from pathlib import Path

    data_dir = Path(os.environ.get("ENGRAM_DIR", "") or Path.home() / ".engram")
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "stats.log"

    snapshot: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}

    repo = _gh("")
    if repo:
        snapshot["github"] = {
            "stars": repo.get("stargazers_count", 0),
            "forks": repo.get("forks_count", 0),
            "open_issues": repo.get("open_issues_count", 0),
            "watchers": repo.get("subscribers_count", 0),
        }

    views = _gh("traffic/views")
    if views:
        snapshot["views_14d"] = {
            "total": views.get("count", 0),
            "unique": views.get("uniques", 0),
        }

    clones = _gh("traffic/clones")
    if clones:
        snapshot["clones_14d"] = {
            "total": clones.get("count", 0),
            "unique": clones.get("uniques", 0),
        }

    pypi = _pypi_recent()
    if pypi and "data" in pypi:
        snapshot["pypi"] = pypi["data"]

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    print(t(f"  统计快照已保存到 {log_path}",
           f"  Stats snapshot saved to {log_path}"))


def main() -> None:
    import sys
    if "--log" in sys.argv:
        log_stats()
    else:
        run_stats()


if __name__ == "__main__":
    main()
