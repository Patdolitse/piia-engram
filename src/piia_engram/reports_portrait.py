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

    def build_user_portrait_rich(self) -> dict:
        """A richer portrait for the showcase HTML — the "it really knows me" view.

        Layers depth signals onto the lean :meth:`build_user_portrait`: work
        patterns / communication style, a few representative recent lessons &
        decisions (summaries / question→choice only), and project names. All
        reads — no store writes. Heavier than the lean snapshot, so it is NOT
        persisted; only the HTML view uses it.
        """
        p = self.build_user_portrait()

        prefs = self.get_preferences() or {}
        wp = prefs.get("work_patterns") or {}
        patterns: dict = {}
        if isinstance(wp, dict):
            for k, v in wp.items():
                if isinstance(v, (list, tuple)):
                    patterns[str(k)] = ", ".join(str(x) for x in v)
                elif isinstance(v, dict):
                    patterns[str(k)] = "; ".join(f"{ik}: {iv}" for ik, iv in v.items())
                else:
                    patterns[str(k)] = str(v)
        p["work_patterns"] = patterns
        p["communication"] = prefs.get("communication", "") or ""

        def _recent(items, n=6):
            def _ts(it):
                return it.get("timestamp") or it.get("created_at") or it.get("created") or ""
            return sorted(items, key=_ts, reverse=True)[:n]

        lessons = self.get_lessons(limit=None, _update_access=False, _migrate_fields=False) or []
        decisions = self.get_decisions(limit=None, _update_access=False, _migrate_fields=False) or []
        p["recent_lessons"] = [
            {"summary": l.get("summary", ""), "tier": l.get("tier", "")}
            for l in _recent(lessons) if l.get("summary")
        ]
        p["recent_decisions"] = [
            {"question": d.get("question", ""), "choice": d.get("choice", "")}
            for d in _recent(decisions) if (d.get("question") or d.get("choice"))
        ]

        projects = self.list_projects() or []
        names = []
        for pr in projects:
            nm = pr.get("title") or pr.get("name") or pr.get("folder") or ""
            if nm:
                names.append(nm)
        p["projects"] = names[:10]

        # --- drill-in detail + composition (the "show me everything" view) ---
        from collections import Counter

        def _trunc(s, n=600):
            s = (s or "").strip()
            return s if len(s) <= n else s[:n].rstrip() + "…"

        p["all_lessons"] = [
            {"summary": l.get("summary", ""), "detail": _trunc(l.get("detail", "")),
             "tier": l.get("tier", ""), "domain": l.get("domain", "")}
            for l in _recent(lessons, 100000) if l.get("summary")
        ]
        p["all_decisions"] = [
            {"question": d.get("question", ""), "choice": d.get("choice", ""),
             "reasoning": _trunc(d.get("reasoning", "")), "tier": d.get("tier", "")}
            for d in _recent(decisions, 100000) if (d.get("question") or d.get("choice"))
        ]

        by_tier: Counter = Counter()
        by_domain: Counter = Counter()
        for it in (*lessons, *decisions):
            by_tier[(it.get("tier") or "—")] += 1
            for dm in (it.get("domain") or "").split(","):
                dm = dm.strip()
                if dm:
                    by_domain[dm] += 1
        p["composition"] = {
            "by_tier": dict(by_tier),
            "by_domain": [{"name": k, "count": c} for k, c in by_domain.most_common(12)],
        }

        all_ts = [
            t for t in (
                (x.get("timestamp") or x.get("created_at") or x.get("created") or "")
                for x in (*lessons, *decisions)
            ) if t
        ]
        p["first_memory"] = min(all_ts) if all_ts else ""

        tool_counts: Counter = Counter()
        for it in (*lessons, *decisions):
            st = (it.get("source_tool") or "").strip()
            if st:
                tool_counts[st] += 1
        p["tool_usage"] = [{"name": k, "count": c} for k, c in tool_counts.most_common()]

        return p

    def render_user_portrait_html(self, portrait: dict, growth: dict | None = None) -> str:
        """Standalone styled HTML view of a portrait (Memory Lens aesthetic).

        A complete, self-contained page (inline CSS) so a desktop client can open
        it in the browser like the context-preview report. Read-only rendering;
        sensitive identity fields pass through ``redact_export_text``.
        """
        import html as _html

        zh = get_lang() == "zh"
        p = portrait or {}
        ident = p.get("identity", {}) or {}
        stats = p.get("stats", {}) or {}
        lang = "zh" if zh else "en"

        def esc(v):
            return _html.escape("" if v is None else str(v))

        title = "我的用户写照" if zh else "My User Portrait"
        desc = redact_export_text(ident.get("description", "") or "")
        subtitle = desc or (
            "你的身份、积累与成长的只读快照"
            if zh else "A read-only snapshot of your identity and growth"
        )

        def stat(num, label, cls):
            return (
                f'<div class="stat-card"><div class="stat-number {cls}">{esc(num)}</div>'
                f'<div class="stat-label">{esc(label)}</div></div>'
            )

        stat_cards = "".join([
            stat(stats.get("lesson_count", 0), "经验" if zh else "Lessons", "c-accent"),
            stat(stats.get("decision_count", 0), "决策" if zh else "Decisions", "c-green"),
            stat(stats.get("domain_count", 0), "领域" if zh else "Domains", "c-cyan"),
            stat(stats.get("project_count", 0), "项目" if zh else "Projects", "c-pink"),
            stat(stats.get("tool_count", 0), "工具" if zh else "Tools", "c-orange"),
        ])

        id_rows = []
        for kz, ke, val, red in (
            ("角色", "Role", ident.get("role", ""), True),
            ("描述", "Description", ident.get("description", ""), True),
            ("语言", "Language", ident.get("language", ""), False),
            ("技术水平", "Technical level", ident.get("technical_level", ""), True),
        ):
            if not val:
                continue
            shown = redact_export_text(val) if red else val
            id_rows.append(
                f'<li><span class="key">{esc(kz if zh else ke)}</span>'
                f'<span class="val">{esc(shown)}</span></li>'
            )
        id_html = "".join(id_rows) or (
            f'<li><span class="val empty">'
            f'{esc("（暂无身份信息）" if zh else "(no identity yet)")}</span></li>'
        )

        # work style ("how I work") — the deepest "it knows me" signal
        wp = p.get("work_patterns", {}) or {}
        wp_rows = ""
        comm = redact_export_text(p.get("communication", "") or "")
        if comm:
            wp_rows += (
                f'<li><span class="key">{esc("沟通" if zh else "Communication")}</span>'
                f'<span class="val">{esc(comm)}</span></li>'
            )
        for k, v in wp.items():
            if not v:
                continue
            wp_rows += (
                f'<li><span class="key">{esc(redact_export_text(str(k)))}</span>'
                f'<span class="val">{esc(redact_export_text(str(v)))}</span></li>'
            )
        wp_html = ""
        if wp_rows:
            wp_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">格</div>'
                f'<h2>{esc("工作风格" if zh else "How I Work")}</h2></div>'
                f'<div class="card"><ul class="card-list">{wp_rows}</ul></div></div>'
            )

        # lessons — recent teaser + drill-in to the full list ("what you taught me")
        les = p.get("recent_lessons", []) or []
        all_les = p.get("all_lessons", []) or []
        les_items = "".join(
            (
                '<div class="val-item">'
                + (f'<span class="vk">{esc(l.get("tier"))}</span>' if l.get("tier") else "")
                + f'<span>{esc(redact_export_text(l.get("summary", "")))}</span></div>'
            )
            for l in les if l.get("summary")
        )
        les_drill = ""
        if all_les:
            dl = "".join(
                (
                    '<div class="dl-item">'
                    f'<div class="t">{esc(redact_export_text(l.get("summary", "")))}</div>'
                    + (f'<div class="b">{esc(redact_export_text(l.get("detail", "")))}</div>'
                       if l.get("detail") else "")
                    + (
                        '<div class="m">' + esc(l.get("tier", "") or "")
                        + (f' · {esc(redact_export_text(l.get("domain", "")))}' if l.get("domain") else "")
                        + '</div>'
                        if (l.get("tier") or l.get("domain")) else ""
                    )
                    + '</div>'
                )
                for l in all_les
            )
            label = f"查看全部 {len(all_les)} 条经验" if zh else f"View all {len(all_les)} lessons"
            les_drill = (
                f'<details class="drill"><summary>{esc(label)}</summary>'
                f'<div class="dl">{dl}</div></details>'
            )
        les_html = ""
        if les_items or les_drill:
            les_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">验</div>'
                f'<h2>{esc("你教会我的" if zh else "What you taught me")}</h2>'
                f'<span class="count">{len(all_les)}</span></div>'
                f'<div class="card"><div class="val-list">{les_items}</div>{les_drill}</div></div>'
            )

        # decisions — recent teaser + drill-in to the full list
        dec = p.get("recent_decisions", []) or []
        all_dec = p.get("all_decisions", []) or []
        dec_items = "".join(
            (
                '<div class="val-item"><span>'
                f'{esc(redact_export_text(d.get("question", "")))}'
                '<span class="arr"> → </span>'
                f'{esc(redact_export_text(d.get("choice", "")))}</span></div>'
            )
            for d in dec if (d.get("question") or d.get("choice"))
        )
        dec_drill = ""
        if all_dec:
            dd = "".join(
                (
                    '<div class="dl-item">'
                    f'<div class="t">{esc(redact_export_text(d.get("question", "")))}'
                    '<span class="arr"> → </span>'
                    f'{esc(redact_export_text(d.get("choice", "")))}</div>'
                    + (f'<div class="b">{esc(redact_export_text(d.get("reasoning", "")))}</div>'
                       if d.get("reasoning") else "")
                    + (f'<div class="m">{esc(d.get("tier", ""))}</div>' if d.get("tier") else "")
                    + '</div>'
                )
                for d in all_dec
            )
            label = f"查看全部 {len(all_dec)} 条决策" if zh else f"View all {len(all_dec)} decisions"
            dec_drill = (
                f'<details class="drill"><summary>{esc(label)}</summary>'
                f'<div class="dl">{dd}</div></details>'
            )
        dec_html = ""
        if dec_items or dec_drill:
            dec_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">策</div>'
                f'<h2>{esc("你的关键决策" if zh else "Your key decisions")}</h2>'
                f'<span class="count">{len(all_dec)}</span></div>'
                f'<div class="card"><div class="val-list">{dec_items}</div>{dec_drill}</div></div>'
            )

        # projects
        projs = p.get("projects", []) or []
        proj_tags = "".join(
            f'<span class="tag tag-dim">{esc(redact_export_text(pr))}</span>' for pr in projs
        )
        proj_html = ""
        if proj_tags:
            proj_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">目</div>'
                f'<h2>{esc("项目" if zh else "Projects")}</h2></div>'
                f'<div class="card"><div class="tag-wrap">{proj_tags}</div></div></div>'
            )

        # knowledge composition — bars by tier and by domain
        comp = p.get("composition", {}) or {}

        def _bars(pairs):
            mx = max((c for _, c in pairs), default=0) or 1
            return "".join(
                (
                    '<div class="bar">'
                    f'<span class="bl">{esc(redact_export_text(str(name)))}</span>'
                    f'<span class="bt"><span class="bf" style="width:{round(100 * c / mx)}%"></span></span>'
                    f'<span class="bn">{esc(c)}</span></div>'
                )
                for name, c in pairs
            )

        tier_pairs = sorted((comp.get("by_tier", {}) or {}).items(), key=lambda kv: -kv[1])
        dom_pairs = [(d.get("name", ""), d.get("count", 0)) for d in (comp.get("by_domain", []) or [])]
        comp_html = ""
        if tier_pairs or dom_pairs:
            inner = ""
            if tier_pairs:
                inner += (
                    f'<div class="card-title">{esc("按可信度" if zh else "By tier")}</div>'
                    f'<div class="bars">{_bars(tier_pairs)}</div>'
                )
            if dom_pairs:
                inner += (
                    f'<div class="card-title" style="margin-top:18px">'
                    f'{esc("高频领域" if zh else "Top domains")}</div>'
                    f'<div class="bars">{_bars(dom_pairs)}</div>'
                )
            comp_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">构</div>'
                f'<h2>{esc("知识构成" if zh else "Knowledge Composition")}</h2></div>'
                f'<div class="card">{inner}</div></div>'
            )

        # collaboration tools with usage counts
        tu = p.get("tool_usage", []) or []
        tool_usage_tags = "".join(
            f'<span class="tag tag-public">{esc(redact_export_text(t.get("name", "")))}'
            f' · {esc(t.get("count", 0))}</span>'
            for t in tu
        )
        tools_html = ""
        if tool_usage_tags:
            tools_html = (
                '<div class="section"><div class="section-header"><div class="section-icon">具</div>'
                f'<h2>{esc("协作工具" if zh else "Tools")}</h2></div>'
                f'<div class="card"><div class="tag-wrap">{tool_usage_tags}</div></div></div>'
            )

        # "days together" since the first memory
        days_meta = ""
        fm = (p.get("first_memory", "") or "")[:10]
        if fm:
            try:
                from datetime import date
                y, mo, dy = (int(x) for x in fm.split("-")[:3])
                delta = (date.today() - date(y, mo, dy)).days
                if delta >= 0:
                    lbl = f"已陪伴 {delta} 天" if zh else f"{delta} days together"
                    days_meta = f'<span class="meta-item">{esc(lbl)}</span>'
            except Exception:
                days_meta = ""

        growth_html = ""
        if growth:
            deltas = growth.get("deltas", {}) or {}
            labels = {
                "lesson_count": "经验" if zh else "Lessons",
                "decision_count": "决策" if zh else "Decisions",
                "domain_count": "领域" if zh else "Domains",
                "project_count": "项目" if zh else "Projects",
                "tool_count": "工具" if zh else "Tools",
            }
            rows = []
            for k, lbl in labels.items():
                info = deltas.get(k)
                if not info:
                    continue
                delta = info.get("delta", 0)
                if not delta:
                    continue
                sign = f"+{delta}" if delta > 0 else str(delta)
                cls = "c-green" if delta > 0 else "c-orange"
                rows.append(
                    f'<li><span class="key">{esc(lbl)}</span>'
                    f'<span class="val {cls}">{esc(sign)}</span></li>'
                )
            if rows:
                growth_html = (
                    '<div class="section"><div class="section-header">'
                    '<div class="section-icon">↑</div>'
                    f'<h2>{esc("成长" if zh else "Growth")}</h2>'
                    f'<span class="count">{esc(growth.get("from", ""))} → '
                    f'{esc(growth.get("to", ""))}</span></div>'
                    f'<div class="card"><ul class="card-list">{"".join(rows)}</ul></div></div>'
                )

        footer = (
            '由 <span class="brand">ENGRAM</span> 自动生成 · 只读快照，未改动任何记忆'
            if zh else 'Auto-generated by <span class="brand">ENGRAM</span> · read-only snapshot'
        )

        css = """
  :root { --bg:#0a0a0f; --bg-card:#101119; --surface-strip:#13141d;
    --line:rgba(255,255,255,0.07); --line-strong:rgba(255,255,255,0.12);
    --line-faint:rgba(255,255,255,0.045); --border-glow:rgba(139,148,249,0.30);
    --text:#e9e9f3; --text-dim:#9a9ab2; --text-muted:#61617a;
    --accent:#8b94f9; --accent-soft:rgba(139,148,249,0.16);
    --green:#34d399; --orange:#fbbf6e; --cyan:#38bdf8; --pink:#b6a9f6;
    --r:14px; --r-sm:9px; --r-strip:16px; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:"Segoe UI",system-ui,-apple-system,"Microsoft YaHei","PingFang SC",sans-serif;
    background:var(--bg); color:var(--text); line-height:1.62; min-height:100vh;
    -webkit-font-smoothing:antialiased; letter-spacing:0.1px; }
  body::before { content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
    background:radial-gradient(ellipse 90% 55% at 18% 0%, rgba(139,148,249,0.06) 0%, transparent 55%),
      radial-gradient(ellipse 70% 45% at 85% 100%, rgba(56,189,248,0.045) 0%, transparent 55%),
      linear-gradient(180deg,#0c0c13 0%,var(--bg) 38%); }
  body::after { content:''; position:fixed; top:0; left:0; right:0; height:1px; z-index:2; opacity:0.6;
    background:linear-gradient(90deg,transparent,rgba(139,148,249,0.5),rgba(56,189,248,0.35),transparent); }
  ::selection { background:rgba(139,148,249,0.28); color:#fff; }
  .container { max-width:880px; margin:0 auto; padding:44px 24px 64px; position:relative; z-index:1; }
  .hero { text-align:center; padding:6px 0 36px; position:relative; }
  .hero::before { content:''; display:block; width:46px; height:46px; margin:0 auto 20px; border-radius:50%;
    border:1px solid rgba(139,148,249,0.55);
    background:radial-gradient(circle at 50% 42%, rgba(139,148,249,0.30), transparent 68%),
      radial-gradient(circle at 50% 50%, rgba(56,189,248,0.10), transparent 60%);
    box-shadow:0 0 0 6px rgba(139,148,249,0.05),0 8px 26px -10px rgba(139,148,249,0.55),
      inset 0 0 10px rgba(139,148,249,0.25); }
  .hero::after { content:''; position:absolute; bottom:0; left:50%; transform:translateX(-50%);
    width:78%; height:1px; background:linear-gradient(90deg,transparent,var(--line-strong) 35%,var(--line-strong) 65%,transparent); }
  .hero h1 { font-size:2rem; font-weight:750; letter-spacing:-0.025em; line-height:1.16; margin-bottom:12px;
    background:linear-gradient(135deg,#f3f3fb 18%,var(--accent) 130%); -webkit-background-clip:text;
    background-clip:text; -webkit-text-fill-color:transparent; color:transparent; }
  .hero .subtitle { font-size:0.875rem; color:var(--text-dim); margin:0 auto 18px; max-width:560px; line-height:1.6; }
  .hero .tagline { display:inline-flex; align-items:center; gap:8px; padding:6px 16px; border-radius:999px;
    font-size:0.76rem; font-weight:600; color:var(--accent); font-family:Consolas,"JetBrains Mono",monospace;
    background:var(--accent-soft); border:1px solid rgba(139,148,249,0.28); letter-spacing:0.04em; }
  .hero .tagline::before { content:'◉'; font-size:0.72rem; opacity:0.85; }
  .meta-row { display:flex; justify-content:center; gap:10px 22px; margin-top:22px; flex-wrap:wrap; }
  .meta-item { font-size:0.74rem; color:var(--text-muted); display:inline-flex; align-items:center; gap:7px; }
  .meta-item b { color:var(--text-dim); font-weight:600; font-family:Consolas,monospace; }
  .meta-item .dot { width:5px; height:5px; border-radius:50%; background:var(--green); box-shadow:0 0 7px var(--green); }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); margin:30px 0 44px;
    background:linear-gradient(180deg,var(--surface-strip),#101017); border:1px solid var(--line);
    border-radius:var(--r-strip); overflow:hidden;
    box-shadow:inset 0 1px 0 rgba(255,255,255,0.04),0 16px 40px -28px #000; }
  .stat-card { padding:20px 14px 18px; text-align:center; border-right:1px solid var(--line-faint); }
  .stat-card:last-child { border-right:none; }
  .stat-number { font-size:1.85rem; font-weight:700; line-height:1.1; font-variant-numeric:tabular-nums;
    font-family:Consolas,"JetBrains Mono",monospace; }
  .stat-number::after { content:''; display:block; width:20px; height:2px; margin:9px auto 0;
    background:currentColor; border-radius:2px; opacity:0.65; }
  .stat-label { font-size:0.66rem; color:var(--text-muted); letter-spacing:0.09em; text-transform:uppercase;
    margin-top:8px; font-weight:600; }
  .c-accent { color:var(--accent); } .c-green { color:var(--green); } .c-orange { color:var(--orange); }
  .c-cyan { color:var(--cyan); } .c-pink { color:var(--pink); }
  .section { margin-bottom:40px; }
  .section-header { display:flex; align-items:center; gap:13px; margin-bottom:18px; padding-bottom:12px;
    border-bottom:1px solid var(--line); }
  .section-icon { width:32px; height:32px; border-radius:var(--r-sm); display:flex; align-items:center;
    justify-content:center; font-size:14px; font-weight:700; flex-shrink:0; color:var(--accent);
    border:1px solid var(--line-strong); background:var(--accent-soft);
    box-shadow:inset 0 1px 0 rgba(255,255,255,0.05); }
  .section-header h2 { font-size:1.02rem; font-weight:700; letter-spacing:-0.01em; }
  .section-header .count { margin-left:auto; font-size:0.7rem; color:var(--text-dim);
    font-family:Consolas,monospace; padding:3px 12px; border:1px solid var(--line); border-radius:999px;
    background:rgba(255,255,255,0.03); }
  .card { background:var(--bg-card); border:1px solid var(--line); border-radius:var(--r); padding:22px 24px;
    box-shadow:inset 0 1px 0 rgba(255,255,255,0.04),0 10px 30px -24px #000; }
  .card-title { font-size:0.82rem; font-weight:600; margin-bottom:12px; color:var(--text); letter-spacing:0.01em; }
  .card-list { list-style:none; }
  .card-list li { font-size:0.84rem; color:var(--text-dim); padding:10px 0; border-bottom:1px solid var(--line-faint);
    display:flex; align-items:flex-start; gap:14px; }
  .card-list li:last-child { border-bottom:none; padding-bottom:0; }
  .card-list li:first-child { padding-top:0; }
  .card-list .key { color:#aab2ee; min-width:84px; max-width:140px; flex-shrink:0; overflow-wrap:anywhere;
    font-weight:600; letter-spacing:0.02em; }
  .card-list .val { color:var(--text); flex:1; min-width:0; overflow-wrap:anywhere; }
  .empty { color:var(--text-muted); font-style:italic; }
  .val-list { display:flex; flex-direction:column; gap:11px; }
  .val-item { display:flex; gap:10px; align-items:flex-start; line-height:1.6; color:var(--text-dim);
    font-size:0.84rem; }
  .val-item::before { content:""; width:5px; height:5px; border-radius:50%; background:var(--accent);
    margin-top:9px; flex-shrink:0; opacity:0.7; box-shadow:0 0 6px rgba(139,148,249,0.5); }
  .val-item .vk { color:var(--cyan); font-weight:600; flex-shrink:0; font-size:0.7rem;
    background:rgba(56,189,248,0.13); border:1px solid rgba(56,189,248,0.2); border-radius:6px;
    padding:1px 8px; letter-spacing:0.01em; }
  .val-item span:last-child { overflow-wrap:anywhere; }
  .arr { color:var(--accent); font-weight:700; padding:0 2px; }
  .tag-dim { background:rgba(255,255,255,0.045); color:#b6bad4; border:1px solid var(--line-strong);
    padding:5px 12px; font-weight:500; font-family:inherit; }
  .tag { display:inline-block; padding:4px 12px; border-radius:8px; font-size:0.74rem; font-weight:600;
    font-family:Consolas,monospace; white-space:nowrap; letter-spacing:0.02em; }
  .tag-work { background:var(--accent-soft); color:var(--accent); border:1px solid rgba(139,148,249,0.28); }
  .tag-public { background:rgba(56,189,248,0.13); color:var(--cyan); border:1px solid rgba(56,189,248,0.25); }
  .tag-wrap { display:flex; flex-wrap:wrap; gap:8px; }
  .drill { margin-top:13px; border-top:1px solid var(--line-faint); padding-top:11px; }
  .drill > summary { cursor:pointer; font-size:0.74rem; color:var(--accent); font-weight:600;
    list-style:none; letter-spacing:0.02em; padding:3px 0; user-select:none; }
  .drill > summary::-webkit-details-marker { display:none; }
  .drill > summary::before { content:'▸ '; }
  .drill[open] > summary::before { content:'▾ '; }
  .drill[open] > summary { margin-bottom:12px; }
  .dl { display:flex; flex-direction:column; gap:13px; }
  .dl-item { border-left:2px solid var(--line-strong); padding:1px 0 1px 13px; }
  .dl-item .t { color:#bcc2fb; font-weight:600; font-size:0.8rem; line-height:1.5; overflow-wrap:anywhere; }
  .dl-item .b { color:var(--text-dim); font-size:0.77rem; line-height:1.6; margin-top:5px;
    overflow-wrap:anywhere; white-space:pre-wrap; }
  .dl-item .m { font-size:0.6rem; color:var(--text-muted); margin-top:5px; letter-spacing:0.05em;
    text-transform:uppercase; }
  .bars { display:flex; flex-direction:column; gap:9px; }
  .bar { display:grid; grid-template-columns:104px 1fr 34px; align-items:center; gap:10px; font-size:0.74rem; }
  .bar .bl { color:var(--text-dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .bar .bt { height:8px; background:rgba(255,255,255,0.05); border-radius:4px; overflow:hidden; }
  .bar .bf { height:100%; border-radius:4px; background:linear-gradient(90deg,var(--accent),var(--cyan)); min-width:2px; }
  .bar .bn { color:var(--text-muted); text-align:right; font-variant-numeric:tabular-nums;
    font-family:Consolas,monospace; }
  .footer { text-align:center; padding-top:32px; margin-top:26px; border-top:1px solid var(--line); position:relative; }
  .footer::before { content:'✓'; position:absolute; top:-16px; left:50%; transform:translateX(-50%); width:32px;
    height:32px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:14px;
    color:var(--accent); background:var(--bg); border:1px solid rgba(139,148,249,0.4);
    box-shadow:0 0 16px -4px rgba(139,148,249,0.5); }
  .footer p { font-size:0.72rem; color:var(--text-muted); line-height:1.85; margin-top:6px; }
  .footer .brand { font-family:Consolas,monospace; font-weight:700; color:var(--accent); letter-spacing:0.12em; }
  ::-webkit-scrollbar { width:11px; height:11px; }
  ::-webkit-scrollbar-track { background:var(--bg); }
  ::-webkit-scrollbar-thumb { background:#24243a; border-radius:6px; border:3px solid var(--bg); }
  @media (max-width:768px) { .container { padding:32px 16px 48px; } .hero h1 { font-size:1.55rem; } }
"""

        return (
            "<!doctype html>\n"
            f'<html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)}</title><style>{css}</style></head><body>"
            '<div class="container">'
            f'<div class="hero"><h1>{esc(title)}</h1>'
            f'<div class="subtitle">{esc(subtitle)}</div>'
            f'<div class="tagline">{esc(p.get("generated_at", ""))}</div>'
            '<div class="meta-row">'
            f'<span class="meta-item"><span class="dot"></span>{esc("只读快照" if zh else "read-only")}</span>'
            f'<span class="meta-item">{esc("语言" if zh else "Language")} '
            f'<b>{esc(ident.get("language", ""))}</b></span>'
            f"{days_meta}"
            '</div></div>'
            f'<div class="stats">{stat_cards}</div>'
            '<div class="section"><div class="section-header"><div class="section-icon">身</div>'
            f'<h2>{esc("身份" if zh else "Identity")}</h2></div>'
            f'<div class="card"><ul class="card-list">{id_html}</ul></div></div>'
            f"{wp_html}"
            f"{comp_html}"
            f"{les_html}"
            f"{dec_html}"
            f"{tools_html}"
            f"{proj_html}"
            f"{growth_html}"
            f'<div class="footer"><p>{footer}</p></div>'
            "</div></body></html>"
        )
