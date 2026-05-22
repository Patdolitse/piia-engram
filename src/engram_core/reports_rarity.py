"""Engram rarity classification — WoW/Diablo-style quality tiers.

Provided as ``RarityMixin`` so the methods can be composed onto the ``Engram``
class at runtime via ``ReportsMixin``.
"""

from __future__ import annotations


class RarityMixin:
    """WoW/Diablo-style knowledge quality classification."""

    # Simplified 3-tier quality system (only verified knowledge gets color)
    # Staging items shown in neutral gray — they haven't earned a color yet.
    RARITY_TIERS = {
        "legendary": {"color": "#ff8000", "label_zh": "传说", "label_en": "Legendary", "glow": "rgba(255,128,0,0.15)"},
        "epic":      {"color": "#a335ee", "label_zh": "史诗", "label_en": "Epic",      "glow": "rgba(163,53,238,0.15)"},
        "rare":      {"color": "#0070dd", "label_zh": "精华", "label_en": "Quality",   "glow": "rgba(0,112,221,0.12)"},
        "staging":   {"color": "#9d9d9d", "label_zh": "暂存", "label_en": "Staging",   "glow": "rgba(157,157,157,0.05)"},
    }

    def classify_rarity(self, item: dict, item_type: str = "lesson") -> str:
        """Classify a knowledge item into a rarity tier.

        Simplified 3-tier system for verified knowledge:
          legendary (★★★) — strategic decisions, identity, core principles
          epic      (★★)  — impactful lessons, workflow-changing experience
          rare      (★)   — verified useful knowledge

        Staging items always return "staging" (gray, no stars).
        Only items that pass the quality bar earn a color.
        """
        knowledge_tier = item.get("tier", "verified")
        if knowledge_tier == "staging":
            return "staging"

        score = 0
        domain = (item.get("domain") or "").lower()
        source = (item.get("source_tool") or "").lower()
        detail = item.get("detail") or item.get("reasoning") or ""
        summary = item.get("summary") or item.get("question") or ""
        access_count = item.get("access_count", 0)

        # --- Type bonus ---
        if item_type == "decision":
            score += 3  # Decisions are inherently more valuable

        # --- Identity / foundational content ---
        identity_keywords = {"身份", "identity", "角色", "role", "核心", "core",
                             "原则", "principle", "基础", "foundation", "定位"}
        combined_text = f"{summary} {detail} {domain}".lower()
        if any(kw in combined_text for kw in identity_keywords):
            score += 4

        # --- Detail richness ---
        if len(detail) > 200:
            score += 2
        elif len(detail) > 50:
            score += 1

        # --- Domain specificity ---
        if domain and domain not in ("general", "auto_reconcile", "ai_config"):
            score += 1

        # --- Reasoning quality (decisions) ---
        if item.get("reasoning") and len(item["reasoning"]) > 30:
            score += 2

        # --- Access frequency ---
        if access_count >= 5:
            score += 2
        elif access_count >= 2:
            score += 1

        # --- Source penalty ---
        if source in ("auto_reconcile", "config_scan"):
            score -= 1

        # --- Map score to 3 tiers ---
        if score >= 7:
            return "legendary"
        elif score >= 4:
            return "epic"
        return "rare"
