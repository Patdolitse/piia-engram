"""Export redaction boundary linter — keep secrets/PII out of export surfaces.

WHY: Engram produces several *export surfaces* that are meant to leave the
machine — the portable identity card (``identity_card.md``), AGENTS.md / CLAUDE.md
exports, and (potentially) public reports or harness traces. The AGENTS.md path
is already hardened item-by-item via :func:`sensitivity.classify_item`, but a few
surfaces render free-text lesson/decision summaries directly, and any surface can
in principle embed an absolute user path or a credential shape that leaked into a
stored summary.

This module is a *string-level* boundary linter: given the already-rendered text
of an export surface, it scans for credential shapes (reusing the audited
``sensitivity._SECRET_VALUE_RE`` so the two stay in lockstep) and for absolute
user-home paths. It returns **metadata-only findings** — a category label, the
character offset, and a *redacted* preview (e.g. ``sk-***``) — never the raw
secret. It performs no I/O and no network access, so it is safe to run in CI and
to embed in committable harness reports.

It is deliberately conservative: free prose is allowed; only high-confidence
credential shapes and absolute-home paths are flagged, mirroring the same
near-zero-false-positive bar the sensitivity value-floor already commits to.
"""

from __future__ import annotations

import re
from typing import Any

from .sensitivity import _EMAIL_RE, _SECRET_VALUE_RE

# Absolute user-home paths that must never appear in a committable / shared
# export artifact. Kept narrow (home directories only) so ordinary repo-relative
# paths in a lesson ("see src/foo.py") are not flagged.
_WIN_HOME_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/:*?\"<>|\r\n]+", re.IGNORECASE)
_POSIX_HOME_RE = re.compile(r"/(?:home|Users)/[^/\s:]+")

# Ordered so the most specific / most severe categories win when ranges overlap.
_SCANNERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("secret", "high", _SECRET_VALUE_RE),
    ("user_path", "warn", _WIN_HOME_RE),
    ("user_path", "warn", _POSIX_HOME_RE),
    ("email", "warn", _EMAIL_RE),
)


def _redact(match: str) -> str:
    """Metadata-safe preview: keep a short, non-reconstructable prefix only."""
    if len(match) <= 4:
        return "***"
    return match[:4] + "***"


def scan_export_text(text: str) -> list[dict[str, Any]]:
    """Scan rendered export ``text`` for secret/PII shapes.

    Returns a list of metadata-only findings, each::

        {"category": "secret"|"user_path"|"email",
         "severity": "high"|"warn",
         "offset": int,           # character index of the match start
         "preview": "sk-***"}     # redacted, non-reconstructable

    The raw matched substring is NEVER returned. ``high`` findings indicate a
    credential-shaped body that must block an export; ``warn`` findings are
    boundary leaks (absolute home path, bare email) that should be reviewed.
    """
    if not isinstance(text, str) or not text:
        return []
    findings: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()  # de-dup overlapping ranges, first wins
    for category, severity, pattern in _SCANNERS:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            # Skip if this exact range was already claimed by a higher-priority
            # scanner (e.g. an email inside a path is reported once, as the path).
            if any(span[0] >= s and span[1] <= e for s, e in seen):
                continue
            seen.add(span)
            findings.append(
                {
                    "category": category,
                    "severity": severity,
                    "offset": m.start(),
                    "preview": _redact(m.group(0)),
                }
            )
    findings.sort(key=lambda f: f["offset"])
    return findings


def is_export_clean(text: str, *, allow_warn: bool = True) -> bool:
    """True if ``text`` carries no blocking findings.

    ``high`` (credential) findings always block. ``warn`` findings (absolute
    home paths, bare emails) block only when ``allow_warn=False`` — the strict
    mode used for committable / publicly shared artifacts.
    """
    for f in scan_export_text(text):
        if f["severity"] == "high":
            return False
        if not allow_warn and f["severity"] == "warn":
            return False
    return True


def redact_export_text(text: str, *, placeholder: str = "[REDACTED]") -> str:
    """Return ``text`` with every detected secret/PII shape replaced in place.

    Used as a last-resort scrubber for surfaces that render free prose. Replaces
    highest-severity, longest matches first so nested ranges collapse cleanly.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    spans: list[tuple[int, int]] = []
    for _category, _severity, pattern in _SCANNERS:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))
    if not spans:
        return text
    # Merge overlapping spans, then splice from the end so offsets stay valid.
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out = text
    for s, e in reversed(merged):
        out = out[:s] + placeholder + out[e:]
    return out


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact metadata-only roll-up of :func:`scan_export_text` output.

    Safe to embed in a committable harness report: counts only, no offsets or
    previews of individual hits beyond category tallies.
    """
    by_category: dict[str, int] = {}
    high = 0
    for f in findings:
        by_category[f["category"]] = by_category.get(f["category"], 0) + 1
        if f["severity"] == "high":
            high += 1
    return {
        "total": len(findings),
        "high_severity": high,
        "by_category": by_category,
        "clean": high == 0,
    }
