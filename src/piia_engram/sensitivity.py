"""Sensitivity classification — assigns each field/item a sensitivity level
so the governance gate (governance.py) can enforce a ceiling per agent.

KEY DESIGN POINT (per product owner): **safe by default with ZERO config.**
Most users never set ``trust_boundaries.restricted_fields``, so protection
must NOT depend on configuration. Built-in defaults treat PII
(``ENCRYPTED_PROFILE_FIELDS`` — email/phone/address/…) as ``private`` and
credential-shaped field names as ``secret``, for everyone, out of the box.
A user's ``restricted_fields`` is an *additive* layer on top for power users
(like the founder, who has configured a lot) — it can only RAISE
sensitivity, never lower the built-in floor.

Levels (low → high): public < work < private < secret. Anything unlabeled
defaults to ``work`` (never ``public``) — fail toward not-leaking.

This module is pure (no I/O); it's the input layer feeding ``governance.gate``
and is not itself wired into the read path yet.
"""

from __future__ import annotations

from typing import Iterable

from .storage import ENCRYPTED_PROFILE_FIELDS

VALID_LEVELS = ("public", "work", "private", "secret")
DEFAULT_LEVEL = "work"

# Built-in "private by default" field names (PII). Reuses the same set that is
# already encrypted at rest, so the floor matches what the product already
# treats as sensitive.
_BUILTIN_PRIVATE_FIELDS = frozenset(f.lower() for f in ENCRYPTED_PROFILE_FIELDS)

# Credential-shaped substrings → "secret" by default, wherever they appear.
# Fail-closed bias: better to over-protect a field than leak a key.
_SECRET_NAME_PATTERNS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "private_key", "privatekey", "access_key", "accesskey",
)


def classify_field(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a single (identity/profile) field by its NAME.

    Built-in floor applies with zero config; ``restricted_fields`` only adds.
    """
    n = (name or "").strip().lower()
    if not n:
        return DEFAULT_LEVEL
    if any(p in n for p in _SECRET_NAME_PATTERNS):
        return "secret"
    restricted = {str(f).strip().lower() for f in restricted_fields}
    if n in _BUILTIN_PRIVATE_FIELDS or n in restricted:
        return "private"
    return DEFAULT_LEVEL


def classify_item(item: dict, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a knowledge item (lesson/decision/playbook).

    Honors an explicit, valid ``item['sensitivity']``; otherwise defaults to
    ``work`` (never ``public``). ``restricted_fields`` is accepted for a
    future content-aware pass; v1 does not down-rank below ``work``.
    """
    if isinstance(item, dict):
        s = str(item.get("sensitivity", "")).strip().lower()
        if s in VALID_LEVELS:
            return s
    return DEFAULT_LEVEL


def annotate_items(items: Iterable[dict], restricted_fields: Iterable[str] = ()) -> list[dict]:
    """Return shallow copies of ``items`` with a ``sensitivity`` field set
    (for feeding straight into ``governance.gate``). Non-dict items are kept
    as-is (the gate fail-safes on them)."""
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            c = dict(it)
            c["sensitivity"] = classify_item(it, restricted_fields)
            out.append(c)
        else:
            out.append(it)
    return out
