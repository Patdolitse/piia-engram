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

import re
from typing import Iterable

from .governance import SENSITIVITY_ORDER
from .storage import ENCRYPTED_PROFILE_FIELDS

_SEP_RE = re.compile(r"[-.\s]+")


def _norm(name: str) -> str:
    """Normalize separators so api-key / api.key / 'api key' == api_key."""
    return _SEP_RE.sub("_", (name or "").strip().lower())

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
    norm = _norm(n)
    if any(p in norm for p in _SECRET_NAME_PATTERNS):
        return "secret"
    restricted = {_norm(f) for f in restricted_fields}
    if norm in _BUILTIN_PRIVATE_FIELDS or norm in restricted:
        return "private"
    return DEFAULT_LEVEL


def _field_floor(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity FLOOR contributed by a field NAME: ``private``/``secret``
    if the name is sensitive, else ``public`` (no constraint). Used so an
    item carrying a sensitive-named field can't be marked below that level."""
    lvl = classify_field(name, restricted_fields)
    return lvl if lvl in ("private", "secret") else "public"


def _all_field_names(obj, *, max_nodes: int = 10000) -> tuple[set[str], bool]:
    """Collect dict keys nested ANYWHERE in ``obj`` (dicts/lists), iteratively
    (no recursion-limit risk). Returns (names, truncated). ``truncated`` is
    True if the node budget was hit — caller should fail closed, since an
    unscanned region might hide a sensitive field (e.g. metadata.api_key)."""
    names: set[str] = set()
    stack = [obj]
    count = 0
    while stack:
        cur = stack.pop()
        count += 1
        if count > max_nodes:
            return names, True
        if isinstance(cur, dict):
            for k, v in cur.items():
                names.add(str(k))
                stack.append(v)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return names, False


def classify_item(item: dict, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a knowledge item (lesson/decision/playbook).

    Honors an explicit, valid ``item['sensitivity']``; otherwise defaults to
    ``work`` (never ``public``). ``restricted_fields`` is accepted for a
    future content-aware pass; v1 does not down-rank below ``work``.
    """
    if not isinstance(item, dict):
        return DEFAULT_LEVEL
    explicit = str(item.get("sensitivity", "")).strip().lower()
    base = explicit if explicit in VALID_LEVELS else DEFAULT_LEVEL
    # Field-name floor: an item carrying e.g. an `api_key` field — at ANY
    # nesting depth (metadata.api_key, steps[].private_key, …) — is at least
    # `secret`. Explicit level can only RAISE, never lower, this floor.
    names, truncated = _all_field_names(item)
    floor = "secret" if truncated else "public"  # fail closed if not fully scanned
    for name in names:
        f = _field_floor(name, restricted_fields)
        if SENSITIVITY_ORDER[f] > SENSITIVITY_ORDER[floor]:
            floor = f
    return base if SENSITIVITY_ORDER[base] >= SENSITIVITY_ORDER[floor] else floor


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
