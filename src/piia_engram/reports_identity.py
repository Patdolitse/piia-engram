"""Engram identity card export — portable Markdown summary for any AI tool.

Provided as ``IdentityCardMixin`` so the methods can be composed onto the
``Engram`` class at runtime via ``ReportsMixin``.
"""

from __future__ import annotations

from .storage import _now_iso


class IdentityCardMixin:
    """Export a portable Markdown identity card."""

    def export_identity_card(self) -> str:
        """Export a portable Markdown identity card.

        User can paste this into ANY AI tool (Claude, GPT, Cursor, etc.)
        and the AI will immediately understand their work style.
        """
        lines = [
            "# 我的 AI 协作身份卡",
            f"_生成时间: {_now_iso()}_",
            f"_由 Engram 自动生成_",
            "",
        ]

        profile = self.get_safe_profile()
        if profile:
            lines.append("## 我是谁")
            if profile.get("role"):
                lines.append(f"- {profile['role']}")
            if profile.get("description"):
                lines.append(f"- {profile['description']}")
            if profile.get("language"):
                lines.append(f"- 使用语言: {profile['language']}")
            if profile.get("technical_level"):
                lines.append(f"- 技术水平: {profile['technical_level']}")
            lines.append("")

        style = self.get_work_style()
        if style:
            lines.append("## 我的工作方式")
            if style.get("preferences"):
                for k, v in style["preferences"].items():
                    lines.append(f"- {k}: {v}")
            if style.get("communication"):
                lines.append(f"- {style['communication']}")
            lines.append("")

        standards = self.get_quality_standards()
        if standards:
            lines.append("## 我的质量标准")
            if standards.get("rules"):
                for rule in standards["rules"]:
                    lines.append(f"- {rule}")
            lines.append("")

        domains = self.get_domains()
        if domains:
            lines.append("## 我的经验")
            for name, info in sorted(
                domains.items(),
                key=lambda x: x[1].get("project_count", 0),
                reverse=True,
            ):
                count = info.get("project_count", 0)
                lines.append(f"- {name} ({count} 个项目)")
            lines.append("")

        lessons = self.get_lessons(limit=10)
        if lessons:
            lines.append("## 我踩过的坑（请帮我避免）")
            for l in lessons:
                lines.append(f"- {l.get('summary', '')}")
            lines.append("")

        decisions = self.get_decisions(limit=8, _update_access=False)
        if decisions:
            lines.append("## 我的关键决策（请遵循）")
            for d in decisions:
                question = d.get("question") or d.get("title") or ""
                choice = d.get("choice", "")
                if question and choice:
                    lines.append(f"- {question} → {choice}")
                elif question:
                    lines.append(f"- {question}")
                elif choice:
                    lines.append(f"- {choice}")
            lines.append("")

        lines.append("---")
        lines.append("_把这段文字粘贴到任何 AI 对话的开头，AI 就能立刻了解你。_")

        card = "\n".join(lines)

        # Also save to exports folder
        export_path = self._exports_dir / "identity_card.md"
        export_path.write_text(card, encoding="utf-8")

        return card
