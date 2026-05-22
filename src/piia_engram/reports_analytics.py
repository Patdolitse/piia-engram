"""Engram analytics layer — health reports, stale detection, digest, stats.

Provided as ``AnalyticsMixin`` so the methods can be composed onto the ``Engram``
class at runtime via ``ReportsMixin``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .storage import (
    MAX_KNOWLEDGE_ENTRIES,
    SCHEMA_VERSION,
    STALE_KNOWLEDGE_DAYS,
    _parse_iso,
)


class AnalyticsMixin:
    """Health reports, stale detection, knowledge digest, stats, and report export."""

    def get_health_report(self) -> dict:
        """Generate a health report for the knowledge asset."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")

        active_lessons = [l for l in lessons if l.get("status") == "active"]
        outdated_lessons = [l for l in lessons if l.get("status") == "outdated"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        outdated_decisions = [d for d in decisions if d.get("status") == "outdated"]

        domain_counts: dict[str, int] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "unknown"
            for _d in raw.split(","):
                _d = _d.strip() or "unknown"
                domain_counts[_d] = domain_counts.get(_d, 0) + 1

        source_counts: dict[str, int] = {}
        for item in active_lessons + active_decisions:
            source = item.get("source_tool", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        duplicates = []
        for i, first in enumerate(active_lessons):
            for second in active_lessons[i + 1:]:
                first_summary = first.get("summary", "")
                second_summary = second.get("summary", "")
                if min(len(first_summary), len(second_summary)) < 8:
                    continue
                sim = self._bigram_similarity(first_summary, second_summary)
                if sim >= 0.45:
                    duplicates.append({
                        "a_id": first.get("id"),
                        "a_summary": first_summary[:50],
                        "b_id": second.get("id"),
                        "b_summary": second_summary[:50],
                        "similarity": round(sim, 2),
                    })

        warnings = []
        if len(active_lessons) > 150:
            warnings.append(f"教训数量较多（{len(active_lessons)}/{MAX_KNOWLEDGE_ENTRIES}），建议清理过时条目")
        if duplicates:
            warnings.append(f"发现 {len(duplicates)} 对近似重复教训，建议合并")
        if outdated_lessons:
            warnings.append(f"{len(outdated_lessons)} 条教训已标记过时，可考虑清理")

        review_cutoff = datetime.now() - timedelta(days=STALE_KNOWLEDGE_DAYS)
        archive_cutoff = datetime.now() - timedelta(days=60)
        lifecycle_items = [
            ("lesson", item)
            for item in active_lessons
        ] + [
            ("decision", item)
            for item in active_decisions
        ]
        items_needing_review = []
        items_to_archive = []
        for item_type, item in lifecycle_items:
            reviewed_at = self._reviewed_at(item)
            if not reviewed_at:
                continue
            view = self._lifecycle_review_view(item_type, item)
            if item.get("access_count", 0) >= 3 and reviewed_at <= review_cutoff:
                items_needing_review.append(view)
            if item.get("access_count", 0) == 0 and reviewed_at <= archive_cutoff:
                items_to_archive.append(view)

        items_needing_review.sort(key=lambda item: item.get("last_reviewed", ""))
        items_to_archive.sort(key=lambda item: item.get("last_reviewed", ""))

        return {
            "summary": {
                "total_lessons": len(lessons),
                "active_lessons": len(active_lessons),
                "outdated_lessons": len(outdated_lessons),
                "total_decisions": len(decisions),
                "active_decisions": len(active_decisions),
                "outdated_decisions": len(outdated_decisions),
            },
            "domain_distribution": domain_counts,
            "source_distribution": source_counts,
            "potential_duplicates": duplicates[:10],
            "items_needing_review": items_needing_review[:10],
            "items_to_archive": items_to_archive[:10],
            "warnings": warnings,
        }

    def _reviewed_at(self, item: dict) -> datetime | None:
        return (
            _parse_iso(item.get("last_reviewed"))
            or _parse_iso(item.get("created_at"))
            or _parse_iso(item.get("timestamp"))
        )

    def _lifecycle_review_view(self, item_type: str, item: dict) -> dict:
        return {
            "id": item.get("id", ""),
            "type": item_type,
            "title": self._knowledge_title(item_type, item),
            "last_reviewed": item.get("last_reviewed") or item.get("created_at", ""),
            "access_count": item.get("access_count", 0),
        }

    def get_stale_knowledge(self, days: int = STALE_KNOWLEDGE_DAYS, limit: int | None = 20) -> dict:
        """Return active lessons and decisions not reviewed for more than days."""
        days = max(0, int(days))
        cutoff = datetime.now() - timedelta(days=days)
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")

        stale_lessons = []
        for lesson in lessons:
            if lesson.get("status") != "active":
                continue
            reviewed_at = self._reviewed_at(lesson)
            if reviewed_at and reviewed_at <= cutoff:
                stale_lessons.append({
                    "id": lesson.get("id", ""),
                    "type": "lesson",
                    "title": lesson.get("summary", ""),
                    "domain": lesson.get("domain", ""),
                    "last_reviewed": lesson.get("last_reviewed") or lesson.get("created_at", ""),
                    "access_count": lesson.get("access_count", 0),
                })

        stale_decisions = []
        for decision in decisions:
            if decision.get("status") != "active":
                continue
            reviewed_at = self._reviewed_at(decision)
            if reviewed_at and reviewed_at <= cutoff:
                stale_decisions.append({
                    "id": decision.get("id", ""),
                    "type": "decision",
                    "title": self._entry_identity_text(decision, "decision"),
                    "domain": decision.get("domain") or decision.get("project", ""),
                    "last_reviewed": decision.get("last_reviewed") or decision.get("created_at", ""),
                    "access_count": decision.get("access_count", 0),
                })

        stale_items = stale_lessons + stale_decisions
        stale_items.sort(key=lambda item: item.get("last_reviewed", ""))
        if limit is not None:
            limit = max(0, int(limit))
            stale_items = stale_items[:limit]
        stale_lessons = [item for item in stale_items if item.get("type") == "lesson"]
        stale_decisions = [item for item in stale_items if item.get("type") == "decision"]

        return {
            "days": days,
            "limit": limit,
            "lessons": stale_lessons,
            "decisions": stale_decisions,
        }

    def get_knowledge_digest(self) -> dict:
        """Return aggregate knowledge summary without updating access metadata."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]

        recent_cutoff = datetime.now() - timedelta(days=7)
        recent_items = []
        for lesson in active_lessons:
            created_at = lesson.get("created_at") or lesson.get("timestamp", "")
            created_dt = _parse_iso(created_at)
            if created_dt and created_dt >= recent_cutoff:
                recent_items.append({
                    "type": "lesson",
                    "title": lesson.get("summary", ""),
                    "domain": lesson.get("domain", ""),
                    "created_at": created_at,
                })
        for decision in active_decisions:
            created_at = decision.get("created_at") or decision.get("timestamp", "")
            created_dt = _parse_iso(created_at)
            if created_dt and created_dt >= recent_cutoff:
                recent_items.append({
                    "type": "decision",
                    "title": self._entry_identity_text(decision, "decision"),
                    "domain": decision.get("domain") or decision.get("project", ""),
                    "created_at": created_at,
                })
        recent_items.sort(key=lambda item: item.get("created_at", ""), reverse=True)

        by_domain: dict[str, dict[str, int]] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "unknown"
            for _d in raw.split(","):
                _d = _d.strip() or "unknown"
                bucket = by_domain.setdefault(_d, {"lessons": 0, "decisions": 0})
                bucket["lessons"] += 1
        for decision in active_decisions:
            domain = (
                decision.get("domain")
                or decision.get("project")
                or decision.get("source_project")
                or "unknown"
            )
            bucket = by_domain.setdefault(domain, {"lessons": 0, "decisions": 0})
            bucket["decisions"] += 1

        top_lessons = sorted(
            active_lessons,
            key=lambda item: item.get("access_count", 0),
            reverse=True,
        )[:5]
        top_decisions = sorted(
            active_decisions,
            key=lambda item: item.get("access_count", 0),
            reverse=True,
        )[:5]
        stale = self.get_stale_knowledge(days=STALE_KNOWLEDGE_DAYS)

        return {
            "total_lessons": len(active_lessons),
            "total_decisions": len(active_decisions),
            "recent_additions": {
                "last_7_days": len(recent_items),
                "items": recent_items[:10],
            },
            "top_accessed": {
                "lessons": [
                    {
                        "title": item.get("summary", ""),
                        "access_count": item.get("access_count", 0),
                        "domain": item.get("domain", ""),
                    }
                    for item in top_lessons
                ],
                "decisions": [
                    {
                        "title": self._entry_identity_text(item, "decision"),
                        "access_count": item.get("access_count", 0),
                    }
                    for item in top_decisions
                ],
            },
            "by_domain": by_domain,
            "stale_count": len(stale["lessons"]) + len(stale["decisions"]),
        }

    def get_knowledge_overview(self, section: str = "all", stale_days: int = STALE_KNOWLEDGE_DAYS) -> dict:
        """Unified overview combining digest, health, and stale checks."""
        result: dict = {}
        if section in ("all", "digest"):
            result["digest"] = self.get_knowledge_digest()
        if section in ("all", "health"):
            result["health"] = self.get_health_report()
        if section in ("all", "stale"):
            result["stale"] = self.get_stale_knowledge(days=stale_days)
        if not result:
            return {"error": f"Unknown section: {section}. Use: all, digest, health, stale."}
        return result

    def export_knowledge_report(self) -> str:
        """Generate and save a Chinese Markdown knowledge report."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        domains = sorted({l.get("domain") for l in active_lessons if l.get("domain")})
        stale = self.get_stale_knowledge(days=STALE_KNOWLEDGE_DAYS)
        title_by_id = {
            **{lesson.get("id", ""): lesson.get("summary", "") for lesson in lessons},
            **{
                decision.get("id", ""): self._entry_identity_text(decision, "decision")
                for decision in decisions
            },
        }

        def related_title_line(item: dict) -> str:
            titles = [
                title_by_id[item_id]
                for item_id in item.get("related_ids", [])
                if title_by_id.get(item_id)
            ]
            return f"  *关联：{', '.join(titles)}*" if titles else ""

        lines = [
            "# 个人知识报告",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 概览",
            f"- 经验教训：{len(active_lessons)} 条（活跃）",
            f"- 关键决策：{len(active_decisions)} 条（活跃）",
            f"- 覆盖领域：{', '.join(domains) if domains else '暂无'}",
            "",
            "## 经验教训",
            "",
        ]

        lessons_by_domain: dict[str, list[dict]] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "未分类"
            domains_list = [d.strip() for d in raw.split(",") if d.strip()] or ["未分类"]
            for _d in domains_list:
                lessons_by_domain.setdefault(_d, []).append(lesson)
        if lessons_by_domain:
            for domain in sorted(lessons_by_domain):
                lines.append(f"### {domain}")
                for lesson in lessons_by_domain[domain]:
                    source = lesson.get("source_tool", "unknown")
                    access_count = lesson.get("access_count", 0)
                    summary = lesson.get("summary", "")
                    detail = lesson.get("detail", "")
                    body = f" — {detail}" if detail else ""
                    lines.append(
                        f"- **{summary}**{body} *(来源: {source}, 访问 {access_count} 次)*"
                    )
                    related_line = related_title_line(lesson)
                    if related_line:
                        lines.append(related_line)
                lines.append("")
        else:
            lines.extend(["暂无经验教训。", ""])

        lines.extend(["## 关键决策", ""])
        decisions_by_month: dict[str, list[dict]] = {}
        for decision in active_decisions:
            created = decision.get("created_at") or decision.get("timestamp", "")
            month = created[:7] if len(created) >= 7 else "未知时间"
            decisions_by_month.setdefault(month, []).append(decision)
        if decisions_by_month:
            for month in sorted(decisions_by_month, reverse=True):
                lines.append(f"### {month}")
                for decision in decisions_by_month[month]:
                    title = self._entry_identity_text(decision, "decision")
                    rationale = decision.get("reasoning") or decision.get("choice", "")
                    status = decision.get("status", "active")
                    lines.append(f"- **{title}** — {rationale} *(状态: {status})*")
                    related_line = related_title_line(decision)
                    if related_line:
                        lines.append(related_line)
                lines.append("")
        else:
            lines.extend(["暂无关键决策。", ""])

        lines.extend([f"## 待复查（超过 {STALE_KNOWLEDGE_DAYS} 天未访问）", ""])
        stale_items = stale["lessons"] + stale["decisions"]
        if stale_items:
            for item in stale_items:
                lines.append(
                    f"- **{item.get('title', '')}** — 最后访问: {item.get('last_reviewed', '')}"
                )
        else:
            lines.append(f"暂无超过 {STALE_KNOWLEDGE_DAYS} 天未访问的活跃知识。")
        lines.append("")

        report = "\n".join(lines)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self._exports_dir / f"knowledge_report_{date_str}.md"
        counter = 1
        while report_path.exists():
            report_path = self._exports_dir / f"knowledge_report_{date_str}_{counter}.md"
            counter += 1
        report_path.write_text(report, encoding="utf-8")
        return report

    def get_stats(self) -> dict:
        """Return statistics about the user's knowledge asset."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        domains = self.get_domains()
        projects = self.list_projects()
        profile = self.get_profile()

        source_breakdown = {}
        for lesson in active_lessons:
            tool = lesson.get("source_tool", "unknown")
            source_breakdown[tool] = source_breakdown.get(tool, 0) + 1

        return {
            "total_lessons": len(lessons),
            "active_lessons": len(active_lessons),
            "outdated_lessons": len(lessons) - len(active_lessons),
            "total_decisions": len(decisions),
            "active_decisions": len(active_decisions),
            "outdated_decisions": len(decisions) - len(active_decisions),
            "domain_count": len(domains),
            "project_count": len(projects),
            "domains": {
                name: info.get("project_count", 0)
                for name, info in domains.items()
            },
            "lessons_by_source_tool": source_breakdown,
            "has_profile": bool(profile),
            "has_preferences": bool(self.get_preferences()),
            "has_quality_standards": bool(self.get_quality_standards()),
            "has_trust_boundaries": bool(self.get_trust_boundaries()),
            "schema_version": SCHEMA_VERSION,
        }
