#!/usr/bin/env python3
"""piia-engram 分发监测脚本。

拉取 GitHub + PyPI 指标，输出到终端和本地 JSON 日志。
无外部依赖——仅用标准库 + gh CLI。

用法:
    python scripts/metrics.py              # 一次性拉取并展示
    python scripts/metrics.py --log        # 拉取并追加到 ~/.engram/metrics_log.jsonl
    python scripts/metrics.py --dashboard  # 展示历史趋势（需先有 log）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = "Patdolitse/piia-engram"
PYPI_PACKAGE = "piia-engram"
ENGRAM_DIR = Path.home() / ".engram"
LOG_FILE = ENGRAM_DIR / "metrics_log.jsonl"


# ── GitHub API (via gh CLI) ──────────────────────────────────────────

def _gh_api(endpoint: str) -> dict | list | None:
    """调用 GitHub API，返回 JSON 或 None。"""
    try:
        path = f"repos/{REPO}/{endpoint}".rstrip("/")
        r = subprocess.run(
            ["gh", "api", path],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout:
            return json.loads(r.stdout.decode("utf-8", errors="replace"))
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def fetch_github_stats() -> dict:
    """获取 GitHub 仓库核心指标。"""
    stats: dict = {}

    # 基本信息
    repo = _gh_api("")
    if repo:
        stats["stars"] = repo.get("stargazers_count", 0)
        stats["forks"] = repo.get("forks_count", 0)
        stats["watchers"] = repo.get("subscribers_count", 0)
        stats["open_issues"] = repo.get("open_issues_count", 0)

    # Traffic — 需要 push 权限
    views = _gh_api("traffic/views")
    if views:
        stats["views_14d"] = views.get("count", 0)
        stats["unique_visitors_14d"] = views.get("uniques", 0)

    clones = _gh_api("traffic/clones")
    if clones:
        stats["clones_14d"] = clones.get("count", 0)
        stats["unique_cloners_14d"] = clones.get("uniques", 0)

    # Referral sources
    referrers = _gh_api("traffic/popular/referrers")
    if referrers:
        stats["top_referrers"] = [
            {"source": r["referrer"], "count": r["count"], "uniques": r["uniques"]}
            for r in referrers[:5]
        ]

    return stats


# ── PyPI Stats ───────────────────────────────────────────────────────

def fetch_pypi_stats() -> dict:
    """获取 PyPI 下载量（最近 30 天）。"""
    stats: dict = {}
    # 尝试两个 API 端点
    urls = [
        f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/recent",
        f"https://pypistats.org/api/packages/{PYPI_PACKAGE.replace('-', '_')}/recent",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "piia-engram-metrics/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if "data" in data:
                    stats["pypi_downloads_last_day"] = data["data"].get("last_day", 0)
                    stats["pypi_downloads_last_week"] = data["data"].get("last_week", 0)
                    stats["pypi_downloads_last_month"] = data["data"].get("last_month", 0)
                    break
        except Exception:
            continue
    return stats


# ── 本地使用信号 ─────────────────────────────────────────────────────

def fetch_local_signals() -> dict:
    """收集本地使用指标（隐私安全，不出本机）。"""
    signals: dict = {}

    engram_dir = ENGRAM_DIR
    if not engram_dir.exists():
        signals["installed"] = False
        return signals

    signals["installed"] = True

    # 知识条目数
    for name, filename in [
        ("lessons_count", "lessons.json"),
        ("decisions_count", "decisions.json"),
        ("domains_count", "domains.json"),
    ]:
        p = engram_dir / filename
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    signals[name] = len(data)
                elif isinstance(data, dict):
                    signals[name] = len(data)
            except (json.JSONDecodeError, OSError):
                pass

    # quick_context 新鲜度
    qc = engram_dir / "quick_context.md"
    if qc.exists():
        import time
        age_days = (time.time() - qc.stat().st_mtime) / 86400
        signals["quick_context_age_days"] = round(age_days, 1)

    # 已配置工具数（通过 identity.json）
    identity = engram_dir / "identity.json"
    if identity.exists():
        try:
            data = json.loads(identity.read_text(encoding="utf-8"))
            profile = data.get("profile", {})
            signals["profile_fields_set"] = sum(1 for v in profile.values() if v)
        except (json.JSONDecodeError, OSError):
            pass

    return signals


# ── 展示 ─────────────────────────────────────────────────────────────

def display_metrics(gh: dict, pypi: dict, local: dict) -> None:
    """终端友好展示。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*50}")
    print(f"  piia-engram Metrics  |  {now}")
    print(f"{'='*50}")

    # GitHub
    print("\n  GitHub")
    print(f"  {'─'*40}")
    if gh:
        print(f"  Stars:           {gh.get('stars', '?')}")
        print(f"  Forks:           {gh.get('forks', '?')}")
        print(f"  Watchers:        {gh.get('watchers', '?')}")
        if "views_14d" in gh:
            print(f"  Views (14d):     {gh['views_14d']}  (unique: {gh.get('unique_visitors_14d', '?')})")
        if "clones_14d" in gh:
            print(f"  Clones (14d):    {gh['clones_14d']}  (unique: {gh.get('unique_cloners_14d', '?')})")
        if "top_referrers" in gh:
            print(f"  Top referrers:")
            for r in gh["top_referrers"]:
                print(f"    {r['source']:20s}  {r['count']:>4} views  ({r['uniques']} unique)")
    else:
        print("  (无法获取——确认 gh CLI 已登录)")

    # PyPI
    print(f"\n  PyPI Downloads")
    print(f"  {'─'*40}")
    if pypi:
        print(f"  Last day:        {pypi.get('pypi_downloads_last_day', '?')}")
        print(f"  Last week:       {pypi.get('pypi_downloads_last_week', '?')}")
        print(f"  Last month:      {pypi.get('pypi_downloads_last_month', '?')}")
    else:
        print("  (无法获取)")

    # Local
    print(f"\n  Local Usage")
    print(f"  {'─'*40}")
    if not local.get("installed"):
        print("  Engram 未安装")
    else:
        print(f"  Lessons:         {local.get('lessons_count', 0)}")
        print(f"  Decisions:       {local.get('decisions_count', 0)}")
        print(f"  Domains:         {local.get('domains_count', 0)}")
        print(f"  Profile fields:  {local.get('profile_fields_set', 0)}")
        if "quick_context_age_days" in local:
            age = local["quick_context_age_days"]
            freshness = "fresh" if age < 1 else ("ok" if age < 7 else "STALE")
            print(f"  Context age:     {age}d  ({freshness})")

    print(f"\n{'='*50}\n")


# ── 日志 ─────────────────────────────────────────────────────────────

def append_log(gh: dict, pypi: dict, local: dict) -> None:
    """追加一行到 JSONL 日志。"""
    ENGRAM_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "github": gh,
        "pypi": pypi,
        "local": local,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  Logged to {LOG_FILE}")


def show_dashboard() -> None:
    """展示历史趋势。"""
    if not LOG_FILE.exists():
        print("  没有历史日志。先运行: python scripts/metrics.py --log")
        return

    entries = []
    for line in LOG_FILE.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            entries.append(json.loads(line))

    if not entries:
        print("  日志为空。")
        return

    print(f"\n{'='*60}")
    print(f"  piia-engram Metrics Dashboard  ({len(entries)} data points)")
    print(f"{'='*60}")

    print(f"\n  {'Date':<22s} {'Stars':>6s} {'PyPI/wk':>8s} {'Lessons':>8s} {'Visitors':>9s}")
    print(f"  {'─'*55}")

    for e in entries[-14:]:  # 最近 14 条
        ts = e["timestamp"][:16].replace("T", " ")
        stars = e.get("github", {}).get("stars", "?")
        pypi_wk = e.get("pypi", {}).get("pypi_downloads_last_week", "?")
        lessons = e.get("local", {}).get("lessons_count", "?")
        visitors = e.get("github", {}).get("unique_visitors_14d", "?")
        print(f"  {ts:<22s} {str(stars):>6s} {str(pypi_wk):>8s} {str(lessons):>8s} {str(visitors):>9s}")

    # 增长计算
    if len(entries) >= 2:
        first, last = entries[0], entries[-1]
        star_first = first.get("github", {}).get("stars", 0)
        star_last = last.get("github", {}).get("stars", 0)
        if star_first and star_last:
            print(f"\n  Star growth: {star_first} → {star_last} (+{star_last - star_first})")

    print(f"\n{'='*60}\n")


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="piia-engram 分发监测")
    parser.add_argument("--log", action="store_true", help="追加到本地日志")
    parser.add_argument("--dashboard", action="store_true", help="展示历史趋势")
    args = parser.parse_args()

    if args.dashboard:
        show_dashboard()
        return

    print("  Fetching metrics...")
    gh = fetch_github_stats()
    pypi = fetch_pypi_stats()
    local = fetch_local_signals()

    display_metrics(gh, pypi, local)

    if args.log:
        append_log(gh, pypi, local)


if __name__ == "__main__":
    main()
