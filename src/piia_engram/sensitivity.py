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

VALID_LEVELS = ("public", "work", "private", "secret")
DEFAULT_LEVEL = "work"

# ── Token-based field-name classifier ────────────────────────────────────
# Codex round-5 P1: the old separator-normalization approach (api-key →
# api_key, then substring match) was bypassable with other separators —
# api/key, api:key, api[key], email-address, contact.email all slipped past
# and leaked. The fix is to TOKENIZE the field name instead of enumerating
# separators: split camelCase, then split on any run of non-alphanumeric
# characters. Every separator is a token boundary by construction, so no new
# separator can bypass it. Classification is then by whole-token membership
# and token-group containment — no substring matching, so no false positives
# like "valid_numbers" matching "idnumber".

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _field_tokens(name: str) -> list[str]:
    """Split a field name into lowercase alphanumeric tokens.

    ``apiKey`` → [api, key]; ``api/key`` / ``api[key]`` / ``API Key`` →
    [api, key]; ``email-address`` → [email, address]. Separator-agnostic:
    any non-alphanumeric run is a token boundary, so new separators (``/``,
    ``:``, ``[]``, ``.``, space, …) can't be used to bypass classification.
    """
    s = (name or "").strip()
    if not s:
        return []
    s = _CAMEL_RE.sub(" ", s).lower()
    return [t for t in _NONALNUM_RE.split(s) if t]


# Single tokens that, on their own, mark a field as a credential → "secret".
# Glued separator-less forms (apikey, privatekey, …) tokenize to one token, so
# they live here too rather than needing a substring scan.
_SECRET_TOKENS = frozenset({
    "password", "passwd", "secret", "token", "credential",
    "apikey", "privatekey", "secretkey", "accesskey", "clientsecret",
    "bearertoken", "refreshtoken", "accesstoken",
})
# Token GROUPS (ALL members must be present) that mark a field "secret".
# "key"/"access" alone are too generic (primary_key, access_count) to flag, so
# they only count in combination.
_SECRET_GROUPS = (
    frozenset({"api", "key"}),
    frozenset({"private", "key"}),
    frozenset({"access", "key"}),
    frozenset({"secret", "key"}),
    frozenset({"client", "secret"}),
    frozenset({"bearer", "token"}),
    frozenset({"refresh", "token"}),
)

# PII → "private", derived from the fields already encrypted at rest so the
# floor matches what the product treats as sensitive. Single-word fields are
# single tokens; multi-word ones (real_name, id_number) contribute BOTH a
# group (real+name) and a glued single token (realname) for the separator-less
# spelling.
_PRIVATE_TOKENS = frozenset(
    {f.lower() for f in ENCRYPTED_PROFILE_FIELDS if "_" not in f}
    | {"".join(_field_tokens(f)) for f in ENCRYPTED_PROFILE_FIELDS if "_" in f}
)
_PRIVATE_GROUPS = tuple(
    frozenset(_field_tokens(f)) for f in ENCRYPTED_PROFILE_FIELDS if "_" in f
)


def _groups_match(tokens: set[str], groups: Iterable[frozenset]) -> bool:
    """True if any non-empty group is fully contained in ``tokens``."""
    return any(g and g <= tokens for g in groups)


def classify_field(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a single (identity/profile) field by its NAME.

    Token-based and separator-agnostic. The built-in floor applies with zero
    config; ``restricted_fields`` is an additive layer — it can only RAISE a
    field to ``private`` (the secret check runs first and is never lowered).
    """
    tokens = _field_tokens(name)
    if not tokens:
        return DEFAULT_LEVEL
    tokenset = set(tokens)

    if tokenset & _SECRET_TOKENS or _groups_match(tokenset, _SECRET_GROUPS):
        return "secret"
    if tokenset & _PRIVATE_TOKENS or _groups_match(tokenset, _PRIVATE_GROUPS):
        return "private"
    restricted_groups = [frozenset(_field_tokens(f)) for f in restricted_fields]
    if _groups_match(tokenset, restricted_groups):
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
