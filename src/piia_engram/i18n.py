"""Engram shared i18n — bilingual text support (中文/English).

Usage::

    from piia_engram.i18n import t, get_lang

    print(t("你好", "Hello"))
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Runtime override — setup_wizard sets this during interactive setup.
_runtime_lang: str | None = None


def set_lang(lang: str) -> None:
    """Override the runtime language (called by setup_wizard during interactive flow)."""
    global _runtime_lang
    _runtime_lang = lang


def get_lang() -> str:
    """Detect user language. Priority: runtime override > profile preference > default zh."""
    if _runtime_lang is not None:
        return _runtime_lang
    # Read from profile.json without importing core (avoids circular deps)
    try:
        engram_dir = Path.home() / ".engram"
        profile_path = engram_dir / "identity" / "profile.json"
        if profile_path.is_file():
            import json
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            lang = (data.get("language") or "").lower()
            if "en" in lang:
                return "en"
    except Exception:
        pass
    return "zh"


def t(zh: str, en: str) -> str:
    """Return Chinese or English text based on current language."""
    return zh if get_lang() == "zh" else en
