"""Engram user portrait — timestamped, versioned identity+stats snapshot.

Provided as ``PortraitMixin`` so the methods compose onto the ``Engram``
class at runtime via ``ReportsMixin``.

The portrait is intentionally **lean**: identity fields + aggregate stats
(counts, top domains, active tools) — NOT raw knowledge content. This keeps
each stored snapshot small and privacy-safe while still letting us track how
the user's collaboration profile grows over time (``compare_user_portraits``).
"""

from __future__ import annotations

from typing import Any

from .export_redaction import redact_export_text
from .i18n import get_lang
from .storage import _now_iso, _read_json, _write_json

# How many portrait snapshots to keep on disk (oldest pruned beyond this).
_MAX_PORTRAITS = 50
# How many domains to surface in the human-readable "top" list.
_TOP_DOMAINS = 10
# Schema version so future readers can migrate older snapshots.
_PORTRAIT_SCHEMA_VERSION = 1


def _safe_ts(generated_at: str) -> str:
    """Turn an ISO timestamp into a filesystem-safe stem (no ``:``)."""
    return generated_at.replace(":", "-")


class PortraitMixin:
    """Build, store, and diff lean user portraits."""

    # ------------------------------------------------------------------ build
    def build_user_portrait(self) -> dict[str, Any]:
        """Assemble a lean, read-only portrait of the user.

        Identity (role/description/language/technical_level) + aggregate
        stats (counts, top domains, active tools). No raw lesson/decision
        text is included, so the snapshot stays small and privacy-safe.
        """
        profile = self.get_safe_profile() or {}
        identity = {
            "role": profile.get("role", ""),
            "description": profile.get("description", ""),
            "language": profile.get("language", ""),
            "technical_level": profile.get("technical_level", ""),
        }

        lessons = self.get_lessons(limit=None, _update_access=False, _migrate_fields=False)
        decisions = self.get_decisions(limit=None, _update_access=False, _migrate_fields=False)

        lesson_verified = sum(1 for l in lessons if l.get("tier") == "verified")
        decision_verified = sum(1 for d in decisions if d.get("tier") == "verified")

        # active tools = distinct non-empty source_tool across lessons+decisions
        tools: set[str] = set()
        for item in (*lessons, *decisions):
            st = (item.get("source_tool") or "").strip()
            if st:
                tools.add(st)
        active_tools = sorted(tools)

        domains = self.get_domains() or {}
        domain_sorted = sorted(
            domains.items(),
            key=lambda x: (x[1].get("project_count", 0), x[0]),
            reverse=True,
        )
        top_domains = [
            {"name": name, "count": info.get("project_count", 0)}
            for name, info in domain_sorted[:_TOP_DOMAINS]
        ]
        # FULL domain name list (not just top N) so compare set-diff is accurate.
        domain_names = sorted(domains.keys())

        projects = self.list_projects() or []

        return {
            "schema_version": _PORTRAIT_SCHEMA_VERSION,
            "generated_at": _now_iso(),
            "identity": identity,
            "stats": {
                "lesson_count": len(lessons),
                "lesson_verified": lesson_verified,
                "decision_count": len(decisions),
                "decision_verified": decision_verified,
                "domain_count": len(domains),
                "project_count": len(projects),
                "tool_count": len(active_tools),
            },
            "top_domains": top_domains,
            "domains": domain_names,
            "active_tools": active_tools,
        }

    # ------------------------------------------------------------------ store
    @property
    def _portraits_dir(self):
        return self.root / "portraits"

    def save_user_portrait(self, portrait: dict | None = None) -> dict:
        """Persist a timestamped portrait snapshot; prune to last N.

        Returns the saved portrait (with ``_path`` added). If ``portrait`` is
        None a fresh one is built.
        """
        if portrait is None:
            portrait = self.build_user_portrait()

        self._portraits_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_ts(portrait.get("generated_at", _now_iso()))
        path = self._portraits_dir / f"{stem}.json"
        # Collision (two snapshots in the same second) → append -N suffix.
        n = 1
        while path.exists():
            path = self._portraits_dir / f"{stem}-{n}.json"
            n += 1
        _write_json(path, portrait)

        self._prune_portraits()
        result = dict(portrait)
        result["_path"] = str(path)
        return result

    def _portrait_files(self) -> list:
        """Portrait files sorted oldest→newest by in-file ``generated_at``.

        Sort by the stored timestamp rather than filename: the ``-N``
        collision suffix sorts BEFORE ``.json`` lexically, so filename order
        would misorder same-second snapshots.
        """
        if not self._portraits_dir.exists():
            return []
        files = list(self._portraits_dir.glob("*.json"))

        def _key(p):
            data = _read_json(p)
            ga = data.get("generated_at", "") if isinstance(data, dict) else ""
            return (ga, p.name)

        return sorted(files, key=_key)

    def _prune_portraits(self) -> None:
        files = self._portrait_files()
        excess = len(files) - _MAX_PORTRAITS
        for p in files[:max(0, excess)]:
            try:
                p.unlink()
            except OSError:
                pass

    def list_user_portraits(self) -> list[dict]:
        """List stored portraits (newest first), lightweight metadata only."""
        out = []
        for p in reversed(self._portrait_files()):
            data = _read_json(p)
            if not isinstance(data, dict):
                continue
            out.append({
                "path": str(p),
                "generated_at": data.get("generated_at", ""),
                "stats": data.get("stats", {}),
            })
        return out

    def get_latest_portrait(self) -> dict | None:
        files = self._portrait_files()
        if not files:
            return None
        data = _read_json(files[-1])
        return data if isinstance(data, dict) else None

    def get_previous_portrait(self) -> dict | None:
        """Second-newest stored portrait, or None if fewer than 2 exist."""
        files = self._portrait_files()
        if len(files) < 2:
            return None
        data = _read_json(files[-2])
        return data if isinstance(data, dict) else None

    # ---------------------------------------------------------------- compare
    def compare_user_portraits(self, old: dict, new: dict) -> dict:
        """Compute the growth delta between two portraits.

        Returns count deltas, newly-added domains/tools, and identity changes.
        """
        old = old or {}
        new = new or {}
        old_stats = old.get("stats", {}) or {}
        new_stats = new.get("stats", {}) or {}

        deltas = {}
        for key in sorted(set(old_stats) | set(new_stats)):
            o = old_stats.get(key, 0) or 0
            nv = new_stats.get(key, 0) or 0
            deltas[key] = {"from": o, "to": nv, "delta": nv - o}

        old_domains = set(old.get("domains", []) or [])
        new_domains = set(new.get("domains", []) or [])
        old_tools = set(old.get("active_tools", []) or [])
        new_tools = set(new.get("active_tools", []) or [])

        identity_changes = {}
        old_id = old.get("identity", {}) or {}
        new_id = new.get("identity", {}) or {}
        for key in sorted(set(old_id) | set(new_id)):
            ov = old_id.get(key, "")
            nv = new_id.get(key, "")
            if ov != nv:
                identity_changes[key] = {"from": ov, "to": nv}

        return {
            "from": old.get("generated_at", ""),
            "to": new.get("generated_at", ""),
            "deltas": deltas,
            "new_domains": sorted(new_domains - old_domains),
            "removed_domains": sorted(old_domains - new_domains),
            "new_tools": sorted(new_tools - old_tools),
            "identity_changes": identity_changes,
        }

    # ----------------------------------------------------------------- render
    def render_user_portrait(self, portrait: dict) -> str:
        """Bilingual Markdown view of a single portrait."""
        zh = get_lang() == "zh"
        p = portrait or {}
        ident = p.get("identity", {}) or {}
        stats = p.get("stats", {}) or {}

        lines = [
            "# 我的用户写照" if zh else "# My User Portrait",
            (f"_生成时间: {p.get('generated_at', '')}_" if zh
             else f"_Generated: {p.get('generated_at', '')}_"),
            "_由 Engram 自动生成_" if zh else "_Auto-generated by Engram_",
            "",
            "## 身份" if zh else "## Identity",
        ]
        if ident.get("role"):
            lines.append(f"- {'角色' if zh else 'Role'}: {redact_export_text(ident['role'])}")
        if ident.get("description"):
            lines.append(f"- {'描述' if zh else 'Description'}: {redact_export_text(ident['description'])}")
        if ident.get("language"):
            lines.append(f"- {'语言' if zh else 'Language'}: {ident['language']}")
        if ident.get("technical_level"):
            lines.append(f"- {'技术水平' if zh else 'Technical level'}: {redact_export_text(ident['technical_level'])}")
        lines.append("")

        lines.append("## 积累统计" if zh else "## Accumulation Stats")
        if zh:
            lines.append(f"- 经验: {stats.get('lesson_count', 0)} 条（已验证 {stats.get('lesson_verified', 0)}）")
            lines.append(f"- 决策: {stats.get('decision_count', 0)} 条（已验证 {stats.get('decision_verified', 0)}）")
            lines.append(f"- 领域: {stats.get('domain_count', 0)} 个")
            lines.append(f"- 项目: {stats.get('project_count', 0)} 个")
            lines.append(f"- 活跃工具: {stats.get('tool_count', 0)} 个")
        else:
            lines.append(f"- Lessons: {stats.get('lesson_count', 0)} (verified {stats.get('lesson_verified', 0)})")
            lines.append(f"- Decisions: {stats.get('decision_count', 0)} (verified {stats.get('decision_verified', 0)})")
            lines.append(f"- Domains: {stats.get('domain_count', 0)}")
            lines.append(f"- Projects: {stats.get('project_count', 0)}")
            lines.append(f"- Active tools: {stats.get('tool_count', 0)}")
        lines.append("")

        top = p.get("top_domains", []) or []
        if top:
            lines.append("## 主要领域" if zh else "## Top Domains")
            unit = "条" if zh else "items"
            for d in top:
                lines.append(f"- {redact_export_text(d.get('name', ''))} ({d.get('count', 0)} {unit})")
            lines.append("")

        tools = p.get("active_tools", []) or []
        if tools:
            lines.append("## 活跃工具" if zh else "## Active Tools")
            lines.append("- " + ", ".join(redact_export_text(t) for t in tools))
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def render_portrait_growth(self, diff: dict) -> str:
        """Bilingual Markdown view of a growth comparison."""
        zh = get_lang() == "zh"
        d = diff or {}
        deltas = d.get("deltas", {}) or {}

        lines = [
            "# 成长对比" if zh else "# Growth Comparison",
            (f"_{d.get('from', '')} → {d.get('to', '')}_"),
            "",
        ]

        label_zh = {
            "lesson_count": "经验",
            "lesson_verified": "已验证经验",
            "decision_count": "决策",
            "decision_verified": "已验证决策",
            "domain_count": "领域",
            "project_count": "项目",
            "tool_count": "活跃工具",
        }
        label_en = {
            "lesson_count": "Lessons",
            "lesson_verified": "Verified lessons",
            "decision_count": "Decisions",
            "decision_verified": "Verified decisions",
            "domain_count": "Domains",
            "project_count": "Projects",
            "tool_count": "Active tools",
        }
        lines.append("## 变化" if zh else "## Changes")
        labels = label_zh if zh else label_en
        any_change = False
        for key, lbl in labels.items():
            info = deltas.get(key)
            if not info:
                continue
            delta = info.get("delta", 0)
            sign = f"+{delta}" if delta > 0 else str(delta)
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            lines.append(f"- {lbl}: {info.get('from', 0)} → {info.get('to', 0)} ({arrow} {sign})")
            if delta != 0:
                any_change = True
        if not any_change:
            lines.append("- " + ("无数量变化" if zh else "No count changes"))
        lines.append("")

        new_domains = d.get("new_domains", []) or []
        if new_domains:
            lines.append("## 新增领域" if zh else "## New Domains")
            for name in new_domains:
                lines.append(f"- {redact_export_text(name)}")
            lines.append("")

        new_tools = d.get("new_tools", []) or []
        if new_tools:
            lines.append("## 新增工具" if zh else "## New Tools")
            lines.append("- " + ", ".join(redact_export_text(t) for t in new_tools))
            lines.append("")

        identity_changes = d.get("identity_changes", {}) or {}
        if identity_changes:
            lines.append("## 身份变化" if zh else "## Identity Changes")
            for key, ch in identity_changes.items():
                lines.append(
                    f"- {key}: {redact_export_text(str(ch.get('from', '')))} "
                    f"→ {redact_export_text(str(ch.get('to', '')))}"
                )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
