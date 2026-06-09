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

def _configure_console_utf8() -> None:
    """在 Windows 控制台启用 UTF-8 输出。

    放在函数里而非模块顶层：避免 import 时产生副作用（替换 sys.stdout 会破坏
    pytest 的输出捕获）。只有作为脚本运行时 main() 才调用。
    """
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass


REPO = "Patdolitse/piia-engram"
PYPI_PACKAGE = "piia-engram"

# 牵引力硬闸门 #78：进入 M1 的前提（开源核心地基与之并行，互不阻塞）。
STAR_GATE = 500            # GitHub stars
PYPI_WEEKLY_GATE = 1000    # PyPI 周下载


def engram_dir() -> Path:
    """解析 Engram 数据目录，尊重 ENGRAM_DIR 环境变量（便于隔离测试）。"""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".engram"


def log_file() -> Path:
    """指标历史 JSONL 日志路径（随 ENGRAM_DIR 变化）。"""
    return engram_dir() / "metrics_log.jsonl"


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

    root = engram_dir()
    if not root.exists():
        signals["installed"] = False
        return signals

    signals["installed"] = True

    # 知识条目数
    for name, filename in [
        ("lessons_count", "lessons.json"),
        ("decisions_count", "decisions.json"),
        ("domains_count", "domains.json"),
    ]:
        p = root / filename
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
    qc = root / "quick_context.md"
    if qc.exists():
        import time
        age_days = (time.time() - qc.stat().st_mtime) / 86400
        signals["quick_context_age_days"] = round(age_days, 1)

    # 已配置工具数（通过 identity.json）
    identity = root / "identity.json"
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
    root = engram_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = log_file()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "github": gh,
        "pypi": pypi,
        "local": local,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  Logged to {path}")


def show_dashboard() -> None:
    """展示历史趋势。"""
    path = log_file()
    if not path.exists():
        print("  没有历史日志。先运行: python scripts/metrics.py --log")
        return

    entries = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
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


# ── 周报（牵引力闸门追踪） ───────────────────────────────────────────

def _parse_ts(entry: dict) -> datetime | None:
    """解析一条日志的 timestamp（ISO 8601），失败返回 None。"""
    try:
        return datetime.fromisoformat(entry["timestamp"])
    except (KeyError, ValueError, TypeError):
        return None


def load_log_entries() -> list[dict]:
    """读取并按时间排序历史日志条目（无日志返回空列表）。"""
    path = log_file()
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


def _baseline_entry(entries: list[dict], ref_dt: datetime, target_days: int = 7) -> dict | None:
    """返回最接近 ref_dt 之前 target_days 天的历史条目（严格早于 ref_dt）。"""
    target = target_days * 86400
    best: dict | None = None
    best_diff: float | None = None
    for e in entries:
        ts = _parse_ts(e)
        if ts is None:
            continue
        delta = (ref_dt - ts).total_seconds()
        if delta < 60:  # 忽略「就是现在」或未来的条目
            continue
        diff = abs(delta - target)
        if best_diff is None or diff < best_diff:
            best, best_diff = e, diff
    return best


def _delta(current, previous):
    """两个数值的差；任一缺失返回 None。"""
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)):
        return current - previous
    return None


def _gap(current, gate: int):
    """距离闸门门槛还差多少（已达标为 0）；current 缺失返回 None。"""
    if isinstance(current, (int, float)):
        return max(0, gate - current)
    return None


def build_weekly_digest(gh: dict, pypi: dict) -> dict:
    """构造牵引力周报数据结构（week-over-week + 距 #78 闸门差距）。

    current 取自本次实时抓取（gh/pypi），baseline 取自本地日志中最接近
    7 天前的一条。纯计算、不发网络请求，便于隔离测试。
    """
    now = datetime.now(timezone.utc)
    entries = load_log_entries()
    baseline = _baseline_entry(entries, now, target_days=7)

    cur_stars = gh.get("stars")
    cur_pypi_wk = pypi.get("pypi_downloads_last_week")

    base_stars = baseline.get("github", {}).get("stars") if baseline else None
    base_pypi_wk = baseline.get("pypi", {}).get("pypi_downloads_last_week") if baseline else None

    base_ts = _parse_ts(baseline) if baseline else None
    window_days = round((now - base_ts).total_seconds() / 86400, 1) if base_ts else None

    stars_gap = _gap(cur_stars, STAR_GATE)
    pypi_gap = _gap(cur_pypi_wk, PYPI_WEEKLY_GATE)
    gate_met = (
        isinstance(cur_stars, (int, float)) and cur_stars >= STAR_GATE
        and isinstance(cur_pypi_wk, (int, float)) and cur_pypi_wk >= PYPI_WEEKLY_GATE
    )

    return {
        "generated_at": now.isoformat(),
        "window": {
            "latest": now.isoformat(),
            "baseline": base_ts.isoformat() if base_ts else None,
            "days": window_days,
            "has_baseline": baseline is not None,
        },
        "stars": {
            "current": cur_stars,
            "previous": base_stars,
            "delta": _delta(cur_stars, base_stars),
        },
        "pypi_weekly": {
            "current": cur_pypi_wk,
            "previous": base_pypi_wk,
            "delta": _delta(cur_pypi_wk, base_pypi_wk),
        },
        "gate_78": {
            "star_gate": STAR_GATE,
            "pypi_weekly_gate": PYPI_WEEKLY_GATE,
            "stars_gap": stars_gap,
            "pypi_weekly_gap": pypi_gap,
            "met": gate_met,
        },
    }


def _fmt_delta(d) -> str:
    if d is None:
        return "(无基线)"
    if d > 0:
        return f"+{d}"
    return str(d)


def render_weekly_digest(digest: dict) -> None:
    """终端友好渲染周报。"""
    print(f"\n{'='*56}")
    print(f"  piia-engram Weekly Traction Digest")
    print(f"  {digest['generated_at'][:16].replace('T', ' ')} UTC")
    print(f"{'='*56}")

    win = digest["window"]
    if win["has_baseline"]:
        print(f"\n  对比窗口: 约 {win['days']} 天 (baseline {win['baseline'][:10]} → 今天)")
    else:
        print("\n  对比窗口: 无历史基线 (先跑 `--log` 累积，下周即可显示环比)")

    stars = digest["stars"]
    pypi = digest["pypi_weekly"]
    print(f"\n  指标            当前        环比")
    print(f"  {'─'*44}")
    print(f"  GitHub stars    {str(stars['current']):<10s}  {_fmt_delta(stars['delta'])}")
    print(f"  PyPI 周下载     {str(pypi['current']):<10s}  {_fmt_delta(pypi['delta'])}")

    gate = digest["gate_78"]
    print(f"\n  硬闸门 #78 (进入 M1 的前提)")
    print(f"  {'─'*44}")
    star_gap = gate["stars_gap"]
    pypi_gap = gate["pypi_weekly_gap"]
    star_state = "达标" if star_gap == 0 else (f"还差 {star_gap}" if star_gap is not None else "?")
    pypi_state = "达标" if pypi_gap == 0 else (f"还差 {pypi_gap}" if pypi_gap is not None else "?")
    print(f"  Stars ≥ {gate['star_gate']:<6d}   {str(stars['current']):>6s} / {gate['star_gate']}   {star_state}")
    print(f"  周下载 ≥ {gate['pypi_weekly_gate']:<5d}  {str(pypi['current']):>6s} / {gate['pypi_weekly_gate']}   {pypi_state}")
    print(f"\n  闸门状态: {'✅ 已达标，可进 M1' if gate['met'] else '⛔ 未达标，M1 工程投入保持冻结'}")
    print(f"\n{'='*56}\n")


def weekly_report(gh: dict, pypi: dict, as_json: bool = False) -> dict:
    """生成并输出周报，返回 digest 数据结构。"""
    digest = build_weekly_digest(gh, pypi)
    if as_json:
        print(json.dumps(digest, ensure_ascii=False, indent=2))
    else:
        render_weekly_digest(digest)
    return digest


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    _configure_console_utf8()
    parser = argparse.ArgumentParser(
        description="piia-engram 分发监测",
        epilog=(
            "周报调度建议（不自动创建任何计划任务）：\n"
            "  Windows 任务计划程序 / cron 每周一次运行：\n"
            "    python scripts/metrics.py --log         # 先写一条数据点\n"
            "    python scripts/metrics.py --weekly      # 再输出环比 + 闸门差距\n"
            "  环境变量 ENGRAM_DIR 可重定向数据目录（测试隔离用）。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", action="store_true", help="追加到本地日志")
    parser.add_argument("--dashboard", action="store_true", help="展示历史趋势")
    parser.add_argument("--weekly", action="store_true",
                        help="输出牵引力周报（环比 + 距 #78 闸门差距）")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出（当前仅 --weekly 支持）")
    args = parser.parse_args()

    if args.dashboard:
        show_dashboard()
        return

    if args.weekly:
        gh = fetch_github_stats()
        pypi = fetch_pypi_stats()
        if args.log:
            local = fetch_local_signals()
            append_log(gh, pypi, local)
        weekly_report(gh, pypi, as_json=args.json)
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
