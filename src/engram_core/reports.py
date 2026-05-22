"""Engram reports layer — health reports, identity card, review HTML, stats.

This module composes four sub-mixins into a single ``ReportsMixin``:

- ``RarityMixin``        — WoW-style quality classification
- ``ReviewMixin``        — interactive HTML review page, promote/archive
- ``IdentityCardMixin``  — portable Markdown identity card
- ``AnalyticsMixin``     — health reports, stale detection, digest, stats
"""

from __future__ import annotations

from .reports_rarity import RarityMixin
from .reports_review import ReviewMixin
from .reports_identity import IdentityCardMixin
from .reports_analytics import AnalyticsMixin


class ReportsMixin(RarityMixin, ReviewMixin, IdentityCardMixin, AnalyticsMixin):
    """Reports, reviews, identity card, stats."""
